"""Run the local HTML client, headless Codex gateway, and unchanged chat API."""

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
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
BACKEND_URL = "http://127.0.0.1:8080"


class ChatUiHandler(SimpleHTTPRequestHandler):
    """Serve the UI and proxy its two API routes to the local FastAPI process."""

    backend_url: ClassVar[str] = BACKEND_URL

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            self._proxy("GET", "/health")
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/chat/inspect":
            self._proxy("POST", "/chat/inspect")
            return
        if self.path == "/api/chat":
            self._proxy("POST", "/chat")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _proxy(self, method: str, target: str) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        if length > 65_536:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        body = self.rfile.read(length) if length else None
        request = urllib.request.Request(
            self.backend_url + target,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=155) as response:
                payload = response.read()
                self._send_proxy_response(response.status, payload)
        except urllib.error.HTTPError as error:
            self._send_proxy_response(error.code, error.read())
        except (urllib.error.URLError, TimeoutError):
            payload = json.dumps(
                {"detail": "The local chat API is unavailable. Restart the UI runner."}
            ).encode()
            self._send_proxy_response(HTTPStatus.BAD_GATEWAY, payload)

    def _send_proxy_response(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format_string: str, *args: object) -> None:
        # Paths and statuses are useful; request bodies and model output are never logged here.
        print(f"UI {self.address_string()} {format_string % args}")


def wait_for_health(process: subprocess.Popen[bytes], timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("The local chat API stopped during startup")
        try:
            with urllib.request.urlopen(BACKEND_URL + "/health", timeout=1) as response:
                if response.status == HTTPStatus.OK:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    raise RuntimeError("The local chat API did not become ready")


def start_services() -> list[subprocess.Popen[bytes]]:
    gateway = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "codex_gateway.py")],
        cwd=ROOT,
    )
    backend_environment = os.environ.copy()
    backend_environment.update(
        {
            "OPENAI_API_KEY": "local-codex-placeholder",
            "OPENAI_BASE_URL": "http://127.0.0.1:8091/v1",
            "LLM_TIMEOUT_SECONDS": "120",
            "PORT": "8080",
        }
    )
    backend = subprocess.Popen(
        [sys.executable, str(ROOT / "main.py")],
        cwd=ROOT,
        env=backend_environment,
    )
    try:
        wait_for_health(backend)
    except Exception:
        stop_services([gateway, backend])
        raise
    return [gateway, backend]


def stop_services(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3000, help="UI port (default: 3000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    args = parser.parse_args()

    processes: list[subprocess.Popen[bytes]] = []
    server: ThreadingHTTPServer | None = None
    try:
        processes = start_services()
        server = ThreadingHTTPServer(("127.0.0.1", args.port), ChatUiHandler)
        url = f"http://127.0.0.1:{args.port}"
        print(f"\nMarket Lens is ready at {url}")
        print("Press Ctrl+C to stop the UI, API, and Codex gateway.\n")
        if not args.no_browser:
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Market Lens...")
    finally:
        if server is not None:
            server.server_close()
        stop_services(processes)


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        main()
