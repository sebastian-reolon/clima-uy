"""Tiny Markdown subset for the site's own docs: headings, paragraphs, lists, bold, code, links.

The site's docs are written by hand in this repo; anything outside this subset
is rendered as plain escaped text. No dependency needed.
"""
from __future__ import annotations

import re
from html import escape

_INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)"), r'<a href="\2">\1</a>'),
]


def _inline(text: str) -> str:
    out = escape(text, quote=False)
    for pattern, repl in _INLINE:
        out = pattern.sub(repl, out)
    return out


def render(md: str, heading_offset: int = 0) -> str:
    """HTML for `md`; `heading_offset` shifts heading levels (1 turns # into <h2>)."""
    html: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ""

    def flush() -> None:
        nonlocal list_tag
        if para:
            html.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()
        if items:
            html.append(f"<{list_tag}>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + f"</{list_tag}>")
            items.clear()
            list_tag = ""

    for raw in md.splitlines():
        line = raw.strip()
        heading = re.match(r"(#{1,4})\s+(.*)", line)
        bullet = re.match(r"[-*]\s+(.*)", line)
        numbered = re.match(r"\d+\.\s+(.*)", line)
        if not line:
            flush()
        elif heading:
            flush()
            level = min(len(heading.group(1)) + heading_offset, 6)
            html.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif bullet or numbered:
            tag = "ul" if bullet else "ol"
            if para or (items and list_tag != tag):
                flush()
            list_tag = tag
            items.append((bullet or numbered).group(1))
        elif items and raw.startswith((" ", "\t")):
            items[-1] += " " + line
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return "\n".join(html)
