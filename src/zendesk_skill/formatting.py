"""Markdown-to-HTML formatting for Zendesk write operations.

Converts Markdown content to HTML for use with Zendesk's html_body field,
which renders reliably for all author types in Agent Workspace.
"""

import html
import re

import mistune

# Zendesk's maximum content size for comments
MAX_CONTENT_SIZE = 65536  # 64KB

# Markdown renderer with hard_wrap enabled - converts single newlines to <br>
# This matches user expectations in a support context (WYSIWYG behavior)
_md = mistune.create_markdown(hard_wrap=True)

# Pattern to detect content that's already HTML: must start with an HTML tag
# (after optional whitespace). This avoids false positives from Markdown that
# merely *mentions* HTML tags in code spans or text.
_HTML_START_PATTERN = re.compile(
    r"^\s*<(?:!DOCTYPE|html|p|div|br|strong|em|ul|ol|li|h[1-6]|a|img|table|pre|code|blockquote)\b",
    re.IGNORECASE,
)

# Zendesk Agent Workspace renders H1/H2 disproportionately large in ticket
# replies. Shift all heading levels down by 1 (H1→H2, H2→H3, etc.) so that
# section headers look proportionate. Zendesk supports H1–H4 only.
_HEADING_SHIFT = 1
_MAX_HEADING = 4


def _downgrade_headings(html_content: str) -> str:
    """Shift heading levels down and add spacing for Zendesk rendering."""

    def _replace(match: re.Match) -> str:
        slash = match.group(1)
        level = min(int(match.group(2)) + _HEADING_SHIFT, _MAX_HEADING)
        if slash:
            return f"</h{level}>"
        return f'<h{level} style="margin-bottom:0.4em;">'

    return re.sub(r"<(/?)h([1-6])>", _replace, html_content)


def markdown_to_html(content: str) -> str:
    """Convert Markdown content to HTML.

    If the content already appears to be HTML (starts with an HTML tag),
    it is returned as-is to avoid double-conversion.

    Args:
        content: Markdown or HTML content.

    Returns:
        HTML string.
    """
    if not content:
        return ""

    # Pass through if content starts with an HTML tag
    if _HTML_START_PATTERN.match(content):
        return content

    return _downgrade_headings(_md(content))


def plain_text_to_html(content: str) -> str:
    """Convert plain text to HTML, preserving line breaks.

    Escapes HTML special characters and wraps text in <p> tags,
    converting newlines to <br> tags.

    Args:
        content: Plain text content.

    Returns:
        HTML string with escaped content and preserved formatting.
    """
    if not content:
        return ""

    escaped = html.escape(content)
    # Convert newlines to <br> and wrap in <p>
    paragraphs = escaped.split("\n\n")
    html_parts = []
    for para in paragraphs:
        para = para.replace("\n", "<br>")
        html_parts.append(f"<p>{para}</p>")
    return "\n".join(html_parts)


def format_for_zendesk(
    content: str,
    *,
    plain_text: bool = False,
) -> dict:
    """Convert content to a dict with html_body ready for the Zendesk API.

    Args:
        content: The content to format (Markdown by default, or plain text).
        plain_text: If True, treat content as plain text instead of Markdown.

    Returns:
        Dict like ``{"html_body": "<p>...</p>"}`` ready to merge into a
        Zendesk API comment payload.

    Raises:
        ValueError: If the converted HTML exceeds Zendesk's 64KB limit.
    """
    if plain_text:
        html_content = plain_text_to_html(content)
    else:
        html_content = markdown_to_html(content)

    if len(html_content.encode("utf-8")) > MAX_CONTENT_SIZE:
        raise ValueError(
            f"Content exceeds Zendesk's 64KB limit "
            f"({len(html_content.encode('utf-8'))} bytes after conversion)"
        )

    return {"html_body": html_content}
