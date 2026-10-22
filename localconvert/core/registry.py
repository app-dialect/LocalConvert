"""Central registry of every conversion the app knows how to do.

Converter modules call :func:`register` to declare which source extensions they
read, which targets they write and which engine they need.  The registry then
answers "what can I turn a .docx into?" and picks the best available converter
for each source/target pair.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional

from . import engines

CATEGORIES = {
    "image": ["png", "jpg", "webp", "heic", "avif", "gif", "bmp", "tiff", "ico", "icns",
              "psd", "tga", "ppm", "pgm", "pbm", "jp2"],
    "pdf": ["pdf"],
    "document": ["docx", "doc", "odt", "rtf", "pages", "txt", "md", "html", "epub"],
    "spreadsheet": ["xlsx", "xls", "xlsm", "ods", "numbers", "csv", "tsv"],
    "presentation": ["pptx", "ppt", "odp", "key"],
    "data": ["json", "yaml"],
    "audio": ["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "aiff", "wma", "amr"],
    "video": ["mp4", "mov", "mkv", "avi", "webm", "m4v", "wmv", "flv", "mpg", "3gp"],
    "archive": ["zip", "tar", "tar.gz", "tar.bz2", "tar.xz"],
    "subtitle": ["srt", "vtt"],
    "code": ["py", "js", "ts", "java", "c", "h", "cpp", "cs", "go", "rs", "rb", "php", "swift", "kt", "sh",
             "bat", "ps1", "sql", "css", "xml", "ini", "cfg", "conf", "toml", "log"],
}
_CATEGORY_OF = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}


def category_of(ext: str) -> str:
    return _CATEGORY_OF.get(ext, "other")


@dataclass
class Options:
    """User-tunable settings passed to every converter."""
    quality: int = 90             # JPEG/WebP/AVIF quality, 1-100
    dpi: int = 150                # resolution when rasterising PDFs/documents
    page_size: str = "A4"         # A4 | Letter | image  (image = page matches picture)
    keep_metadata: bool = False   # keep EXIF (camera, GPS...) when converting photos
    progress: Callable[[float], None] = field(default=lambda f: None, repr=False)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Options":
        d = d or {}
        o = cls()
        if "quality" in d:
            o.quality = max(1, min(100, int(d["quality"])))
        if "dpi" in d:
            o.dpi = max(36, min(600, int(d["dpi"])))
        if d.get("page_size") in ("A4", "Letter", "image"):
            o.page_size = d["page_size"]
        if "keep_metadata" in d:
            o.keep_metadata = bool(d["keep_metadata"])
        return o


# func(src_path, out_dir, target_ext, options) -> list of files written into out_dir
ConvertFunc = Callable[[Path, Path, str, Options], List[Path]]


@dataclass
class Converter:
    name: str
    sources: FrozenSet[str]
    targets: FrozenSet[str]
    func: ConvertFunc
    engine: str = "builtin"
    priority: int = 50
    basic_pairs: Callable[[str, str], bool] = field(default=lambda s, t: False, repr=False)
    note: str = ""

    def handles(self, src: str, dst: str) -> bool:
        return src in self.sources and dst in self.targets and src != dst

    @property
    def available(self) -> bool:
        return engines.available(self.engine)


_CONVERTERS: List[Converter] = []


def register(name: str, sources: Iterable[str], targets: Iterable[str], *, engine: str = "builtin",
             priority: int = 50, basic: Optional[Callable[[str, str], bool]] = None, note: str = ""):
    """Decorator that adds a conversion function to the registry.

    *basic* is an optional predicate ``(src, dst) -> bool`` marking pairs where
    the result keeps the content but not the original layout.
    """
    def deco(func: ConvertFunc) -> ConvertFunc:
        _CONVERTERS.append(Converter(
            name=name, sources=frozenset(sources), targets=frozenset(targets), func=func,
            engine=engine, priority=priority, basic_pairs=basic or (lambda s, t: False), note=note,
        ))
        return func
    return deco


def candidates(src: str, dst: str) -> List[Converter]:
    found = [c for c in _CONVERTERS if c.handles(src, dst)]
    return sorted(found, key=lambda c: (not c.available, -c.priority))


def best(src: str, dst: str) -> Optional[Converter]:
    for c in candidates(src, dst):
        if c.available:
            return c
    return None


def all_sources() -> List[str]:
    return sorted({s for c in _CONVERTERS for s in c.sources})


def targets_for(src: str) -> Dict[str, dict]:
    """Every target reachable from *src*, with the engine used and its quality."""
    result: Dict[str, dict] = {}
    targets = {t for c in _CONVERTERS if src in c.sources for t in c.targets if t != src}
    for dst in sorted(targets, key=lambda t: (category_of(t), t)):
        cands = candidates(src, dst)
        chosen = cands[0]
        result[dst] = {
            "category": category_of(dst),
            "engine": chosen.engine,
            "available": chosen.available,
            "basic": chosen.basic_pairs(src, dst),
            "note": chosen.note,
        }
    return result


def format_table() -> Dict[str, Dict[str, dict]]:
    return {src: targets_for(src) for src in all_sources()}
