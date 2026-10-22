/* The click-wheel widget.
 *
 * Drag a file over the wheel: the ring fills with the formats it can become and the segment under
 * the pointer lights up (with a soft click). Drop on a segment to convert straight away, or drop on
 * the centre to load the file, spin the wheel to choose, and press the centre to convert.
 */
(function () {
  "use strict";

  const { api, load, save, uploadFiles, DEFAULT_TARGET, token } = window.LC;
  const $ = (id) => document.getElementById(id);
  const SVGNS = "http://www.w3.org/2000/svg";
  const NATIVE = new URLSearchParams(location.search).get("shell") === "native";
  if (!NATIVE) document.body.classList.add("in-browser");

  const device = $("device"), wheel = $("wheel"), segG = $("segments"), hub = $("hub");
  const R_IN = 68, R_OUT = 146, R_LABEL = 107, R_PROGRESS = 152, MAX_SEGMENTS = 16;

  // MIME types browsers report while a file is being dragged (before we know its name).
  const MIME_EXT = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic", "image/heif": "heic",
    "image/avif": "avif", "image/gif": "gif", "image/bmp": "bmp", "image/tiff": "tiff", "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico", "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx", "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx", "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.ms-powerpoint": "ppt", "application/vnd.oasis.opendocument.text": "odt",
    "text/plain": "txt", "text/markdown": "md", "text/html": "html", "text/csv": "csv", "application/json": "json",
    "application/x-yaml": "yaml", "text/yaml": "yaml", "application/epub+zip": "epub", "application/rtf": "rtf",
    "text/rtf": "rtf", "video/mp4": "mp4", "video/quicktime": "mov", "video/x-matroska": "mkv", "video/webm": "webm",
    "video/x-msvideo": "avi", "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/mp4": "m4a",
    "audio/x-m4a": "m4a", "audio/flac": "flac", "audio/ogg": "ogg", "audio/aac": "aac", "application/zip": "zip",
    "application/x-zip-compressed": "zip", "application/gzip": "tar.gz", "application/x-gzip": "tar.gz",
    "application/x-tar": "tar",
  };
  const MIME_PREFIX = { "image/": "jpg", "video/": "mp4", "audio/": "mp3", "text/": "txt" };
  const GENERIC = ["pdf", "jpg", "png", "docx", "txt", "mp3", "mp4", "zip"];
  const POPULAR = ["pdf", "jpg", "png", "docx", "mp3", "mp4", "webp", "heic", "txt", "xlsx", "csv", "gif", "mov",
    "wav", "m4a", "html", "md", "json", "yaml", "tiff", "avif", "webm", "mkv", "flac", "aac", "ogg", "opus", "aiff",
    "avi", "m4v", "bmp", "ico", "tsv", "zip", "tar.gz", "tar", "tar.xz", "tar.bz2", "srt", "vtt"];
  const CATEGORY_COLOR = {
    image: "#7c3aed", pdf: "#dc2626", document: "#2563eb", spreadsheet: "#16a34a", presentation: "#ea580c",
    audio: "#db2777", video: "#9333ea", archive: "#78716c", data: "#0891b2", subtitle: "#4f46e5", code: "#475569",
  };
  const ICONS = {
    drop: '<path d="M12 4v11"/><path d="M7.5 10.5L12 15l4.5-4.5"/><path d="M5 19h14"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    alert: '<path d="M12 7v6"/><path d="M12 17h.01"/><circle cx="12" cy="12" r="9"/>',
    spin: '<path d="M12 3a9 9 0 1 0 9 9"/>',
  };

  const st = {
    phase: "idle",        // idle | drag | loaded | working | done | error
    formats: {},
    files: [],            // [{id, name, ext, category}]
    targets: [],          // formats on the ring
    guessed: false,       // ring built from a drag guess rather than the real file
    sel: -1,              // highlighted segment
    hover: null,          // during drag: segment index or "hub"
    jobs: [],
    outputs: [],
    lastTarget: "",
    message: "",
    prevPhase: "idle",
  };

  // ------------------------------------------------------------------ sound: the iPod click
  let soundOn = load("lc-wheel-sound", true);
  let audio = null;
  function tick() {
    if (!soundOn) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === "suspended") audio.resume();
      const len = Math.floor(audio.sampleRate * 0.005);
      const buf = audio.createBuffer(1, len, audio.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 5);
      const src = audio.createBufferSource();
      src.buffer = buf;
      const hp = audio.createBiquadFilter();
      hp.type = "highpass"; hp.frequency.value = 2200;
      const gain = audio.createGain();
      gain.gain.value = 0.35;
      src.connect(hp).connect(gain).connect(audio.destination);
      src.start();
    } catch (e) { /* audio unavailable */ }
  }

  // ------------------------------------------------------------------ targets
  function sortTargets(list) {
    const rank = (t) => { const i = POPULAR.indexOf(t); return i < 0 ? 999 : i; };
    return list.slice().sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)).slice(0, MAX_SEGMENTS);
  }

  function targetsForExts(exts) {
    let common = null;
    for (const ext of exts) {
      const t = st.formats[ext] || {};
      const avail = Object.keys(t).filter((k) => t[k].available);
      common = common === null ? avail : common.filter((k) => avail.includes(k));
    }
    return sortTargets(common || []);
  }

  function universe() {
    const all = new Set();
    for (const src of Object.values(st.formats)) for (const [t, i] of Object.entries(src)) if (i.available) all.add(t);
    return all;
  }

  function guessFromDrag(dt) {
    const items = Array.from((dt && dt.items) || []).filter((i) => i.kind === "file");
    const exts = items.map((i) => MIME_EXT[i.type] ||
      Object.entries(MIME_PREFIX).filter(([p]) => (i.type || "").startsWith(p)).map(([, e]) => e)[0]);
    if (items.length && exts.every(Boolean)) {
      const t = targetsForExts([...new Set(exts)]);
      if (t.length) return { targets: t, exts };
    }
    const all = universe();
    return { targets: GENERIC.filter((t) => all.has(t)), exts: null };
  }

  // ------------------------------------------------------------------ drawing the ring
  function polar(r, deg) { const a = deg * Math.PI / 180; return [r * Math.cos(a), r * Math.sin(a)]; }

  function arcPath(r1, r2, a0, a1) {
    const large = a1 - a0 > 180 ? 1 : 0;
    const [x0, y0] = polar(r2, a0), [x1, y1] = polar(r2, a1), [x2, y2] = polar(r1, a1), [x3, y3] = polar(r1, a0);
    return `M${x0} ${y0}A${r2} ${r2} 0 ${large} 1 ${x1} ${y1}L${x2} ${y2}A${r1} ${r1} 0 ${large} 0 ${x3} ${y3}Z`;
  }

  function drawRing() {
    segG.textContent = "";
    const n = st.targets.length;
    if (!n) return;
    const span = 360 / n;
    const size = n <= 6 ? 15 : n <= 9 ? 13.5 : n <= 12 ? 12 : 10.5;
    const idle = st.phase === "idle";
    st.targets.forEach((t, i) => {
      const mid = i * span - 90;
      const g = document.createElementNS(SVGNS, "g");
      g.setAttribute("class", "seg" + (idle ? " dim idle-seg" : ""));
      g.dataset.index = i;
      const path = document.createElementNS(SVGNS, "path");
      const a0 = n === 1 ? -269.99 : mid - span / 2, a1 = n === 1 ? 89.99 : mid + span / 2;
      path.setAttribute("d", arcPath(R_IN, R_OUT, a0, a1));
      const [lx, ly] = polar(R_LABEL, mid);
      const text = document.createElementNS(SVGNS, "text");
      text.setAttribute("x", lx.toFixed(1)); text.setAttribute("y", ly.toFixed(1));
      text.setAttribute("font-size", size);
      text.textContent = t.toUpperCase();
      g.append(path, text);
      segG.appendChild(g);
    });
    paintActive();
  }

  function activeIndex() {
    if (st.phase === "drag") return typeof st.hover === "number" ? st.hover : -1;
    if (["loaded", "done", "working"].includes(st.phase)) return st.sel;
    return -1;
  }

  function paintActive() {
    const idx = activeIndex();
    for (const g of segG.children) g.classList.toggle("active", +g.dataset.index === idx);
    hub.classList.toggle("target", st.phase === "drag" && st.hover === "hub");
  }

  function setProgress(frac) {
    const p = $("progress");
    const c = 2 * Math.PI * R_PROGRESS;
    p.style.strokeDasharray = `${c}`;
    p.style.strokeDashoffset = `${c * (1 - Math.max(0, Math.min(1, frac)))}`;
    p.classList.toggle("on", frac > 0 && st.phase === "working");
  }

  // ------------------------------------------------------------------ centre display
  function setHub({ icon = null, badge = null, badgeColor = null, title = "", big = false, sub = "", wrap = false }) {
    const ic = $("hubIcon");
    ic.toggleAttribute("hidden", !icon);   // SVG elements have no .hidden property
    if (icon) ic.innerHTML = ICONS[icon];
    const b = $("hubBadge");
    b.hidden = !badge;
    if (badge) { b.textContent = badge; b.style.background = badgeColor || "#64748b"; }
    const t = $("hubTitle");
    t.textContent = title; t.classList.toggle("big", big);
    const s = $("hubSub");
    s.textContent = sub; s.classList.toggle("wrap", wrap);
    hub.className = "hub state-" + st.phase;
  }

  function sourceBadge() {
    if (st.files.length > 1) return { badge: `${st.files.length} files`, badgeColor: "#475569" };
    const f = st.files[0];
    return f ? { badge: f.ext || "?", badgeColor: CATEGORY_COLOR[f.category] } : {};
  }

  function render() {
    device.classList.toggle("dragging", st.phase === "drag");
    device.classList.toggle("show-controls", st.phase === "idle" || st.phase === "done");
    const sel = st.targets[st.sel];
    switch (st.phase) {
      case "idle":
        setHub({ icon: "drop", title: "Drop a file", sub: "or press to choose" });
        break;
      case "drag": {
        const h = st.hover;
        if (typeof h === "number") setHub({ title: st.targets[h].toUpperCase(), big: true, sub: "release to convert" });
        else if (h === "hub") setHub({ icon: "drop", title: "Drop here", sub: "then spin to choose" });
        else setHub({ icon: "drop", title: "Pick a format", sub: "or drop in the centre" });
        break;
      }
      case "loaded":
        setHub(Object.assign(sourceBadge(), { title: sel ? sel.toUpperCase() : "", big: true,
          sub: st.message || "press to convert", wrap: !!st.message }));
        break;
      case "working":
        setHub({ title: `${Math.round(st.progress * 100)}%`, big: true, sub: `to ${st.lastTarget.toUpperCase()}` });
        break;
      case "done": {
        const name = st.outputs.length === 1 ? st.outputs[0].name : `${st.outputs.length} files saved`;
        setHub({ icon: "check", title: name, sub: "press to show" });
        break;
      }
      case "error":
        setHub({ icon: "alert", title: "Can't convert", sub: st.message, wrap: true });
        break;
    }
    $("controls").classList.toggle("muted", !soundOn);
    paintActive();
  }

  function announce(text) { $("live").textContent = text; }

  // ------------------------------------------------------------------ state changes
  function goIdle() {
    for (const f of st.files) api(`/api/files/${f.id}`, { method: "DELETE" }).catch(() => {});
    Object.assign(st, { phase: "idle", files: [], sel: -1, hover: null, jobs: [], outputs: [], message: "", guessed: false });
    const all = universe();
    st.targets = GENERIC.filter((t) => all.has(t));
    setProgress(0);
    drawRing(); render();
  }

  function loadFiles(entries, wanted) {
    for (const f of st.files) if (!entries.some((e) => e.id === f.id)) api(`/api/files/${f.id}`, { method: "DELETE" }).catch(() => {});
    st.files = entries;
    st.targets = targetsForExts([...new Set(entries.map((e) => e.ext))]);
    st.guessed = false;
    st.outputs = []; st.jobs = []; st.message = "";
    if (!st.targets.length) {
      st.phase = "error";
      st.message = entries.length === 1 ? `.${entries[0].ext || "?"} files aren't supported yet` : "These files have no format in common";
      drawRing(); render(); announce(st.message);
      return;
    }
    const preferred = load("lc-last-target-" + entries[0].ext, null) || DEFAULT_TARGET[entries[0].ext];
    st.sel = Math.max(0, st.targets.indexOf(preferred));
    st.phase = "loaded";
    drawRing();
    if (wanted && st.targets.includes(wanted)) {
      st.sel = st.targets.indexOf(wanted);
      convert();
      return;
    }
    if (wanted) st.message = `${wanted.toUpperCase()} isn't possible here`;
    render();
    announce(`${entries.length === 1 ? entries[0].name : entries.length + " files"} ready. Choose a format.`);
  }

  function select(i) {
    const n = st.targets.length;
    if (!n) return;
    const next = ((i % n) + n) % n;
    if (next !== st.sel) tick();
    st.sel = next;
    if (st.phase === "done" || st.phase === "error") st.phase = "loaded";
    st.message = "";
    render();
    announce(st.targets[st.sel].toUpperCase());
  }

  async function convert() {
    const target = st.targets[st.sel];
    if (!target || !st.files.length) return;
    save("lc-last-target-" + st.files[0].ext, target);
    st.phase = "working"; st.progress = 0; st.lastTarget = target; st.message = "";
    render(); setProgress(0.02);
    try {
      const jobs = await api("/api/convert", {
        json: { items: st.files.map((f) => ({ file_id: f.id, target })), options: load("lc-options", {}) },
      });
      st.jobs = jobs.map((j) => j.id);
      poll();
    } catch (e) {
      st.phase = "error"; st.message = e.message; render();
    }
  }

  async function poll() {
    try {
      const res = await api("/api/jobs?ids=" + st.jobs.join(","));
      const jobs = st.jobs.map((id) => res[id]).filter(Boolean);
      st.progress = jobs.reduce((a, j) => a + (j.status === "done" ? 1 : j.progress), 0) / Math.max(1, jobs.length);
      setProgress(st.progress);
      if (jobs.some((j) => j.status === "queued" || j.status === "running")) {
        render();
        setTimeout(poll, 300);
        return;
      }
      const failed = jobs.filter((j) => j.status === "error");
      st.outputs = jobs.filter((j) => j.status === "done").flatMap((j) => j.outputs);
      st.doneJob = (jobs.find((j) => j.status === "done") || {}).id;
      if (failed.length && !st.outputs.length) {
        st.phase = "error"; st.message = failed[0].message;
      } else {
        st.phase = "done";
        tick();
      }
      setProgress(0);
      render();
      announce(st.phase === "done" ? "Converted." : "Conversion failed: " + st.message);
    } catch (e) {
      setTimeout(poll, 800);
    }
  }

  async function addFiles(fileList, wanted) {
    st.phase = "working"; st.progress = 0; st.lastTarget = wanted || "";
    setHub({ icon: "spin", title: "Adding…", sub: "" });
    try {
      const entries = await uploadFiles(fileList, (f) => { st.progress = f; setProgress(f * 0.3); });
      setProgress(0);
      loadFiles(entries, wanted);
    } catch (e) {
      st.phase = "error"; st.message = e.message; render();
    }
  }

  // ------------------------------------------------------------------ geometry
  function locate(clientX, clientY) {
    const r = wheel.getBoundingClientRect();
    const scale = 320 / r.width;
    const x = (clientX - (r.left + r.width / 2)) * scale;
    const y = (clientY - (r.top + r.height / 2)) * scale;
    const d = Math.hypot(x, y);
    const ang = (Math.atan2(y, x) * 180 / Math.PI + 90 + 360) % 360;   // 0deg = top, clockwise
    if (d < R_IN - 2) return { zone: "hub", ang, d };
    if (d > 162) return { zone: "out", ang, d };
    const n = st.targets.length;
    return { zone: "ring", idx: n ? Math.round(ang / (360 / n)) % n : -1, ang, d };
  }

  // ------------------------------------------------------------------ drag & drop from the desktop
  let depth = 0;
  window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    if (depth++ > 0) return;
    if (st.phase === "working") return;
    st.prevPhase = st.phase;
    st.prevTargets = st.targets;
    const guess = guessFromDrag(e.dataTransfer);
    st.targets = guess.targets; st.guessed = !guess.exts;
    st.phase = "drag"; st.hover = null;
    drawRing(); render();
  });
  window.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (st.phase !== "drag") return;
    e.dataTransfer.dropEffect = "copy";
    const loc = locate(e.clientX, e.clientY);
    const hover = loc.zone === "ring" ? loc.idx : loc.zone === "hub" ? "hub" : null;
    if (hover !== st.hover) {
      if (typeof hover === "number") tick();
      st.hover = hover;
      render();
    }
  });
  window.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (depth || st.phase !== "drag") return;
    st.phase = st.prevPhase; st.targets = st.prevTargets; st.hover = null;
    drawRing(); render();
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    if (st.phase !== "drag") return;
    const files = e.dataTransfer.files;
    const wanted = typeof st.hover === "number" ? st.targets[st.hover] : null;
    st.hover = null;
    if (!files || !files.length) { st.phase = st.prevPhase; st.targets = st.prevTargets; drawRing(); render(); return; }
    addFiles(files, wanted);
  });

  // ------------------------------------------------------------------ spinning the wheel
  let spin = null;
  const canSpin = () => ["loaded", "done", "error"].includes(st.phase) && st.files.length && st.targets.length;

  segG.addEventListener("pointerdown", (e) => {
    if (!canSpin()) return;
    e.preventDefault();
    const loc = locate(e.clientX, e.clientY);
    spin = { id: e.pointerId, last: loc.ang, acc: 0, x: e.clientX, y: e.clientY, moved: false, idx: loc.idx };
    try { e.target.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  });
  window.addEventListener("pointermove", (e) => {
    if (!spin || e.pointerId !== spin.id) return;
    const loc = locate(e.clientX, e.clientY);
    let delta = loc.ang - spin.last;
    if (delta > 180) delta -= 360;
    if (delta < -180) delta += 360;
    spin.last = loc.ang;
    if (Math.hypot(e.clientX - spin.x, e.clientY - spin.y) > 6) spin.moved = true;
    if (!spin.moved) return;
    spin.acc += delta;
    const step = Math.max(15, Math.min(30, 360 / st.targets.length));
    while (spin.acc >= step) { spin.acc -= step; select(st.sel + 1); }
    while (spin.acc <= -step) { spin.acc += step; select(st.sel - 1); }
  });
  window.addEventListener("pointerup", (e) => {
    if (!spin || e.pointerId !== spin.id) return;
    const s = spin;
    spin = null;
    if (!s.moved && s.idx >= 0) {   // a tap on a format converts straight to it
      select(s.idx);
      convert();
    }
  });
  window.addEventListener("pointercancel", () => { spin = null; });

  let wheelAcc = 0;
  device.addEventListener("wheel", (e) => {
    if (!canSpin()) return;
    e.preventDefault();
    wheelAcc += Math.abs(e.deltaY) > Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
    while (wheelAcc >= 40) { wheelAcc -= 40; select(st.sel + 1); }
    while (wheelAcc <= -40) { wheelAcc += 40; select(st.sel - 1); }
  }, { passive: false });

  // ------------------------------------------------------------------ the centre button
  async function pressHub() {
    tick();
    if (st.phase === "idle") {
      try {
        const res = await api("/api/pick-files", { json: {} });
        if (res.supported) { if (res.files.length) loadFiles(res.files, null); return; }
      } catch (e) { /* fall back to the browser picker */ }
      pickerInput().click();
    } else if (st.phase === "loaded") {
      convert();
    } else if (st.phase === "done" && st.doneJob) {
      api(`/api/reveal/${st.doneJob}`, { json: {} }).catch(() => {});
    } else if (st.phase === "error") {
      if (st.files.length && st.targets.length) { st.phase = "loaded"; st.message = ""; render(); } else goIdle();
    }
  }

  let input = null;
  function pickerInput() {
    if (!input) {
      input = document.createElement("input");
      input.type = "file"; input.multiple = true; input.hidden = true;
      input.addEventListener("change", () => { addFiles(input.files, null); input.value = ""; });
      document.body.appendChild(input);
    }
    return input;
  }

  hub.addEventListener("click", pressHub);
  hub.addEventListener("contextmenu", (e) => { e.preventDefault(); goIdle(); });   // right-click = clear

  device.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight" || e.key === "ArrowDown") { if (canSpin()) select(st.sel + 1); e.preventDefault(); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") { if (canSpin()) select(st.sel - 1); e.preventDefault(); }
    else if (e.key === "Enter" || e.key === " ") { hub.classList.add("pressed"); pressHub(); e.preventDefault(); }
    else if (e.key === "Escape") goIdle();
  });
  device.addEventListener("keyup", () => hub.classList.remove("pressed"));

  // ------------------------------------------------------------------ small controls
  $("btnSound").addEventListener("click", (e) => {
    e.stopPropagation();
    soundOn = !soundOn; save("lc-wheel-sound", soundOn);
    render(); tick();
  });
  $("btnApp").addEventListener("click", async (e) => {
    e.stopPropagation();
    const res = await api("/api/window/open", { json: { name: "main" } }).catch(() => ({ native: false }));
    if (!res.native) window.open(`/#token=${encodeURIComponent(token)}`, "localconvert-main");
  });
  $("btnClose").addEventListener("click", async (e) => {
    e.stopPropagation();
    const res = await api("/api/window/close", { json: { name: "widget" } }).catch(() => ({ native: false }));
    if (!res.native) window.close();
  });

  // ------------------------------------------------------------------ start
  async function init() {
    try {
      st.formats = await api("/api/formats");
    } catch (e) {
      st.phase = "error"; st.message = e.message; render();
      return;
    }
    goIdle();
    setInterval(() => api("/api/ping", { json: {} }).catch(() => {}), 20000);
  }
  init();
})();
