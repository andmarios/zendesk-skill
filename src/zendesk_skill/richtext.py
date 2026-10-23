"""Turn a Zendesk comment's ``html_body`` into readable Markdown or safe HTML.

``plain_body`` flattens formatting (lists, bold, links, headings, code, tables),
which makes long exports hard for people to read. ``html_body`` keeps it, but it
is customer-controlled HTML, so it is never passed through. Instead it is parsed
into a small tree that keeps only an allowlist of formatting tags; both outputs
are then *generated* from that tree:

* unknown tags are unwrapped (their text is kept), dangerous ones
  (script, style, iframe, object, embed, svg, ...) are dropped with their content;
* attributes are discarded, except ``href`` on links, which must be http(s) or mailto;
* all text is escaped for the target format.

Hostile input can only cost time, never safety: nesting is capped, end-tag
matching looks at a bounded window, and the renderers are bounded-depth recursion.
"""

import html
import re
from html.parser import HTMLParser

MAX_HTML_CHARS = 300_000   # larger bodies fall back to plain text
MAX_DEPTH = 40             # real nesting kept in the tree (deeper tags are unwrapped)
MATCH_WINDOW = 64          # how far up the open-tag stack an end tag is matched

VOID = {"br", "hr", "img"}
DROP = {"script", "style", "iframe", "object", "embed", "svg", "noscript", "template",
        "head", "title", "textarea", "select", "canvas", "audio", "video", "math"}
BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "figure",
         "ul", "ol", "li", "blockquote", "pre", "hr", "table", "thead", "tbody", "tfoot",
         "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "dl", "dt", "dd", "center"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
STRONG, EM, STRIKE = {"strong", "b"}, {"em", "i"}, {"s", "del", "strike"}
SAFE_LINK = re.compile(r"^(https?://|mailto:)", re.I)


class Node:
    __slots__ = ("tag", "href", "alt", "children")

    def __init__(self, tag: str, href: str | None = None, alt: str | None = None):
        self.tag, self.href, self.alt, self.children = tag, href, alt, []


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)  # entities decoded exactly once, here
        self.root = Node("root")
        self.stack: list[tuple[str, Node | None]] = []   # (tag, node) - node None = unwrapped/dropped
        self.dropping = 0
        self.depth = 0

    def _current(self) -> Node:
        for _, node in reversed(self.stack):
            if node is not None and node is not _DROPPED:
                return node
        return self.root

    def handle_starttag(self, tag, attrs):
        if self.dropping:
            if tag in DROP:
                self.stack.append((tag, _DROPPED)); self.dropping += 1
            return
        if tag in DROP:
            self.stack.append((tag, _DROPPED)); self.dropping += 1
            return
        attrs = dict(attrs)
        if tag == "img":
            node = Node("img", href=attrs.get("src"), alt=(attrs.get("alt") or "").strip())
            self._current().children.append(node)
            return
        if tag in VOID:
            self._current().children.append(Node(tag))
            return
        keep = self.depth < MAX_DEPTH and (tag in BLOCK or tag in STRONG | EM | STRIKE | {"a", "code", "u"})
        if keep:
            href = attrs.get("href") if tag == "a" else None
            node = Node(tag, href=href if href and SAFE_LINK.match(href.strip()) else None)
            self._current().children.append(node)
            self.stack.append((tag, node)); self.depth += 1
        else:
            self.stack.append((tag, None))  # unwrapped: children join the nearest kept ancestor

    def handle_startendtag(self, tag, attrs):
        if tag in VOID or tag == "img":
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        window = self.stack[-MATCH_WINDOW:]
        for i in range(len(window) - 1, -1, -1):
            if window[i][0] == tag:
                for t, node in self.stack[len(self.stack) - len(window) + i:]:
                    if node is _DROPPED:
                        self.dropping -= 1
                    elif node is not None:
                        self.depth -= 1
                del self.stack[len(self.stack) - len(window) + i:]
                return
        # unmatched end tag: ignore

    def handle_data(self, data):
        if not self.dropping and data:
            self._current().children.append(data)


_DROPPED = Node("dropped")


