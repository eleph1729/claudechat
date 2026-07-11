/* Splitting Headache — browser edition.
 *
 * Everything runs client-side: pdf.js reads the table of contents (embedded
 * bookmarks, or a printed index parsed from word positions), pdf-lib copies
 * page ranges into new PDFs, fflate packs them into a zip. The uploaded file
 * never leaves the machine.
 *
 * The TOC logic mirrors splitting_headache/toc.py and splitter.py.
 */
"use strict";

// pdf.js legacy build: run the worker on the main thread ("fake worker").
window.pdfjsWorker = window["pdfjs-dist/build/pdf.worker"];
pdfjsLib.GlobalWorkerOptions.workerSrc = " ";

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// TOC extraction
// ---------------------------------------------------------------------------

const LEADER_CHARS = ".·•…_";
const PAGE_TOKEN = /^\(?(\d{1,5})(?:\s*[-–—‒]\s*(\d{1,5}))?\)?[.,]?$/;
const DASH_TOKEN = /^[-–—‒]$/;
const NUMBERING = /^(\d{1,3}(?:\.\d{1,3})*)[.)]?\s+/;
const MIN_GAP_PT = 12;

async function outlineEntries(pdf) {
  const outline = await pdf.getOutline();
  if (!outline || !outline.length) return null;
  const flat = [];
  async function walk(items, level) {
    for (const it of items) {
      let pageIdx = null;
      try {
        let dest = it.dest;
        if (typeof dest === "string") dest = await pdf.getDestination(dest);
        if (Array.isArray(dest) && dest[0]) pageIdx = await pdf.getPageIndex(dest[0]);
      } catch (e) { /* broken link — skip, like toc.py does for page < 1 */ }
      if (pageIdx !== null) {
        flat.push({ level, title: (it.title || "").trim() || "Untitled", start: pageIdx + 1, end: null });
      }
      if (it.items && it.items.length) await walk(it.items, level + 1);
    }
  }
  await walk(outline, 1);
  if (!flat.length) return null;
  computeEnds(flat, pdf.numPages);
  return { entries: flat, offset: 0 };
}

// Split text items into words with approximate x extents, then group into
// visual lines by y. TOCs are often typeset as tables, so the title and the
// page number can arrive as separate items on the same line.
function linesOf(textContent) {
  const words = [];
  for (const item of textContent.items) {
    const s = item.str;
    if (!s || !s.trim()) continue;
    const x = item.transform[4], y = item.transform[5];
    const perChar = item.width / Math.max(1, s.length);
    const re = /\S+/g;
    let m;
    while ((m = re.exec(s)) !== null) {
      words.push({
        x0: x + perChar * m.index,
        x1: x + perChar * (m.index + m[0].length),
        y,
        str: m[0],
      });
    }
  }
  words.sort((a, b) => b.y - a.y || a.x0 - b.x0); // PDF y grows upward
  const lines = [];
  for (const w of words) {
    if (lines.length && Math.abs(w.y - lines[lines.length - 1][0].y) <= 3) {
      lines[lines.length - 1].push(w);
    } else {
      lines.push([w]);
    }
  }
  for (const line of lines) line.sort((a, b) => a.x0 - b.x0);
  return lines;
}

function isLeaderRun(s) {
  return [...s].every((c) => LEADER_CHARS.includes(c));
}

function parseLine(line) {
  if (line.length < 2) return null;
  const words = line.slice();
  const m = PAGE_TOKEN.exec(words[words.length - 1].str);
  if (!m) return null;
  let start = parseInt(m[1], 10);
  let end = m[2] ? parseInt(m[2], 10) : start;
  let pageX0 = words[words.length - 1].x0;
  words.pop();
  if (words.length >= 2 && DASH_TOKEN.test(words[words.length - 1].str)) {
    const m2 = PAGE_TOKEN.exec(words[words.length - 2].str);
    if (m2 && !m2[2]) {
      end = start;
      start = parseInt(m2[1], 10);
      pageX0 = words[words.length - 2].x0;
      words.splice(words.length - 2, 2);
    }
  }
  if (!words.length) return null;

  const prev = words[words.length - 1];
  const gap = pageX0 - prev.x1;
  const prevIsLeader = isLeaderRun(prev.str);
  const titleWords = prevIsLeader ? words.slice(0, -1) : words;
  if (!titleWords.length) return null;
  const rawTitle = titleWords.map((w) => w.str).join(" ").trim();
  const hasLeaders = prevIsLeader || rawTitle.endsWith("..");
  if (gap < MIN_GAP_PT && !hasLeaders) return null;
  const title = rawTitle.replace(new RegExp(`[${LEADER_CHARS}\\s\\-–—]+$`), "");
  if (!/[A-Za-z]/.test(title)) return null;
  if (end < start) end = start;
  return { title, x: titleWords[0].x0, start, end };
}

