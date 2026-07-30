"""The public reading surface and the staff back-office.

Two audiences share one set of rules. Readers get whatever the service
says is visible right now and a 404 for everything else, so a page that
has not been released is indistinguishable from one that was never
written. Staff get the same screens with the withheld items still on
them, marked, because the whole point of scheduling material is being
able to see what is queued before it goes out.

Nothing here decides visibility on its own: every branch asks
CoursesService, which is the single place the rule lives.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import nh3
from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_babel import gettext as _
from flask_login import current_user
from markdown_it import MarkdownIt
from markupsafe import Markup
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

from splent_io.splent_feature_courses import courses_bp
from splent_io.splent_feature_courses.forms import (
    AttachmentForm,
    CategoryForm,
    ConfirmForm,
    CopyCourseForm,
    CourseForm,
    NewCourseForm,
    PageForm,
)
from splent_io.splent_feature_courses.services import (
    STAFF_ROLES,
    local_timezone,
    slugify,
)
from splent_framework.decorators.decorators import role_required
from splent_framework.services.service_locator import service_proxy

courses_service = service_proxy("CoursesService")

# The search scopes a reader can pick. "course" is only offered while
# inside one; outside, the newest course is what a question is almost
# always about.
SEARCH_SCOPES = ("course", "newest", "all")


# ── Markdown ─────────────────────────────────────────────────────────────


def _build_renderer() -> MarkdownIt:
    """The markdown dialect the stored bodies are written in.

    markdown-it-py with the GFM preset, because the material being
    migrated is GitHub-flavoured markdown: tables, strikethrough and bare
    URLs already appear in it and a stricter CommonMark reader would show
    them as literal text. Raw HTML is enabled for the same reason, the old
    wiki's bodies contain it, and is made safe afterwards rather than
    dropped, which would silently lose content.

    The plugins are the four that the material actually uses: heading
    anchors so a long practical can be linked to by section, definition
    lists, footnotes and task lists.
    """
    return (
        MarkdownIt("gfm-like", {"html": True, "linkify": True, "typographer": False})
        .use(anchors_plugin, max_level=4)
        .use(deflist_plugin)
        .use(footnote_plugin)
        .use(tasklists_plugin)
    )


_MARKDOWN = _build_renderer()

# The allowlist the rendered HTML is cut down to. Built from nh3's own
# list rather than from scratch, so the day an element turns out to be
# dangerous the library takes it away here too.
_ALLOWED_TAGS = set(nh3.ALLOWED_TAGS) | {"input"}
_ALLOWED_ATTRIBUTES = {tag: set(attrs) for tag, attrs in nh3.ALLOWED_ATTRIBUTES.items()}
# id carries the heading anchors, class carries both the plugins' own
# markup and whatever the migrated bodies were styled with. Neither can
# execute anything.
_ALLOWED_ATTRIBUTES["*"] = _ALLOWED_ATTRIBUTES.get("*", set()) | {"id", "class"}
_ALLOWED_ATTRIBUTES["input"] = {"checked", "disabled"}
# A task list checkbox is the only input a body may contain; anything
# else would be a form pointing somewhere of the author's choosing.
_ALLOWED_INPUT_VALUES = {"input": {"type": {"checkbox"}}}
_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def render_markdown(body_md: str) -> Markup:
    """A stored body as HTML a reader can be shown.

    Rendered here rather than in the template because the sanitising step
    is not optional: a body is written by staff but was migrated from a
    wiki anyone with an account could edit, and marking it safe without
    cleaning it would run whatever it happens to contain.
    """
    html = _MARKDOWN.render(body_md or "")
    return Markup(
        nh3.clean(
            html,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
            tag_attribute_values=_ALLOWED_INPUT_VALUES,
            url_schemes=_ALLOWED_URL_SCHEMES,
        )
    )


# ── Release moments ──────────────────────────────────────────────────────


def _zone() -> ZoneInfo:
    return ZoneInfo(local_timezone())


def to_utc(moment: datetime | None) -> datetime | None:
    """A moment typed by staff, as the instant it will be compared against.

    A datetime-local input submits a wall clock with no zone attached:
    08:00 means 08:00 where the course is taught. Reading it as UTC would
    publish an hour or two early depending on the time of year, so the
    course's zone is attached first and the instant computed from it.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_zone())
    return moment.astimezone(timezone.utc)


