"""Image <-> image and image -> PDF conversions using Pillow."""
from __future__ import annotations

import io
from pathlib import Path
from typing import List

from PIL import Image, ImageOps, ImageSequence, features

from ..core import engines
from ..core.registry import Options, register
from ..core.util import ConversionError, get_ext, stem_of

Image.MAX_IMAGE_PIXELS = 300_000_000  # allow big scans/panoramas, still guards against decompression bombs

PIL_FORMAT = {
    "png": "PNG", "jpg": "JPEG", "webp": "WEBP", "bmp": "BMP", "gif": "GIF",
    "tiff": "TIFF", "ico": "ICO", "heic": "HEIF", "avif": "AVIF",
}

READ = ["png", "jpg", "webp", "bmp", "gif", "tiff", "ico", "icns", "psd", "tga", "ppm", "pgm", "pbm"]
WRITE = ["png", "jpg", "webp", "bmp", "gif", "tiff", "ico"]
if features.check("avif"):
    READ.append("avif")
    WRITE.append("avif")
if features.check("jpg_2000"):
    READ.append("jp2")

ANIMATED_TARGETS = {"gif", "webp"}
MULTIPAGE_TARGETS = {"tiff"}
LOSSY_PHOTO_SOURCES = {"jpg", "heic", "avif", "webp"}


def open_image(path: Path) -> Image.Image:
    if get_ext(path) == "heic" and not engines.has_heif():
        raise ConversionError("HEIC support is not installed (pip install pillow-heif).")
    try:
        im = Image.open(path)
        im.load()
        return im
    except Image.DecompressionBombError:
        raise ConversionError("This image is too large to process safely.")
    except Exception:
        raise ConversionError("Could not read this image - it may be damaged or in an unsupported variant.")


def _has_alpha(im: Image.Image) -> bool:
    return im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)


def _flatten(im: Image.Image, bg=(255, 255, 255)) -> Image.Image:
    """Drop transparency by painting the image on a solid background."""
    if _has_alpha(im):
        rgba = im.convert("RGBA")
        base = Image.new("RGB", rgba.size, bg)
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    return im if im.mode == "RGB" else _to_8bit(im).convert("RGB")


def _to_8bit(im: Image.Image) -> Image.Image:
    if im.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        return im.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    return im


def _for_target(im: Image.Image, target: str) -> Image.Image:
    """Convert pixel mode to something the target format can store."""
    im = _to_8bit(im) if target not in ("png", "tiff") else im
    if target == "jpg":
        return im if im.mode == "L" else _flatten(im)
    if target in ("png", "tiff"):
        ok = {"1", "L", "LA", "P", "RGB", "RGBA", "I;16", "I"} | ({"CMYK"} if target == "tiff" else set())
        if im.mode in ok:
            return im
    if target == "gif" and im.mode in ("1", "L", "P", "RGB", "RGBA"):
        return im
    return im.convert("RGBA" if _has_alpha(im) else "RGB")


def _save_kwargs(im: Image.Image, target: str, opts: Options) -> dict:
    kw: dict = {}
    icc = im.info.get("icc_profile")
    if icc and target in ("jpg", "png", "webp", "tiff", "heic", "avif"):
        kw["icc_profile"] = icc
    if opts.keep_metadata and im.info.get("exif") and target in ("jpg", "png", "webp", "tiff", "heic", "avif"):
        kw["exif"] = im.info["exif"]
    if target == "jpg":
        kw.update(quality=opts.quality, optimize=True, progressive=True)
    elif target in ("webp", "avif", "heic"):
        kw["quality"] = opts.quality
    elif target == "tiff":
        kw["compression"] = "tiff_lzw"
    elif target == "ico":
        sizes = [s for s in (16, 24, 32, 48, 64, 128, 256) if s <= max(im.size)]
        kw["sizes"] = [(s, s) for s in sizes] or [(16, 16)]
    return kw


def _frames(im: Image.Image) -> List[Image.Image]:
    return [f.copy() for f in ImageSequence.Iterator(im)]


def _save(im: Image.Image, out: Path, target: str, opts: Options, src_info: dict) -> None:
    frame = _for_target(im, target)
    frame.info.update({k: v for k, v in src_info.items() if k in ("exif", "icc_profile")})
    if target == "ico" and max(frame.size) > 256:
        frame = frame.copy()
        frame.thumbnail((256, 256), Image.LANCZOS)
    frame.save(out, PIL_FORMAT[target], **_save_kwargs(frame, target, opts))


