"""Turning a BookStack page written in the visual editor into markdown.

BookStack offers two editors and keeps a column for each. A page written in
the markdown editor has its source in ``markdown``; a page written in the
WYSIWYG editor has only ``html``, and its ``markdown`` is the empty string.
The WYSIWYG editor is the one BookStack ships as the default, so this is not
an edge case: a wiki whose authors never changed that setting stores every
one of its pages this way.

Reading only ``markdown`` therefore imports those pages as blank, and says
so in a way that reads like a fact about the source rather than a failure to
read it: "has an empty body in the source". EGC 2026/2027 came across as
eleven empty pages that way, 169 KB of a teacher's material for the course
starting in September, and every one of them looked like a page nobody had
written yet.
"""

from __future__ import annotations

import re


# BookStack stamps an id on nearly every block so its editor can anchor to
# it. They mean nothing outside that editor and would otherwise survive into
# the markdown as raw HTML.
_BOOKSTACK_ANCHOR = re.compile(r'\s+id="bkmrk-[^"]*"')

# What a code block declares itself to be, kept so the reader gets the
# highlighting the author chose.
_LANGUAGE = re.compile(r"language-([A-Za-z0-9_+-]+)")

# Elements whose text is not prose. They have to go before the conversion
# rather than during it: markdownify's ``strip`` drops the tag and keeps
# what is inside, which turns a stylesheet into a paragraph.
_NOT_PROSE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)


def _language_of(element) -> str:
    """The language a ``<pre>`` block declares, lowercased.

    BookStack writes it on the inner ``<code>`` as ``language-bash``. The
    same wiki spells it both ``yaml`` and ``YAML``, and a fence is matched
    against a lowercase name, so it is normalised here rather than left to
    produce two different results for the same language.
    """
    for node in (element, *element.find_all("code")):
        for value in node.get("class", []) or []:
            found = _LANGUAGE.match(value)
            if found:
                return found.group(1).lower()
    return ""


def html_to_markdown(html: str) -> str:
    """Convert a BookStack HTML body to markdown.

    Fenced code blocks keep their language, tables stay tables, and the
    non-breaking spaces the visual editor sprinkles through its output
    become ordinary spaces: they are invisible in a rendered page but show
    up as a literal "&nbsp;" wherever the text is used as text, which is
    what a search result extract is.
    """
    if not html or not html.strip():
        return ""

    from markdownify import markdownify

    cleaned = _NOT_PROSE.sub("", html)
    cleaned = _BOOKSTACK_ANCHOR.sub("", cleaned)
    # The column stores escaped newlines rather than real ones.
    cleaned = cleaned.replace("\\n", "\n")

    text = markdownify(
        cleaned,
        heading_style="ATX",
        code_language_callback=_language_of,
        bullets="-",
    )

    # markdownify leaves the entity decoded as U+00A0. It renders as a space
    # and reads as one, but it is not one, and it breaks a line that a
    # reader would expect to wrap.
    text = text.replace(" ", " ")
    # Three or more blank lines say nothing that two do not.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def body_of(row: dict) -> tuple[str, bool]:
    """The markdown for a source page, and whether it had to be converted.

    ``markdown`` wins whenever it holds anything, because it is what the
    author actually wrote. The conversion is the fallback, not the
    preference: a round trip through HTML loses the shape of the source
    even when it keeps every word.
    """
    markdown = (row.get("markdown") or "").strip()
    if markdown:
        # Same normalisation as the converted path. These arrive from
        # BookStack already carrying undecoded entities where somebody
        # pasted rich text into the markdown editor.
        return (row["markdown"].replace("&nbsp;", " ").replace(" ", " "), False)

    html = row.get("html") or ""
    if not html.strip():
        return ("", False)

    return (html_to_markdown(html), True)
