// ── Config ───────────────────────────────────────────────────────────────────

const API_BASE = "http://127.0.0.1:8000"; // ← Change to your Railway URL
// For local dev: const API_BASE = "http://localhost:8000";

// ── State ─────────────────────────────────────────────────────────────────────

let currentMode = "deep";
let currentReport = "";
let eventSource = null;

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  loadSessions();
  loadMemory();
  setupModeButtons();
  setupSidebar();

  // Enter key submits
  document.getElementById("queryInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      startResearch();
    }
  });
});

// ── Mode toggle ───────────────────────────────────────────────────────────────

function setupModeButtons() {
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentMode = btn.dataset.mode;
    });
  });
}

// ── Sidebar toggle ────────────────────────────────────────────────────────────

function setupSidebar() {
  document.getElementById("toggleSidebar").addEventListener("click", () => {
    const sidebar = document.getElementById("sidebar");
    const collapsed = sidebar.classList.toggle("collapsed");
    document.getElementById("toggleSidebar").textContent = collapsed ? "▶" : "◀";
  });
}

// ── Helper: set query from chip ───────────────────────────────────────────────

function setQuery(text) {
  document.getElementById("queryInput").value = text;
  document.getElementById("queryInput").focus();
}

// ── Main: start research ──────────────────────────────────────────────────────

async function startResearch() {
  const query = document.getElementById("queryInput").value.trim();
  if (!query) return;

  // Close any existing stream
  if (eventSource) { eventSource.close(); eventSource = null; }

  // UI reset
  setSearching(true);
  resetFeed();
  hideReport();
  hideError();
  showFeed();

  addFeedLine(`Starting ${currentMode} research...`, "ok");

  try {
    // POST /research
    const res = await fetch(`${API_BASE}/research`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, mode: currentMode }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to start research");
    }

    const { session_id } = await res.json();
    addFeedLine(`Session started: ${session_id.slice(0, 8)}...`, "ok");

    // Connect to SSE stream
    connectStream(session_id);

  } catch (err) {
    showError(err.message);
    setSearching(false);
  }
}

// ── SSE Stream ────────────────────────────────────────────────────────────────

function connectStream(sessionId) {
  eventSource = new EventSource(`${API_BASE}/research/${sessionId}/stream`);

  eventSource.onmessage = (e) => {
    try {
      const payload = JSON.parse(e.data);
      handleEvent(payload.event, payload.data);
    } catch (err) {
      console.error("SSE parse error:", err);
    }
  };

  eventSource.onerror = () => {
    if (eventSource.readyState === EventSource.CLOSED) {
      addFeedLine("Stream closed", "warn");
      setSearching(false);
    }
  };
}

// ── Event handlers ────────────────────────────────────────────────────────────

function handleEvent(event, data) {
  switch (event) {
    case "status":
      addFeedLine(data, "");
      break;

    case "plan":
      addFeedLine(`Plan: ${data.report_type} — topics: ${data.sub_topics.join(", ")}`, "ok");
      if (data.players?.length) addFeedLine(`Players detected: ${data.players.join(", ")}`, "");
      break;

    case "fbref_complete":
      if (data.players_found?.length)
        addFeedLine(`FBRef: Found stats for ${data.players_found.join(", ")}`, "ok");
      if (data.players_missing?.length)
        addFeedLine(`FBRef: No data for ${data.players_missing.join(", ")}`, "warn");
      break;

    case "topic_complete":
      addFeedLine(`✓ ${data.topic} — quality: ${data.data_quality} (${data.results_found} results)`, "ok");
      break;

    case "fact_base_ready":
      addFeedLine(`Fact base built — confidence: ${data.confidence}`, "ok");
      if (data.conflicts?.length)
        data.conflicts.forEach((c) => addFeedLine(`Conflict: ${c}`, "conflict"));
      if (data.missing_fields?.length)
        addFeedLine(`Missing: ${data.missing_fields.join(", ")}`, "warn");
      break;

    case "comparison_ready":
      addFeedLine(`Comparison data loaded for ${data.player}`, "ok");
      break;

    case "report_drafted":
      addFeedLine(`Report drafted (${data.length} chars)`, "ok");
      break;

    case "verification_result":
      const icon = data.status === "APPROVED" ? "✓" : "✗";
      const cls = data.status === "APPROVED" ? "ok" : "conflict";
      addFeedLine(`${icon} Verification attempt ${data.attempt}: ${data.status} (${data.errors_found} errors)`, cls);
      break;

    case "report":
      // Final report arrives
      currentReport = data.markdown;
      renderReport(data.markdown, data.audit);
      setSearching(false);
      stopSpinner();
      if (eventSource) { eventSource.close(); eventSource = null; }
      loadSessions(); // refresh sidebar
      break;

    case "complete":
      addFeedLine("Research complete", "ok");
      break;

    case "error":
      const msg = data.errors?.join(", ") || "Unknown error";
      showError(msg);
      setSearching(false);
      stopSpinner();
      if (eventSource) { eventSource.close(); eventSource = null; }
      break;

    default:
      console.log("Unknown event:", event, data);
  }
}

