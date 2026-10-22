"""Document conversions (txt, md, html, docx, pdf, pptx, epub -> pdf, docx, html, md, txt, images).

Everything goes through :mod:`docmodel`. These conversions keep text,
headings, lists, tables and images, but not exact page layout; for
pixel-perfect Office -> PDF the LibreOffice engine (office.py) is preferred
automatically when it is installed.
"""
from __future__ import annotations

import base64
import html as htmllib
import io
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional
from xml.etree import ElementTree as ET

from ..core.registry import Options, register
from ..core.util import ConversionError, get_ext, stem_of
from .docmodel import Block, Document, ListCounter, Run, document_to_text, html_to_document

# --------------------------------------------------------------------------- readers


def read_text_file(path: Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_txt(path: Path) -> Document:
    text = read_text_file(path).replace("\r\n", "\n").replace("\r", "\n")
    doc = Document(title=stem_of(path))
    for chunk in re.split(r"\n\s*\n", text):
        if chunk.strip():
            doc.add(Block("para", runs=[Run(chunk.strip("\n"))]))
    return doc


def _file_image_resolver(base: Path) -> Callable[[str], Optional[bytes]]:
    def resolve(src: str) -> Optional[bytes]:
        src = src.split("#")[0].split("?")[0]
        if src.startswith("file://"):
            src = src[7:]
        p = (base / src) if not Path(src).is_absolute() else Path(src)
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff") and p.is_file():
            return p.read_bytes()
        return None
    return resolve


def read_md(path: Path) -> Document:
    import markdown
    html = markdown.markdown(read_text_file(path), extensions=["tables", "fenced_code", "sane_lists"])
    doc = html_to_document(html, _file_image_resolver(Path(path).parent))
    doc.title = doc.title or stem_of(path)
    return doc


def read_html(path: Path) -> Document:
    doc = html_to_document(read_text_file(path), _file_image_resolver(Path(path).parent))
    doc.title = doc.title or stem_of(path)
    return doc


def read_docx(path: Path) -> Document:
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        d = docx.Document(str(path))
    except Exception as e:
        raise ConversionError("This doesn't look like a valid Word (.docx) file - it may be damaged or a different format.")
    doc = Document(title=d.core_properties.title or stem_of(path))
    rels = d.part.related_parts

    def runs_of(p: Paragraph) -> List[Run]:
        out: List[Run] = []
        items = p.iter_inner_content() if hasattr(p, "iter_inner_content") else p.runs
        for item in items:
            link = getattr(item, "address", None)
            for r in (item.runs if hasattr(item, "runs") else [item]):
                for br in r._r.findall(qn("w:br")):
                    if br.get(qn("w:type")) == "page":
                        pending_breaks.append(True)
                if r.text:
                    out.append(Run(r.text.replace("\t", "    "), bool(r.bold), bool(r.italic), False, link or None))
                for blip in r._r.iter(qn("a:blip")):
                    rid = blip.get(qn("r:embed"))
                    if rid in rels:
                        pending_images.append(rels[rid].blob)
        return out

    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            pending_images: List[bytes] = []
            pending_breaks: List[bool] = []
            p = Paragraph(child, d)
            style = (p.style.name if p.style is not None else "") or ""
            runs = runs_of(p)
            num_pr = child.find(f"{qn('w:pPr')}/{qn('w:numPr')}")
            if style == "Title":
                doc.add(Block("heading", runs=runs, level=1))
            elif style.startswith("Heading") and style[-1:].isdigit():
                doc.add(Block("heading", runs=runs, level=min(6, int(style.split()[-1]))))
            elif "List" in style or num_pr is not None:
                level = 0
                if num_pr is not None:
                    ilvl = num_pr.find(qn("w:ilvl"))
                    level = int(ilvl.get(qn("w:val"))) if ilvl is not None else 0
                elif style[-1:].isdigit():
                    level = int(style[-1]) - 1
                prev = doc.blocks[-1] if doc.blocks else None
                ordered = "Number" in style
                doc.add(Block("li", runs=runs, level=level, ordered=ordered,
                              start=not (prev and prev.kind == "li")))
            else:
                doc.add(Block("para", runs=runs))
            for img in pending_images:
                doc.add(Block("image", image=img))
            if pending_breaks:
                doc.add(Block("pagebreak"))
        elif tag == "tbl":
            t = Table(child, d)
            rows = []
            for row in t.rows:
                cells, last = [], None
                for cell in row.cells:      # merged cells repeat; keep them once
                    if cell._tc is last:
                        continue
                    last = cell._tc
                    cells.append(cell.text.strip())
                rows.append(cells)
            if rows:
                doc.add(Block("table", rows=rows))
    return doc


def read_pdf_text(path: Path) -> Document:
    from .pdf import open_pdf
    pdf = open_pdf(path)
    doc = Document(title=stem_of(path))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            text = page.get_textpage().get_text_range().replace("\r\n", "\n").replace("\r", "\n")
            if i:
                doc.add(Block("pagebreak"))
            for chunk in re.split(r"\n\s*\n", text):
                if chunk.strip():
                    doc.add(Block("para", runs=[Run(chunk.strip())]))
    finally:
        pdf.close()
    if not any(b.kind == "para" for b in doc.blocks):
        raise ConversionError("No text found in this PDF - it is probably a scanned image (OCR comes in a later version).")
    return doc


def read_pptx(path: Path) -> Document:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

    try:
        prs = Presentation(str(path))
    except Exception as e:
        raise ConversionError("This doesn't look like a valid PowerPoint (.pptx) file - it may be damaged or a different format.")
    doc = Document(title=stem_of(path), landscape=True)

    def walk(shapes):
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from walk(shape.shapes)
            else:
                yield shape

    for n, slide in enumerate(prs.slides, 1):
        if n > 1:
            doc.add(Block("pagebreak"))
        title_shape = slide.shapes.title
        title = title_shape.text_frame.text.strip() if title_shape is not None and title_shape.has_text_frame else ""
        doc.add(Block("heading", runs=[Run(f"Slide {n}" + (f": {title}" if title else ""))], level=2))
        for shape in walk(slide.shapes):
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                continue
            if shape.has_text_frame:
                is_body = shape.is_placeholder and shape.placeholder_format.type in (PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT)
                for para in shape.text_frame.paragraphs:
                    runs = [Run(r.text, bool(r.font.bold), bool(r.font.italic)) for r in para.runs if r.text]
                    if runs:
                        prev = doc.blocks[-1] if doc.blocks else None
                        doc.add(Block("li" if is_body else "para", runs=runs, level=para.level,
                                      start=not (prev and prev.kind == "li")))
            elif getattr(shape, "has_table", False) and shape.has_table:
                rows = [[c.text.strip() for c in row.cells] for row in shape.table.rows]
                doc.add(Block("table", rows=rows))
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    doc.add(Block("image", image=shape.image.blob))
                except Exception:
                    pass
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                doc.add(Block("para", runs=[Run("Notes: ", bold=True), Run(notes, italic=True)]))
    return doc


def read_epub(path: Path) -> Document:
    try:
        z = zipfile.ZipFile(str(path))
        container = ET.fromstring(z.read("META-INF/container.xml"))
        ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container", "opf": "http://www.idpf.org/2007/opf",
              "dc": "http://purl.org/dc/elements/1.1/"}
        opf_path = container.find(".//c:rootfile", ns).get("full-path")
        opf = ET.fromstring(z.read(opf_path))
    except Exception as e:
        raise ConversionError(f"Could not read this EPUB ({e}).")
    base = posixpath.dirname(opf_path)
    manifest = {i.get("id"): i.get("href") for i in opf.findall(".//opf:manifest/opf:item", ns)}
    spine = [manifest.get(r.get("idref")) for r in opf.findall(".//opf:spine/opf:itemref", ns)]
    title_el = opf.find(".//dc:title", ns)
    doc = Document(title=(title_el.text or "").strip() if title_el is not None else stem_of(path))
    names = set(z.namelist())
    for i, href in enumerate(h for h in spine if h):
        item = posixpath.normpath(posixpath.join(base, href))
        if item not in names:
            continue
        chapter_dir = posixpath.dirname(item)

        def resolve(src: str, chapter_dir=chapter_dir) -> Optional[bytes]:
            name = posixpath.normpath(posixpath.join(chapter_dir, src.split("#")[0]))
            return z.read(name) if name in names else None

        chapter = html_to_document(z.read(item).decode("utf-8", errors="replace"), resolve)
        if i and chapter.blocks:
            doc.add(Block("pagebreak"))
        doc.blocks.extend(chapter.blocks)
    return doc