def to_local(moment: datetime | None) -> datetime | None:
    """A stored moment as staff read and re-edit it.

    The tzinfo check is not defensive padding: MySQL hands back naive
    datetimes even from a timezone-aware column, and treating one of those
    as local time would move every date already saved.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_zone())


def _format_local(moment: datetime | None) -> str:
    """A release moment written out for a human, in the course's zone."""
    moment = to_local(moment)
    return moment.strftime("%Y-%m-%d %H:%M") if moment else ""


# ── Reading helpers ──────────────────────────────────────────────────────


def _visible_course_or_404(course_slug: str):
    """The course under this slug, or nothing a reader can tell apart.

    404 rather than 403 everywhere below: a withheld course must not be
    confirmed to exist by the status code of a guess.
    """
    course = courses_service.course_by_slug(course_slug)
    if not courses_service.course_visible(course, current_user):
        abort(404)
    return course


def _sections(course):
    """A course as it reads: each visible category with its visible pages."""
    return [
        (category, courses_service.visible_pages(category, current_user))
        for category in courses_service.visible_categories(course, current_user)
    ]


def _uncategorised(course):
    """Visible pages of a course that no category claims.

    Material imported from the old wiki does not always land in a
    section, and a page reachable by URL but listed nowhere is a page
    nobody finds.
    """
    return [
        page
        for page in courses_service.pages.list_for_course(course.id)
        if page.category_id is None and courses_service.page_visible(page, current_user)
    ]


def _attachment_links(page):
    """A page's visible files as (label, href) rows.

    The href is the media serving route, which asks this feature again
    before sending a byte, so a link that leaks into a chat is worth
    nothing until the page it belongs to is released.
    """
    rows = []
    for attachment in courses_service.visible_attachments(page, current_user):
        rows.append(
            {
                "label": attachment.name or _default_attachment_label(attachment),
                "href": f"/media/file/{attachment.media_item_id}",
            }
        )
    return rows


def _default_attachment_label(attachment) -> str:
    """The media library's own name for a file attached without one."""
    try:
        item = service_proxy("MediaService").get(attachment.media_item_id)
    except Exception:
        item = None
    if item is None:
        return _("Attachment")
    return item.title or item.filename


# ── Public screens ───────────────────────────────────────────────────────


def course_index():
    """Every course the reader may see, newest first."""
    return render_template(
        "courses/index.html",
        courses=courses_service.visible_courses(current_user),
    )


def course_detail(course_slug):
    course = _visible_course_or_404(course_slug)
    return render_template(
        "courses/course.html",
        course=course,
        sections=_sections(course),
        uncategorised=_uncategorised(course),
    )


def category_detail(course_slug, category_slug):
    course = _visible_course_or_404(course_slug)
    category = courses_service.category_by_slug(course, category_slug)
    if not courses_service.category_visible(category, current_user):
        abort(404)
    return render_template(
        "courses/category.html",
        course=course,
        category=category,
        pages=courses_service.visible_pages(category, current_user),
    )


def page_detail(course_slug, page_slug):
    course = _visible_course_or_404(course_slug)
    page = courses_service.page_by_slug(course, page_slug)
    if not courses_service.page_visible(page, current_user):
        abort(404)
    return render_template(
        "courses/page.html",
        course=course,
        page=page,
        body_html=render_markdown(page.body_md),
        attachments=_attachment_links(page),
    )