function assignLevels(rows) {
  const numbered = rows.map((r) => NUMBERING.exec(r.title));
  const nWith = numbered.filter(Boolean).length;
  const depths = new Set(numbered.filter(Boolean).map((m) => m[1].split(".").length));
  if (nWith >= 0.6 * rows.length && depths.size > 1) {
    return numbered.map((m) => (m ? m[1].split(".").length : 1));
  }
  const xs = [...new Set(rows.map((r) => Math.round(r.x)))].sort((a, b) => a - b);
  const edges = [];
  for (const x of xs) {
    if (edges.length && x - edges[edges.length - 1].last <= 8) {
      edges[edges.length - 1].last = x;
    } else {
      edges.push({ first: x, last: x });
    }
  }
  const firsts = edges.map((e) => e.first);
  return rows.map((r) => {
    for (let i = firsts.length - 1; i >= 0; i--) {
      if (r.x >= firsts[i] - 4) return Math.min(i + 1, 6);
    }
    return 1;
  });
}

async function printedIndexEntries(pdf) {
  const scan = Math.min(50, pdf.numPages);
  const perPage = [];
  for (let pno = 1; pno <= scan; pno++) {
    const page = await pdf.getPage(pno);
    const tc = await page.getTextContent();
    const hits = [];
    for (const line of linesOf(tc)) {
      const parsed = parseLine(line);
      if (parsed) hits.push(parsed);
    }
    perPage.push(hits);
  }
  let first = perPage.findIndex((h) => h.length >= 4);
  if (first === -1) return null;
  let last = first;
  while (last + 1 < scan && perPage[last + 1].length >= 4) last++;

  const rows = [];
  for (let p = first; p <= last; p++) rows.push(...perPage[p]);
  if (!rows.length) return null;

  const levels = assignLevels(rows);
  const entries = rows.map((r, i) => ({
    level: levels[i],
    title: r.title,
    start: r.start,
    end: r.end > r.start ? r.end : null,
  }));
  const offset = await guessOffset(pdf, entries, last);
  computeEnds(entries, Math.max(1, pdf.numPages - offset));
  return { entries, offset, indexPages: [first, last] };
}

async function guessOffset(pdf, entries, indexLast) {
  const maxPrinted = Math.max(...entries.map((e) => e.start));
  try {
    const labels = await pdf.getPageLabels();
    if (labels) {
      const i = labels.indexOf("1");
      if (i >= 0 && maxPrinted - 1 + i < pdf.numPages) return i;
    }
  } catch (e) { /* no labels */ }
  const firstPrinted = entries[0].start;
  if (firstPrinted - 1 <= indexLast) {
    const guess = indexLast + 1 - (firstPrinted - 1);
    if (maxPrinted - 1 + guess < pdf.numPages) return guess;
  }
  return 0;
}

function computeEnds(entries, lastPage) {
  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    if (e.end !== null && e.end !== undefined) continue;
    e.end = i + 1 < entries.length
      ? Math.max(e.start, entries[i + 1].start - 1)
      : Math.max(e.start, lastPage);
  }
}

function toTree(entries) {
  const root = [];
  const stack = [];
  for (const e of entries) {
    const node = { ...e, children: [] };
    while (stack.length && stack[stack.length - 1].level >= e.level) stack.pop();
    (stack.length ? stack[stack.length - 1].children : root).push(node);
    stack.push(node);
  }
  return root;
}

// ---------------------------------------------------------------------------
// Splitting
// ---------------------------------------------------------------------------

const MAX_NAME = 110;

