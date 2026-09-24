const composer = document.querySelector("#composer");
const query = document.querySelector("#query");
const send = document.querySelector("#send");
const messages = document.querySelector("#messages");
const characterCount = document.querySelector("#character-count");
const sessionLabel = document.querySelector("#session-id");
const connection = document.querySelector("#connection");
const connectionLabel = document.querySelector("#connection-label");
const newSession = document.querySelector("#new-session");
const modelSelect = document.querySelector("#model");
const effortSelect = document.querySelector("#reasoning-effort");
const traceContent = document.querySelector("#trace-content");
const traceState = document.querySelector("#trace-state");
const traceSubtitle = document.querySelector("#trace-subtitle");

let busy = false;
let sessionId = restoreSession();
sessionLabel.textContent = sessionId;
restoreRuntime();

function restoreSession() {
  const saved = sessionStorage.getItem("market-lens-session");
  if (saved && /^[A-Za-z0-9_-]{1,128}$/.test(saved)) return saved;
  return createSession();
}

function createSession() {
  const id = `web-${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;
  sessionStorage.setItem("market-lens-session", id);
  return id;
}

function restoreRuntime() {
  const savedModel = sessionStorage.getItem("market-lens-model");
  const savedEffort = sessionStorage.getItem("market-lens-effort");
  if (["sol", "terra", "luna"].includes(savedModel)) modelSelect.value = savedModel;
  if (["low", "medium", "high", "xhigh"].includes(savedEffort)) {
    effortSelect.value = savedEffort;
  }
}

function setConnection(state, label) {
  connection.className = `connection ${state}`;
  connectionLabel.textContent = label;
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) throw new Error("unavailable");
    setConnection("online", "API ready");
  } catch {
    setConnection("offline", "API unavailable");
  }
}

function appendInlineText(container, text) {
  const tokenPattern = /(\*\*[^*]+\*\*|https?:\/\/[^\s<>]+)/g;
  let cursor = 0;
  for (const match of text.matchAll(tokenPattern)) {
    const start = match.index ?? 0;
    container.append(document.createTextNode(text.slice(cursor, start)));
    if (match[0].startsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = match[0].slice(2, -2);
      container.append(strong);
    } else {
      const anchor = document.createElement("a");
      anchor.href = match[0];
      anchor.target = "_blank";
      anchor.rel = "noreferrer noopener";
      anchor.textContent = match[0];
      container.append(anchor);
    }
    cursor = start + match[0].length;
  }
  container.append(document.createTextNode(text.slice(cursor)));
}

function renderAssistantText(container, text) {
  let list = null;
  for (const line of text.split("\n")) {
    const bullet = line.match(/^[-*]\s+(.+)/);
    if (bullet) {
      if (!list) {
        list = document.createElement("ul");
        container.append(list);
      }
      const item = document.createElement("li");
      appendInlineText(item, bullet[1]);
      list.append(item);
      continue;
    }
    list = null;
    if (!line.trim()) continue;
    const paragraph = document.createElement("p");
    appendInlineText(paragraph, line);
    container.append(paragraph);
  }
}

function appendMessage(role, text, options = {}) {
  document.querySelector("#empty-state")?.remove();
  const article = document.createElement("article");
  article.className = `message ${role}-message`;
  if (options.error) article.classList.add("error-message");

  if (role === "assistant") {
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const author = document.createElement("span");
    author.textContent = options.error ? "Request failed" : "Market Lens";
    const runtime = document.createElement("span");
    runtime.textContent = options.label ?? "Response";
    meta.append(author, runtime);
    article.append(meta);
  }

  const body = document.createElement("div");
  body.className = "message-body";
  if (role === "assistant" && !options.error) {
    renderAssistantText(body, text);
  } else {
    appendInlineText(body, text);
  }
  article.append(body);

  if (role === "assistant" && !options.error) {
    const tools = document.createElement("div");
    tools.className = "message-tools";
    const copy = document.createElement("button");
    copy.className = "copy-response";
    copy.type = "button";
    copy.textContent = "Copy response";
    copy.addEventListener("click", async () => {
      await navigator.clipboard.writeText(text);
      copy.textContent = "Copied";
      window.setTimeout(() => { copy.textContent = "Copy response"; }, 1400);
    });
    tools.append(copy);
    const draftIds = [...new Set(text.match(/draft_[a-f0-9]{32}/g) ?? [])];
    if (/watch preview/i.test(text) && draftIds.length === 1) {
      const confirm = document.createElement("button");
      confirm.className = "confirm-watch";
      confirm.type = "button";
      confirm.textContent = "Confirm exact preview";
      confirm.addEventListener("click", async () => {
        confirm.disabled = true;
        const completed = await sendQuery(`confirm ${draftIds[0]}`);
        confirm.textContent = completed ? "Confirmation sent" : "Retry confirmation";
        confirm.disabled = completed;
      });
      tools.append(confirm);
    }
    article.append(tools);
  }

  messages.append(article);
  messages.scrollTop = messages.scrollHeight;
  return article;
}

function appendSkeleton() {
  document.querySelector("#empty-state")?.remove();
  const skeleton = document.createElement("div");
  skeleton.className = "response-skeleton";
  skeleton.setAttribute("role", "status");
  skeleton.setAttribute("aria-label", "Research request in progress");
  for (let index = 0; index < 3; index += 1) {
    const line = document.createElement("span");
    line.className = "skeleton-line";
    skeleton.append(line);
  }
  messages.append(skeleton);
  messages.scrollTop = messages.scrollHeight;
  return skeleton;
}

function addDefinition(list, term, value) {
  const dt = document.createElement("dt");
  dt.textContent = term;
  const dd = document.createElement("dd");
  dd.textContent = value;
  list.append(dt, dd);
}

function showRunningTrace(model, effort) {
  traceState.className = "trace-state running";
  traceState.textContent = "Running";
  traceSubtitle.textContent = `${model} / ${effort}`;
  traceContent.replaceChildren();
  const skeleton = document.createElement("div");
  skeleton.className = "response-skeleton";
  skeleton.setAttribute("aria-label", "Waiting for run trace");
  for (let index = 0; index < 3; index += 1) {
    const line = document.createElement("span");
    line.className = "skeleton-line";
    skeleton.append(line);
  }
  traceContent.append(skeleton);
}

function showTrace(activity, model, effort, elapsedMs) {
  traceState.className = "trace-state";
  traceState.textContent = "Complete";
  traceSubtitle.textContent = activity.length
    ? `${activity.length} tool call${activity.length === 1 ? "" : "s"}`
    : "No tool calls";
  traceContent.replaceChildren();

  const summary = document.createElement("dl");
  summary.className = "run-summary";
  for (const [term, value] of [
    ["Model", model],
    ["Effort", effort],
    ["Elapsed", `${(elapsedMs / 1000).toFixed(1)} s`],
    ["Tools", String(activity.length)],
  ]) {
    const group = document.createElement("div");
    addDefinition(group, term, value);
    summary.append(group);
  }
  traceContent.append(summary);

  if (!activity.length) {
    const empty = document.createElement("div");
    empty.className = "trace-empty";
    const title = document.createElement("h3");
    title.textContent = "Answered without MCP";
    const body = document.createElement("p");
    body.textContent = "The model used conversation context and general knowledge only.";
    empty.append(title, body);
    traceContent.append(empty);
    return;
  }

  const list = document.createElement("div");
  list.className = "trace-list";
  activity.forEach((item, index) => {
    const entry = document.createElement("article");
    entry.className = `trace-entry ${item.status}`;
    const header = document.createElement("header");
    const title = document.createElement("h3");
    title.textContent = item.tool;
    const meta = document.createElement("span");
    meta.className = "trace-meta";
    meta.textContent = `${index + 1} / ${item.status} / ${item.duration_ms} ms`;
    header.append(title, meta);
    entry.append(header);

    const argumentsList = document.createElement("dl");
    argumentsList.className = "trace-args";
    addDefinition(argumentsList, "Server", item.server);
    for (const [key, value] of Object.entries(item.arguments)) {
      addDefinition(argumentsList, key, value === null ? "null" : String(value));
    }
    entry.append(argumentsList);

    const result = document.createElement("p");
    result.className = "trace-result";
    const label = document.createElement("span");
    label.className = "trace-result-label";
    label.textContent = "Result";
    result.append(label, document.createTextNode(item.summary));
    entry.append(result);
    list.append(entry);
  });
  traceContent.append(list);
}

function showTraceError(model, effort, elapsedMs) {
  traceState.className = "trace-state failed";
  traceState.textContent = "Failed";
  traceSubtitle.textContent = `${model} / ${effort} / ${(elapsedMs / 1000).toFixed(1)} s`;
  traceContent.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "trace-empty";
  const title = document.createElement("h3");
  title.textContent = "No trace returned";
  const body = document.createElement("p");
  body.textContent = "The request failed before a valid inspection response was available.";
  empty.append(title, body);
  traceContent.append(empty);
}

function resetTrace() {
  traceState.className = "trace-state";
  traceState.textContent = "Idle";
  traceSubtitle.textContent = "No request yet";
  traceContent.innerHTML =
    '<div class="trace-empty"><h3>No tool activity</h3><p>Send a question to inspect model choice and MCP calls.</p></div>';
}

async function sendQuery(text) {
  if (busy || !text.trim()) return false;
  let completed = false;
  busy = true;
  query.disabled = true;
  send.disabled = true;
  appendMessage("user", text.trim());
  query.value = "";
  characterCount.value = "0";
  const pending = appendSkeleton();
  const selectedModel = modelSelect.value;
  const selectedEffort = effortSelect.value;
  const started = performance.now();
  showRunningTrace(selectedModel, selectedEffort);

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 150000);
  try {
    const response = await fetch("/api/chat/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: text.trim(),
        session_id: sessionId,
        model: selectedModel,
        reasoning_effort: selectedEffort,
      }),
      signal: controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || typeof data.response !== "string" || !Array.isArray(data.activity)) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item) => item.msg).join("; ")
        : data.detail;
      throw new Error(detail || `Request failed with status ${response.status}`);
    }
    const elapsed = performance.now() - started;
    pending.remove();
    appendMessage("assistant", data.response, { label: `${selectedModel} / ${selectedEffort}` });
    showTrace(data.activity, selectedModel, selectedEffort, elapsed);
    setConnection("online", "API ready");
    completed = true;
  } catch (error) {
    const elapsed = performance.now() - started;
    pending.remove();
    const timedOut = error instanceof DOMException && error.name === "AbortError";
    appendMessage(
      "assistant",
      timedOut
        ? "The local research request timed out. Check the gateway and try again."
        : `The request could not be completed. ${error.message}`,
      { error: true, label: "Not completed" },
    );
    showTraceError(selectedModel, selectedEffort, elapsed);
    setConnection("offline", "Check services");
  } finally {
    window.clearTimeout(timeout);
    busy = false;
    query.disabled = false;
    send.disabled = false;
    query.focus();
  }
  return completed;
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  void sendQuery(query.value);
});

query.addEventListener("input", () => { characterCount.value = String(query.value.length); });

query.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

for (const prompt of document.querySelectorAll("[data-prompt]")) {
  prompt.addEventListener("click", () => {
    query.value = prompt.dataset.prompt;
    characterCount.value = String(query.value.length);
    query.focus();
  });
}

newSession.addEventListener("click", () => {
  sessionId = createSession();
  sessionLabel.textContent = sessionId;
  messages.innerHTML =
    '<div class="empty-state" id="empty-state"><h2>Ask a question or describe a watch.</h2><p>Every watch preview pins the exact game, contracts, thresholds, and delivery.</p></div>';
  resetTrace();
  query.focus();
});

for (const control of [modelSelect, effortSelect]) {
  control.addEventListener("change", () => {
    sessionStorage.setItem("market-lens-model", modelSelect.value);
    sessionStorage.setItem("market-lens-effort", effortSelect.value);
  });
}

void checkHealth();
window.setInterval(checkHealth, 15000);
