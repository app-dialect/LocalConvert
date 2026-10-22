"""Subtitle conversions (SRT <-> WebVTT)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

from ..core.registry import Options, register
from ..core.util import get_ext, stem_of
from .documents import read_text_file


def convert_subtitles(src: Path, out_dir: Path, target: str, opts: Options) -> List[Path]:
    text = read_text_file(src).replace("\r\n", "\n").strip()
    if get_ext(src) == "srt" and target == "vtt":
        body = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)
        result = "WEBVTT\n\n" + body + "\n"
    else:
        text = re.sub(r"^WEBVTT[^\n]*\n+", "", text)
        cues = [c for c in re.split(r"\n\s*\n", text) if "-->" in c]
        out = []
        for i, cue in enumerate(cues, 1):
            lines = cue.split("\n")
            while lines and "-->" not in lines[0]:
                lines.pop(0)
            timing = re.sub(r"(\d{2}:\d{2}:\d{2})\.(\d{3})", r"\1,\2", lines[0])
            timing = re.sub(r"(?<![\d:])(\d{2}:\d{2})[.,](\d{3})", r"00:\1,\2", timing)
            timing = re.sub(r"(,\d{3})\s+\S+:\S+.*$", r"\1", timing)  # drop VTT cue settings
            out.append(f"{i}\n{timing}\n" + "\n".join(lines[1:]))
        result = "\n\n".join(out) + "\n"
    out_path = out_dir / f"{stem_of(src)}.{target}"
    out_path.write_text(result, encoding="utf-8")
    return [out_path]


register("Subtitles", ["srt", "vtt"], ["srt", "vtt"])(convert_subtitles)
