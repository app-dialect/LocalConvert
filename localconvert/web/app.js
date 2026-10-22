/* LocalConvert UI. Talks only to the local server on 127.0.0.1. */
(function () {
  "use strict";

  const { api, load, save, DEFAULT_TARGET } = window.LC;
  const TOKEN = window.LC.token;

  const $ = (sel) => document.querySelector(sel);
  const state = { formats: {}, info: null, files: [], combinedJob: null, polling: null };

  const CATEGORY_LABEL = {
    image: "Image", pdf: "PDF", document: "Document", spreadsheet: "Spreadsheet", presentation: "Presentation",
    data: "Data", audio: "Audio", video: "Video", archive: "Archive", subtitle: "Subtitles", code: "Code", other: "Other",
  };
  const ENGINE_LABEL = { libreoffice: "LibreOffice", ffmpeg: "FFmpeg", heif: "HEIC support", builtin: "built-in" };

  // ------------------------------------------------------------------ helpers
  function extOf(name) {
    const n = name.toLowerCase();
    for (const c of ["tar.gz", "tar.bz2", "tar.xz"]) if (n.endsWith("." + c)) return c;
    const i = n.lastIndexOf(".");
    return i > 0 ? n.slice(i + 1) : "";
  }

  function size(n) {
    if (n < 1024) return n + " B";
    const u = ["KB", "MB", "GB"]; let i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return n.toFixed(1) + " " + u[i];
  }

  let toastTimer;
  function toast(msg, ms = 3500) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, ms);
  }

  function categoryColor(cat) { return `var(--cat-${cat || "other"})`; }

  // ------------------------------------------------------------------ target menus
  function fillTargetSelect(select, targets, selected) {
    select.textContent = "";
    const groups = {};
    for (const [ext, info] of Object.entries(targets)) {
      (groups[info.category] = groups[info.category] || []).push([ext, info]);
    }
    for (const [cat, items] of Object.entries(groups)) {
      const og = document.createElement("optgroup");
      og.label = CATEGORY_LABEL[cat] || cat;
      for (const [ext, info] of items) {
        const o = document.createElement("option");
        o.value = ext;
        let label = ext.toUpperCase();
        if (!info.available) { label += ` (needs ${ENGINE_LABEL[info.engine] || info.engine})`; o.disabled = true; }
        else if (info.basic) label += " · basic layout";
        o.textContent = label;
        o.title = info.note || "";
        og.appendChild(o);
      }
      select.appendChild(og);
    }
    if (selected && targets[selected] && targets[selected].available) select.value = selected;
  }

  function pickDefault(ext, targets) {
    const remembered = load("lc-last-target-" + ext, null);
    for (const t of [remembered, DEFAULT_TARGET[ext], "pdf"]) {
      if (t && targets[t] && targets[t].available) return t;
    }
    const first = Object.entries(targets).find(([, i]) => i.available);
    return first ? first[0] : "";
  }

  function refreshBulk() {
    const select = $("#bulkTarget");
    const convertible = state.files.filter((f) => Object.keys(f.targets).length);
    let common = null;
    for (const f of convertible) {
      const avail = Object.keys(f.targets).filter((t) => f.targets[t].available);
      common = common === null ? avail : common.filter((t) => avail.includes(t));
    }
    common = common || [];
    const targets = {};
    for (const t of common) targets[t] = convertible[0].targets[t];
    fillTargetSelect(select, targets);
    const ph = document.createElement("option");
    ph.value = ""; ph.textContent = common.length ? "Choose…" : "No common format"; ph.selected = true;
    select.insertBefore(ph, select.firstChild);
    select.value = "";
    select.disabled = common.length === 0;

    // Combine-to-PDF is offered when 2+ files can all become PDF.
    const canCombine = convertible.length >= 2 && convertible.length === state.files.length &&
      convertible.every((f) => f.ext === "pdf" || (f.targets.pdf && f.targets.pdf.available));
    $("#combineWrap").hidden = !canCombine;
    if (!canCombine) $("#combine").checked = false;
    updateConvertButton();
  }

  function updateConvertButton() {
    const btn = $("#convert");
    const combine = $("#combine").checked;
    const ready = state.files.filter((f) => f.target && (f.status === "idle" || f.status === "error"));
    if (combine) {
      btn.textContent = `Combine ${state.files.length} files into one PDF`;
      btn.disabled = state.files.length < 2;
    } else {
      btn.textContent = ready.length > 1 ? `Convert ${ready.length} files` : "Convert";
      btn.disabled = ready.length === 0;
    }
    for (const f of state.files) f.el.querySelector(".target").disabled = combine || !Object.keys(f.targets).length;
  }

  // ------------------------------------------------------------------ file rows
  function addFiles(list) {
    for (const meta of list) {
      const targets = state.formats[meta.ext] || {};
      const f = Object.assign({}, meta, { targets, target: "", status: "idle", jobId: null, progress: 0, message: "" });
      f.target = pickDefault(meta.ext, targets);
      f.el = buildRow(f);
      state.files.push(f);
      $("#fileList").appendChild(f.el);
      renderRow(f);
    }
    showQueue();
    refreshBulk();
  }

  function buildRow(f) {
    const el = $("#rowTpl").content.firstElementChild.cloneNode(true);
    const badge = el.querySelector(".badge");
    badge.textContent = f.ext || "?";
    badge.style.background = categoryColor(f.category);
    el.querySelector(".name").textContent = f.name;
    el.querySelector(".name").title = f.path || f.name;
    const select = el.querySelector(".target");
    if (Object.keys(f.targets).length) {
      fillTargetSelect(select, f.targets, f.target);
    } else {
      const o = document.createElement("option"); o.textContent = "Not supported"; select.appendChild(o);
      select.disabled = true;
    }
    select.addEventListener("change", () => {
      f.target = select.value;
      save("lc-last-target-" + f.ext, f.target);
      if (f.status === "done" || f.status === "error") { f.status = "idle"; f.message = ""; }
      renderRow(f); updateConvertButton();
    });
    el.querySelector(".remove").addEventListener("click", () => removeFile(f));
    el.querySelector(".show").addEventListener("click", () => api(`/api/reveal/${f.jobId}`, { json: {} }).catch((e) => toast(e.message)));
    return el;
  }

  function renderRow(f) {
    const el = f.el;
    el.className = "file " + f.status;
    const sub = el.querySelector(".sub");
    const info = f.targets[f.target];
    sub.className = "sub";
    if (!Object.keys(f.targets).length) {
      sub.textContent = `${size(f.size)} · .${f.ext || "?"} files can't be converted yet`;
      sub.classList.add("error");
    } else if (f.status === "error") {
      sub.textContent = f.message; sub.classList.add("error");
    } else if (f.status === "done" && f.outputs) {
      sub.textContent = "Saved: " + f.outputs.map((o) => o.name).join(", ");
      sub.title = f.outputs.map((o) => o.path).join("\n");
    } else if (info && info.basic) {
      sub.textContent = `${size(f.size)} · ${info.note}`; sub.classList.add("note");
    } else {
      sub.textContent = size(f.size) + (info && info.engine !== "builtin" ? ` · via ${ENGINE_LABEL[info.engine]}` : "");
    }
    const fill = el.querySelector(".fill");
    fill.style.width = Math.round((f.progress || 0) * 100) + "%";
    const st = el.querySelector(".state");
    st.textContent = { idle: "", queued: "Waiting…", running: `Converting… ${Math.round(f.progress * 100)}%`,
      done: "✓ Done", error: "Failed" }[f.status] || "";
    const done = f.status === "done";
    el.querySelector(".show").hidden = !done;
    const dl = el.querySelector(".download");
    dl.hidden = !done;
    if (done) dl.href = `/api/download/${f.jobId}?token=${encodeURIComponent(TOKEN)}`;
  }

  function removeFile(f) {
    state.files = state.files.filter((x) => x !== f);
    f.el.remove();
    api(`/api/files/${f.id}`, { method: "DELETE" }).catch(() => {});
    if (!state.files.length) showQueue();
    refreshBulk();
  }

  function showQueue() {
    const has = state.files.length > 0;
    $("#queue").hidden = !has;
    $("#drop").classList.toggle("compact", has);
    $("#count").textContent = has ? `(${state.files.length})` : "";
  }

  // ------------------------------------------------------------------ adding files
  function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const big = files.reduce((n, f) => n + f.size, 0) > 20 * 1024 * 1024;
    window.LC.uploadFiles(files, big ? (f) => toast(`Adding files\u2026 ${Math.round(f * 100)}%`, 2000) : null)
      .then(addFiles, (e) => toast(e.message));
  }

  async function chooseFiles() {
    try {
      const res = await api("/api/pick-files", { json: {} });
      if (!res.supported) { $("#fileInput").click(); return; }
      if (res.files.length) addFiles(res.files);
    } catch (e) {
      $("#fileInput").click();
    }
  }

  // ------------------------------------------------------------------ converting
  function options() {
    return {
      quality: +$("#quality").value, dpi: +$("#dpi").value, page_size: $("#pageSize").value,
      keep_metadata: $("#keepMeta").checked,
    };
  }

  async function convert() {
    const btn = $("#convert");
    btn.disabled = true;
    try {
      if ($("#combine").checked) {
        const jobs = await api("/api/convert", { json: { combine: true, file_ids: state.files.map((f) => f.id), options: options() } });
        state.combinedJob = jobs[0];
        renderCombined();
      } else {
        const items = state.files.filter((f) => f.target && (f.status === "idle" || f.status === "error"))
          .map((f) => ({ file_id: f.id, target: f.target }));
        const jobs = await api("/api/convert", { json: { items, options: options() } });
        for (const j of jobs) {
          const f = state.files.find((x) => x.id === j.file_id);
          if (f) { f.jobId = j.id; f.status = "queued"; f.progress = 0; f.message = ""; renderRow(f); }
        }
      }
      startPolling();
    } catch (e) {
      toast(e.message);
    }
    updateConvertButton();
  }

  function renderCombined() {
    const box = $("#combinedResult");
    const j = state.combinedJob;
    box.textContent = "";
    if (!j) return;
    const wrap = document.createElement("div");
    wrap.className = "combined";
    const meta = document.createElement("div");
    meta.className = "meta";
    const name = document.createElement("div");
    name.className = "name";
    const sub = document.createElement("div");
    sub.className = "sub";
    if (j.status === "done") {
      name.textContent = "✓ " + j.outputs[0].name;
      sub.textContent = `${j.inputs.length} files combined`;
      const show = document.createElement("button");
      show.className = "ghost small"; show.type = "button"; show.textContent = "Show";
      show.addEventListener("click", () => api(`/api/reveal/${j.id}`, { json: {} }));
      const dl = document.createElement("a");
      dl.className = "ghost small btn"; dl.textContent = "Download";
      dl.href = `/api/download/${j.id}?token=${encodeURIComponent(TOKEN)}`;
      meta.append(name, sub); wrap.append(meta, show, dl);
    } else if (j.status === "error") {
      name.textContent = "Combining failed"; sub.textContent = j.message; sub.classList.add("error");
      meta.append(name, sub); wrap.append(meta);
    } else {
      name.textContent = `Combining ${j.inputs.length} files into one PDF… ${Math.round(j.progress * 100)}%`;
      meta.append(name); wrap.append(meta);
    }
    box.appendChild(wrap);
  }

  function activeJobIds() {
    const ids = state.files.filter((f) => f.jobId && ["queued", "running"].includes(f.status)).map((f) => f.jobId);
    if (state.combinedJob && ["queued", "running"].includes(state.combinedJob.status)) ids.push(state.combinedJob.id);
    return ids;
  }

  function startPolling() {
    if (state.polling) return;
    state.polling = setInterval(async () => {
      const ids = activeJobIds();
      if (!ids.length) { clearInterval(state.polling); state.polling = null; updateConvertButton(); return; }
      try {
        const jobs = await api("/api/jobs?ids=" + ids.join(","));
        for (const [id, j] of Object.entries(jobs)) {
          if (state.combinedJob && state.combinedJob.id === id) { state.combinedJob = j; renderCombined(); continue; }
          const f = state.files.find((x) => x.jobId === id);
          if (!f) continue;
          f.status = j.status; f.progress = j.progress; f.message = j.message; f.outputs = j.outputs;
          renderRow(f);
        }
        updateConvertButton();
      } catch (e) { /* transient; try again */ }
    }, 400);
  }

  // ------------------------------------------------------------------ settings, engines, quit
  function renderEngines(engines) {
    const box = $("#engines");
    box.textContent = "";
    const label = document.createElement("span");
    label.textContent = "Engines:";
    box.appendChild(label);
    for (const [key, e] of Object.entries(engines)) {
      if (key === "builtin") continue;
      const chip = document.createElement("span");
      chip.className = "chip" + (e.available ? "" : " off");
      const dot = document.createElement("span"); dot.className = "dot";
      chip.append(dot, document.createTextNode(ENGINE_LABEL[key] + (e.available ? "" : " — not installed")));
      chip.title = e.description + (key === "libreoffice" && !e.available
        ? ". Install the free LibreOffice app (libreoffice.org) for exact Word/Excel/PowerPoint to PDF, then click Re-check." : "");
      box.appendChild(chip);
    }
    const re = document.createElement("button");
    re.className = "ghost small"; re.type = "button"; re.textContent = "Re-check";
    re.addEventListener("click", async () => {
      const res = await api("/api/engines/refresh", { json: {} });
      state.formats = res.formats;
      for (const f of state.files) {
        f.targets = state.formats[f.ext] || {};
        fillTargetSelect(f.el.querySelector(".target"), f.targets, f.target);
        renderRow(f);
      }
      renderEngines(res.engines); refreshBulk();
      toast("Engines re-checked.");
    });
    box.appendChild(re);
  }

  function setOutDir(path) {
    const el = $("#outDir");
    el.textContent = path; el.title = path;
  }

  function restoreOptions() {
    const o = load("lc-options", null);
    if (!o) return;
    if (o.quality) $("#quality").value = o.quality;
    if (o.dpi) $("#dpi").value = o.dpi;
    if (o.page_size) $("#pageSize").value = o.page_size;
    $("#keepMeta").checked = !!o.keep_metadata;
    $("#qualityOut").textContent = $("#quality").value;
  }

  function wire() {
    $("#browse").addEventListener("click", chooseFiles);
    $("#addMore").addEventListener("click", chooseFiles);
    $("#fileInput").addEventListener("change", (e) => { uploadFiles(e.target.files); e.target.value = ""; });
    $("#drop").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); chooseFiles(); } });

    let depth = 0;
    window.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; $("#dropOverlay").hidden = false; });
    window.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) $("#dropOverlay").hidden = true; });
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => {
      e.preventDefault(); depth = 0; $("#dropOverlay").hidden = true;
      uploadFiles(e.dataTransfer.files);
    });

    $("#bulkTarget").addEventListener("change", (e) => {
      const t = e.target.value;
      if (!t) return;
      for (const f of state.files) {
        if (f.targets[t] && f.targets[t].available) {
          f.target = t; f.el.querySelector(".target").value = t;
          if (f.status === "done" || f.status === "error") f.status = "idle";
          renderRow(f);
        }
      }
      updateConvertButton();
    });
    $("#clear").addEventListener("click", () => {
      for (const f of state.files.slice()) removeFile(f);
      state.combinedJob = null; renderCombined();
    });
    $("#combine").addEventListener("change", updateConvertButton);
    $("#convert").addEventListener("click", convert);

    for (const id of ["quality", "dpi", "pageSize", "keepMeta"]) {
      $("#" + id).addEventListener("input", () => { $("#qualityOut").textContent = $("#quality").value; save("lc-options", options()); });
    }
    $("#changeDir").addEventListener("click", async () => {
      try {
        const res = await api("/api/pick-folder", { json: {} });
        if (res.supported) { setOutDir(res.output_dir); return; }
        const typed = prompt("Folder where converted files are saved:", $("#outDir").textContent);
        if (typed) { const r = await api("/api/settings", { json: { output_dir: typed } }); setOutDir(r.output_dir); }
      } catch (e) { toast(e.message); }
    });
    $("#openDir").addEventListener("click", () => api("/api/open-output", { json: {} }).catch((e) => toast(e.message)));
    $("#quit").addEventListener("click", async () => {
      if (activeJobIds().length && !confirm("Conversions are still running. Quit anyway?")) return;
      try { await api("/api/quit", { json: {} }); } catch (e) { /* already gone */ }
      document.body.innerHTML = '<div class="closed"><h1>LocalConvert has closed</h1><p class="muted">You can close this tab.</p></div>';
    });
    setInterval(() => api("/api/ping", { json: {} }).catch(() => {}), 20000);
    $("#openWheel").addEventListener("click", async () => {
      const res = await api("/api/window/open", { json: { name: "widget" } }).catch(() => ({ native: false }));
      if (!res.native) window.open(`/widget#token=${encodeURIComponent(TOKEN)}`, "localconvert-wheel", "popup,width=360,height=380");
    });
  }

  async function init() {
    wire();
    restoreOptions();
    if (!TOKEN) { toast("Missing access token - please start LocalConvert again.", 10000); return; }
    try {
      const [info, formats] = await Promise.all([api("/api/info"), api("/api/formats")]);
      state.info = info; state.formats = formats;
      setOutDir(info.output_dir);
      renderEngines(info.engines);
    } catch (e) {
      toast(e.message, 10000);
    }
  }

  init();
})();
