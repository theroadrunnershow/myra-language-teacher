(function () {
  "use strict";

  function el(tag, text) {
    var node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderStatus(status) {
    var block = document.getElementById("kt-status-block");
    block.innerHTML = "";
    var rows = [
      ["Mode", status.mode],
      ["Model", status.model],
      ["Enabled languages", (status.enabled_languages || []).join(", ")],
      ["Default explanation language", status.default_explanation_language],
      ["Profile", (status.profile || {}).name + " (voice=" + (status.profile || {}).voice + ")"],
      ["Profile locked", String((status.profile || {}).locked)],
      ["Transcripts persisted", String((status.review || {}).transcripts_enabled)],
      ["Audio persisted", String((status.review || {}).audio_enabled)],
    ];
    var dl = el("dl");
    rows.forEach(function (row) {
      dl.appendChild(el("dt", row[0]));
      dl.appendChild(el("dd", row[1] == null ? "—" : String(row[1])));
    });
    block.appendChild(dl);
  }

  function renderSessions(sessions) {
    var wrap = document.getElementById("kt-sessions-wrap");
    var list = document.getElementById("kt-sessions-list");
    list.innerHTML = "";
    if (!sessions || !sessions.length) {
      list.appendChild(el("li", "No sessions recorded yet."));
    } else {
      sessions.forEach(function (s) {
        var label = s.session_id + " — " + (s.started_at || "unknown start");
        list.appendChild(el("li", label));
      });
    }
    wrap.hidden = false;
  }

  function loadSessions() {
    fetch("/api/kids-teacher/review/sessions", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) renderSessions(data.sessions || []); })
      .catch(function () { /* non-fatal */ });
  }

  function load() {
    fetch("/api/kids-teacher/status", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (status) {
        renderStatus(status);
        if (status && status.review && status.review.transcripts_enabled) {
          loadSessions();
        }
      })
      .catch(function (err) {
        var block = document.getElementById("kt-status-block");
        block.textContent = "Failed to load status: " + err;
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
