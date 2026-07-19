"use strict";

const form = document.getElementById("search-form");
const goBtn = document.getElementById("go");
const progressEl = document.getElementById("progress");
const progressText = document.getElementById("progress-text");
const errorEl = document.getElementById("error");
const resultEl = document.getElementById("result");

let source = null;

const STATUS_HEADINGS = {
  negative: "⚠ Negative treatment found",
  caution: "⚠ Treated with caution",
  positive: "✓ Positive / neutral treatment only",
  neutral: "Cited without significant treatment",
  unknown: "No citing cases found",
};

const TREATMENT_CLASS = {
  overruled: "negative", not_followed: "negative", departed_from: "negative",
  doubted: "negative", criticised: "negative",
  distinguished: "caution",
  applied: "positive", followed: "positive", approved: "positive",
  considered: "neutral", cited: "neutral", unclear: "neutral",
};

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const citation = document.getElementById("citation").value.trim();
  if (!citation) return;
  const name = document.getElementById("case-name").value.trim();
  const refresh = document.getElementById("refresh").checked ? "1" : "0";

  if (source) source.close();
  errorEl.hidden = true;
  resultEl.hidden = true;
  progressEl.hidden = false;
  progressText.textContent = "Starting…";
  goBtn.disabled = true;

  const params = new URLSearchParams({ citation, name, refresh });
  source = new EventSource(`/api/check?${params}`);

  source.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    if (event.type === "progress") {
      progressText.textContent = event.message;
    } else if (event.type === "result") {
      finish();
      renderResult(event.data);
    } else if (event.type === "error") {
      finish();
      showError(event.message);
    }
  };
  source.onerror = () => {
    if (!goBtn.disabled) return; // already finished
    finish();
    showError("Connection to the server was lost.");
  };
});

function finish() {
  if (source) { source.close(); source = null; }
  progressEl.hidden = true;
  goBtn.disabled = false;
}

function showError(message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

function renderResult(data) {
  const banner = document.getElementById("banner");
  banner.className = data.overall;
  document.getElementById("banner-status").textContent =
    STATUS_HEADINGS[data.overall] || data.overall;
  document.getElementById("banner-case").textContent =
    (data.case_name ? `${data.case_name} ` : "") + data.citation;

  const meta = [];
  meta.push(`${data.total_hits} citing judgment(s) found`);
  if (data.truncated) meta.push(`showing the first ${data.citing.length}`);
  meta.push(`classifier: ${data.classifier === "claude" ? "Claude" : "keyword heuristic"}`);
  if (data.from_cache) meta.push("cached result");
  meta.push(`checked ${data.checked_at.slice(0, 10)}`);
  document.getElementById("meta").textContent = meta.join(" · ");

  const counts = document.getElementById("counts");
  counts.replaceChildren();
  for (const [treatment, n] of Object.entries(data.counts).sort((a, b) => b[1] - a[1])) {
    const chip = document.createElement("span");
    chip.className = `chip ${TREATMENT_CLASS[treatment] || "neutral"}`;
    chip.textContent = `${label(treatment)} × ${n}`;
    counts.appendChild(chip);
  }

  const cards = document.getElementById("cards");
  cards.replaceChildren();
  for (const c of data.citing) cards.appendChild(renderCard(c));

  document.getElementById("coverage").textContent = data.coverage_note;
  resultEl.hidden = false;
}

function renderCard(c) {
  const card = el("div", "card");

  const head = el("div", "card-head");
  const title = el("div", "card-title");
  const a = document.createElement("a");
  a.href = c.link;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = c.name;
  title.appendChild(a);
  const badge = el("span", `badge ${TREATMENT_CLASS[c.treatment] || "neutral"}`);
  badge.textContent = label(c.treatment);
  head.append(title, badge);
  card.appendChild(head);

  const sub = el("div", "card-sub");
  sub.textContent = [c.cite, c.date ? c.date.slice(0, 10) : null,
                     `confidence: ${c.confidence}`].filter(Boolean).join(" · ");
  card.appendChild(sub);

  if (c.evidence_quote) {
    const q = document.createElement("blockquote");
    q.textContent = `“${c.evidence_quote}”`;
    card.appendChild(q);
  }
  if (c.explanation) {
    const p = el("p", "card-expl");
    p.textContent = c.explanation;
    card.appendChild(p);
  }
  if (c.method !== "claude") {
    const m = el("div", "card-method");
    m.textContent = c.method === "heuristic"
      ? "Classified by keyword heuristic (Claude API unavailable)"
      : c.method === "no-context"
        ? "Citation passage not located in the judgment text"
        : "";
    if (m.textContent) card.appendChild(m);
  }
  return card;
}

function label(treatment) {
  return treatment.replaceAll("_", " ");
}

function el(tag, className) {
  const node = document.createElement(tag);
  node.className = className;
  return node;
}
