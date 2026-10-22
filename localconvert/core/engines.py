"""Detection of the conversion engines installed on this computer.

* builtin      - pure-Python libraries shipped with the app (always present)
* ffmpeg       - audio/video; bundled through imageio-ffmpeg, or found on PATH
* libreoffice  - optional, gives high-fidelity Office <-> PDF conversions
* heif         - HEIC/HEIF support through pillow-heif
"""
from __future__ import annotations

import functools
import glob
import os
import shutil
import sys
from typing import Dict, Optional


@functools.lru_cache(maxsize=None)
def find_ffmpeg() -> Optional[str]:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


@functools.lru_cache(maxsize=None)
def find_soffice() -> Optional[str]:
    env = os.environ.get("LOCALCONVERT_SOFFICE")
    if env and os.path.exists(env):
        return env
    candidates = []
    if sys.platform == "darwin":
        candidates += ["/Applications/LibreOffice.app/Contents/MacOS/soffice",
                       os.path.expanduser("~/Applications/LibreOffice.app/Contents/MacOS/soffice")]
    elif sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            candidates.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
            candidates += glob.glob(os.path.join(base, "LibreOffice*", "program", "soffice.exe"))
    for c in candidates:
        if os.path.exists(c):
            return c
    return shutil.which("soffice") or shutil.which("libreoffice")


@functools.lru_cache(maxsize=None)
def has_heif() -> bool:
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        return True
    except Exception:
        return False


_CHECKS = {
    "builtin": lambda: True,
    "ffmpeg": lambda: find_ffmpeg() is not None,
    "libreoffice": lambda: find_soffice() is not None,
    "heif": has_heif,
}

DESCRIPTIONS = {
    "builtin": "Built-in converters (images, PDF, documents, spreadsheets, data, archives)",
    "ffmpeg": "FFmpeg - audio and video",
    "libreoffice": "LibreOffice - high-fidelity Word/Excel/PowerPoint conversions (optional, free)",
    "heif": "HEIC/HEIF support - iPhone photos",
}


def available(engine: str) -> bool:
    check = _CHECKS.get(engine)
    return bool(check and check())


def status() -> Dict[str, dict]:
    paths = {"ffmpeg": find_ffmpeg(), "libreoffice": find_soffice()}
    return {name: {"available": available(name), "description": DESCRIPTIONS[name], "path": paths.get(name)}
            for name in _CHECKS}


def refresh() -> None:
    """Forget cached detection results (e.g. after the user installs LibreOffice)."""
    find_ffmpeg.cache_clear()
    find_soffice.cache_clear()