def parse(html_text: str) -> Node | None:
    """Parse into the allowlisted tree; None if the input is unusable (caller falls back to text)."""
    if not html_text or len(html_text) > MAX_HTML_CHARS:
        return None
    builder = _TreeBuilder()
    try:
        builder.feed(html_text)
        builder.close()
    except Exception:  # parser edge cases must never break an export
        return None
    return builder.root


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_WS = re.compile(r"[ \t\r\n\f\v ]+")


def _text_of(node: Node | str) -> str:
    """All descendant text, whitespace untouched (used for <pre>)."""
    if isinstance(node, str):
        return node
    return "".join(_text_of(c) for c in node.children)


def _is_block(child: Node | str) -> bool:
    return isinstance(child, Node) and (child.tag in BLOCK)


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

_MD_TEXT = re.compile(r"([\\`*_\[\]<>])")
_LINE_START = re.compile(r"^(\s*)([#+\-]|\d+[.)])(?=\s)")


def _md_text(text: str) -> str:
    return _MD_TEXT.sub(r"\\\1", _WS.sub(" ", text))


_URL_ESCAPES = {" ": "%20", "(": "%28", ")": "%29", "<": "%3C", ">": "%3E", '"': "%22", "\\": "%5C", "\n": "", "\r": ""}


def _md_url(url: str) -> str:
    return "".join(_URL_ESCAPES.get(ch, ch) for ch in url.strip())


def _wrap(marker: str, inner: str) -> str:
    core = inner.strip()
    if not core:
        return inner
    lead = inner[: len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()):]
    return f"{lead}{marker}{core}{marker}{trail}"


def _md_inline(children: list) -> str:
    out = []
    for c in children:
        if isinstance(c, str):
            out.append(_md_text(c))
        elif c.tag == "br":
            out.append("  \n")
        elif c.tag == "img":
            label = c.alt or "image"
            out.append(f"[image: {_md_text(label)}]")
        elif c.tag in BLOCK:
            out.append("\n" + "\n\n".join(_md_blocks([c])) + "\n")
        elif c.tag in STRONG:
            out.append(_wrap("**", _md_inline(c.children)))
        elif c.tag in EM:
            out.append(_wrap("*", _md_inline(c.children)))
        elif c.tag in STRIKE:
            out.append(_wrap("~~", _md_inline(c.children)))
        elif c.tag == "code":
            code = _text_of(c).replace("\n", " ")
            fence = "`" * (max((len(m) for m in re.findall(r"`+", code)), default=0) + 1)
            out.append(f"{fence} {code} {fence}" if code.startswith("`") or code.endswith("`") else f"{fence}{code}{fence}")
        elif c.tag == "a":
            inner = _md_inline(c.children).strip()
            out.append(f"[{inner or c.href}]({_md_url(c.href)})" if c.href else inner)
        else:  # u, and anything else transparent
            out.append(_md_inline(c.children))
    return "".join(out)


def _paragraph(text: str) -> str | None:
    lines = [_LINE_START.sub(lambda m: f"{m.group(1)}\\{m.group(2)}", ln.strip()) for ln in text.split("\n")]
    text = "\n".join(ln for ln in lines)
    return text.strip("\n") if text.strip() else None


