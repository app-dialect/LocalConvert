"""High-fidelity Office conversions through LibreOffice (optional, free, runs offline).

LibreOffice is started headless with its own private profile so it never
interferes with a copy the user has open, and calls are serialised because
one profile can only be used by one process at a time.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import List

from ..core import engines
from ..core.registry import Options, register
from ..core.util import ConversionError, app_data_dir, run_quiet, stem_of

_LOCK = threading.Lock()

WRITER_SOURCES = ["doc", "docx", "odt", "rtf", "pages"]
WRITER_TARGETS = ["pdf", "docx", "doc", "odt", "rtf", "epub"]
CALC_SOURCES = ["xls", "xlsx", "xlsm", "ods", "numbers"]
CALC_TARGETS = ["pdf", "xlsx", "xls", "ods"]
IMPRESS_SOURCES = ["ppt", "pptx", "odp", "key"]
IMPRESS_TARGETS = ["pdf", "pptx", "ppt", "odp"]

# LibreOffice filter names where the bare extension is ambiguous.
FILTERS = {"txt": "txt:Text (encoded):UTF8"}


def libreoffice_convert(src: Path, out_dir: Path, target: str, opts: Options) -> List[Path]:
    soffice = engines.find_soffice()
    if not soffice:
        raise ConversionError("LibreOffice is not installed.")
    profile = (app_data_dir() / "libreoffice-profile").as_uri()
    cmd = [soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--nolockcheck",
           "--nodefault", "--nofirststartwizard", "--convert-to", FILTERS.get(target, target),
           "--outdir", str(out_dir), str(src)]
    with _LOCK:
        try:
            proc = run_quiet(cmd, timeout=600)
        except subprocess.TimeoutExpired:
            raise ConversionError("LibreOffice took too long (over 10 minutes) and was stopped.")
    out = out_dir / f"{stem_of(src)}.{target}"
    if not out.exists():
        detail = (proc.stderr or proc.stdout or b"").decode(errors="replace").strip().splitlines()
        raise ConversionError("LibreOffice could not convert this file" + (f": {detail[-1]}" if detail else "."))
    return [out]


_NOTE = "Uses LibreOffice for an exact copy of the original layout."
register("LibreOffice Writer", WRITER_SOURCES, WRITER_TARGETS, engine="libreoffice", priority=80, note=_NOTE)(libreoffice_convert)
register("LibreOffice Calc", CALC_SOURCES, CALC_TARGETS, engine="libreoffice", priority=80, note=_NOTE)(libreoffice_convert)
register("LibreOffice Impress", IMPRESS_SOURCES, IMPRESS_TARGETS, engine="libreoffice", priority=80, note=_NOTE)(libreoffice_convert)
# Builtin text conversions are good enough for these, so LibreOffice only wins if nothing else exists.
register("LibreOffice text", ["txt", "html"], ["odt", "doc", "rtf"],
         engine="libreoffice", priority=30, note=_NOTE)(libreoffice_convert)
register("LibreOffice CSV", ["csv"], ["ods", "xls"], engine="libreoffice", priority=30, note=_NOTE)(libreoffice_convert)
