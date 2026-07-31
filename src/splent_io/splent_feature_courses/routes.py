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
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

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

from splent_framework.markdown import render_markdown

from splent_io.splent_feature_courses import (
    category_url,
    course_url,
    courses_bp,
)
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


# The dialect, the code highlighting and the allowlist a stored body is cut
# down to all live in splent_framework.markdown now. They were here,
# privately, which meant the next feature to hold written material would
# either copy them or do without, and what would have been copied is a
# security decision rather than a convenience.


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


def _external_links_open_beside(default: bool = True) -> bool:
    """Whether a link out of the wiki opens in a tab of its own.

    True by default, because that is what a wiki is: a reader following a
    reference to a Vagrant manual is not leaving the lab script, they are
    checking something, and taking the page away from them loses their
    place in a document they were halfway through.

    Read from the admin's settings, so a product can decide otherwise
    without a redeploy. Read per request rather than cached because a
    setting changed in the panel has to take effect on the next page, and
    a wrong answer here is a link that behaves oddly, not a broken page:
    when the settings feature is not installed the default stands.
    """
    try:
        value = service_proxy("SettingsService").get(
            "courses_external_links_new_tab", None
        )
    except Exception:
        return default
    if value in (None, ""):
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def _diff_lines(before: str, after: str) -> list[dict]:
    """The two versions lined up, so a person can see what moved.

    difflib from the standard library rather than a dependency, and line by
    line rather than word by word: prose is edited a paragraph at a time,
    and a word-level diff of a rewritten paragraph is noise pretending to
    be precision.

    Each entry is {kind, text} with kind in {same, added, removed}. The
    template decides how that looks; this only decides what changed.
    """
    import difflib

    rows = []
    matcher = difflib.SequenceMatcher(
        None, before.splitlines(), after.splitlines(), autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for line in before.splitlines()[i1:i2]:
                rows.append({"kind": "same", "text": line})
        else:
            for line in before.splitlines()[i1:i2]:
                rows.append({"kind": "removed", "text": line})
            for line in after.splitlines()[j1:j2]:
                rows.append({"kind": "added", "text": line})
    return rows


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


def _trail(course=None, category=None, page=None) -> list[dict]:
    """Where the reader is, for the shell's breadcrumb.

    A plain list of {"label", "url"}, which is all the theme asks for, so
    this feature needs to import nothing to be placed in it. The last
    entry is where they are and carries no link.

    A page's category is included when it has one, because a reader who
    landed from a search has not walked the hierarchy and the trail is the
    only thing telling them what this page belongs to.
    """
    trail = [{"label": _("Courses"), "url": f"/{current_app.config['COURSES_PATH']}"}]
    if course is not None:
        trail.append({"label": course.name, "url": course_url(course)})
    if category is not None:
        trail.append({"label": category.name, "url": category_url(course, category)})
    if page is not None:
        trail.append({"label": page.name, "url": None})
    if trail:
        trail[-1] = {"label": trail[-1]["label"], "url": None}
    return trail


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
        breadcrumb=_trail(course),
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
        breadcrumb=_trail(course, category),
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
        body_html=render_markdown(
            page.body_md,
            external_links_new_tab=_external_links_open_beside(),
        ),
        attachments=_attachment_links(page),
        breadcrumb=_trail(course, page.category, page),
        # A page is read on its own, and a reader who finishes one has
        # nowhere to go but back. These two put the rest of the course
        # beside it: everything it holds, and what moved lately. Both are
        # about the course this page belongs to, whichever page that is.
        sections=_sections(course),
        uncategorised=_uncategorised(course),
        recent=courses_service.recent_pages(course, current_user),
    )


