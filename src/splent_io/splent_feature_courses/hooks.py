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

# Where the back office lives. Held as a literal as well as looked up by
# endpoint, because this hook renders on every authenticated page and a
# BuildError here would take the whole layout down rather than one link.
ADMIN_PATH = "/admin/courses"


def _admin_url() -> str:
    try:
        return url_for("courses.admin_index")
    except BuildError:
        return ADMIN_PATH


def _is_active() -> bool:
    endpoint = request.endpoint or ""
    return endpoint.startswith("courses.admin") or request.path.startswith(ADMIN_PATH)


def courses_admin_link():
    """Sidebar entry for the Courses management screen (the WP-plugin pattern).

    Only staff see it. An ordinary account reaching those screens gets a
    403, so offering the link would be an invitation to a closed door.
    """
    if not courses_service.is_staff(current_user):
        return ""
    active = "active" if _is_active() else ""
    return (
        f'<li class="sidebar-item {active}">'
        f'<a class="sidebar-link" href="{_admin_url()}">'
        '<i class="align-middle" data-feather="book-open"></i> '
        f'<span class="align-middle">{_("Courses")}</span>'
        "</a>"
        "</li>"
    )


register_template_hook("layout.authenticated_sidebar", courses_admin_link)
