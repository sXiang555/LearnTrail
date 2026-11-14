"""Deterministic text normalization without fetching linked content."""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal


@dataclass(frozen=True)
class TextBlock:
    start: int
    end: int
    section: str
    kind: Literal["heading", "paragraph", "code"]


@dataclass(frozen=True)
class CleanedDocument:
    title: str
    text: str
    blocks: tuple[TextBlock, ...]

    @property
    def content_hash(self) -> str:
        return hashlib.sha256((self.title + "\n\n" + self.text).encode("utf-8")).hexdigest()


class _HTMLText(HTMLParser):
    ignored = {"script", "style", "iframe", "object", "svg", "template", "noscript"}
    blocks = {"p", "div", "section", "article", "blockquote", "ul", "ol", "table", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0
        self.code: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.ignored:
            self.suppressed += 1
        if self.suppressed:
            return
        if tag == "pre":
            self.code = []
        elif self.code is None:
            if re.fullmatch(r"h[1-6]", tag):
                self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
            elif tag in self.blocks:
                self.parts.append("\n\n")
            elif tag == "br":
                self.parts.append("\n")
            elif tag == "li":
                self.parts.append("\n- ")
            elif tag == "code":
                self.parts.append("`")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.ignored:
            self.suppressed = max(0, self.suppressed - 1)
            return
        if self.suppressed:
            return
        if tag == "pre" and self.code is not None:
            code = "".join(self.code).strip("\n")
            longest = max((len(match) for match in re.findall(r"`+", code)), default=0)
            fence = "`" * max(3, longest + 1)
            self.parts.append("\n\n" + fence + "\n" + code + "\n" + fence + "\n\n")
            self.code = None
        elif self.code is None:
            if tag in self.blocks or re.fullmatch(r"h[1-6]", tag):
                self.parts.append("\n\n")
            elif tag == "code":
                self.parts.append("`")
            elif tag == "td":
                self.parts.append(" | ")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            if self.code is not None:
                self.code.append(data)
            else:
                self.parts.append(data)


def _html_text(value: str) -> str:
    parser = _HTMLText()
    parser.feed(value)
    parser.close()
    if parser.code is not None:
        parser.handle_endtag("pre")
    return "".join(parser.parts)


def _inline(value: str) -> str:
    # Inline code is kept verbatim; only common prose markup is removed.
    pieces = re.split(r"(`+[^`]*`+)", value)
    for index in range(0, len(pieces), 2):
        text = _html_text(pieces[index])
        text = re.sub(r"!?\[([^\]]*)\]\([^\n)]*\)", r"\1", text)
        text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
        text = re.sub(r"(?<!\w)([*_])(\S(?:.*?\S)?)\1(?!\w)", r"\2", text)
        pieces[index] = re.sub(r"[ \t\u00a0]+", " ", text)
    return "".join(pieces).strip()


def clean_document(title: str, body: str, content_format: Literal["text", "markdown", "html"]) -> CleanedDocument:
    if len(body) > 200000 or content_format not in {"text", "markdown", "html"}:
        raise ValueError("Unsupported or oversized article")
    title = " ".join(unicodedata.normalize("NFC", title).replace("\x00", "").split())
    body = unicodedata.normalize("NFC", body.replace("\r\n", "\n").replace("\r", "\n")).replace("\x00", "")
    if content_format == "html":
        body = _html_text(body)
    lines = body.split("\n")
    sections: list[tuple[int, str]] = []
    values: list[tuple[str, str, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            normalized = []
            for value in paragraph:
                text = " ".join(value.split()) if content_format == "text" else _inline(value)
                if text:
                    normalized.append(text)
            if normalized:
                values.append(("\n".join(normalized), " / ".join(name for _, name in sections) or title, "paragraph"))
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        fenced = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line) if content_format != "text" else None
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line) if content_format != "text" else None
        if fenced:
            flush()
            fence, info = fenced.groups()
            code = []
            index += 1
            while index < len(lines) and not re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", lines[index]):
                code.append(lines[index])
                index += 1
            values.append((fence + info.strip() + "\n" + "\n".join(code) + "\n" + fence,
                           " / ".join(name for _, name in sections) or title, "code"))
        elif heading:
            flush()
            level = len(heading[1])
            name = _inline(heading[2])
            sections = [(depth, item) for depth, item in sections if depth < level] + [(level, name)]
            values.append(("#" * level + " " + name, " / ".join(item for _, item in sections), "heading"))
        elif not line.strip():
            flush()
        else:
            paragraph.append(line)
        index += 1
    flush()
    if not title or not values:
        raise ValueError("Article has no indexable text")
    parts, blocks, position = [], [], 0
    for text, section, kind in values:
        parts.append(text)
        blocks.append(TextBlock(position, position + len(text), section, kind))
        position += len(text) + 2
    return CleanedDocument(title, "\n\n".join(parts), tuple(blocks))