def search():
    """Matching pages, within the scope the reader chose.

    The scope defaults to the course the reader came from, and to the
    newest one they can see otherwise, because a question about the
    subject is nearly always a question about this year.
    """
    term = (request.args.get("q") or "").strip()
    origin = courses_service.course_by_slug(request.args.get("course") or "")
    if not courses_service.course_visible(origin, current_user):
        origin = None

    scope = request.args.get("scope") or ("course" if origin else "newest")
    if scope not in SEARCH_SCOPES or (scope == "course" and origin is None):
        scope = "newest"

    if scope == "all":
        target = None
    elif scope == "course":
        target = origin
    else:
        # The newest course this reader can see, which for staff is simply
        # the newest one. Searching a course they cannot read would return
        # nothing and look like the material had gone missing.
        visible = courses_service.visible_courses(current_user)
        target = visible[0] if visible else None

    results = courses_service.search_pages(term, target, current_user) if term else []
    return render_template(
        "courses/search.html",
        term=term,
        scope=scope,
        course=origin,
        target=target,
        results=results,
    )


def register_public_routes(state):
    """Build the reading URLs out of the product's configuration.

    The words in these paths are read for years, in slides and e-mails
    that nobody is going to reissue, so a wiki replacing an older one sets
    them to whatever it used to serve. That makes them configuration, and
    configuration is only known once the app exists, which is why these
    rules are added when the blueprint is registered rather than by a
    decorator at import time. It is the same move splent_feature_post
    makes for its permalink, from inside the blueprint because the
    feature's __init__ already has its own job.

    The Jinja filter is registered here for the same reason: it needs the
    app, and it formats release moments in the configured timezone.
    """
    app = state.app
    path = app.config["COURSES_PATH"]
    category_segment = app.config["COURSES_CATEGORY_SEGMENT"]
    page_segment = app.config["COURSES_PAGE_SEGMENT"]

    app.jinja_env.filters["course_datetime"] = _format_local

    # A static rule wins over a dynamic one in Werkzeug, so this reserves
    # the word from the course slugs. Worth it: the search box appears on
    # every screen and its URL has to be shareable.
    state.add_url_rule(f"/{path}/search", endpoint="search", view_func=search)
    state.add_url_rule(f"/{path}", endpoint="index", view_func=course_index)
    state.add_url_rule(
        f"/{path}/<course_slug>", endpoint="course", view_func=course_detail
    )
    state.add_url_rule(
        f"/{path}/<course_slug>/{category_segment}/<category_slug>",
        endpoint="category",
        view_func=category_detail,
    )
    state.add_url_rule(
        f"/{path}/<course_slug>/{page_segment}/<page_slug>",
        endpoint="page",
        view_func=page_detail,
    )

    # The wiki is the product, so the list of courses is its front page.
    # Claimed only if nothing else has: a product that also installs a
    # landing feature meant that feature's home page.
    if not any(rule.rule == "/" for rule in app.url_map.iter_rules()):
        state.add_url_rule("/", endpoint="home", view_func=course_index)


# Deferred until the blueprint is registered, which is when app.config
# has been through this feature's inject_config.
courses_bp.record_once(register_public_routes)


# ── Back-office ──────────────────────────────────────────────────────────
#
# Screens of its own rather than fields bolted onto a generic editor: a
# course, a section and a page are different things to release and are
# edited on different days.


def _course_or_404(course_id: int):
    course = courses_service.get_by_id(course_id)
    if course is None:
        abort(404)
    return course


def _category_or_404(category_id: int):
    category = courses_service.categories.get_by_id(category_id)
    if category is None:
        abort(404)
    return category


def _page_or_404(page_id: int):
    page = courses_service.pages.get_by_id(page_id)
    if page is None:
        abort(404)
    return page


def _apply_visibility(item, form):
    """Persist the two controls exactly as the form describes them.

    publish_at is always passed, never omitted, so clearing the field
    clears the date instead of quietly leaving the old one in place.
    """
    courses_service.set_visibility(
        item, hidden=bool(form.hidden.data), publish_at=to_utc(form.publish_at.data)
    )


def _load_visibility(form, item):
    """Show the stored moment back as the wall clock it was typed as."""
    form.hidden.data = item.hidden
    form.publish_at.data = to_local(item.publish_at)


