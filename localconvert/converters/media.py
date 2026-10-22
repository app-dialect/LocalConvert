"""Audio and video conversions with FFmpeg (bundled via imageio-ffmpeg)."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from ..core import engines
from ..core.registry import Options, register
from ..core.util import ConversionError, run_quiet, stem_of

AUDIO_IN = ["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "aiff", "wma", "amr"]
AUDIO_OUT = ["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "aiff"]
VIDEO_IN = ["mp4", "mov", "mkv", "avi", "webm", "m4v", "wmv", "flv", "mpg", "3gp"]
VIDEO_OUT = ["mp4", "mov", "mkv", "avi", "webm", "m4v", "gif"]

_H264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
         "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
VIDEO_ARGS = {
    "mp4": _H264 + ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],
    "m4v": _H264 + ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],
    "mov": _H264 + ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],
    "mkv": _H264 + ["-c:a", "aac", "-b:a", "160k"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-deadline", "good",
             "-cpu-used", "4", "-c:a", "libopus", "-b:a", "128k"],
    "avi": ["-c:v", "mpeg4", "-q:v", "4", "-c:a", "libmp3lame", "-q:a", "4"],
    "gif": ["-vf", "fps=12,scale='min(640,iw)':-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
            "-loop", "0", "-an"],
}
AUDIO_ARGS = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "wav": ["-c:a", "pcm_s16le"],
    "aiff": ["-c:a", "pcm_s16be"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "aac": ["-c:a", "aac", "-b:a", "192k"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    "opus": ["-c:a", "libopus", "-b:a", "128k"],
}

_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")


def _duration(ffmpeg: str, src: Path) -> Optional[float]:
    proc = run_quiet([ffmpeg, "-hide_banner", "-i", str(src)], timeout=60)
    m = _DURATION.search(proc.stderr.decode(errors="replace"))
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def run_ffmpeg(args: List[str], src: Path, out: Path, opts: Options) -> None:
    ffmpeg = engines.find_ffmpeg()
    if not ffmpeg:
        raise ConversionError("FFmpeg is not available.")
    total = _duration(ffmpeg, src)
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(src)] + args + \
          ["-progress", "pipe:1", "-nostats", str(out)]
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    # stderr is drained on a thread so a chatty FFmpeg can never block on a full pipe
    import threading
    err_chunks: List[bytes] = []
    t = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)
    t.start()
    for line in proc.stdout:
        if total and line.startswith(b"out_time_us="):
            try:
                opts.progress(min(int(line.split(b"=")[1]) / 1e6 / total, 0.99))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=5)
    if proc.returncode != 0 or not out.exists():
        lines = b"".join(err_chunks).decode(errors="replace").strip().splitlines()
        reason = next((l for l in reversed(lines) if l.strip()), "unknown error")
        if "does not contain any stream" in reason or "Output file #0 does not contain" in reason or \
                "matches no streams" in reason:
            reason = "the file has no audio/video track to convert"
        raise ConversionError(f"FFmpeg failed: {reason}")


def convert_media(src: Path, out_dir: Path, target: str, opts: Options) -> List[Path]:
    out = out_dir / f"{stem_of(src)}.{target}"
    if target in AUDIO_ARGS:
        args = ["-vn", "-map", "0:a:0"] + AUDIO_ARGS[target]
    else:
        args = VIDEO_ARGS[target]
        if target != "gif":
            args = ["-map", "0:v:0", "-map", "0:a:0?"] + args
    run_ffmpeg(args, src, out, opts)
    return [out]


register("Audio", AUDIO_IN, AUDIO_OUT, engine="ffmpeg")(convert_media)
register("Video", VIDEO_IN, VIDEO_OUT + AUDIO_OUT, engine="ffmpeg")(convert_media)
register("GIF to video", ["gif"], ["mp4", "webm", "mov"], engine="ffmpeg")(convert_media)
