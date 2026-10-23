"""Render a full Zendesk ticket (metadata, custom fields, conversation) as Markdown or HTML.

Pure functions: they take already-fetched, already-annotated API data and return
a string. All ticket content is customer-controlled, so both renderers escape
it: the HTML renderer escapes everything and never emits the original comment
HTML, and the Markdown renderer escapes markup characters (and ``<``) so the
file is safe to open in an HTML-permissive Markdown viewer.
"""

import html
import re
from typing import Any

from zendesk_skill import richtext

FORMATS = {"md": "markdown", "markdown": "markdown", "html": "html"}

INTERNAL_NOTES_WARNING = "Contains internal notes - do not share with the customer."
PUBLIC_ONLY_NOTE = "Public replies only; internal notes are omitted."


_NOT_ENCODABLE = re.compile("[\ud800-\udfff]")  # lone surrogates: cannot be written as UTF-8
# C0 controls except tab/newline (ESC starts terminal escape sequences), DEL, and C1 controls
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def scrub_text(text: str) -> tuple[str, int, int]:
    """Make rendered text safe to write, print and read in a terminal.

    * line endings are normalised to ``\\n`` (a lone carriage return can overwrite
      text on a terminal);
    * characters UTF-8 cannot encode (lone surrogates, e.g. from a truncated emoji in
      Zendesk JSON) and control characters other than tab/newline (terminal escape
      sequences, NUL, ...) are replaced with U+FFFD.

    Returns the cleaned text and how many characters of each kind were replaced.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text, unencodable = _NOT_ENCODABLE.subn("\ufffd", text)
    text, control = _CONTROL.subn("\ufffd", text)
    return text, unencodable, control


def normalize_format(fmt: str) -> str:
    """Map a user-supplied format name to 'markdown' or 'html'."""
    try:
        return FORMATS[fmt.strip().lower()]
    except KeyError:
        raise ValueError(f"Unsupported format: {fmt!r} (use 'md' or 'html')")


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def display_value(field: dict) -> str:
    """Human-readable value for a custom field (labels for dropdown options)."""
    value = field.get("value_label", field.get("value"))
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "" if value is None else str(value)


def _has_value(field: dict) -> bool:
    value = field.get("value")
    return value not in (None, "", [], False)


def _comment_body(comment: dict) -> str:
    """The comment text as people would read it.

    Zendesk's ``plain_body`` is derived from HTML and still carries entities
    (``&lt;``, ``&gt;``, ``&amp;``, ``&nbsp;``), which would otherwise show up
    literally - or double-escaped, in the HTML export. Decode them exactly once
    here; the renderers then escape the result for their own output format, so
    decoding never lets markup through. ``body`` is already plain text.
    """
    plain = comment.get("plain_body")
    if plain:
        return html.unescape(plain).replace("\xa0", " ").strip()
    return (comment.get("body") or "").strip()


def _rich_body(comment: dict, extra: dict) -> richtext.Node | None:
    """Parsed formatted body (html_body) for a comment, or None to use plain text.

    None when text-only output was requested, there is no html_body, it is too
    large, or it holds nothing readable.
    """
    if extra.get("text_only"):
        return None
    return richtext.parse(comment.get("html_body") or "")


def _notes(extra: dict) -> list[str]:
    """Lookup/scrub notes to show in the document (empty when everything resolved)."""
    return [str(w) for w in extra.get("warnings") or []]


def _banner(comments: list[dict], extra: dict) -> str | None:
    if extra.get("public_only"):
        return PUBLIC_ONLY_NOTE
    if any(not c.get("public") for c in comments):
        return INTERNAL_NOTES_WARNING
    return None


def _people(users: list[dict] | None) -> str:
    """'Name <email>, Name <email>' for a list of {id, name, email} dicts."""
    parts = []
    for u in users or []:
        name, email = u.get("name"), u.get("email")
        parts.append(f"{name} <{email}>" if name and email else name or email or str(u.get("id")))
    return ", ".join(parts)


def _addresses(side: Any) -> list[str]:
    """Email addresses from a via.source from/to value (dict, list of dicts, or other)."""
    items = side if isinstance(side, list) else [side]
    out = []
    for item in items:
        if isinstance(item, dict):
            label = item.get("name")
            addr = item.get("address")
            out.append(f"{label} <{addr}>" if label and addr else addr or label)
    return [o for o in out if o]


def email_recipients(comment: dict) -> dict[str, list[str]]:
    """From/To/CC and other recipient addresses recorded on an email comment.

    Zendesk records who an email went to in via.source: ``from.original_recipients``
    lists every address on the message (sender and To included), and some accounts
    also set ``to.email_ccs``. Anyone beyond the sender and the To address is
    reported as "Also sent to" (CCs are listed separately when explicitly marked).
    Empty for non-email channels.
    """
    source = (comment.get("via") or {}).get("source") or {}
    frm, to = source.get("from"), source.get("to")
    ccs = [str(c) for c in (to.get("email_ccs") if isinstance(to, dict) else None) or []]

    originals = frm.get("original_recipients") if isinstance(frm, dict) else None
    named = {
        str(a).lower()
        for side in (frm, to) if isinstance(side, dict)
        for a in [side.get("address")] if a
    } | {c.lower() for c in ccs}
    others: list[str] = []
    for addr in originals if isinstance(originals, list) else []:
        if isinstance(addr, str) and addr.lower() not in named and addr not in others:
            others.append(addr)

    result = {
        "From": _addresses(frm),
        "To": _addresses(to),
        "CC": ccs,
        "Also sent to": others,
    }
    return {k: v for k, v in result.items() if v}


def _recipient_line(comment: dict) -> str:
    return " · ".join(f"{k}: {', '.join(v)}" for k, v in email_recipients(comment).items())


def _meta(ticket: dict, extra: dict) -> list[tuple[str, str]]:
    def person(name_key: str, id_key: str, email_key: str) -> str:
        name, uid, email = ticket.get(name_key), ticket.get(id_key), ticket.get(email_key)
        if uid is None:
            return ""
        label = f"{name} ({uid})" if name else str(uid)
        return f"{label} <{email}>" if email else label

    rows = [
        ("Status", ticket.get("status")),
        ("Priority", ticket.get("priority")),
        ("Type", ticket.get("type")),
        ("Requester", person("requester_name", "requester_id", "requester_email")),
        ("Assignee", person("assignee_name", "assignee_id", "assignee_email") or "(unassigned)"),
        ("CC", _people(ticket.get("email_cc_users"))),
        ("Followers", _people(ticket.get("follower_users"))),
        ("Organization", extra.get("organization_name") or ticket.get("organization_id")),
        ("Group", extra.get("group_name") or ticket.get("group_id")),
        ("Channel", (ticket.get("via") or {}).get("channel")),
        ("Created", ticket.get("created_at")),
        ("Updated", ticket.get("updated_at")),
        ("Due", ticket.get("due_at")),
        ("URL", ticket.get("url")),
    ]
    return [(k, str(v)) for k, v in rows if v not in (None, "")]


def _attachments(comment: dict) -> list[dict]:
    return [a for a in comment.get("attachments") or [] if isinstance(a, dict)]


def _http_url(url: Any) -> str | None:
    url = str(url or "")
    return url if url.startswith(("https://", "http://")) else None


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|#])")


def md_inline(text: Any) -> str:
    """Escape text for inline Markdown so names/subjects can't inject markup or raw HTML."""
    return _MD_SPECIAL.sub(r"\\\1", "" if text is None else str(text))