def search():
    """Where the old search page used to be.

    This feature had its own box and its own results page, which meant two
    search inputs on every screen once a search feature existed that serves
    the whole product. One box won, the one in the header, and this URL
    stays because it is in bookmarks and in links people sent each other.

    The scope travels across: a reader who searched inside a course lands
    on the same search, still inside that course.
    """
    term = (request.args.get("q") or "").strip()
    origin = courses_service.course_by_slug(request.args.get("course") or "")
    if not courses_service.course_visible(origin, current_user):
        origin = None

    scope = request.args.get("scope") or ("course" if origin else "")
    if scope == "course" and origin is not None:
        target = origin.slug
    elif scope == "newest":
        visible = courses_service.visible_courses(current_user)
        target = visible[0].slug if visible else ""
    else:
        # "all", anything unrecognised, or no scope at all: the whole site,
        # which is what the search feature means by an empty scope.
        target = ""

    query = {"q": term} if term else {}
    if target:
        query["scope"] = target
    return redirect(f"/{current_app.config['SEARCH_PATH']}?{urlencode(query)}")


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
    # Say what came across. "Copied" on its own was true of a copy that
    # silently left every file behind, and the person reading it had no
    # reason to go and check.
    report = courses_service.last_copy_report
    flash(
        _(
            "Copied into %(name)s: %(pages)s page(s) and %(files)s file(s).",
            name=copy.name,
            pages=report["pages"],
            files=report["files"],
        ),
        "success",
    )
    if report["missing"]:
        # Named, not counted. A missing file is something somebody has to
        # go and upload again, and they need to know which.
        flash(
            _(
                "These files could not be copied and are missing from the new "
                "course: %(names)s",
                names=", ".join(report["missing"]),
            ),
            "danger",
        )
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
        # Through the service, because that is the one place that decides
        # what goes into the history. A route writing the page directly
        # would be an edit missing from it.
        kept = courses_service.save_page(
            page,
            name=form.name.data,
            body_md=form.body_md.data or "",
            author=current_user,
            category_id=form.category_id.data,
            position=form.position.data or 0,
        )
        _apply_visibility(page, form)
        if kept:
            flash(
                _(
                    "Saved %(name)s. The previous version is kept in the history.",
                    name=page.name,
                ),
                "success",
            )
        else:
            flash(_("Saved %(name)s.", name=page.name), "success")
        return redirect(url_for("courses.admin_page_edit", page_id=page.id))

    return _page_form(form, course, page)


@courses_bp.route("/admin/courses/preview", methods=["POST"])
@role_required(*STAFF_ROLES)
def admin_preview():
    """The body being typed, rendered exactly as a reader will be served it.

    This is what makes the live preview honest. The editor's bundled
    previewer renders markdown in the browser with its own library: no
    Pygments, none of the plugins, a different sanitiser, so what it showed
    was almost what would be published, and "almost" is what an editor
    cannot check against. This endpoint runs the very same render_markdown
    call as the public page, external-link policy included.

    CSRF is validated from the header even though nothing here is stored:
    the response is trusted markup put straight into the editor's DOM, and
    an endpoint that renders attacker-supplied markdown on demand should
    not be callable cross-site.
    """
    # Same CSRF policy as every form on this screen: enforced normally,
    # skipped when the app's config turns it off, which is what the test
    # configuration does for validate_on_submit too.
    if current_app.config.get("WTF_CSRF_ENABLED", True):
        from flask_wtf.csrf import validate_csrf

        try:
            validate_csrf(request.headers.get("X-CSRFToken", ""))
        except Exception:
            abort(400)
    return render_markdown(
        request.form.get("body_md") or "",
        external_links_new_tab=_external_links_open_beside(),
    )


@courses_bp.route("/admin/courses/pages")
@role_required(*STAFF_ROLES)
def admin_pages():
    """Every page in the wiki, flat, filtered, newest edit first.

    The screen that was missing. Until now a page could only be reached by
    remembering which of fourteen years it was under and clicking through,
    which is the friction that makes staff stop editing.
    """
    term = (request.args.get("q") or "").strip()
    course_id = request.args.get("course", type=int)
    return render_template(
        "courses/admin/pages.html",
        pages=courses_service.pages.search_all(term=term, course_id=course_id),
        courses=courses_service.visible_courses(current_user),
        term=term,
        course_id=course_id,
    )


