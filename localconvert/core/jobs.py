"""Running conversions: one-shot helpers for the CLI and a background job queue for the GUI."""
from __future__ import annotations

import dataclasses
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .registry import Options, best
from .util import ConversionError, get_ext, make_workdir, rmtree_quiet, stem_of, unique_path


def convert_one(src: Path, target: str, out_root: Path, opts: Options) -> List[Path]:
    """Convert *src* to *target* and place the result(s) in *out_root*."""
    src = Path(src)
    ext = get_ext(src)
    conv = best(ext, target)
    if conv is None:
        raise ConversionError(f"Converting .{ext} to .{target} isn't supported (or needs an engine that isn't installed).")
    out_root.mkdir(parents=True, exist_ok=True)
    work = make_workdir()
    try:
        outputs = [p for p in conv.func(src, work, target, opts) if p.exists()]
        if not outputs:
            raise ConversionError("The conversion produced no output.")
        if len(outputs) == 1:
            final = unique_path(out_root / outputs[0].name)
            shutil.move(str(outputs[0]), str(final))
            return [final]
        folder = unique_path(out_root / f"{stem_of(src)} ({target})")
        folder.mkdir()
        return [Path(shutil.move(str(p), str(folder / p.name))) for p in sorted(outputs)]
    finally:
        rmtree_quiet(work)


def combine_to_pdf(sources: List[Path], out_root: Path, opts: Options, name: str = "Combined") -> Path:
    """Convert every source to PDF (if needed) and merge them, in order, into one file."""
    from ..converters.images import READ as IMAGE_EXTS, images_to_pdf
    from ..converters.pdf import merge_pdfs

    out_root.mkdir(parents=True, exist_ok=True)
    work = make_workdir()
    try:
        pdfs: List[Path] = []
        n = len(sources)
        for i, src in enumerate(sources):
            ext = get_ext(src)
            if ext == "pdf":
                pdfs.append(Path(src))
            elif ext in IMAGE_EXTS or ext == "heic":
                pdfs.append(images_to_pdf([Path(src)], work / f"{i:04d}.pdf", opts))
            else:
                conv = best(ext, "pdf")
                if conv is None:
                    raise ConversionError(f"{Path(src).name} can't be converted to PDF.")
                sub = work / f"{i:04d}"
                sub.mkdir()
                pdfs.extend(conv.func(Path(src), sub, "pdf", dataclasses.replace(opts, progress=lambda f: None)))
            opts.progress((i + 1) / (n + 1))
        final = unique_path(out_root / f"{name}.pdf")
        merge_pdfs(pdfs, final)
        return final
    finally:
        rmtree_quiet(work)


@dataclass
class Job:
    inputs: List[Tuple[Path, str]]      # (path on disk, name shown to the user)
    target: str
    options: dict
    out_root: Path
    combine: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "queued"              # queued | running | done | error
    progress: float = 0.0
    message: str = ""
    outputs: List[Path] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "status": self.status, "progress": round(self.progress, 3), "message": self.message,
            "target": self.target, "combine": self.combine, "inputs": [name for _, name in self.inputs],
            "outputs": [{"name": p.name, "path": str(p)} for p in self.outputs],
        }


class JobManager:
    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="convert")
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, job: Job) -> Job:
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def busy(self) -> bool:
        return any(j.status in ("queued", "running") for j in self._jobs.values())

    def _run(self, job: Job) -> None:
        job.status = "running"
        opts = Options.from_dict(job.options)
        opts.progress = lambda f: setattr(job, "progress", max(job.progress, float(f)))
        try:
            if job.combine:
                name = stem_of(job.inputs[0][1]) + " (combined)"
                job.outputs = [combine_to_pdf([p for p, _ in job.inputs], job.out_root, opts, name)]
            else:
                job.outputs = convert_one(job.inputs[0][0], job.target, job.out_root, opts)
            job.progress = 1.0
            job.status = "done"
        except ConversionError as e:
            job.status, job.message = "error", str(e)
        except Exception as e:  # unexpected - keep the app alive and report it
            traceback.print_exc()
            job.status, job.message = "error", f"Unexpected error: {e}"

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
