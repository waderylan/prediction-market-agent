"""Run the local browser workbench through Kessel's Claude and Codex routes."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
PORTS = {"claude": 8080, "codex": 8081}
DEFAULT_KESSEL = ROOT.parents[1] / "kessel" / ".venv" / "Scripts" / "kessel.exe"


class KesselUiHandler(SimpleHTTPRequestHandler):
    """Keep Kessel credentials in backend processes, never in browser responses."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            status = {provider: _healthy(port) for provider, port in PORTS.items()}
            payload = json.dumps({"providers": status}).encode()
            self._send_json(
                HTTPStatus.OK if all(status.values()) else HTTPStatus.BAD_GATEWAY, payload
            )
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/api/chat", "/api/chat/inspect"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65_536:
                raise ValueError
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError
            provider = body.pop("provider", None)
            if provider not in PORTS:
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, b'{"detail":"Invalid request or provider."}')
            return
        request = urllib.request.Request(
            f"http://127.0.0.1:{PORTS[provider]}{self.path.removeprefix('/api')}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=185) as response:
                self._send_json(response.status, response.read())
        except urllib.error.HTTPError as error:
            self._send_json(error.code, error.read())
        except (urllib.error.URLError, TimeoutError):
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                b'{"detail":"The selected local agent is unavailable."}',
            )

    def _send_json(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format_string: str, *args: object) -> None:
        # Never log request bodies, responses, or credentials.
        print(f"UI {self.address_string()} {format_string % args}")


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return response.status == HTTPStatus.OK
    except (urllib.error.URLError, TimeoutError):
        return False


def _wait_for_health(process: subprocess.Popen[bytes], port: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Kessel-backed agent on port {port} stopped during startup")
        if _healthy(port):
            return
        time.sleep(0.2)
    raise RuntimeError(f"Kessel-backed agent on port {port} did not become ready")


def _stop(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            if sys.platform == "win32":
                # Kessel starts the API as a child; terminate that exact process tree.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3000, help="Browser UI port")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--kessel-exe", type=Path, default=DEFAULT_KESSEL)
    args = parser.parse_args()
    if not args.kessel_exe.is_file():
        parser.error("Kessel executable is missing; pass --kessel-exe")

    processes: list[subprocess.Popen[bytes]] = []
    server: ThreadingHTTPServer | None = None
    try:
        for provider, port in PORTS.items():
            environment = os.environ.copy()
            environment.update(
                {"PORT": str(port), "OPENAI_MODEL": "default", "LLM_TIMEOUT_SECONDS": "150"}
            )
            process = subprocess.Popen(
                [
                    str(args.kessel_exe),
                    "run",
                    "--provider",
                    provider,
                    "--",
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "market_agent.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=ROOT,
                env=environment,
            )
            processes.append(process)
            _wait_for_health(process, port)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), KesselUiHandler)
        url = f"http://127.0.0.1:{args.port}"
        print(f"Market Lens (Kessel) is ready at {url}")
        print("Press Ctrl+C to stop the UI and its two local agents.")
        if not args.no_browser:
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.server_close()
        _stop(processes)


if __name__ == "__main__":
    main()
