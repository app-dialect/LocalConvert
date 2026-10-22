"""A tiny, format-neutral document model.

Readers turn a source file (txt, md, html, docx, pdf, pptx, epub ...) into a
:class:`Document`; writers turn a Document into pdf, docx, html, md, txt.
With N readers and M writers this gives N x M conversions without writing
each pair by hand.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, List, Optional


@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: Optional[str] = None


@dataclass
class Block:
    kind: str  # heading | para | li | code | table | image | hr | pagebreak
    runs: List[Run] = field(default_factory=list)
    level: int = 0             # heading level (1-6) or list nesting depth (0-based)
    ordered: bool = False      # numbered list item
    start: bool = False        # first item of a new list (numbering restarts)
    text: str = ""             # code block contents
    rows: Optional[List[List[str]]] = None
    header: bool = True        # first table row is a header
    image: Optional[bytes] = None
    alt: str = ""

    def plain(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass
class Document:
    title: str = ""
    blocks: List[Block] = field(default_factory=list)
    landscape: bool = False

    def add(self, block: Block) -> None:
        if block.kind in ("para", "heading", "li") and not block.plain().strip() and block.image is None:
            return
        self.blocks.append(block)


def text_to_runs(text: str) -> List[Run]:
    return [Run(text)] if text else []


class ListCounter:
    """Numbers list items; restarts on a new list, a level change or a bullet/number switch."""

    def __init__(self):
        self.counts: dict = {}
        self.kinds: dict = {}

    def bullet(self, b: Block, dash: str = "-") -> str:
        if b.kind != "li":
            self.counts, self.kinds = {}, {}
            return ""
        for k in [k for k in self.counts if k > b.level]:
            del self.counts[k]
            self.kinds.pop(k, None)
        if b.start or self.kinds.get(b.level) != b.ordered:
            self.counts[b.level] = 0
        self.kinds[b.level] = b.ordered
        self.counts[b.level] = self.counts.get(b.level, 0) + 1
        return f"{self.counts[b.level]}." if b.ordered else dash


# --------------------------------------------------------------------------- HTML reader

BLOCK_TAGS = {"p", "div", "section", "article", "blockquote", "header", "footer", "main", "nav",
              "aside", "figure", "figcaption", "address", "dd", "dt", "dl", "body", "center", "form"}
SKIP_TAGS = {"script", "style", "head", "noscript", "template", "svg", "math", "button", "select"}
VOID_TAGS = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr", "source"}

ImageResolver = Callable[[str], Optional[bytes]]


class _HTMLToDoc(HTMLParser):
    def __init__(self, resolve_image: Optional[ImageResolver] = None):
        super().__init__(convert_charrefs=True)
        self.doc = Document()
        self.resolve_image = resolve_image
        self.cur = Block("para")
        self.style = {"bold": 0, "italic": 0, "code": 0}
        self.links: List[Optional[str]] = []
        self.lists: List[bool] = []          # stack of "ordered?" flags
        self.new_list = False
        self.skip = 0
        self.in_title = False
        self.pre: Optional[List[str]] = None
        self.table: Optional[List[List[str]]] = None
        self.row: Optional[List[str]] = None
        self.cell: Optional[List[str]] = None

    # -- helpers
    def flush(self, next_block: Optional[Block] = None) -> None:
        runs = self._clean(self.cur.runs)
        if runs or self.cur.kind not in ("para", "heading", "li"):
            self.cur.runs = runs
            self.doc.add(self.cur)
        self.cur = next_block or Block("para")

    @staticmethod
    def _clean(runs: List[Run]) -> List[Run]:
        out: List[Run] = []
        at_line_start = True
        for r in runs:
            t = r.text
            if at_line_start:
                t = t.lstrip(" ")
            t = re.sub(r"\n +", "\n", t)
            if not t:
                continue
            at_line_start = t.endswith("\n")
            out.append(Run(t, r.bold, r.italic, r.code, r.link))
        while out and not out[-1].text.strip():
            out.pop()
        if out:
            out[-1].text = out[-1].text.rstrip()
        return out

    def _run(self, text: str) -> Run:
        return Run(text, bool(self.style["bold"]), bool(self.style["italic"]),
                   bool(self.style["code"]), self.links[-1] if self.links else None)

    def _image(self, attrs: dict) -> None:
        src = attrs.get("src") or ""
        data = None
        if src.startswith("data:") and ";base64," in src:
            try:
                data = base64.b64decode(src.split(",", 1)[1])
            except Exception:
                data = None
        elif self.resolve_image and not re.match(r"^[a-z]+://", src, re.I):
            data = self.resolve_image(src)   # local files only - never fetched from the internet
        if data:
            if self.table is None:
                self.flush()
                self.doc.add(Block("image", image=data, alt=attrs.get("alt", "")))
        elif attrs.get("alt"):
            self.handle_data(f"[{attrs['alt']}]")

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP_TAGS:
            if tag not in VOID_TAGS:
                self.skip += 1
            return
        if tag == "title":
            self.in_title = True
            return
        if self.skip:
            return
        if self.table is not None and tag not in ("tr", "td", "th", "table", "br", "img"):
            if tag in BLOCK_TAGS or tag in ("li",):
                self._cell_text(" ")
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.flush(Block("heading", level=int(tag[1])))
        elif tag in ("ul", "ol"):
            self.flush()
            self.lists.append(tag == "ol")
            self.new_list = True
        elif tag == "li":
            self.flush(Block("li", level=max(0, len(self.lists) - 1),
                             ordered=bool(self.lists and self.lists[-1]), start=self.new_list))
            self.new_list = False
        elif tag in BLOCK_TAGS:
            if not (self.cur.kind == "li" and tag in ("p", "div")):
                self.flush()
        elif tag == "pre":
            self.flush()
            self.pre = []
        elif tag in ("b", "strong"):
            self.style["bold"] += 1
        elif tag in ("i", "em", "cite", "var"):
            self.style["italic"] += 1
        elif tag in ("code", "kbd", "samp", "tt"):
            self.style["code"] += 1
        elif tag == "a":
            self.links.append(a.get("href"))
        elif tag == "br":
            if self.pre is not None:
                self.pre.append("\n")
            else:
                self.cur.runs.append(self._run("\n"))
        elif tag == "hr":
            self.flush()
            self.doc.add(Block("hr"))
        elif tag == "img":
            self._image(a)
        elif tag == "table":
            self.flush()
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "title":
            self.in_title = False
            return
        if self.skip:
            return
        if tag in ("td", "th") and self.row is not None and self.cell is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr" and self.table is not None and self.row is not None:
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            if self.table:
                self.doc.add(Block("table", rows=self.table))
            self.table = None
        elif self.table is not None:
            return
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "li"):
            self.flush()
        elif tag in ("ul", "ol"):
            self.flush()
            if self.lists:
                self.lists.pop()
        elif tag in BLOCK_TAGS:
            if not (self.cur.kind == "li" and tag in ("p", "div")):
                self.flush()
        elif tag == "pre" and self.pre is not None:
            self.doc.add(Block("code", text="".join(self.pre).strip("\n")))
            self.pre = None
        elif tag in ("b", "strong"):
            self.style["bold"] = max(0, self.style["bold"] - 1)
        elif tag in ("i", "em", "cite", "var"):
            self.style["italic"] = max(0, self.style["italic"] - 1)
        elif tag in ("code", "kbd", "samp", "tt"):
            self.style["code"] = max(0, self.style["code"] - 1)
        elif tag == "a" and self.links:
            self.links.pop()

    def _cell_text(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_data(self, data):
        if self.in_title:
            self.doc.title += data.strip()
            return
        if self.skip:
            return
        if self.table is not None:
            self._cell_text(data)
        elif self.pre is not None:
            self.pre.append(data)
        else:
            text = re.sub(r"\s+", " ", data)
            if text:
                self.cur.runs.append(self._run(text))

    def close(self):
        super().close()
        self.flush()
        if self.table:
            self.doc.add(Block("table", rows=self.table))


def html_to_document(html: str, resolve_image: Optional[ImageResolver] = None) -> Document:
    parser = _HTMLToDoc(resolve_image)
    parser.feed(html)
    parser.close()
    return parser.doc


# --------------------------------------------------------------------------- Plain text helpers

def document_to_text(doc: Document) -> str:
    chunks: List[tuple] = []          # (block kind, text)
    counter = ListCounter()
    for b in doc.blocks:
        bullet = counter.bullet(b)
        if b.kind == "heading":
            t = b.plain()
            underline = "\n" + ("=" if b.level == 1 else "-") * min(len(t), 80) if b.level <= 2 else ""
            chunks.append((b.kind, t + underline))
        elif b.kind == "para":
            chunks.append((b.kind, b.plain()))
        elif b.kind == "li":
            chunks.append((b.kind, "  " * b.level + f"{bullet} {b.plain()}"))
        elif b.kind == "code":
            chunks.append((b.kind, b.text))
        elif b.kind == "table" and b.rows:
            chunks.append((b.kind, "\n".join("\t".join(r) for r in b.rows)))
        elif b.kind == "image":
            chunks.append((b.kind, f"[image{': ' + b.alt if b.alt else ''}]"))
        elif b.kind == "hr":
            chunks.append((b.kind, "-" * 40))
        elif b.kind == "pagebreak":
            chunks.append((b.kind, "\f"))
    # list items sit on consecutive lines; everything else is separated by a blank line
    text = ""
    for i, (kind, chunk) in enumerate(chunks):
        if i:
            text += "\n" if kind == "li" and chunks[i - 1][0] == "li" else "\n\n"
        text += chunk
    return text.strip() + "\n"
