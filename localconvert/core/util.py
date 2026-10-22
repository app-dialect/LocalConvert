"""Small helpers shared by the converters, the job runner and the server."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

# Extensions that are spelled several ways are normalised to one canonical name.
ALIASES = {
    "jpeg": "jpg",
    "jpe": "jpg",
    "jfif": "jpg",
    "tif": "tiff",
    "htm": "html",
    "xhtml": "html",
    "yml": "yaml",
    "markdown": "md",
    "text": "txt",
    "heif": "heic",
    "tgz": "tar.gz",
    "tbz2": "tar.bz2",
    "txz": "tar.xz",
    "aif": "aiff",
    "mpeg": "mpg",
}

_COMPOUND = ("tar.gz", "tar.bz2", "tar.xz")


def get_ext(path) -> str:
    """Return the canonical, lower-case extension of *path* without the dot."""
    name = Path(str(path)).name.lower()
    for compound in _COMPOUND:
        if name.endswith("." + compound):
            return compound
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return ALIASES.get(ext, ext)


def stem_of(path) -> str:
    """File name without its (possibly compound) extension."""
    name = Path(str(path)).name
    ext = get_ext(name)
    if ext in _COMPOUND:
        return name[: -(len(ext) + 1)]
    return Path(name).stem


_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def safe_filename(name: str, default: str = "file") -> str:
    """Strip path components and characters that are illegal on Windows or macOS."""
    name = Path(name.replace("\\", "/")).name
    name = _UNSAFE.sub("_", name).strip(" .")
    return name[:200] or default


def unique_path(path: Path) -> Path:
    """Return *path*, or 'name (2).ext' style variants if it already exists."""
    if not path.exists():
        return path
    ext = get_ext(path.name)
    stem = stem_of(path.name)
    suffix = "." + ext if ext else ""
    n = 2
    while True:
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def make_workdir(prefix: str = "lc-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def rmtree_quiet(path) -> None:
    shutil.rmtree(str(path), ignore_errors=True)


def app_data_dir() -> Path:
    """Per-user folder for settings and engine profiles (never synced anywhere)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "LocalConvert"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_output_dir() -> Path:
    downloads = Path.home() / "Downloads"
    base = downloads if downloads.is_dir() else Path.home()
    return base / "LocalConvert"


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def run_quiet(cmd: List[str], timeout: Optional[float] = None, **kw) -> subprocess.CompletedProcess:
    """Run a subprocess without flashing a console window on Windows."""
    if sys.platform == "win32":
        kw.setdefault("creationflags", 0x08000000)  # CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)


def reveal_in_file_manager(path: Path) -> None:
    path = Path(path)
    if sys.platform == "darwin":
        args = ["open", "-R", str(path)] if path.is_file() else ["open", str(path)]
    elif sys.platform == "win32":
        args = ["explorer", f"/select,{path}"] if path.is_file() else ["explorer", str(path)]
    else:
        args = ["xdg-open", str(path if path.is_dir() else path.parent)]
    subprocess.Popen(args)


class ConversionError(Exception):
    """An error whose message is safe and useful to show to the user."""