@courses_bp.route("/admin/courses/activity")
@role_required(*STAFF_ROLES)
def admin_activity():
    """What has been edited lately, anywhere in the wiki.

    Two people preparing the same course in the same week is the ordinary
    case here, and until now neither could see what the other had touched.
    """
    return render_template(
        "courses/admin/activity.html",
        revisions=courses_service.wiki.recent_revisions(),
    )


@courses_bp.route("/admin/courses/files")
@role_required(*STAFF_ROLES)
def admin_files():
    """Every file in the wiki, with the page it hangs off.

    Files inherit their page's visibility, so the state shown here is the
    page's: a person asking "is this exam downloadable yet" is asking about
    the page, and answering with the file's own row would be answering a
    different question.
    """
    course_id = request.args.get("course", type=int)
    kind = request.args.get("kind") or None
    attachments = courses_service.wiki.all_attachments(course_id=course_id, kind=kind)
    return render_template(
        "courses/admin/files.html",
        attachments=attachments,
        courses=courses_service.visible_courses(current_user),
        course_id=course_id,
        kind=kind,
        confirm_form=ConfirmForm(),
    )


@courses_bp.route("/admin/courses/pages/<int:page_id>/history")
@role_required(*STAFF_ROLES)
def admin_page_history(page_id):
    """Every version this page has had, newest first."""
    page = _page_or_404(page_id)
    revisions = courses_service.page_history(page)
    return render_template(
        "courses/admin/page_history.html",
        course=page.course,
        page=page,
        revisions=revisions,
        confirm_form=ConfirmForm(),
    )


@courses_bp.route("/admin/courses/pages/<int:page_id>/history/<int:revision_id>")
@role_required(*STAFF_ROLES)
def admin_page_revision(page_id, revision_id):
    """One old version, rendered, beside what the page says now.

    Rendered rather than shown as markdown because the question a person
    asks here is "what did the page look like", and reading a diff of
    markdown to answer that is work the screen should have done.
    """
    page = _page_or_404(page_id)
    revision = courses_service.revisions.get_for_page(page.id, revision_id)
    if revision is None:
        abort(404)
    return render_template(
        "courses/admin/page_revision.html",
        course=page.course,
        page=page,
        revision=revision,
        diff=_diff_lines(revision.body_md or "", page.body_md or ""),
        revision_html=render_markdown(
            revision.body_md or "",
            external_links_new_tab=_external_links_open_beside(),
        ),
        confirm_form=ConfirmForm(),
    )


@courses_bp.route(
    "/admin/courses/pages/<int:page_id>/history/<int:revision_id>/restore",
    methods=["POST"],
)
@role_required(*STAFF_ROLES)
def admin_page_restore(page_id, revision_id):
    """Put an old version back. What the page says now is archived first."""
    page = _page_or_404(page_id)
    revision = courses_service.revisions.get_for_page(page.id, revision_id)
    if revision is None:
        abort(404)
    form = ConfirmForm()
    if not form.validate_on_submit():
        abort(400)
    courses_service.restore_revision(page, revision, author=current_user)
    flash(
        _(
            "Restored the version from %(moment)s. What the page said before "
            "is kept in the history.",
            moment=_format_local(revision.created_at),
        ),
        "success",
    )
    return redirect(url_for("courses.admin_page_edit", page_id=page.id))


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
    # Back where the person was. An allowlist of this feature's own screens
    # rather than a next= URL or the Referer header: both of those are an
    # open redirect waiting to be found, and there are exactly two places
    # a file can be removed from.
    if request.form.get("back") == "files":
        return redirect(url_for("courses.admin_files"))
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
    # The preview shows what a reader will see, links included: an editor
    # checking a reference should find out here that it opens beside the
    # page rather than after publishing it.
    preview_html = (
        render_markdown(
            form.body_md.data,
            external_links_new_tab=_external_links_open_beside(),
        )
        if form.preview.data
        else None
    )
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