def _md_blocks(children: list) -> list[str]:
    blocks: list[str] = []
    run: list = []

    def flush():
        if run:
            para = _paragraph(_md_inline(run))
            if para:
                blocks.append(para)
            run.clear()

    for c in children:
        if not _is_block(c):
            run.append(c)
            continue
        flush()
        tag = c.tag
        if tag == "hr":
            blocks.append("---")
        elif tag == "pre":
            code = _text_of(c).strip("\n")
            fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", code)), default=0) + 1)
            blocks.append(f"{fence}\n{code}\n{fence}")
        elif tag in ("ul", "ol"):
            items = []
            n = 0
            for li in c.children:
                if not (isinstance(li, Node) and li.tag == "li"):
                    continue
                n += 1
                marker = f"{n}. " if tag == "ol" else "- "
                inner = _md_blocks(li.children) or [""]
                joined = inner[0]
                for prev, block in zip(inner, inner[1:]):  # keep a nested list tight against its item
                    joined += ("\n" if re.match(r"(?:- |\d+\. )", block) else "\n\n") + block
                first, *rest = joined.split("\n")
                pad = " " * len(marker)
                items.append(marker + first + "".join("\n" + (pad + ln if ln else "") for ln in rest))
            if items:
                blocks.append("\n".join(items))
        elif tag == "blockquote":
            inner = "\n\n".join(_md_blocks(c.children))
            if inner:
                blocks.append("\n".join(f"> {ln}" if ln else ">" for ln in inner.split("\n")))
        elif tag in HEADINGS:
            text = _md_inline(c.children).strip()
            if text:
                blocks.append(f"**{text}**" if not text.startswith("**") else text)
        elif tag == "table":
            rows = []
            stack = [c]
            while stack:
                n_ = stack.pop(0)
                for ch in n_.children:
                    if isinstance(ch, Node) and ch.tag == "tr":
                        rows.append(ch)
                    elif isinstance(ch, Node) and ch.tag in ("thead", "tbody", "tfoot"):
                        stack.insert(0, ch)
            lines = []
            for tr in rows:
                cells = [
                    _md_inline(td.children).strip().replace("\n", " ").replace("|", "\\|")
                    for td in tr.children if isinstance(td, Node) and td.tag in ("td", "th")
                ]
                if not cells:
                    continue
                lines.append("| " + " | ".join(cells) + " |")
                if len(lines) == 1:  # header separator after the first row
                    lines.append("|" + " --- |" * len(cells))
            if lines:
                blocks.append("\n".join(lines))
        elif tag in ("tr", "td", "th", "li", "dt", "dd"):
            blocks.extend(_md_blocks(c.children))  # stray table/list parts: keep their text
        else:  # p, div, section, ...
            blocks.extend(_md_blocks(c.children))
    flush()
    return blocks


def to_markdown(root: Node) -> str:
    """Markdown for a parsed body ('' if nothing readable)."""
    return "\n\n".join(_md_blocks(root.children)).strip()


# --------------------------------------------------------------------------
# HTML (rebuilt from the tree; nothing from the input is echoed raw)
# --------------------------------------------------------------------------

_HTML_PASS = {"p", "ul", "ol", "li", "blockquote", "pre", "table", "thead", "tbody", "tfoot",
              "tr", "td", "th", "dl", "dt", "dd"}


def _h(text: str) -> str:
    return html.escape(text)


def _html_children(children: list) -> str:
    out = []
    for c in children:
        if isinstance(c, str):
            out.append(_h(c))
        elif c.tag == "br":
            out.append("<br>")
        elif c.tag == "hr":
            out.append("<hr>")
        elif c.tag == "img":
            label = _h(c.alt or "image")
            href = c.href if c.href and SAFE_LINK.match(c.href.strip()) else None
            out.append(f'<a href="{html.escape(href.strip(), quote=True)}" rel="noopener noreferrer">[image: {label}]</a>'
                       if href else f"[image: {label}]")
        elif c.tag in HEADINGS:
            out.append(f"<p><strong>{_html_children(c.children)}</strong></p>")
        elif c.tag in STRONG:
            out.append(f"<strong>{_html_children(c.children)}</strong>")
        elif c.tag in EM:
            out.append(f"<em>{_html_children(c.children)}</em>")
        elif c.tag in STRIKE:
            out.append(f"<s>{_html_children(c.children)}</s>")
        elif c.tag == "u":
            out.append(f"<u>{_html_children(c.children)}</u>")
        elif c.tag == "code":
            out.append(f"<code>{_h(_text_of(c))}</code>")
        elif c.tag == "pre":
            out.append(f"<pre>{_h(_text_of(c))}</pre>")
        elif c.tag == "a":
            inner = _html_children(c.children)
            out.append(f'<a href="{html.escape(c.href.strip(), quote=True)}" rel="noopener noreferrer">{inner}</a>'
                       if c.href else inner)
        elif c.tag in _HTML_PASS:
            out.append(f"<{c.tag}>{_html_children(c.children)}</{c.tag}>")
        else:  # div, section, center, ... : a plain block
            out.append(f"<div>{_html_children(c.children)}</div>")
    return "".join(out)


def to_html(root: Node) -> str:
    """Sanitised HTML fragment for a parsed body ('' if nothing readable)."""
    out = _html_children(root.children)
    return out if _text_of(root).strip() or "<img" in out or "[image" in out else ""
