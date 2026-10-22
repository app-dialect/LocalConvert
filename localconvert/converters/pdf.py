"""PDF -> images, and PDF helpers (rendering, merging) used elsewhere."""
from __future__ import annotations

from pathlib import Path
from typing import List

from ..core.registry import Options, register
from ..core.util import ConversionError, stem_of

RASTER_TARGETS = ["png", "jpg", "webp", "tiff", "bmp"]


def open_pdf(path: Path):
    import pypdfium2 as pdfium
    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as e:
        if "password" in str(e).lower():
            raise ConversionError("This PDF is password-protected. Remove the password first.")
        raise ConversionError(f"Could not read this PDF ({e}).")


def render_pages(path: Path, dpi: int, progress=None):
    """Yield each page of the PDF as a PIL image."""
    pdf = open_pdf(path)
    try:
        n = len(pdf)
        for i in range(n):
            page = pdf[i]
            yield page.render(scale=dpi / 72.0, may_draw_forms=True).to_pil()
            page.close()
            if progress:
                progress((i + 1) / n)
    finally:
        pdf.close()


def pdf_to_images(src: Path, out_dir: Path, target: str, opts: Options) -> List[Path]:
    from .images import PIL_FORMAT, _flatten

    stem = stem_of(src)
    pages = list(render_pages(src, opts.dpi, opts.progress))
    if not pages:
        raise ConversionError("This PDF has no pages.")
    fmt = PIL_FORMAT[target]
    kw = {"quality": opts.quality} if target in ("jpg", "webp") else {}
    if target == "tiff":  # one multi-page TIFF is more useful than many files
        out = out_dir / f"{stem}.tiff"
        pages = [_flatten(p) for p in pages]
        pages[0].save(out, fmt, save_all=True, append_images=pages[1:], compression="tiff_lzw",
                      dpi=(opts.dpi, opts.dpi))
        return [out]
    outs = []
    for i, page in enumerate(pages, 1):
        name = f"{stem}.{target}" if len(pages) == 1 else f"{stem}-page-{i:03d}.{target}"
        out = out_dir / name
        img = _flatten(page) if target in ("jpg", "bmp") else page
        img.save(out, fmt, dpi=(opts.dpi, opts.dpi), **kw)
        outs.append(out)
    return outs


register("PDF to image", ["pdf"], RASTER_TARGETS, priority=80)(pdf_to_images)


def merge_pdfs(sources: List[Path], out: Path) -> Path:
    from pypdf import PdfWriter
    writer = PdfWriter()
    for src in sources:
        try:
            writer.append(str(src))
        except Exception as e:
            raise ConversionError(f"Could not merge {Path(src).name} ({e}).")
    with open(out, "wb") as f:
        writer.write(f)
    return out