def _category_choices(course):
    """The categories a page can be filed under, plus none of them."""
    choices = [("", _("No category"))]
    choices += [
        (str(category.id), category.name)
        for category in courses_service.categories.list_for_course(course.id)
    ]
    return choices


@courses_bp.route("/admin/courses", methods=["GET"])
@role_required(*STAFF_ROLES)
def admin_index():
    """Every academic year, withheld ones included, newest first.

    The same call the public listing makes: staff see everything through
    it, so there is no second listing that could disagree about what
    exists.
    """
    return render_template(
        "courses/admin/courses.html",
        courses=courses_service.visible_courses(current_user),
        copy_form=CopyCourseForm(),
        confirm_form=ConfirmForm(),
    )


@courses_bp.route("/admin/courses/new", methods=["GET", "POST"])
@role_required(*STAFF_ROLES)
def admin_course_new():
    """Start a year from the product's own conventions.

    The prefilled name and sections come from configuration, so this
    feature knows nothing about any particular subject and a wiki for
    another one is the same code with different values.
    """
    form = NewCourseForm()
    defaults = current_app.config.get("COURSES_DEFAULT_CATEGORIES") or []
    if request.method == "GET":
        form.name.data = current_app.config.get("COURSES_NAME_PREFIX", "")
        form.description.data = current_app.config.get(
            "COURSES_DESCRIPTION_TEMPLATE", ""
        )
        form.categories.data = "\n".join(defaults)

    if form.validate_on_submit():
        categories = [
            line.strip() for line in (form.categories.data or "").splitlines()
        ]
        try:
            course = courses_service.create_course(
                name=form.name.data.strip(),
                description=(form.description.data or "").strip(),
                categories=[name for name in categories if name],
                hidden=bool(form.hidden.data),
            )
        except ValueError as error:
            flash(str(error), "danger")
        else:
            flash(_("Created %(name)s.", name=course.name), "success")
            return redirect(url_for("courses.admin_course_edit", course_id=course.id))

    return render_template("courses/admin/course_form.html", form=form, course=None)


@courses_bp.route("/admin/courses/<int:course_id>/edit", methods=["GET", "POST"])
@role_required(*STAFF_ROLES)
def admin_course_edit(course_id):
    """The year itself, and the tree of what is inside it."""
    course = _course_or_404(course_id)
    form = CourseForm(obj=course)
    if request.method == "GET":
        _load_visibility(form, course)

    if form.validate_on_submit():
        # The slug is left alone: renaming a course fixes how it reads,
        # it does not invalidate the links already handed out.
        courses_service.update(
            course.id,
            name=form.name.data.strip(),
            description=(form.description.data or "").strip(),
        )
        _apply_visibility(course, form)
        flash(_("Saved %(name)s.", name=course.name), "success")
        return redirect(url_for("courses.admin_course_edit", course_id=course.id))

    return render_template(
        "courses/admin/course_form.html",
        form=form,
        course=course,
        sections=[
            (category, courses_service.pages.list_for_category(category.id))
            for category in courses_service.categories.list_for_course(course.id)
        ],
        uncategorised=[
            page
            for page in courses_service.pages.list_for_course(course.id)
            if page.category_id is None
        ],
        confirm_form=ConfirmForm(),
    )


@courses_bp.route("/admin/courses/<int:course_id>/copy", methods=["POST"])
@role_required(*STAFF_ROLES)
def admin_course_copy(course_id):
    """Duplicate a year into the next one, everything withheld."""
    course = _course_or_404(course_id)
    form = CopyCourseForm()
    if not form.validate_on_submit():
        flash(_("A name is required to copy a course."), "danger")
        return redirect(url_for("courses.admin_index"))
    try:
        copy = courses_service.copy_course(course, form.name.data.strip())
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("courses.admin_index"))
    flash(_("Copied into %(name)s.", name=copy.name), "success")
    return redirect(url_for("courses.admin_course_edit", course_id=copy.id))