function sanitize(name) {
  name = name.replace(/[<>:"/\\|?*\x00-\x1f]/g, " ").replace(/\s+/g, " ").trim().replace(/^\.+|\.+$/g, "");
  if (name.length > MAX_NAME) name = name.slice(0, MAX_NAME - 1).trimEnd() + "…";
  return name || "Untitled";
}

function pageRef(start, end) {
  return start === end ? `[p ${start}]` : `[pp ${start}-${end}]`;
}

function prepare(entries, pageCount, offset, printed) {
  const out = [];
  const skipped = [];
  for (const e of entries) {
    const start = e.start - 1 + (printed ? offset : 0);
    if (start < 0 || start >= pageCount) { skipped.push(e.title); continue; }
    let end = null;
    if (e.end !== null && e.end !== e.start) {
      end = Math.min(Math.max(start, e.end - 1 + (printed ? offset : 0)), pageCount - 1);
    }
    out.push({ level: e.level, title: e.title, start, end });
  }
  computeEnds(out, pageCount - 1);
  for (const e of out) e.end = Math.min(e.end, pageCount - 1);
  return { prepared: out, skipped };
}

function range(a, b) {
  return Array.from({ length: b - a + 1 }, (_, i) => a + i);
}

async function splitToZip(srcBytes, entries, rootName, { offset = 0, printed = false, onProgress } = {}) {
  const { prepared, skipped } = prepare(entries, window.__pageCount, offset, printed);
  const tree = toTree(prepared);
  const src = await PDFLib.PDFDocument.load(srcBytes, { ignoreEncryption: true, updateMetadata: false });

  const files = {};
  const rows = [["path", "title", "first_page", "last_page", "pages"]];
  const root = sanitize(rootName);
  let docs = 0, dirs = 0, done = 0;

  const totalLeaves = countJobs(tree);

  async function emitPdf(parts, title, start, end) {
    const sub = await PDFLib.PDFDocument.create();
    const pages = await sub.copyPages(src, range(start, end));
    for (const p of pages) sub.addPage(p);
    const data = await sub.save({ useObjectStreams: true });
    const path = [root, ...parts].join("/");
    files[path] = [data, { level: 0 }]; // PDFs are already compressed — store
    rows.push([path, title, start + 1, end + 1, end - start + 1]);
    docs++; done++;
    if (onProgress && (done % 5 === 0 || done === totalLeaves)) {
      onProgress(done, totalLeaves);
      await new Promise((r) => setTimeout(r, 0)); // let the UI paint
    }
  }

  function countJobs(nodes) {
    let n = 0;
    for (const node of nodes) {
      if (node.children.length) {
        if (node.children[0].start > node.start) n++;
        n += countJobs(node.children);
      } else n++;
    }
    return n;
  }

  function uniqueName(names, name) {
    let out = name, n = 2;
    const dot = name.lastIndexOf(".");
    const stem = dot > 0 ? name.slice(0, dot) : name;
    const ext = dot > 0 ? name.slice(dot) : "";
    while (names.has(out.toLowerCase())) out = `${stem} (${n++})${ext}`;
    names.add(out.toLowerCase());
    return out;
  }

  async function walk(nodes, prefix) {
    const width = Math.max(2, String(nodes.length).length);
    const names = new Set();
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      const seq = String(i + 1).padStart(width, "0");
      const title = sanitize(node.title);
      if (node.children.length) {
        const folder = uniqueName(names, `${seq}. ${title}`);
        dirs++;
        const firstChild = node.children[0].start;
        if (firstChild > node.start) {
          await emitPdf(
            [...prefix, folder, `00. ${title} (cover) ${pageRef(node.start + 1, firstChild)}.pdf`],
            `${node.title} (cover)`, node.start, firstChild - 1,
          );
        }
        await walk(node.children, [...prefix, folder]);
      } else {
        const ref = pageRef(node.start + 1, node.end + 1);
        const fname = uniqueName(names, `${seq}. ${title} ${ref}.pdf`);
        await emitPdf([...prefix, fname], node.title, node.start, node.end);
      }
    }
  }

  await walk(tree, []);

  if (docs) {
    const csv = rows.map((r) => r.map((c) => {
      const s = String(c);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    }).join(",")).join("\r\n");
    files[`${root}/manifest.csv`] = new TextEncoder().encode(csv);
  }

  const zipped = fflate.zipSync(files);
  return { zipped, docs, dirs, skipped };
}

// ---------------------------------------------------------------------------
// UI
// ---------------------------------------------------------------------------

let state = null;   // { fileName, bytes, pages, sources: {bookmarks?, index?} }
let source = null;

const drop = $("drop"), fileInput = $("file");
drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("hover"); });
drop.addEventListener("dragleave", () => drop.classList.remove("hover"));
drop.addEventListener("drop", (e) => {
  e.preventDefault(); drop.classList.remove("hover");
  if (e.dataTransfer.files.length) analyze(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) analyze(fileInput.files[0]);
});

