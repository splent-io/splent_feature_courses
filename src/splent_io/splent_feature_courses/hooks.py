"""Template hooks for splent_feature_courses.

Puts the back office within reach of staff who are logged in. The screens
themselves live in this feature's admin routes; this is only the entry in
the sidebar the product renders on every authenticated page.
"""

from flask import request, url_for
from flask_babel import gettext as _
from flask_login import current_user
from werkzeug.routing import BuildError

from splent_framework.hooks.template_hooks import register_template_hook
from splent_framework.services.service_locator import service_proxy

courses_service = service_proxy("CoursesService")

# Where the back office lives. Held as literals as well as looked up by
# endpoint, because this hook renders on every authenticated page and a
# BuildError here would take the whole layout down rather than one link.
ADMIN_PATH = "/admin/courses"

#: The section, in the order the work happens: the years first, because
#: everything hangs off one; then the material, flat, because that is how a
#: person thinks of "the page about Vagrant" when they cannot remember
#: which year it is under; then what has been happening; then the files.
#:
#: (endpoint, fallback path, label, feather icon)
ADMIN_SECTION = (
    ("courses.admin_index", ADMIN_PATH, lambda: _("Courses"), "book-open"),
    ("courses.admin_pages", ADMIN_PATH + "/pages", lambda: _("Pages"), "file-text"),
    (
        "courses.admin_activity",
        ADMIN_PATH + "/activity",
        lambda: _("Activity"),
        "clock",
    ),
    ("courses.admin_files", ADMIN_PATH + "/files", lambda: _("Files"), "paperclip"),
)


def _url(endpoint: str, fallback: str) -> str:
    try:
        return url_for(endpoint)
    except BuildError:
        return fallback


def _is_active(endpoint: str) -> bool:
    """Whether this entry is the screen being looked at.

    Exact match rather than a prefix, so opening a page inside a course does
    not light up both "Courses" and "Pages" at once. The section itself is
    always open, which is what tells a reader where they are.
    """
    return (request.endpoint or "") == endpoint


def courses_admin_link():
    """The wiki's own section of the sidebar.

    One entry per thing a person comes here to do, rather than a single
    link into a screen they then have to navigate out of. Only staff see
    it: an ordinary account reaching these screens gets a 403, so offering
    the link would be an invitation to a closed door.
    """
    if not courses_service.is_staff(current_user):
        return ""

    items = []
    for endpoint, fallback, label, icon in ADMIN_SECTION:
        active = "active" if _is_active(endpoint) else ""
        items.append(
            f'<li class="sidebar-item {active}">'
            f'<a class="sidebar-link" href="{_url(endpoint, fallback)}">'
            f'<i class="align-middle" data-feather="{icon}"></i> '
            f'<span class="align-middle">{label()}</span>'
            "</a>"
            "</li>"
        )

    # A header above them, the same markup the shell uses for its own
    # groups, so the wiki reads as a section of the panel rather than as
    # four loose links that happen to be adjacent.
    heading = f'<li class="sidebar-header">{_("Wiki")}</li>'
    return heading + "".join(items)


register_template_hook("layout.authenticated_sidebar", courses_admin_link)


# ── The editor, on the screens that have one ─────────────────────────────
#
# The authenticated shell does not render the asset registry; only the
# theme's public layout does. So the editor reaches the back office through
# these two hooks instead, and only on the page editor: loading a markdown
# editor on a list of courses would be three hundred kilobytes for nothing.

EDITOR_ENDPOINTS = ("courses.admin_page_new", "courses.admin_page_edit")


def _on_editor_screen() -> bool:
    return (request.endpoint or "") in EDITOR_ENDPOINTS


def courses_editor_css():
    if not _on_editor_screen():
        return ""
    # The reading stylesheets come first: theme, features, skin, in the same
    # order the public shell loads them. The live preview is a piece of the
    # published page embedded in the editor, and a preview wearing admin
    # styles would be a second almost-right rendering, which is the thing
    # the server-side preview exists to end. The admin chrome survives the
    # extra stylesheets because own.css states its rules with !important.
    from splent_framework.assets.asset_registry import get_assets

    links = [f'<link rel="stylesheet" href="{href}">' for href in get_assets("css")]
    vendor = url_for("courses.assets", subfolder="vendor", filename="easymde.min.css")
    own = url_for("courses.assets", subfolder="css", filename="courses_editor.css")
    links.append(f'<link rel="stylesheet" href="{vendor}">')
    links.append(f'<link rel="stylesheet" href="{own}">')
    return "".join(links)


def courses_editor_js():
    if not _on_editor_screen():
        return ""
    vendor = url_for("courses.assets", subfolder="vendor", filename="easymde.min.js")
    own = url_for("courses.assets", subfolder="js", filename="courses_editor.js")
    # The vendored library first, then the initialiser, and neither
    # deferred: the initialiser checks readyState itself.
    return f'<script src="{vendor}"></script><script src="{own}"></script>'


register_template_hook("layout.head.css", courses_editor_css)
register_template_hook("layout.scripts", courses_editor_js)