// ── Report rendering ──────────────────────────────────────────────────────────

function renderReport(markdown, audit) {
  const container = document.getElementById("reportContainer");
  const body = document.getElementById("reportBody");
  const auditBanner = document.getElementById("auditBanner");

  body.innerHTML = marked.parse(markdown);

  // Audit banner
  if (audit) {
    const conflicts = audit.conflicts_detected?.length || 0;
    const missing = audit.missing_fields?.length || 0;
    const confidence = audit.overall_confidence || "unknown";

    if (conflicts > 0 || missing > 0 || confidence === "low") {
      auditBanner.style.display = "block";
      auditBanner.innerHTML = `
        ⚠️ Data Audit: confidence <strong>${confidence}</strong>
        ${conflicts ? ` · ${conflicts} conflict(s) detected` : ""}
        ${missing ? ` · ${missing} field(s) missing` : ""}
      `;
    }
  }

  container.style.display = "block";
  container.scrollIntoView({ behavior: "smooth" });
}

// ── Feed helpers ──────────────────────────────────────────────────────────────

function addFeedLine(msg, type = "") {
  const log = document.getElementById("feedLog");
  const now = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const line = document.createElement("div");
  line.className = "feed-line";
  line.innerHTML = `<span class="ts">${now}</span><span class="msg ${type}">${escapeHtml(msg)}</span>`;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function resetFeed() {
  document.getElementById("feedLog").innerHTML = "";
}

function showFeed() {
  document.getElementById("activityFeed").style.display = "block";
  document.getElementById("spinner").classList.remove("done");
}

function stopSpinner() {
  document.getElementById("spinner").classList.add("done");
}

// ── Copy report ───────────────────────────────────────────────────────────────

async function copyReport() {
  if (!currentReport) return;
  await navigator.clipboard.writeText(currentReport);
  const btn = document.querySelector(".report-actions .btn-secondary");
  const orig = btn.textContent;
  btn.textContent = "✓ Copied!";
  setTimeout(() => btn.textContent = orig, 2000);
}

// ── New search ────────────────────────────────────────────────────────────────

function newSearch() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  document.getElementById("queryInput").value = "";
  hideReport();
  hideError();
  document.getElementById("activityFeed").style.display = "none";
  setSearching(false);
  currentReport = "";
}

// ── UI state ──────────────────────────────────────────────────────────────────

function setSearching(searching) {
  document.getElementById("searchBtn").disabled = searching;
  document.getElementById("searchBtn").textContent = searching ? "Researching..." : "Scout It";
}

function hideReport() {
  document.getElementById("reportContainer").style.display = "none";
  document.getElementById("auditBanner").style.display = "none";
}

function showError(msg) {
  const container = document.getElementById("errorContainer");
  document.getElementById("errorMessage").textContent = msg;
  container.style.display = "flex";
}

function hideError() {
  document.getElementById("errorContainer").style.display = "none";
}

// ── Session history ───────────────────────────────────────────────────────────

async function loadSessions() {
  try {
    const res = await fetch(`${API_BASE}/sessions`);
    const { sessions } = await res.json();
    const list = document.getElementById("sessionList");

    if (!sessions.length) {
      list.innerHTML = '<div class="empty-state">No sessions yet</div>';
      return;
    }

    list.innerHTML = sessions.map((s) => `
      <div class="session-item" onclick="loadSession('${s.id}')">
        <div class="session-query">${escapeHtml(s.query)}</div>
        <div class="session-meta">${s.status} · ${formatDate(s.created_at)}</div>
      </div>
    `).join("");
  } catch (err) {
    console.error("Failed to load sessions:", err);
  }
}

async function loadSession(sessionId) {
  try {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}`);
    const session = await res.json();
    if (session.final_report) {
      document.getElementById("queryInput").value = session.query;
      currentReport = session.final_report;
      renderReport(session.final_report, session.data_audit);
      resetFeed();
      document.getElementById("activityFeed").style.display = "none";
    }
  } catch (err) {
    console.error("Failed to load session:", err);
  }
}

// ── Memory ────────────────────────────────────────────────────────────────────

async function loadMemory() {
  try {
    const res = await fetch(`${API_BASE}/memory`);
    const memory = await res.json();
    const section = document.getElementById("memorySection");

    const players = memory.frequent_players || [];
    const teams = memory.frequent_teams || [];

    if (!players.length && !teams.length) {
      section.style.display = "none";
      return;
    }

    section.innerHTML = `
      <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.1em;color:var(--text3);margin-bottom:6px;">Your Focus</div>
      ${players.map((p) => `<div>⚽ <strong>${p.player}</strong> <span style="color:var(--text3)">(×${p.count})</span></div>`).join("")}
      ${teams.map((t) => `<div>🏟️ <strong>${t.team}</strong></div>`).join("")}
    `;
  } catch (err) {
    console.error("Failed to load memory:", err);
  }
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}