@courses_bp.route("/admin/courses/<int:course_id>/delete", methods=["POST"])
@role_required(*STAFF_ROLES)
def admin_course_delete(course_id):
    course = _course_or_404(course_id)
    if not ConfirmForm().validate_on_submit():
        abort(400)
    name = course.name
    courses_service.delete(course.id)
    flash(_("Removed %(name)s.", name=name), "success")
    return redirect(url_for("courses.admin_index"))


@courses_bp.route(
    "/admin/courses/<int:course_id>/categories/new", methods=["GET", "POST"]
)
@role_required(*STAFF_ROLES)
def admin_category_new(course_id):
    course = _course_or_404(course_id)
    form = CategoryForm()
    if request.method == "GET":
        form.position.data = courses_service.categories.next_position(course.id)

    if form.validate_on_submit():
        name = form.name.data.strip()
        category = courses_service.categories.create(
            course_id=course.id,
            name=name,
            slug=courses_service.unique_category_slug(course.id, slugify(name)),
            position=form.position.data or 0,
            hidden=bool(form.hidden.data),
            publish_at=to_utc(form.publish_at.data),
        )
        flash(_("Added %(name)s.", name=category.name), "success")
        return redirect(url_for("courses.admin_course_edit", course_id=course.id))

    return render_template(
        "courses/admin/category_form.html", form=form, course=course, category=None
    )


@courses_bp.route(
    "/admin/courses/categories/<int:category_id>/edit", methods=["GET", "POST"]
)
@role_required(*STAFF_ROLES)
def admin_category_edit(category_id):
    category = _category_or_404(category_id)
    form = CategoryForm(obj=category)
    if request.method == "GET":
        _load_visibility(form, category)

    if form.validate_on_submit():
        courses_service.categories.update(
            category.id,
            name=form.name.data.strip(),
            position=form.position.data or 0,
        )
        _apply_visibility(category, form)
        flash(_("Saved %(name)s.", name=category.name), "success")
        return redirect(
            url_for("courses.admin_course_edit", course_id=category.course_id)
        )

    return render_template(
        "courses/admin/category_form.html",
        form=form,
        course=category.course,
        category=category,
    )


@courses_bp.route(
    "/admin/courses/categories/<int:category_id>/delete", methods=["POST"]
)
@role_required(*STAFF_ROLES)
def admin_category_delete(category_id):
    category = _category_or_404(category_id)
    if not ConfirmForm().validate_on_submit():
        abort(400)
    course_id, name = category.course_id, category.name
    courses_service.categories.delete(category.id)
    flash(_("Removed %(name)s.", name=name), "success")
    return redirect(url_for("courses.admin_course_edit", course_id=course_id))


@courses_bp.route("/admin/courses/<int:course_id>/pages/new", methods=["GET", "POST"])
@role_required(*STAFF_ROLES)
def admin_page_new(course_id):
    course = _course_or_404(course_id)
    form = PageForm()
    form.category_id.choices = _category_choices(course)
    if request.method == "GET":
        form.category_id.data = request.args.get("category", type=int)

    if form.preview.data:
        return _page_form(form, course, None)

    if form.validate_on_submit():
        name = form.name.data.strip()
        category_id = form.category_id.data
        page = courses_service.pages.create(
            course_id=course.id,
            category_id=category_id,
            name=name,
            slug=courses_service.unique_page_slug(course.id, slugify(name)),
            body_md=form.body_md.data or "",
            position=(
                form.position.data
                if form.position.data is not None
                else courses_service.pages.next_position(category_id)
            ),
            hidden=bool(form.hidden.data),
            publish_at=to_utc(form.publish_at.data),
        )
        flash(_("Added %(name)s.", name=page.name), "success")
        return redirect(url_for("courses.admin_page_edit", page_id=page.id))

    return _page_form(form, course, None)


