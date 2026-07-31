"""Point migrated bodies at the files that were migrated with them.

The import brought every attachment across and rewrote most references,
but eighteen of them across thirteen pages kept the shape the old wiki
wrote them in: an image whose target is a bare filename.

    ![](01-Decide-Install_2021.pdf "01-Decide-Install_2021.pdf")

A relative target resolves against the page's own URL, so the browser asks
for a file that was never there and the reader sees a broken image icon
where a set of slides should be, or, for a PDF, an empty bullet reading
"Presentación en pdf: .".

Nothing is missing. Every one of the eighteen names matches an attachment
on the very page that references it, so this is not a re-import: it is a
rewrite of the reference to the URL the file already answers on.

Two shapes come out, because a PDF is not a picture:

    ![](file.pdf)  ->  [file.pdf](/media/file/158)     a link to download
    ![](shot.png)  ->  ![shot.png](/media/file/507)    still an image

Written as a command rather than done by hand for the same reason the
import was: it is repeatable, it says what it changed, and it can be
inspected before it writes anything.
"""

import re

#: ``![alt](target "optional title")``. The title is why a simpler pattern
#: missed these for so long: it stops at the space and then fails to find
#: the closing bracket.
MARKDOWN_IMAGE = re.compile(
    r'!\[(?P<alt>[^\]]*)\]\(\s*(?P<target>[^)\s]+)(?:\s+"[^"]*")?\s*\)'
)

#: The same thing written as HTML, which a wiki's own editor produces.
HTML_IMAGE = re.compile(r"""<img[^>]+src=["'](?P<target>[^"']+)["'][^>]*>""", re.I)

#: Targets that are already URLs and must be left alone.
ABSOLUTE = ("http://", "https://", "/", "data:", "#", "mailto:")

#: Extensions a reader downloads rather than looks at.
DOCUMENTS = (".pdf", ".zip", ".tar", ".gz", ".doc", ".docx", ".ppt", ".pptx", ".odt")


def _is_relative(target: str) -> bool:
    return bool(target) and not target.startswith(ABSOLUTE)


def _replacement(name: str, url: str) -> str:
    """How this file should appear once it points somewhere real."""
    if name.lower().endswith(DOCUMENTS):
        # A set of slides is not an illustration. Rendered as an image it
        # is a broken icon; rendered as a link it is what the sentence
        # around it already promised.
        return f"[{name}]({url})"
    return f"![{name}]({url})"


def repair_body(body_md: str, attachments) -> tuple[str, list]:
    """Rewrite this page's relative image targets. Returns (body, changes).

    ``attachments`` is the page's own attachments; a name is only resolved
    against the page that references it, never across the wiki, because two
    courses can perfectly well each have their own ``diagrama.png`` and
    guessing between them would silently show the wrong year's material.
    """
    by_name = {a.name: a for a in attachments if a.name}
    changes = []

    def resolve(target: str):
        attachment = by_name.get(target)
        if attachment is None:
            return None
        return f"/media/file/{attachment.media_item_id}"

    def markdown(match):
        target = match.group("target")
        if not _is_relative(target):
            return match.group(0)
        url = resolve(target)
        if url is None:
            return match.group(0)
        changes.append((target, url))
        return _replacement(target, url)

    def html(match):
        target = match.group("target")
        if not _is_relative(target):
            return match.group(0)
        url = resolve(target)
        if url is None:
            return match.group(0)
        changes.append((target, url))
        return _replacement(target, url)

    body = MARKDOWN_IMAGE.sub(markdown, body_md or "")
    body = HTML_IMAGE.sub(html, body)
    return body, changes


def unresolved(body_md: str, attachments) -> list:
    """Relative targets this page has no attachment for.

    Reported rather than repaired: a reference to a file nobody migrated is
    a missing file, and inventing a URL for it would turn a visible problem
    into an invisible one.
    """
    by_name = {a.name for a in attachments if a.name}
    targets = [m.group("target") for m in MARKDOWN_IMAGE.finditer(body_md or "")]
    targets += [m.group("target") for m in HTML_IMAGE.finditer(body_md or "")]
    return [t for t in targets if _is_relative(t) and t not in by_name]
