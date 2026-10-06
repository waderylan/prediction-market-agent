const composer = document.querySelector("#composer");
const query = document.querySelector("#query");
const send = document.querySelector("#send");
const messages = document.querySelector("#messages");
const characterCount = document.querySelector("#character-count");
const sessionLabel = document.querySelector("#session-id");
const connection = document.querySelector("#connection");
const connectionLabel = document.querySelector("#connection-label");
const newSession = document.querySelector("#new-session");
const providerSelect = document.querySelector("#provider");
const cloudRunToggle = document.querySelector("#cloud-run-toggle");
const modelSelect = document.querySelector("#model");
const effortSelect = document.querySelector("#reasoning-effort");
const traceContent = document.querySelector("#trace-content");
const traceState = document.querySelector("#trace-state");
const traceSubtitle = document.querySelector("#trace-subtitle");
const leagueSelect = document.querySelector("#league");
const matchupInput = document.querySelector("#matchup");
const gameDateInput = document.querySelector("#game-date");
const timezoneInput = document.querySelector("#timezone");
const playerInput = document.querySelector("#player-name");
const gameError = document.querySelector("#game-error");
const playerError = document.querySelector("#player-error");
const sessionTag = document.querySelector("#session-tag");

let busy = false;
let sessionId = restoreSession();
sessionLabel.textContent = sessionId;
restoreRuntime();
restoreGameContext();

function restoreSession() {
  const saved = sessionStorage.getItem("sportswatch-session");
  if (saved && /^[A-Za-z0-9_-]{1,128}$/.test(saved)) return saved;
  return createSession();
}