@courses_bp.route("/admin/courses/pages/<int:page_id>/edit", methods=["GET", "POST"])
@role_required(*STAFF_ROLES)
def admin_page_edit(page_id):
    page = _page_or_404(page_id)
    course = page.course
    form = PageForm(obj=page)
    form.category_id.choices = _category_choices(course)
    if request.method == "GET":
        form.category_id.data = page.category_id
        _load_visibility(form, page)

    if form.preview.data:
        return _page_form(form, course, page)

    if form.validate_on_submit():
        # The slug stays as it was for the same reason a course's does:
        # a document's URL is quoted in slides that will not be reissued.
        courses_service.pages.update(
            page.id,
            category_id=form.category_id.data,
            name=form.name.data.strip(),
            body_md=form.body_md.data or "",
            position=form.position.data or 0,
        )
        _apply_visibility(page, form)
        flash(_("Saved %(name)s.", name=page.name), "success")
        return redirect(url_for("courses.admin_page_edit", page_id=page.id))

    return _page_form(form, course, page)


@courses_bp.route("/admin/courses/pages/<int:page_id>/attach", methods=["POST"])
@role_required(*STAFF_ROLES)
def admin_page_attach(page_id):
    """Hang a file off a page, withheld for as long as the page is.

    The bytes go to the media library as a restricted item owned by this
    feature, so serving them asks whether the page is visible. Nothing
    here has to remember to protect the file.
    """
    page = _page_or_404(page_id)
    form = AttachmentForm()
    if not form.validate_on_submit():
        flash(_("Choose a file to upload."), "warning")
        return redirect(url_for("courses.admin_page_edit", page_id=page.id))

    attachment = courses_service.attach_file(
        page, form.file.data, name=(form.name.data or "").strip()
    )
    if attachment is None:
        flash(_("That file could not be stored."), "danger")
    else:
        flash(_("Attached %(name)s.", name=attachment.name), "success")
    return redirect(url_for("courses.admin_page_edit", page_id=page.id))


@courses_bp.route(
    "/admin/courses/attachments/<int:attachment_id>/detach", methods=["POST"]
)
@role_required(*STAFF_ROLES)
def admin_page_detach(attachment_id):
    """Remove a file from a page, and the stored bytes with it."""
    attachment = courses_service.attachments.get_by_id(attachment_id)
    if attachment is None:
        abort(404)
    if not ConfirmForm().validate_on_submit():
        abort(400)
    page_id, name = attachment.page_id, attachment.name
    courses_service.detach_file(attachment)
    flash(_("Removed %(name)s.", name=name), "success")
    return redirect(url_for("courses.admin_page_edit", page_id=page_id))


@courses_bp.route("/admin/courses/pages/<int:page_id>/delete", methods=["POST"])
@role_required(*STAFF_ROLES)
def admin_page_delete(page_id):
    page = _page_or_404(page_id)
    if not ConfirmForm().validate_on_submit():
        abort(400)
    course_id, name = page.course_id, page.name
    courses_service.pages.delete(page.id)
    flash(_("Removed %(name)s.", name=name), "success")
    return redirect(url_for("courses.admin_course_edit", course_id=course_id))


def _page_form(form, course, page):
    """The page editor, with the preview filled in when it was asked for.

    Previewing renders the body through the same helper the public page
    uses, so what staff approve is what readers get. It is a second submit
    button rather than a script: there is no editor to load, from a CDN or
    otherwise, and the preview keeps working with the textarea a browser
    already provides.
    """
    preview_html = render_markdown(form.body_md.data) if form.preview.data else None
    return render_template(
        "courses/admin/page_form.html",
        form=form,
        course=course,
        page=page,
        preview_html=preview_html,
        attachment_form=AttachmentForm(),
        confirm_form=ConfirmForm(),
        # The full list of files, not the reader's view: staff manage the
        # files of a page that is still withheld, which is the normal case
        # while next week's session is being prepared. Embedded images are
        # not files and are managed from the body that references them.
        attachments=(
            courses_service.attachments.list_for_page(page.id) if page else []
        ),
    )