def convert_image(src: Path, out_dir: Path, target: str, opts: Options) -> List[Path]:
    im = open_image(src)
    info = dict(im.info)
    stem = stem_of(src)
    n_frames = getattr(im, "n_frames", 1)
    src_ext = get_ext(src)

    # Animated GIF/WebP -> animated GIF/WebP keeps every frame.
    if n_frames > 1 and src_ext in ("gif", "webp", "png") and target in ANIMATED_TARGETS:
        frames = [_for_target(f.convert("RGBA"), target) for f in ImageSequence.Iterator(im)]
        durations = []
        for f in ImageSequence.Iterator(im):
            durations.append(f.info.get("duration", info.get("duration", 100)))
        out = out_dir / f"{stem}.{target}"
        kw = {"quality": opts.quality} if target == "webp" else {}
        frames[0].save(out, PIL_FORMAT[target], save_all=True, append_images=frames[1:],
                       duration=durations, loop=info.get("loop", 0), disposal=2 if target == "gif" else 0, **kw)
        return [out]

    # Multi-page TIFF -> one file per page (or a multi-page TIFF stays multi-page).
    if n_frames > 1 and src_ext == "tiff":
        pages = _frames(im)
        if target in MULTIPAGE_TARGETS:
            out = out_dir / f"{stem}.{target}"
            pages = [_for_target(p, target) for p in pages]
            pages[0].save(out, PIL_FORMAT[target], save_all=True, append_images=pages[1:], compression="tiff_lzw")
            return [out]
        outs = []
        for i, page in enumerate(pages, 1):
            out = out_dir / f"{stem}-page-{i:03d}.{target}"
            _save(page, out, target, opts, info)
            outs.append(out)
        return outs

    if n_frames > 1:
        im.seek(0)
    frame = ImageOps.exif_transpose(im) or im
    out = out_dir / f"{stem}.{target}"
    _save(frame, out, target, opts, info)
    return [out]


register("Image", READ, WRITE)(convert_image)
# HEIC/HEIF (iPhone photos) needs pillow-heif, so it is registered as its own engine.
register("HEIC image", READ + ["heic"], WRITE + ["heic"], engine="heif", priority=40)(convert_image)


# --------------------------------------------------------------------------- image -> PDF

def images_to_pdf(sources: List[Path], out: Path, opts: Options) -> Path:
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(out))
    c.setTitle(stem_of(sources[0]) if len(sources) == 1 else "Images")
    c.setCreator("LocalConvert")
    for src in sources:
        im = open_image(src)
        photo = get_ext(src) in LOSSY_PHOTO_SOURCES
        for frame in (_frames(im) if getattr(im, "n_frames", 1) > 1 and get_ext(src) == "tiff" else [im]):
            frame = ImageOps.exif_transpose(frame) or frame
            rgb = _flatten(frame)
            buf = io.BytesIO()
            if photo:
                rgb.save(buf, "JPEG", quality=max(opts.quality, 85))
            else:
                rgb.save(buf, "PNG")
            buf.seek(0)
            w, h = rgb.size
            if opts.page_size == "image":
                dpi = frame.info.get("dpi", (96, 96))[0] or 96
                pw, ph = w * 72.0 / dpi, h * 72.0 / dpi
                margin = 0.0
            else:
                pw, ph = A4 if opts.page_size == "A4" else letter
                if w > h:
                    pw, ph = ph, pw
                margin = 36.0
            scale = min((pw - 2 * margin) / w, (ph - 2 * margin) / h)
            if opts.page_size != "image":
                scale = min(scale, 1.0)
            dw, dh = w * scale, h * scale
            c.setPageSize((pw, ph))
            c.drawImage(ImageReader(buf), (pw - dw) / 2, (ph - dh) / 2, dw, dh)
            c.showPage()
    c.save()
    return out


def convert_image_to_pdf(src: Path, out_dir: Path, target: str, opts: Options) -> List[Path]:
    return [images_to_pdf([src], out_dir / f"{stem_of(src)}.pdf", opts)]


register("Image to PDF", READ, ["pdf"])(convert_image_to_pdf)
register("HEIC to PDF", ["heic"], ["pdf"], engine="heif")(convert_image_to_pdf)