def read_image(path: Path) -> Document:
    from .images import open_image
    im = open_image(path)
    buf = io.BytesIO()
    im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(buf, "PNG")
    return Document(title=stem_of(path), blocks=[Block("image", image=buf.getvalue())])


CODE_EXTS = ["py", "js", "ts", "java", "c", "h", "cpp", "cs", "go", "rs", "rb", "php", "swift", "kt", "sh",
             "bat", "ps1", "sql", "css", "xml", "ini", "cfg", "conf", "toml", "log"]


def read_code(path: Path) -> Document:
    """Source code, logs and config files: kept verbatim in a monospaced block."""
    text = read_text_file(path).replace("\r\n", "\n").expandtabs(4)
    return Document(title=Path(path).name, blocks=[
        Block("heading", runs=[Run(Path(path).name)], level=3), Block("code", text=text.rstrip("\n"))])


READERS: Dict[str, Callable[[Path], Document]] = {
    "txt": read_txt, "md": read_md, "html": read_html, "docx": read_docx,
    "pdf": read_pdf_text, "pptx": read_pptx, "epub": read_epub,
}

# --------------------------------------------------------------------------- shared helpers


def _image_for_embedding(data: bytes, allowed=("PNG", "JPEG", "GIF", "BMP")):
    """Return (bytes, (w, h)) re-encoded to PNG/JPEG if the format isn't widely supported."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        size = im.size
        if im.format in allowed:
            return data, size, im.format
        buf = io.BytesIO()
        im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(buf, "PNG")
        return buf.getvalue(), size, "PNG"
    except Exception:
        return None, None, None


# --------------------------------------------------------------------------- PDF writer

_FONT_CACHE: Dict[str, Optional[dict]] = {}

_FONT_CANDIDATES = {
    "darwin": [
        ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial Italic.ttf", "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"),
    ],
    "win32": [("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf",
               "C:/Windows/Fonts/ariali.ttf", "C:/Windows/Fonts/arialbi.ttf")],
    "linux": [("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf")],
}
_WIDE_FONTS = {  # broad Unicode coverage (CJK, Indic, ...) used when the text needs it
    "darwin": ["/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"],
    "win32": ["C:/Windows/Fonts/ARIALUNI.TTF", "C:/Windows/Fonts/seguisym.ttf"],
    "linux": ["/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"],
}
_MONO = {
    "darwin": [("/System/Library/Fonts/Menlo.ttc", 0), ("/System/Library/Fonts/Monaco.ttf", None)],
    "win32": [("C:/Windows/Fonts/consola.ttf", None), ("C:/Windows/Fonts/cour.ttf", None)],
    "linux": [("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", None)],
}


def _platform_key() -> str:
    return "darwin" if sys.platform == "darwin" else "win32" if sys.platform == "win32" else "linux"


def _fonts(text_sample: str) -> dict:
    """Register system TrueType fonts so non-English text renders (Helvetica is Latin-1 only)."""
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    key = _platform_key()
    if "main" not in _FONT_CACHE:
        fonts = {"normal": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique",
                 "bolditalic": "Helvetica-BoldOblique", "family": "Helvetica", "mono": "Courier", "face": None}
        for paths in _FONT_CANDIDATES.get(key, []):
            if all(Path(p).exists() for p in paths):
                try:
                    names = ["LC-Sans", "LC-Sans-Bold", "LC-Sans-Italic", "LC-Sans-BoldItalic"]
                    for name, p in zip(names, paths):
                        pdfmetrics.registerFont(TTFont(name, p))
                    pdfmetrics.registerFontFamily("LC-Sans", normal=names[0], bold=names[1], italic=names[2], boldItalic=names[3])
                    fonts.update(normal=names[0], bold=names[1], italic=names[2], bolditalic=names[3], family="LC-Sans",
                                 face=pdfmetrics.getFont(names[0]).face)
                    break
                except Exception:
                    pass
        for p, idx in _MONO.get(key, []):
            if Path(p).exists():
                try:
                    pdfmetrics.registerFont(TTFont("LC-Mono", p, subfontIndex=idx or 0))
                    fonts["mono"] = "LC-Mono"
                    break
                except Exception:
                    pass
        _FONT_CACHE["main"] = fonts
        _FONT_CACHE["wide"] = None
        for p in _WIDE_FONTS.get(key, []):
            if Path(p).exists():
                try:
                    pdfmetrics.registerFont(TTFont("LC-Wide", p))
                    addMapping("LC-Wide", 1, 0, "LC-Wide")
                    addMapping("LC-Wide", 0, 1, "LC-Wide")
                    addMapping("LC-Wide", 1, 1, "LC-Wide")
                    _FONT_CACHE["wide"] = {"normal": "LC-Wide", "bold": "LC-Wide", "italic": "LC-Wide",
                                           "bolditalic": "LC-Wide", "family": "LC-Wide",
                                           "mono": "LC-Wide", "face": pdfmetrics.getFont("LC-Wide").face}
                    break
                except Exception:
                    pass
    fonts = _FONT_CACHE["main"]
    wide = _FONT_CACHE["wide"]
    if wide and _needs_wide(text_sample, fonts.get("face")):
        return wide
    return fonts


def _needs_wide(text: str, face) -> bool:
    chars = {c for c in text if ord(c) > 0x24F}
    if not chars:
        return False
    if face is None:
        return True
    cmap = getattr(face, "charToGlyph", {}) or {}
    return any(ord(c) not in cmap for c in chars)


def _markup(runs: List[Run], fonts: dict) -> str:
    parts = []
    for r in runs:
        t = htmllib.escape(r.text, quote=False).replace("\n", "<br/>")
        if r.code:
            t = f'<font face="{fonts["mono"]}">{t}</font>'
        if r.bold:
            t = f"<b>{t}</b>"
        if r.italic:
            t = f"<i>{t}</i>"
        if r.link and re.match(r"^(https?:|mailto:)", r.link):
            t = f'<link href="{htmllib.escape(r.link)}" color="#1a5fb4"><u>{t}</u></link>'
        parts.append(t)
    return "".join(parts)


def write_pdf(doc: Document, out: Path, opts: Options) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape, letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (HRFlowable, Image as RLImage, PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    sample = doc.title + "".join(b.plain() + b.text + "".join("".join(r) for r in (b.rows or [])) for b in doc.blocks)
    fonts = _fonts(sample)
    pagesize = letter if opts.page_size == "Letter" else A4
    if doc.landscape:
        pagesize = landscape(pagesize)
    margin = 18 * mm
    frame_w = pagesize[0] - 2 * margin
    frame_h = pagesize[1] - 2 * margin

    base = getSampleStyleSheet()["BodyText"]
    body = ParagraphStyle("LCBody", parent=base, fontName=fonts["normal"], fontSize=10.5, leading=14.5,
                          spaceAfter=6, alignment=TA_LEFT)
    heads = {lvl: ParagraphStyle(f"LCH{lvl}", parent=body, fontName=fonts["bold"], fontSize=size,
                                 leading=size * 1.25, spaceBefore=10 if lvl > 1 else 4, spaceAfter=6)
             for lvl, size in {1: 20, 2: 16, 3: 13.5, 4: 12, 5: 11, 6: 10.5}.items()}
    code_style = ParagraphStyle("LCCode", parent=body, fontName=fonts["mono"], fontSize=8.5, leading=11,
                                backColor=colors.HexColor("#f4f4f4"), borderPadding=5, spaceBefore=4, spaceAfter=10)

    story = []
    counter = ListCounter()
    for b in doc.blocks:
        bullet = counter.bullet(b, dash="\u2022" if fonts["normal"] != "Helvetica" else "-")
        if b.kind == "heading":
            story.append(Paragraph(_markup(b.runs, fonts), heads.get(b.level, heads[6])))
        elif b.kind == "para":
            story.append(Paragraph(_markup(b.runs, fonts), body))
        elif b.kind == "li":
            indent = 14 + 16 * b.level
            style = ParagraphStyle(f"LCLi{b.level}", parent=body, leftIndent=indent, bulletIndent=indent - 11, spaceAfter=2)
            story.append(Paragraph(_markup(b.runs, fonts), style, bulletText=bullet))
        elif b.kind == "code":
            text = htmllib.escape(b.text, quote=False).replace(" ", "&nbsp;").replace("\n", "<br/>")
            story.append(Paragraph(text or "&nbsp;", code_style))
        elif b.kind == "table" and b.rows:
            ncols = max(len(r) for r in b.rows)
            size = 9 if ncols <= 6 else 7.5 if ncols <= 10 else 6
            cell = ParagraphStyle("LCCell", parent=body, fontSize=size, leading=size * 1.25, spaceAfter=0)
            head = ParagraphStyle("LCHead", parent=cell, fontName=fonts["bold"])
            data = [[Paragraph(htmllib.escape(str(c), quote=False).replace("\n", "<br/>"),
                               head if (i == 0 and b.header) else cell)
                     for c in (r + [""] * (ncols - len(r)))] for i, r in enumerate(b.rows)]
            t = Table(data, colWidths=[frame_w / ncols] * ncols, repeatRows=1 if b.header else 0)
            style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b0b0b0")),
                     ("VALIGN", (0, 0), (-1, -1), "TOP"),
                     ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
            if b.header:
                style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ececec")))
            t.setStyle(TableStyle(style))
            story += [t, Spacer(1, 8)]
        elif b.kind == "image" and b.image:
            data, size, _ = _image_for_embedding(b.image, allowed=("PNG", "JPEG", "GIF", "BMP"))
            if data:
                w, h = size
                scale = min(frame_w / w, (frame_h - 20) / h, 1.0)
                story += [RLImage(io.BytesIO(data), width=w * scale, height=h * scale), Spacer(1, 6)]
        elif b.kind == "hr":
            story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#999999"), spaceBefore=4, spaceAfter=8))
        elif b.kind == "pagebreak":
            if story and not isinstance(story[-1], PageBreak):
                story.append(PageBreak())
    if not story:
        story.append(Paragraph("", body))

    pdf = SimpleDocTemplate(str(out), pagesize=pagesize, leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=margin, title=doc.title, creator="LocalConvert")
    pdf.build(story)
    return out


# --------------------------------------------------------------------------- DOCX writer


def write_docx(doc: Document, out: Path, opts: Options) -> Path:
    import docx
    from docx.enum.section import WD_ORIENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Emu, Inches, Pt, RGBColor

    d = docx.Document()
    section = d.sections[0]
    if opts.page_size == "A4":
        section.page_width, section.page_height = Inches(8.27), Inches(11.69)
    if doc.landscape:
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width
    usable = section.page_width - section.left_margin - section.right_margin
    if doc.title:
        d.core_properties.title = doc.title

    def add_runs(p, runs: List[Run]):
        for r in runs:
            pieces = r.text.split("\n")
            for i, piece in enumerate(pieces):
                run = p.add_run(piece)
                run.bold = r.bold or None
                run.italic = r.italic or None
                if r.code:
                    run.font.name = "Consolas"
                if r.link:
                    run.font.color.rgb = RGBColor(0x1A, 0x5F, 0xB4)
                    run.underline = True
                if i < len(pieces) - 1:
                    run.add_break()

    styles = {s.name for s in d.styles}
    for b in doc.blocks:
        if b.kind == "heading":
            p = d.add_heading("", level=min(max(b.level, 1), 9))
            add_runs(p, b.runs)
        elif b.kind == "para":
            add_runs(d.add_paragraph(), b.runs)
        elif b.kind == "li":
            name = ("List Number" if b.ordered else "List Bullet") + (f" {b.level + 1}" if b.level else "")
            p = d.add_paragraph(style=name if name in styles else ("List Number" if b.ordered else "List Bullet"))
            add_runs(p, b.runs)
        elif b.kind == "code":
            p = d.add_paragraph()
            run = p.add_run(b.text)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif b.kind == "table" and b.rows:
            ncols = max(len(r) for r in b.rows)
            t = d.add_table(rows=len(b.rows), cols=ncols)
            t.style = "Table Grid"
            for i, row in enumerate(b.rows):
                for j in range(ncols):
                    cell = t.cell(i, j)
                    cell.text = str(row[j]) if j < len(row) else ""
                    if i == 0 and b.header:
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
            d.add_paragraph()
        elif b.kind == "image" and b.image:
            data, size, _ = _image_for_embedding(b.image, allowed=("PNG", "JPEG", "GIF", "BMP"))
            if data:
                native = Emu(int(size[0] * 914400 / 96))
                d.add_picture(io.BytesIO(data), width=min(native, usable))
        elif b.kind == "hr":
            p = d.add_paragraph()
            ppr = p._p.get_or_add_pPr()
            border = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "999999")):
                bottom.set(qn(k), v)
            border.append(bottom)
            ppr.append(border)
        elif b.kind == "pagebreak":
            d.add_page_break()
    d.save(str(out))
    return out


# --------------------------------------------------------------------------- HTML / Markdown / text writers


def _data_uri(data: bytes) -> Optional[str]:
    data, _, fmt = _image_for_embedding(data, allowed=("PNG", "JPEG", "GIF", "WEBP"))
    if not data:
        return None
    return f"data:image/{fmt.lower()};base64,{base64.b64encode(data).decode()}"


def _html_runs(runs: List[Run]) -> str:
    out = []
    for r in runs:
        t = htmllib.escape(r.text).replace("\n", "<br>")
        if r.code:
            t = f"<code>{t}</code>"
        if r.bold:
            t = f"<strong>{t}</strong>"
        if r.italic:
            t = f"<em>{t}</em>"
        if r.link:
            t = f'<a href="{htmllib.escape(r.link)}">{t}</a>'
        out.append(t)
    return "".join(out)


def write_html(doc: Document, out: Path, opts: Options) -> Path:
    body: List[str] = []
    open_lists: List[str] = []

    def close_lists(to_level: int):
        while len(open_lists) > to_level:
            body.append(f"</{open_lists.pop()}>")

    for b in doc.blocks:
        if b.kind == "li":
            close_lists(b.level + 1)
            want = "ol" if b.ordered else "ul"
            if len(open_lists) == b.level + 1 and (b.start or open_lists[-1] != want):
                close_lists(b.level)
            while len(open_lists) < b.level + 1:
                tag = "ol" if b.ordered else "ul"
                open_lists.append(tag)
                body.append(f"<{tag}>")
            body.append(f"<li>{_html_runs(b.runs)}</li>")
            continue
        close_lists(0)
        if b.kind == "heading":
            body.append(f"<h{b.level}>{_html_runs(b.runs)}</h{b.level}>")
        elif b.kind == "para":
            body.append(f"<p>{_html_runs(b.runs)}</p>")
        elif b.kind == "code":
            body.append(f"<pre><code>{htmllib.escape(b.text)}</code></pre>")
        elif b.kind == "table" and b.rows:
            rows = []
            for i, r in enumerate(b.rows):
                tag = "th" if i == 0 and b.header else "td"
                rows.append("<tr>" + "".join(f"<{tag}>{htmllib.escape(str(c))}</{tag}>" for c in r) + "</tr>")
            body.append("<table>\n" + "\n".join(rows) + "\n</table>")
        elif b.kind == "image" and b.image:
            uri = _data_uri(b.image)
            if uri:
                body.append(f'<p><img src="{uri}" alt="{htmllib.escape(b.alt)}"></p>')
        elif b.kind == "hr":
            body.append("<hr>")
        elif b.kind == "pagebreak":
            body.append('<div class="page-break"></div>')
    close_lists(0)
    css = ("body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:820px;margin:2rem auto;"
           "padding:0 1rem;line-height:1.55;color:#1d1d1f}img{max-width:100%}table{border-collapse:collapse;margin:1em 0}"
           "td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}th{background:#f2f2f2}"
           "pre{background:#f4f4f4;padding:.8em;overflow:auto}.page-break{page-break-after:always;"
           "border-top:1px dashed #ddd;margin:2em 0}")
    html = (f'<!doctype html>\n<html><head><meta charset="utf-8"><title>{htmllib.escape(doc.title)}</title>'
            f"<style>{css}</style></head>\n<body>\n" + "\n".join(body) + "\n</body></html>\n")
    Path(out).write_text(html, encoding="utf-8")
    return out


def _md_escape(text: str) -> str:
    return re.sub(r"([\\`*_\[\]])", r"\\\1", text)


def _md_runs(runs: List[Run]) -> str:
    out = []
    for r in runs:
        if r.code:
            t = f"`{r.text}`"
        else:
            t = _md_escape(r.text)
            lead, core, trail = re.match(r"^(\s*)(.*?)(\s*)$", t, re.S).groups()
            if core and r.bold:
                core = f"**{core}**"
            if core and r.italic:
                core = f"*{core}*"
            t = lead + core + trail
        if r.link:
            t = f"[{t}]({r.link})"
        out.append(t.replace("\n", "  \n"))
    return "".join(out)


def write_md(doc: Document, out: Path, opts: Options) -> Path:
    chunks: List[tuple] = []
    counter = ListCounter()
    for b in doc.blocks:
        bullet = counter.bullet(b)
        if b.kind == "heading":
            chunks.append(("h", "#" * min(b.level, 6) + " " + _md_runs(b.runs)))
        elif b.kind == "para":
            chunks.append(("p", _md_runs(b.runs)))
        elif b.kind == "li":
            chunks.append(("li", "   " * b.level + f"{bullet} {_md_runs(b.runs)}"))
        elif b.kind == "code":
            chunks.append(("code", f"```\n{b.text}\n```"))
        elif b.kind == "table" and b.rows:
            ncols = max(len(r) for r in b.rows)
            rows = [[str(c).replace("|", "\\|").replace("\n", " ") for c in r] + [""] * (ncols - len(r)) for r in b.rows]
            lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * ncols]
            lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
            chunks.append(("table", "\n".join(lines)))
        elif b.kind == "image" and b.image:
            uri = _data_uri(b.image)
            if uri:
                chunks.append(("img", f"![{b.alt}]({uri})"))
        elif b.kind == "hr":
            chunks.append(("hr", "---"))
        elif b.kind == "pagebreak":
            chunks.append(("hr", "---"))
    text = ""
    for i, (kind, chunk) in enumerate(chunks):
        if i:
            text += "\n" if kind == "li" and chunks[i - 1][0] == "li" else "\n\n"
        text += chunk
    Path(out).write_text(text.strip() + "\n", encoding="utf-8")
    return out


def write_txt(doc: Document, out: Path, opts: Options) -> Path:
    Path(out).write_text(document_to_text(doc), encoding="utf-8")
    return out


WRITERS: Dict[str, Callable[[Document, Path, Options], Path]] = {
    "pdf": write_pdf, "docx": write_docx, "html": write_html, "md": write_md, "txt": write_txt,
}
IMAGE_TARGETS = ["png", "jpg"]


def write_document(doc: Document, src: Path, out_dir: Path, target: str, opts: Options) -> List[Path]:
    """Write *doc* as *target*; image targets render one picture per page."""
    stem = stem_of(src)
    if target in WRITERS:
        return [WRITERS[target](doc, out_dir / f"{stem}.{target}", opts)]
    if target in IMAGE_TARGETS:
        from .pdf import pdf_to_images
        tmp_pdf = out_dir / f".{stem}.render.pdf"
        write_pdf(doc, tmp_pdf, opts)
        renamed = tmp_pdf.with_name(f"{stem}.pdf")
        tmp_pdf.rename(renamed)
        try:
            return pdf_to_images(renamed, out_dir, target, opts)
        finally:
            renamed.unlink()
    raise ConversionError(f"Cannot write {target} documents.")


# --------------------------------------------------------------------------- registration

LAYOUT_SOURCES = {"docx", "pptx", "pdf", "epub", "html"}
LAYOUT_TARGETS = {"pdf", "docx", "png", "jpg"}


def _is_basic(src: str, dst: str) -> bool:
    """Layout-heavy sources keep their content but not exact appearance."""
    return src in LAYOUT_SOURCES and dst in LAYOUT_TARGETS


def convert_document(src: Path, out_dir: Path, target: str, opts: Options) -> List[Path]:
    doc = READERS[get_ext(src)](src)
    return write_document(doc, src, out_dir, target, opts)


register("Code / log file", CODE_EXTS, ["pdf", "docx", "html", "txt", "png", "jpg"])(
    lambda src, out_dir, target, opts: write_document(read_code(src), src, out_dir, target, opts))
register("Document", READERS.keys(), list(WRITERS) + IMAGE_TARGETS, basic=_is_basic,
         note="Keeps text, headings, lists, tables and images; page layout may differ.")(convert_document)


def convert_image_to_docx(src: Path, out_dir: Path, target: str, opts: Options) -> List[Path]:
    return write_document(read_image(src), src, out_dir, target, opts)


def _register_image_to_docx():
    from .images import READ
    register("Image to Word", READ, ["docx"])(convert_image_to_docx)
    register("HEIC to Word", ["heic"], ["docx"], engine="heif")(convert_image_to_docx)


_register_image_to_docx()