async function analyze(file) {
  drop.classList.add("hidden");
  $("busyAnalyze").classList.remove("hidden");
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    // pdf.js takes ownership of the buffer it's given — hand it a copy.
    const pdf = await pdfjsLib.getDocument({ data: bytes.slice() }).promise;
    window.__pageCount = pdf.numPages;
    const sources = {};
    const bm = await outlineEntries(pdf);
    if (bm) sources.bookmarks = bm;
    $("busyText").textContent = "Looking for a printed index…";
    const idx = await printedIndexEntries(pdf);
    if (idx) sources.index = idx;
    pdf.destroy();
    if (!Object.keys(sources).length) {
      throw new Error("No table of contents found — the PDF has no bookmarks and no recognisable printed index in its first 50 pages.");
    }
    state = { fileName: file.name, bytes, pages: window.__pageCount, sources };
    source = sources.bookmarks ? "bookmarks" : "index";
    showResult();
  } catch (err) {
    resetUI();
    alert(err.message || String(err));
  } finally {
    $("busyText").textContent = "Reading the table of contents…";
  }
}

function showResult() {
  $("busyAnalyze").classList.add("hidden");
  $("result").classList.remove("hidden");
  $("fname").textContent = state.fileName;
  $("npages").textContent = state.pages.toLocaleString();
  const both = state.sources.bookmarks && state.sources.index;
  $("srcSeg").classList.toggle("hidden", !both);
  document.querySelectorAll("#srcSeg button").forEach((b) => {
    b.classList.toggle("on", b.dataset.src === source);
    b.onclick = () => { source = b.dataset.src; showResult(); };
  });
  const src = state.sources[source];
  $("offsetBox").classList.toggle("hidden", source !== "index");
  if (source === "index" && $("offset").dataset.for !== state.fileName) {
    $("offset").value = src.offset;
    $("offset").dataset.for = state.fileName;
  }
  renderTree(src);
  $("msg").textContent = "";
}

function renderTree(src) {
  const off = source === "index" ? (parseInt($("offset").value, 10) || 0) : 0;
  const clamp = (p) => Math.min(Math.max(p, 1), state.pages);
  let docs = 0, folders = 0;
  const frag = document.createDocumentFragment();
  const tree = toTree(src.entries);
  const build = (nodes, parent) => {
    for (const n of nodes) {
      if (n.children.length) {
        folders++;
        const d = document.createElement("details");
        d.open = true;
        const s = document.createElement("summary");
        s.textContent = n.title;
        d.appendChild(s);
        build(n.children, d);
        parent.appendChild(d);
      } else {
        docs++;
        const a = clamp(n.start + off), b = clamp((n.end ?? n.start) + off);
        const row = document.createElement("div");
        row.className = "doc";
        const t = document.createElement("span");
        t.className = "t"; t.textContent = n.title;
        const pg = document.createElement("span");
        pg.className = "pg";
        pg.textContent = a === b ? `p ${a}` : `pp ${a}–${b}`;
        row.append(t, pg);
        parent.appendChild(row);
      }
    }
  };
  build(tree, frag);
  $("tree").replaceChildren(frag);
  $("ndocs").textContent = docs.toLocaleString();
  $("nfolders").textContent = folders.toLocaleString();
}

$("offset").addEventListener("input", () => state && renderTree(state.sources[source]));

$("split").addEventListener("click", async () => {
  const btn = $("split"), msg = $("msg"), bar = $("bar");
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span> Splitting…';
  bar.classList.remove("hidden");
  bar.firstElementChild.style.width = "0";
  msg.textContent = ""; msg.className = "";
  try {
    const src = state.sources[source];
    const stem = state.fileName.replace(/\.pdf$/i, "");
    window.__pageCount = state.pages;
    const { zipped, docs, skipped } = await splitToZip(
      state.bytes, src.entries, `${stem} — split`,
      {
        offset: source === "index" ? (parseInt($("offset").value, 10) || 0) : 0,
        printed: source === "index",
        onProgress: (done, total) => {
          bar.firstElementChild.style.width = `${Math.round(100 * done / total)}%`;
          btn.innerHTML = `<span class="spin"></span> Splitting… ${done}/${total}`;
        },
      },
    );
    if (!docs) throw new Error("No documents fell inside the PDF at that page offset.");
    const blob = new Blob([zipped], { type: "application/zip" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${stem} — split.zip`;
    a.click();
    URL.revokeObjectURL(a.href);
    msg.className = "ok";
    msg.textContent = `Done — ${docs} documents written` +
      (skipped.length ? ` (${skipped.length} index entries fell outside the PDF and were skipped — check the page offset).` : ".");
  } catch (err) {
    msg.className = "err";
    msg.textContent = err.message || String(err);
  } finally {
    btn.disabled = false;
    btn.textContent = "Split & download zip";
    bar.classList.add("hidden");
  }
});

$("reset").addEventListener("click", resetUI);
function resetUI() {
  state = null;
  fileInput.value = "";
  $("result").classList.add("hidden");
  $("busyAnalyze").classList.add("hidden");
  drop.classList.remove("hidden");
}
