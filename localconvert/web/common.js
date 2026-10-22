/* Shared helpers for the main window and the wheel widget. Talks only to the local app on 127.0.0.1. */
(function () {
  "use strict";

  function sget(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function sset(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* private mode */ } }

  const token = new URLSearchParams(location.hash.slice(1)).get("token") || sget("lc-token") || "";
  sset("lc-token", token);

  function load(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } }
  function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } }

  async function api(path, opts = {}) {
    const headers = Object.assign({ "X-Token": token }, opts.json ? { "Content-Type": "application/json" } : {});
    const res = await fetch(path, {
      method: opts.method || (opts.json ? "POST" : "GET"),
      headers, body: opts.json ? JSON.stringify(opts.json) : opts.body,
    });
    if (!res.ok) {
      let msg = res.status === 403 ? "This page has expired - please reopen LocalConvert." : `Request failed (${res.status})`;
      try { const j = await res.json(); if (j.error) msg = j.error; } catch (e) { /* not json */ }
      throw new Error(msg);
    }
    return res.json();
  }

  /* Hand dropped/picked files to the local app. XHR (not fetch) so big files can report progress. */
  function uploadFiles(fileList, onProgress) {
    return new Promise((resolve, reject) => {
      const files = Array.from(fileList || []);
      if (!files.length) { resolve([]); return; }
      const form = new FormData();
      for (const file of files) form.append("files", file, file.name);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/files");
      xhr.setRequestHeader("X-Token", token);
      if (onProgress) xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      xhr.onload = () => (xhr.status === 200 ? resolve(JSON.parse(xhr.responseText))
        : reject(new Error(xhr.status === 403 ? "Please reopen LocalConvert." : "Could not add files.")));
      xhr.onerror = () => reject(new Error("Could not add files."));
      xhr.send(form);
    });
  }

  // Sensible first choice of target for each source format.
  const DEFAULT_TARGET = {
    jpg: "png", png: "jpg", heic: "jpg", webp: "png", avif: "jpg", gif: "png", bmp: "png", tiff: "pdf",
    ico: "png", psd: "png", pdf: "docx", docx: "pdf", doc: "pdf", odt: "pdf", rtf: "pdf", pages: "pdf",
    txt: "pdf", md: "pdf", html: "pdf", epub: "pdf", xlsx: "pdf", xls: "xlsx", xlsm: "xlsx", ods: "xlsx",
    numbers: "xlsx", csv: "xlsx", tsv: "xlsx", pptx: "pdf", ppt: "pdf", odp: "pdf", key: "pdf",
    json: "csv", yaml: "json", mp4: "mp3", mov: "mp4", mkv: "mp4", avi: "mp4", webm: "mp4", m4v: "mp4",
    wmv: "mp4", flv: "mp4", mpg: "mp4", "3gp": "mp4", wav: "mp3", m4a: "mp3", flac: "mp3", ogg: "mp3",
    opus: "mp3", aiff: "mp3", aac: "mp3", wma: "mp3", amr: "mp3", mp3: "wav", zip: "tar.gz",
    "tar.gz": "zip", tar: "zip", "tar.bz2": "zip", "tar.xz": "zip", srt: "vtt", vtt: "srt",
  };

  window.LC = { token, api, load, save, uploadFiles, DEFAULT_TARGET };
})();
