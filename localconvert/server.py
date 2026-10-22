"""Local web server that powers the user interface.

Privacy and safety guarantees enforced here:

* binds to 127.0.0.1 only - other computers on the network cannot reach it;
* every API call needs a random token created at launch, so other web pages
  open in the browser cannot talk to the app;
* the Host header is checked, which blocks DNS-rebinding attacks;
* a strict Content-Security-Policy stops the page from loading anything from
  the internet - there are no CDNs, fonts, analytics or telemetry.
"""
from __future__ import annotations

import hmac
import json
import secrets
import shutil
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from . import __version__
from .core import engines, registry
from .core.jobs import Job, JobManager
from .core.util import (app_data_dir, default_output_dir, get_ext, make_workdir, reveal_in_file_manager,
                        rmtree_quiet, run_quiet, safe_filename)

WEB_DIR = Path(__file__).resolve().parent / "web"

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@dataclass
class FileEntry:
    id: str
    path: Path
    name: str
    temporary: bool       # True if the browser handed us a copy we must delete later

    def to_dict(self) -> dict:
        ext = get_ext(self.name)
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        return {"id": self.id, "name": self.name, "ext": ext, "size": size,
                "category": registry.category_of(ext), "path": None if self.temporary else str(self.path)}


class Settings:
    def __init__(self):
        self._file = app_data_dir() / "settings.json"
        self.output_dir = default_output_dir()
        try:
            data = json.loads(self._file.read_text())
            if data.get("output_dir"):
                self.output_dir = Path(data["output_dir"])
        except Exception:
            pass

    def save(self):
        self._file.write_text(json.dumps({"output_dir": str(self.output_dir)}))


class AppState:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.port = 0
        self.files: Dict[str, FileEntry] = {}
        self.jobs = JobManager()
        self.settings = Settings()
        self.upload_dir = make_workdir("lc-uploads-")
        self.last_seen = time.time()
        self.shutdown_event = threading.Event()
        self.shell = None   # native window manager when running as a desktop app (see __main__.py)

    def cleanup(self):
        rmtree_quiet(self.upload_dir)


# --------------------------------------------------------------------------- native dialogs

def _pick_files_native() -> Optional[List[str]]:
    """Open the operating system's file picker. Returns None if unsupported, [] if cancelled."""
    if sys.platform == "darwin":
        script = ['-e', 'activate',
                  '-e', 'set fs to choose file with prompt "Choose files to convert" with multiple selections allowed',
                  '-e', 'set out to ""',
                  '-e', 'repeat with f in fs', '-e', 'set out to out & POSIX path of f & linefeed', '-e', 'end repeat',
                  '-e', 'return out']
        proc = run_quiet(["osascript"] + script)
        return [l for l in proc.stdout.decode().splitlines() if l.strip()] if proc.returncode == 0 else []
    if sys.platform == "win32":
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;Add-Type -AssemblyName System.Windows.Forms;"
              "$d=New-Object System.Windows.Forms.OpenFileDialog;$d.Multiselect=$true;$d.Title='Choose files to convert';"
              "if($d.ShowDialog() -eq 'OK'){$d.FileNames -join \"`n\"}")
        proc = run_quiet(["powershell", "-NoProfile", "-STA", "-Command", ps])
        return [l.strip() for l in proc.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
    if shutil.which("zenity"):
        proc = run_quiet(["zenity", "--file-selection", "--multiple", "--separator=\n"])
        return [l for l in proc.stdout.decode().splitlines() if l.strip()]
    return None


def _pick_folder_native() -> Optional[str]:
    if sys.platform == "darwin":
        proc = run_quiet(["osascript", "-e", "activate", "-e",
                          'POSIX path of (choose folder with prompt "Choose where converted files are saved")'])
        return proc.stdout.decode().strip() if proc.returncode == 0 else ""
    if sys.platform == "win32":
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;Add-Type -AssemblyName System.Windows.Forms;"
              "$d=New-Object System.Windows.Forms.FolderBrowserDialog;$d.Description='Choose where converted files are saved';"
              "if($d.ShowDialog() -eq 'OK'){$d.SelectedPath}")
        proc = run_quiet(["powershell", "-NoProfile", "-STA", "-Command", ps])
        return proc.stdout.decode("utf-8", "replace").strip()
    if shutil.which("zenity"):
        proc = run_quiet(["zenity", "--file-selection", "--directory"])
        return proc.stdout.decode().strip()
    return None


