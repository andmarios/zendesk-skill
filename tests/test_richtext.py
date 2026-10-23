"""Formatted comment bodies (html_body): readable output, and safe by construction."""

import re

import pytest

from zendesk_skill import richtext

RICH = (
    '<div class="zd-comment" dir="auto"><p>Hi <strong>Rob</strong>,</p>'
    '<p>See <a href="https://x.test/a_b?q=(1)">the <em>doc</em></a> &amp; <a href="javascript:alert(1)">bad</a></p>'
    "<ul><li>one</li><li>two<ul><li>nested</li></ul></li></ul><ol><li>first</li><li>second</li></ol>"
    "<h2>Heading</h2><blockquote><p>quoted <br>line</p></blockquote><pre>  code\n    block</pre>"
    "<p>inline <code>a`b</code> end</p><hr>"
    "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>x|y</td></tr></table>"
    '<script>alert(1)</script><style>p{}</style><iframe src=x>hidden</iframe>'
    '<img src="https://x.test/i.png" alt="logo" onerror="x()">'
    '<p onclick="evil()">Line one<br>Line two<br/>Line &lt;three&gt; &nbsp;done</p></div>'
)


def md(src):
    return richtext.to_markdown(richtext.parse(src))


def html_(src):
    return richtext.to_html(richtext.parse(src))


# ------------------------------------------------------------ readability


def test_markdown_keeps_structure():
    out = md(RICH)
    assert "Hi **Rob**," in out
    assert "[the *doc*](https://x.test/a_b?q=%281%29)" in out
    assert "- one\n- two\n  - nested" in out                  # tight nested list
    assert "1. first\n2. second" in out
    assert "**Heading**" in out
    assert "> quoted\n> line" in out
    assert "```\n  code\n    block\n```" in out                # whitespace kept in <pre>
    assert "``a`b``" in out                                    # backtick inside inline code
    assert "\n---\n" in out
    assert "| A | B |\n| --- | --- |\n| 1 | x\\|y |" in out
    assert "[image: logo]" in out
    assert "Line one\nLine two\nLine \\<three\\> done" in out  # <br> becomes a line break


def test_html_keeps_structure():
    out = html_(RICH)
    for fragment in ("<strong>Rob</strong>", "<ul><li>one</li><li>two<ul><li>nested</li></ul></li></ul>",
                     "<ol><li>first</li>", "<blockquote>", "<pre>  code\n    block</pre>", "<hr>",
                     "<table>", "<th>A</th>", "Line one<br>Line two<br>Line &lt;three&gt;"):
        assert fragment in out, fragment
    assert '<a href="https://x.test/a_b?q=(1)" rel="noopener noreferrer"><em>' not in out
    assert 'href="https://x.test/a_b?q=(1)" rel="noopener noreferrer">the <em>doc</em></a>' in out


def test_html_whitespace_semantics():
    # source newlines/indentation are not line breaks in HTML; <br> and block tags are
    assert md("<p>a\n   b</p><p>c</p>") == "a b\n\nc"


# ------------------------------------------------------------------ safety


HOSTILE = [
    '<script>alert(1)</script>x', '<style>*{}</style>x', '<iframe src="javascript:alert(1)">x</iframe>',
    '<object data=x>x</object>', '<svg onload=alert(1)><circle/></svg>x',
    '<img src=x onerror=alert(1)>', '<p onclick="alert(1)" style="x:y" class="c" id="i">x</p>',
    '<a href="javascript:alert(1)">x</a>', '<a href="JaVaScRiPt:alert(1)">x</a>',
    '<a href="data:text/html,<script>alert(1)</script>">x</a>', '<a href="vbscript:x">x</a>',
    '<a href="https://x.test/" onmouseover="alert(1)">x</a>', '<a href=\'https://x.test/"onmouseover="a\'>x</a>',
    '<form action=x><input name=a></form>x', '<math><mi>x</mi></math>', '<template><script>1</script></template>x',
    '<!-- <script>alert(1)</script> -->x', '<![CDATA[<script>]]>x', '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>',
]


@pytest.mark.parametrize("src", HOSTILE)
def test_output_never_contains_active_markup(src):
    out = html_(src)
    assert not re.search(r"<(script|style|iframe|object|svg|form|input|math|template|img)\b", out, re.I), out
    assert not re.search(r"\son\w+\s*=", out, re.I), out        # no event-handler attributes
    assert not re.search(r"href=\"(?!https?://|mailto:)", out, re.I), out
    assert "style=" not in out and "class=" not in out and "id=" not in out
    # attribute breakout: every href value is fully quoted/escaped
    for href in re.findall(r'href="([^"]*)"', out):
        assert '"' not in href and "<" not in href

    mdout = md(src)
    assert not re.search(r"(?<!\\)<", mdout), mdout             # no unescaped '<' (raw HTML) in Markdown
    assert "javascript:" not in mdout.lower() and "vbscript:" not in mdout.lower() and "data:text" not in mdout.lower()


def test_escaped_markup_in_text_stays_text():
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")
    assert md("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>") == "\\<script\\>alert(1)\\</script\\>"


def test_markdown_syntax_in_text_is_escaped():
    out = md("<p>*not bold* _not em_ [not](a) `no` # nope\n- no</p>")
    assert out == "\\*not bold\\* \\_not em\\_ \\[not\\](a) \\`no\\` # nope - no"
    assert md("<p># heading?</p><p>- item?</p><p>1. one?</p>") == "\\# heading?\n\n\\- item?\n\n\\1. one?"


def test_link_urls_cannot_break_out_of_markdown_links():
    out = md('<a href="https://x.test/a b)(<>&quot;">t</a>')
    assert out == "[t](https://x.test/a%20b%29%28%3C%3E%22)"


# ----------------------------------------------- robustness / bounded cost


def test_deep_nesting_is_bounded_and_does_not_recurse():
    deep = "<div>" * 20_000 + "bottom" + "</div>" * 20_000   # under the size cap, far over MAX_DEPTH
    assert md(deep) == "bottom" and "bottom" in html_(deep)
    nested_lists = "<ul><li>" * 5_000 + "x" + "</li></ul>" * 5_000
    assert "x" in md(nested_lists)
    assert "x" in html_(nested_lists)


def test_unmatched_end_tags_do_not_go_quadratic():
    import time
    start = time.perf_counter()
    richtext.parse("<div>" * 20_000 + "</span>" * 20_000 + "x")
    assert time.perf_counter() - start < 3


def test_unclosed_and_misnested_tags_are_tolerated():
    out = md("<p><strong>bold <em>both</strong> only em</em> end")
    assert "bold" in out and "both" in out and "end" in out
    assert md("<ul><li>a<li>b</ul>").count("- ") >= 1 and "b" in md("<ul><li>a<li>b</ul>")


def test_unclosed_script_drops_the_rest_safely():
    assert "alert" not in html_("<p>before</p><script>alert(1)") and "before" in html_("<p>before</p><script>alert(1)")


def test_too_large_or_empty_bodies_fall_back():
    assert richtext.parse("") is None
    assert richtext.parse("x" * (richtext.MAX_HTML_CHARS + 1)) is None
