"""Render a full Zendesk ticket (metadata, custom fields, conversation) as Markdown or HTML.

Pure functions: they take already-fetched, already-annotated API data and return
a string. All ticket content is customer-controlled, so the HTML renderer escapes
everything and never emits the original comment HTML.
"""

import html
from typing import Any

FORMATS = {"md": "markdown", "markdown": "markdown", "html": "html"}


def normalize_format(fmt: str) -> str:
    """Map a user-supplied format name to 'markdown' or 'html'."""
    try:
        return FORMATS[fmt.strip().lower()]
    except KeyError:
        raise ValueError(f"Unsupported format: {fmt!r} (use 'md' or 'html')")


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
    return (comment.get("plain_body") or comment.get("body") or "").strip()


def _meta(ticket: dict, extra: dict) -> list[tuple[str, str]]:
    def person(name_key: str, id_key: str) -> str:
        name, uid = ticket.get(name_key), ticket.get(id_key)
        if uid is None:
            return ""
        return f"{name} ({uid})" if name else str(uid)

    rows = [
        ("Status", ticket.get("status")),
        ("Priority", ticket.get("priority")),
        ("Type", ticket.get("type")),
        ("Requester", person("requester_name", "requester_id")),
        ("Assignee", person("assignee_name", "assignee_id") or "(unassigned)"),
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


def render_markdown(ticket: dict, comments: list[dict], extra: dict | None = None) -> str:
    """Render the ticket as a Markdown document."""
    extra = extra or {}
    out = [f"# Ticket #{ticket.get('id')}: {ticket.get('subject') or '(no subject)'}", ""]

    for label, value in _meta(ticket, extra):
        out.append(f"- **{label}:** {value}")
    out.append("")

    fields = [f for f in ticket.get("custom_fields") or [] if _has_value(f)]
    if fields:
        out += ["## Custom fields", ""]
        for f in fields:
            out.append(f"- **{f.get('title') or 'Field ' + str(f.get('id'))}:** {display_value(f)}")
        out.append("")

    if ticket.get("tags"):
        out += [f"**Tags:** {', '.join(ticket['tags'])}", ""]

    out += [f"## Conversation ({len(comments)})", ""]
    if not comments:
        out += [ticket.get("description") or "(no comments)", ""]
    for i, c in enumerate(comments, 1):
        who = c.get("author_name") or f"user {c.get('author_id')}"
        kind = "Public reply" if c.get("public") else "Internal note"
        channel = (c.get("via") or {}).get("channel")
        via = f" via {channel}" if channel else ""
        out += [f"### {i}. {who} — {kind}{via}", f"*{c.get('created_at')}*", ""]
        body = _comment_body(c) or "(empty)"
        out += [f"> {line}" if line else ">" for line in body.splitlines()]
        out.append("")
        for a in _attachments(c):
            out.append(f"- Attachment: [{a.get('file_name')}]({a.get('content_url')})")
        if _attachments(c):
            out.append("")
    return "\n".join(out).rstrip() + "\n"


_CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222;line-height:1.5}
h1{font-size:1.5rem}h2{font-size:1.15rem;border-bottom:1px solid #ddd;padding-bottom:.2rem;margin-top:2rem}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.2rem 1rem}dt{font-weight:600}dd{margin:0}
.comment{border:1px solid #ddd;border-radius:6px;margin:1rem 0}
.comment.note{background:#fff8e1;border-color:#f0d98c}
.comment header{padding:.4rem .8rem;border-bottom:1px solid #eee;font-size:.9rem;color:#555}
.comment .body{padding:.6rem .8rem;white-space:pre-wrap;word-wrap:break-word}
.comment ul{margin:0;padding:.3rem 1.8rem .6rem}.tag{display:inline-block;background:#eef;border-radius:3px;padding:0 .4rem;margin-right:.3rem;font-size:.85rem}
""".strip()


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _safe_href(url: Any) -> str | None:
    url = str(url or "")
    return html.escape(url, quote=True) if url.startswith(("https://", "http://")) else None


def render_html(ticket: dict, comments: list[dict], extra: dict | None = None) -> str:
    """Render the ticket as a standalone HTML document (no scripts, everything escaped)."""
    extra = extra or {}
    title = f"Ticket #{ticket.get('id')}: {ticket.get('subject') or '(no subject)'}"
    out = [
        "<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
        f"<title>{_e(title)}</title>", f"<style>{_CSS}</style></head><body>",
        f"<h1>{_e(title)}</h1>", "<dl>",
    ]
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
        out.append(
            f'<div class="{cls}"><header>{i}. <strong>{_e(who)}</strong> — {_e(kind)}{_e(via)}'
            f" · {_e(c.get('created_at'))}</header>"
            f'<div class="body">{_e(_comment_body(c) or "(empty)")}</div>'
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