function createSession() {
  const id = `web-${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;
  sessionStorage.setItem("sportswatch-session", id);
  return id;
}

function restoreRuntime() {
  cloudRunToggle.checked = sessionStorage.getItem("sportswatch-cloud-run") === "true";
  const savedProvider = sessionStorage.getItem("sportswatch-provider");
  const savedModel = sessionStorage.getItem("sportswatch-model");
  const savedEffort = sessionStorage.getItem("sportswatch-effort");
  if (["claude", "codex"].includes(savedProvider)) providerSelect.value = savedProvider;
  if (savedModel && /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$/.test(savedModel)) modelSelect.value = savedModel;
  if (["low", "medium", "high", "xhigh"].includes(savedEffort)) {
    effortSelect.value = savedEffort;
  }
  updateRuntimeControls();
}

function updateRuntimeControls() {
  cloudRunToggle.disabled = busy;
  providerSelect.disabled = busy || cloudRunToggle.checked;
  modelSelect.disabled = busy || cloudRunToggle.checked;
  effortSelect.disabled = busy || cloudRunToggle.checked;
}

function localDateIn(timezone) {
  try {
    const parts = Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date());
    const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${value.year}-${value.month}-${value.day}`;
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

function restoreGameContext() {
  const timezone = sessionStorage.getItem("sportswatch-timezone")
    || Intl.DateTimeFormat().resolvedOptions().timeZone
    || "America/Los_Angeles";
  timezoneInput.value = timezone;
  gameDateInput.value = sessionStorage.getItem("sportswatch-date") || localDateIn(timezone);
  const league = sessionStorage.getItem("sportswatch-league");
  if (["nfl", "mlb", "ncaa_football"].includes(league)) leagueSelect.value = league;
  matchupInput.value = sessionStorage.getItem("sportswatch-matchup") || "";
  playerInput.value = sessionStorage.getItem("sportswatch-player") || "";
}

function saveGameContext() {
  sessionStorage.setItem("sportswatch-league", leagueSelect.value);
  sessionStorage.setItem("sportswatch-matchup", matchupInput.value);
  sessionStorage.setItem("sportswatch-date", gameDateInput.value);
  sessionStorage.setItem("sportswatch-timezone", timezoneInput.value);
  sessionStorage.setItem("sportswatch-player", playerInput.value);
}

function setConnection(state, label) {
  connection.className = `connection ${state}`;
  connectionLabel.textContent = label;
}

async function checkHealth() {
  try {
    const target = cloudRunToggle.checked ? "cloud" : "local";
    const response = await fetch(`/api/health?target=${target}`, { cache: "no-store" });
    const health = await response.json();
    const selected = cloudRunToggle.checked ? "cloud" : providerSelect.value;
    if (health.providers?.[selected] !== true) throw new Error("unavailable");
    setConnection("online", cloudRunToggle.checked ? "Cloud Run ready" : `${selected} ready`);
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
    author.textContent = options.error ? "Request failed" : "SportsWatch";
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
  skeleton.setAttribute("aria-label", "Sports question in progress");
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

function showRunningTrace(provider, model, effort) {
  traceState.className = "trace-state running";
  traceState.textContent = "Running";
  traceSubtitle.textContent = `${provider} / ${model} / ${effort}`;
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

function showTrace(activity, provider, model, effort, elapsedMs) {
  traceState.className = "trace-state";
  traceState.textContent = "Complete";
  traceSubtitle.textContent = activity.length
    ? `${activity.length} MCP call${activity.length === 1 ? "" : "s"}`
    : "No MCP calls";
  traceContent.replaceChildren();

  const summary = document.createElement("dl");
  summary.className = "run-summary";
  for (const [term, value] of [
    ["Provider", provider],
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
    body.textContent = "This answer did not call an MCP server.";
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

function showTraceError(provider, model, effort, elapsedMs) {
  traceState.className = "trace-state failed";
  traceState.textContent = "Failed";
  traceSubtitle.textContent = `${provider} / ${model} / ${effort} / ${(elapsedMs / 1000).toFixed(1)} s`;
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
    '<div class="trace-empty"><h3>No calls yet</h3><p>After an answer, this panel shows the MCP tools used, their arguments, and outcomes.</p></div>';
}

function showFieldError(element, message) {
  element.textContent = message;
  element.hidden = !message;
}

function selectedDateContext() {
  const date = gameDateInput.value;
  if (!date) {
    showFieldError(gameError, "Choose the game's local date.");
    gameDateInput.focus();
    return null;
  }
  const timezone = timezoneInput.value.trim();
  try {
    Intl.DateTimeFormat("en-US", { timeZone: timezone });
  } catch {
    showFieldError(gameError, "Enter a valid IANA time zone, such as America/Los_Angeles.");
    timezoneInput.focus();
    return null;
  }
  showFieldError(gameError, "");
  const leagues = { nfl: "NFL", mlb: "MLB", ncaa_football: "NCAA football" };
  return { league: leagues[leagueSelect.value], date, timezone };
}

function gameContext() {
  const matchup = matchupInput.value.trim();
  if (!matchup) {
    showFieldError(gameError, "Enter a team or matchup first.");
    matchupInput.focus();
    return null;
  }
  const context = selectedDateContext();
  if (!context) return null;
  return `${context.league} ${matchup} game on ${context.date} in ${context.timezone}`;
}

function draftQuestion(prompt) {
  query.value = prompt;
  characterCount.value = String(prompt.length);
  query.focus();
  query.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function useSchedulePrompt() {
  if (busy) return;
  const context = selectedDateContext();
  if (!context) return;
  draftQuestion(`Who's playing in the ${context.league} on ${context.date} in ${context.timezone}? Show the available matchups and start times.`);
}

function useGamePrompt(intent) {
  if (busy) return;
  const game = gameContext();
  if (!game) return;
  const prompts = {
    score: `Find the ${game}. What is the current score and game situation? Cite the sports-state observation time.`,
    box: `Find the ${game}. Show the summary box score, team totals, and available game leaders.`,
    plays: `Find the ${game}. What happened in the five most recent plays?`,
    markets: `For the ${game}, find matching full-game winner contracts on Kalshi and Polymarket. Compare their current quotes and key settlement rules.`,
    brief: `Give me a sourced brief for the ${game}: current game state, summary box score, matching Kalshi and Polymarket winner contracts, and one focused news search.`,
  };
  draftQuestion(prompts[intent]);
}

function usePlayerPrompt() {
  if (busy) return;
  const game = gameContext();
  if (!game) return;
  const player = playerInput.value.trim();
  if (!player) {
    showFieldError(playerError, "Enter a player's name first.");
    playerInput.focus();
    return;
  }
  showFieldError(playerError, "");
  draftQuestion(`Find the ${game}. Show ${player}'s statistics from this game, not season totals.`);
}

async function sendQuery(text) {
  if (busy || !text.trim()) return;
  busy = true;
  query.disabled = true;
  send.disabled = true;
  newSession.disabled = true;
  updateRuntimeControls();
  appendMessage("user", text.trim());
  query.value = "";
  characterCount.value = "0";
  const pending = appendSkeleton();
  const useCloudRun = cloudRunToggle.checked;
  const selectedProvider = useCloudRun ? "Cloud Run" : providerSelect.value;
  const selectedModel = useCloudRun ? "gemini-3.8-flash" : modelSelect.value.trim() || "default";
  const selectedEffort = useCloudRun ? "low" : effortSelect.value;
  const started = performance.now();
  showRunningTrace(selectedProvider, selectedModel, selectedEffort);

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 150000);
  try {
    const response = await fetch("/api/chat/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: text.trim(),
        session_id: sessionId,
        target: useCloudRun ? "cloud" : "local",
        ...(useCloudRun ? {} : {
          provider: selectedProvider,
          model: selectedModel,
          reasoning_effort: selectedEffort,
        }),
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
    appendMessage("assistant", data.response, { label: `${selectedProvider} / ${selectedModel} / ${selectedEffort}` });
    showTrace(data.activity, selectedProvider, selectedModel, selectedEffort, elapsed);
    setConnection("online", useCloudRun ? "Cloud Run ready" : `${selectedProvider} ready`);
    sessionTag.textContent = "In conversation";
  } catch (error) {
    const elapsed = performance.now() - started;
    pending.remove();
    const timedOut = error instanceof DOMException && error.name === "AbortError";
    appendMessage(
      "assistant",
      timedOut
        ? "The research request timed out. Check the selected service and try again."
        : `The request could not be completed. ${error.message}`,
      { error: true, label: "Not completed" },
    );
    showTraceError(selectedProvider, selectedModel, selectedEffort, elapsed);
    setConnection("offline", "Check services");
  } finally {
    window.clearTimeout(timeout);
    busy = false;
    query.disabled = false;
    send.disabled = false;
    newSession.disabled = false;
    updateRuntimeControls();
    query.focus();
  }
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

for (const action of document.querySelectorAll("[data-intent]")) {
  action.addEventListener("click", () => useGamePrompt(action.dataset.intent));
}

document.querySelector("#player-prompt").addEventListener("click", usePlayerPrompt);
document.querySelector("#schedule-prompt").addEventListener("click", useSchedulePrompt);

function startNewConversation() {
  if (busy) return;
  sessionId = createSession();
  sessionLabel.textContent = sessionId;
  sessionTag.textContent = "New session";
  messages.innerHTML =
    '<div class="empty-state" id="empty-state"><span class="empty-kicker">READY WHEN YOU ARE</span><h3>Who\'s playing?</h3><p>Choose a league and date, then use Who\'s playing? to find a game. Enter a team when you want its score, stats, plays, markets, or a full brief.</p></div>';
  resetTrace();
  query.value = "";
  characterCount.value = "0";
  query.focus();
}

newSession.addEventListener("click", startNewConversation);

for (const control of [leagueSelect, matchupInput, gameDateInput, timezoneInput, playerInput]) {
  control.addEventListener("change", saveGameContext);
  control.addEventListener("input", () => {
    saveGameContext();
    if (control === playerInput) showFieldError(playerError, "");
    else showFieldError(gameError, "");
  });
}

for (const control of [providerSelect, modelSelect, effortSelect]) {
  control.addEventListener("change", () => {
    sessionStorage.setItem("sportswatch-provider", providerSelect.value);
    sessionStorage.setItem("sportswatch-model", modelSelect.value);
    sessionStorage.setItem("sportswatch-effort", effortSelect.value);
  });
}

providerSelect.addEventListener("change", () => {
  startNewConversation();
  void checkHealth();
});

cloudRunToggle.addEventListener("change", () => {
  sessionStorage.setItem("sportswatch-cloud-run", String(cloudRunToggle.checked));
  updateRuntimeControls();
  startNewConversation();
  void checkHealth();
});

void checkHealth();
window.setInterval(checkHealth, 15000);