def _md_body_line(line: str) -> str:
    # Bodies stay readable; only raw-HTML openers are neutralised.
    return line.replace("<", "\\<")


def _md_url(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def render_markdown(ticket: dict, comments: list[dict], extra: dict | None = None) -> str:
    """Render the ticket as a Markdown document."""
    extra = extra or {}
    out = [f"# Ticket #{ticket.get('id')}: {md_inline(ticket.get('subject') or '(no subject)')}", ""]

    banner = _banner(comments, extra)
    if banner:
        out += [f"> **{'⚠ ' if not extra.get('public_only') else ''}{banner}**", ""]
    if _notes(extra):
        out += ["> **Export notes**", ">"] + [f"> - {md_inline(n)}" for n in _notes(extra)] + [""]

    for label, value in _meta(ticket, extra):
        out.append(f"- **{label}:** {value if label == 'URL' else md_inline(value)}")
    out.append("")

    fields = [f for f in ticket.get("custom_fields") or [] if _has_value(f)]
    if fields:
        out += ["## Custom fields", ""]
        for f in fields:
            title = f.get("title") or f"Field {f.get('id')}"
            out.append(f"- **{md_inline(title)}:** {md_inline(display_value(f))}")
        out.append("")

    if ticket.get("tags"):
        out += [f"**Tags:** {md_inline(', '.join(ticket['tags']))}", ""]

    out += [f"## Conversation ({len(comments)})", ""]
    if not comments:
        out += [_md_body_line(ticket.get("description") or "(no comments)"), ""]
    for i, c in enumerate(comments, 1):
        who = c.get("author_name") or f"user {c.get('author_id')}"
        kind = "Public reply" if c.get("public") else "Internal note"
        channel = (c.get("via") or {}).get("channel")
        via = f" via {channel}" if channel else ""
        out += [f"### {i}. {md_inline(who)} — {kind}{md_inline(via)}", f"*{md_inline(c.get('created_at'))}*", ""]
        if _recipient_line(c):
            out += [f"*{md_inline(_recipient_line(c))}*", ""]
        rich = _rich_body(c, extra)
        rich_md = richtext.to_markdown(rich) if rich else ""
        if rich_md:
            out += [f"> {line}" if line else ">" for line in rich_md.splitlines()]
        else:
            body = _comment_body(c) or "(empty)"
            out += [f"> {_md_body_line(line)}" if line else ">" for line in body.splitlines()]
        out.append("")
        for a in _attachments(c):
            url = _http_url(a.get("content_url"))
            name = md_inline(a.get("file_name"))
            out.append(f"- Attachment: [{name}]({_md_url(url)})" if url else f"- Attachment: {name}")
        if _attachments(c):
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

_CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222;line-height:1.5}
h1{font-size:1.5rem}h2{font-size:1.15rem;border-bottom:1px solid #ddd;padding-bottom:.2rem;margin-top:2rem}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.2rem 1rem}dt{font-weight:600}dd{margin:0}
.banner{background:#fff3cd;border:1px solid #f0d98c;border-radius:6px;padding:.5rem .8rem;font-weight:600}
.banner.info{background:#eef;border-color:#cce}
.notes{background:#f5f5f5;border:1px solid #ddd;border-radius:6px;padding:.4rem .8rem;font-size:.9rem}.notes ul{margin:.2rem 0}
.comment{border:1px solid #ddd;border-radius:6px;margin:1rem 0}
.comment.note{background:#fff8e1;border-color:#f0d98c}
.comment header{padding:.4rem .8rem;border-bottom:1px solid #eee;font-size:.9rem;color:#555}
.comment .body{padding:.6rem .8rem;white-space:pre-wrap;word-wrap:break-word}
.comment .body.rich{white-space:normal}.rich p{margin:.5rem 0}.rich blockquote{margin:.5rem 0;padding-left:.8rem;border-left:3px solid #ccc;color:#555}
.rich pre{background:#f6f6f6;padding:.5rem;overflow-x:auto;white-space:pre}.rich code{background:#f6f6f6;padding:0 .2rem;border-radius:3px}
.rich table{border-collapse:collapse}.rich td,.rich th{border:1px solid #ddd;padding:.2rem .5rem}.rich hr{border:0;border-top:1px solid #ddd}
.comment ul{margin:0;padding:.3rem 1.8rem .6rem}.tag{display:inline-block;background:#eef;border-radius:3px;padding:0 .4rem;margin-right:.3rem;font-size:.85rem}
""".strip()


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _safe_href(url: Any) -> str | None:
    url = _http_url(url)
    return html.escape(url, quote=True) if url else None


def render_html(ticket: dict, comments: list[dict], extra: dict | None = None) -> str:
    """Render the ticket as a standalone HTML document (no scripts, everything escaped)."""
    extra = extra or {}
    title = f"Ticket #{ticket.get('id')}: {ticket.get('subject') or '(no subject)'}"
    out = [
        "<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
        f"<title>{_e(title)}</title>", f"<style>{_CSS}</style></head><body>",
        f"<h1>{_e(title)}</h1>",
    ]
    banner = _banner(comments, extra)
    if banner:
        cls = "banner info" if extra.get("public_only") else "banner"
        icon = "" if extra.get("public_only") else "⚠ "
        out.append(f'<p class="{cls}">{icon}{_e(banner)}</p>')
    if _notes(extra):
        out.append('<div class="notes"><strong>Export notes</strong><ul>'
                   + "".join(f"<li>{_e(n)}</li>" for n in _notes(extra)) + "</ul></div>")
    out.append("<dl>")
    for label, value in _meta(ticket, extra):
        if label == "URL" and _safe_href(value):
            value_html = f'<a href="{_safe_href(value)}">{_e(value)}</a>'
        else:
            value_html = _e(value)
        out.append(f"<dt>{_e(label)}</dt><dd>{value_html}</dd>")
    out.append("</dl>")

    fields = [f for f in ticket.get("custom_fields") or [] if _has_value(f)]
    if fields:
        out += ["<h2>Custom fields</h2>", "<dl>"]
        for f in fields:
            label = f.get("title") or f"Field {f.get('id')}"
            out.append(f"<dt>{_e(label)}</dt><dd>{_e(display_value(f))}</dd>")
        out.append("</dl>")

    if ticket.get("tags"):
        out.append("<p>" + "".join(f'<span class="tag">{_e(t)}</span>' for t in ticket["tags"]) + "</p>")

    out.append(f"<h2>Conversation ({len(comments)})</h2>")
    if not comments:
        out.append(f'<div class="comment"><div class="body">{_e(ticket.get("description") or "(no comments)")}</div></div>')
    for i, c in enumerate(comments, 1):
        who = c.get("author_name") or f"user {c.get('author_id')}"
        kind = "Public reply" if c.get("public") else "Internal note"
        channel = (c.get("via") or {}).get("channel")
        via = f" via {channel}" if channel else ""
        cls = "comment" if c.get("public") else "comment note"
        recipients = _recipient_line(c)
        recipients_html = f"<header>{_e(recipients)}</header>" if recipients else ""
        rich = _rich_body(c, extra)
        rich_html = richtext.to_html(rich) if rich else ""
        body_html = (
            f'<div class="body rich">{rich_html}</div>' if rich_html
            else f'<div class="body">{_e(_comment_body(c) or "(empty)")}</div>'
        )
        out.append(
            f'<div class="{cls}"><header>{i}. <strong>{_e(who)}</strong> — {_e(kind)}{_e(via)}'
            f" · {_e(c.get('created_at'))}</header>{recipients_html}{body_html}"
        )
        atts = _attachments(c)
        if atts:
            items = []
            for a in atts:
                href = _safe_href(a.get("content_url"))
                label = _e(a.get("file_name"))
                items.append(f'<li><a href="{href}">{label}</a></li>' if href else f"<li>{label}</li>")
            out.append("<ul>" + "".join(items) + "</ul>")
        out.append("</div>")
    out.append("</body></html>")
    return "\n".join(out) + "\n"
