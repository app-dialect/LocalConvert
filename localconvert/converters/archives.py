"""Archive repacking (zip <-> tar, tar.gz, tar.bz2, tar.xz) without extracting to disk."""
from __future__ import annotations

import io
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Iterator, List, Tuple

from ..core.registry import Options, register
from ..core.util import ConversionError, get_ext, stem_of

FORMATS = ["zip", "tar", "tar.gz", "tar.bz2", "tar.xz"]
_TAR_MODE = {"tar": "", "tar.gz": "gz", "tar.bz2": "bz2", "tar.xz": "xz"}


def _members(src: Path) -> Iterator[Tuple[str, bytes, float]]:
    """Yield (name, data, mtime) for each regular file; unsafe names are rejected."""
    def safe(name: str) -> str:
        name = name.replace("\\", "/").lstrip("/")
        if any(part == ".." for part in name.split("/")):
            raise ConversionError(f"Archive contains an unsafe path: {name}")
        return name

    ext = get_ext(src)
    try:
        if ext == "zip":
            with zipfile.ZipFile(str(src)) as z:
                for info in z.infolist():
                    if not info.is_dir():
                        yield safe(info.filename), z.read(info), time.mktime(info.date_time + (0, 0, -1))
        else:
            with tarfile.open(str(src), "r:" + _TAR_MODE[ext]) as t:
                for m in t.getmembers():
                    if m.isfile():
                        yield safe(m.name), t.extractfile(m).read(), m.mtime
    except (zipfile.BadZipFile, tarfile.TarError) as e:
        raise ConversionError(f"Could not read this archive ({e}).")
    except RuntimeError as e:  # encrypted zip
        raise ConversionError(f"Could not read this archive ({e}).")


def convert_archive(src: Path, out_dir: Path, target: str, opts: Options) -> List[Path]:
    out = out_dir / f"{stem_of(src)}.{target}"
    if target == "zip":
        with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as z:
            for name, data, mtime in _members(src):
                info = zipfile.ZipInfo(name, time.localtime(max(mtime, 315532800))[:6])
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data)
    else:
        with tarfile.open(str(out), "w:" + _TAR_MODE[target]) as t:
            for name, data, mtime in _members(src):
                info = tarfile.TarInfo(name)
                info.size, info.mtime = len(data), int(mtime)
                t.addfile(info, io.BytesIO(data))
    return [out]


register("Archive", FORMATS, FORMATS)(convert_archive)