# --------------------------------------------------------------------------- app

def create_app(state: AppState) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = None
    dialog_lock = threading.Lock()

    @app.before_request
    def guard():
        host = (request.host or "").lower()
        if host not in (f"127.0.0.1:{state.port}", f"localhost:{state.port}"):
            abort(403)
        if request.path.startswith("/api/"):
            supplied = request.headers.get("X-Token") or request.args.get("token") or ""
            if not hmac.compare_digest(supplied, state.token):
                abort(403)
            state.last_seen = time.time()

    @app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # -- static UI
    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/widget")
    def widget():
        return send_from_directory(WEB_DIR, "widget.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(WEB_DIR, name)

    # -- info
    @app.get("/api/info")
    def info():
        return jsonify(version=__version__, platform=sys.platform, engines=engines.status(),
                       output_dir=str(state.settings.output_dir), categories=registry.CATEGORIES)

    @app.get("/api/formats")
    def formats():
        return jsonify(registry.format_table())

    @app.post("/api/engines/refresh")
    def refresh_engines():
        engines.refresh()
        return jsonify(engines=engines.status(), formats=registry.format_table())

    # -- files
    @app.post("/api/files")
    def upload():
        added = []
        for storage in request.files.getlist("files"):
            fid = uuid.uuid4().hex[:12]
            name = safe_filename(storage.filename or "file")
            folder = state.upload_dir / fid
            folder.mkdir()
            path = folder / name
            storage.save(str(path))
            entry = FileEntry(fid, path, name, temporary=True)
            state.files[fid] = entry
            added.append(entry.to_dict())
        return jsonify(added)

    @app.post("/api/pick-files")
    def pick_files():
        with dialog_lock:
            paths = _pick_files_native()
        if paths is None:
            return jsonify(supported=False, files=[])
        added = []
        for p in paths:
            path = Path(p)
            if path.is_file():
                fid = uuid.uuid4().hex[:12]
                entry = FileEntry(fid, path, path.name, temporary=False)
                state.files[fid] = entry
                added.append(entry.to_dict())
        return jsonify(supported=True, files=added)

    @app.delete("/api/files/<fid>")
    def remove_file(fid):
        entry = state.files.pop(fid, None)
        if entry and entry.temporary:
            rmtree_quiet(entry.path.parent)
        return jsonify(ok=True)

    # -- settings
    @app.post("/api/pick-folder")
    def pick_folder():
        with dialog_lock:
            folder = _pick_folder_native()
        if folder is None:
            return jsonify(supported=False)
        if folder:
            state.settings.output_dir = Path(folder)
            state.settings.save()
        return jsonify(supported=True, output_dir=str(state.settings.output_dir))

    @app.post("/api/settings")
    def settings():
        data = request.get_json(force=True) or {}
        if data.get("output_dir"):
            path = Path(data["output_dir"]).expanduser()
            if not path.is_absolute():
                return jsonify(error="Please enter a full folder path."), 400
            state.settings.output_dir = path
            state.settings.save()
        return jsonify(output_dir=str(state.settings.output_dir))

    @app.post("/api/open-output")
    def open_output():
        state.settings.output_dir.mkdir(parents=True, exist_ok=True)
        reveal_in_file_manager(state.settings.output_dir)
        return jsonify(ok=True)

    # -- conversion
    @app.post("/api/convert")
    def convert():
        data = request.get_json(force=True) or {}
        options = data.get("options") or {}
        out_root = state.settings.output_dir
        jobs = []
        if data.get("combine"):
            entries = [state.files[i] for i in data.get("file_ids", []) if i in state.files]
            if not entries:
                return jsonify(error="No files selected."), 400
            job = Job([(e.path, e.name) for e in entries], "pdf", options, out_root, combine=True)
            jobs.append(state.jobs.submit(job).to_dict())
        else:
            for item in data.get("items", []):
                entry = state.files.get(item.get("file_id"))
                target = str(item.get("target") or "").lower()
                if not entry or not target:
                    continue
                job = Job([(entry.path, entry.name)], target, options, out_root)
                d = state.jobs.submit(job).to_dict()
                d["file_id"] = entry.id
                jobs.append(d)
        return jsonify(jobs)

    @app.get("/api/jobs")
    def jobs():
        ids = [i for i in request.args.get("ids", "").split(",") if i]
        return jsonify({i: state.jobs.get(i).to_dict() for i in ids if state.jobs.get(i)})

    def _job_or_404(job_id) -> Job:
        job = state.jobs.get(job_id)
        if not job or job.status != "done" or not job.outputs:
            abort(404)
        return job

    @app.get("/api/download/<job_id>")
    def download(job_id):
        job = _job_or_404(job_id)
        existing = [p for p in job.outputs if p.exists()]
        if not existing:
            abort(410)
        if len(existing) == 1:
            return send_file(str(existing[0]), as_attachment=True, download_name=existing[0].name)
        zip_dir = state.upload_dir / "zips" / uuid.uuid4().hex[:12]
        zip_dir.mkdir(parents=True)
        zip_path = zip_dir / f"{existing[0].parent.name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for p in existing:
                z.write(p, p.name)
        return send_file(str(zip_path), as_attachment=True, download_name=zip_path.name)

    @app.post("/api/reveal/<job_id>")
    def reveal(job_id):
        job = _job_or_404(job_id)
        target = job.outputs[0] if len(job.outputs) == 1 else job.outputs[0].parent
        reveal_in_file_manager(target)
        return jsonify(ok=True)

    # -- native windows (main app <-> wheel widget)
    def _window_name() -> str:
        name = (request.get_json(force=True, silent=True) or {}).get("name")
        if name not in ("main", "widget"):
            abort(400)
        return name

    @app.post("/api/window/open")
    def window_open():
        name = _window_name()
        if state.shell is None:
            return jsonify(native=False)
        state.shell.open(name)
        return jsonify(native=True)

    @app.post("/api/window/close")
    def window_close():
        name = _window_name()
        if state.shell is None:
            return jsonify(native=False)
        state.shell.close(name)
        return jsonify(native=True)

    @app.post("/api/ping")
    def ping():
        return jsonify(ok=True, busy=state.jobs.busy())

    @app.post("/api/quit")
    def quit_app():
        state.shutdown_event.set()
        return jsonify(ok=True)

    return app


class LocalServer:
    """Runs the Flask app on 127.0.0.1 in a background thread."""

    def __init__(self, port: int = 0):
        from werkzeug.serving import WSGIRequestHandler, make_server

        class QuietHandler(WSGIRequestHandler):
            def log_request(self, *args, **kwargs):  # don't print URLs (they can contain the token)
                pass

        self.state = AppState()
        self.app = create_app(self.state)
        self._server = make_server("127.0.0.1", port, self.app, threaded=True, request_handler=QuietHandler)
        self.state.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return self.page_url("main")

    def page_url(self, page: str, native: bool = False) -> str:
        # The token travels in the URL fragment, which browsers never send over the network or log.
        path = "/widget" if page == "widget" else "/"
        query = "?shell=native" if native else ""
        return f"http://127.0.0.1:{self.state.port}{path}{query}#token={self.state.token}"

    def start(self) -> "LocalServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self.state.jobs.shutdown()
        self.state.cleanup()
