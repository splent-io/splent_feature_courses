from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

from flask import current_app

from splent_io.splent_feature_courses.models import (
    KIND_FILE,
    Category,
    Course,
    Page,
    PageAttachment,
)
from splent_io.splent_feature_courses.repositories import (
    CategoryRepository,
    CourseRepository,
    PageAttachmentRepository,
    PageRepository,
    PageRevisionRepository,
    WikiRepository,
)
from splent_framework.db import db
from splent_framework.services.BaseService import BaseService

STAFF_ROLES = ("admin", "staff")

# Sentinel so that clearing a release date is expressible: passing None
# means "no date", omitting the argument means "leave what is there".
KEEP = object()


def slugify(value: str) -> str:
    """A URL segment from a title, accents folded away."""
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "untitled"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_timezone() -> str:
    """The timezone staff type release dates in.

    Moments are stored in UTC, but a lab at 08:00 means 08:00 where the
    course is taught, and getting that wrong releases material an hour
    early for half the year.
    """
    return current_app.config.get("COURSES_TIMEZONE") or "Europe/Madrid"


class CoursesService(BaseService):
    """Reading and editing course material, and who may see what.

    Visibility is answered here rather than inside the queries, so there is
    one rule applied the same way by pages, listings, attachments and
    search. Nothing runs on a schedule: material is visible when a request
    happens to arrive after its release moment.
    """

    def __init__(self):
        super().__init__(CourseRepository())
        self.categories = CategoryRepository()
        self.pages = PageRepository()
        self.attachments = PageAttachmentRepository()
        self.revisions = PageRevisionRepository()
        self.wiki = WikiRepository()

    # ── Who is asking ────────────────────────────────────────────────────

    def is_staff(self, user) -> bool:
        """Staff read everything, including what is still withheld."""
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if not getattr(user, "active", False):
            return False
        return getattr(user, "role", None) in STAFF_ROLES

    # ── Visibility ───────────────────────────────────────────────────────

    def course_visible(self, course: Course, user=None, now=None) -> bool:
        if course is None:
            return False
        if self.is_staff(user):
            return True
        return course.is_released(now or _utcnow())

    def category_visible(self, category: Category, user=None, now=None) -> bool:
        """A category withheld by its course is withheld too.

        Sensitive material is scheduled at course or category level for
        exactly this reason: the whole branch travels with it.
        """
        if category is None:
            return False
        if self.is_staff(user):
            return True
        now = now or _utcnow()
        return category.is_released(now) and category.course.is_released(now)

    def page_visible(self, page: Page, user=None, now=None) -> bool:
        if page is None:
            return False
        if self.is_staff(user):
            return True
        now = now or _utcnow()
        if not page.is_released(now) or not page.course.is_released(now):
            return False
        if page.category is not None and not page.category.is_released(now):
            return False
        return True

    def visible_courses(self, user=None, now=None) -> list[Course]:
        now = now or _utcnow()
        return [
            course
            for course in self.repository.list_newest_first()
            if self.course_visible(course, user, now)
        ]

    def visible_categories(self, course: Course, user=None, now=None) -> list[Category]:
        now = now or _utcnow()
        return [
            category
            for category in self.categories.list_for_course(course.id)
            if self.category_visible(category, user, now)
        ]

    def visible_pages(self, category: Category, user=None, now=None) -> list[Page]:
        now = now or _utcnow()
        return [
            page
            for page in self.pages.list_for_category(category.id)
            if self.page_visible(page, user, now)
        ]

    def recent_pages(
        self, course: Course, user=None, limit: int = 6, now=None
    ) -> list[Page]:
        """What has changed lately in this course, as this reader sees it.

        Visibility is decided here, per page, the same way it is everywhere
        else: a page withheld until next Monday is exactly the kind of thing
        a "recently added" list would announce by name, and the title is the
        whole leak.

        More candidates are asked for than are shown, so a run of withheld
        pages at the top does not leave the list short.
        """
        now = now or _utcnow()
        recent = []
        for page in self.pages.list_recent_for_course(course.id, limit=limit * 4):
            if not self.page_visible(page, user, now):
                continue
            if page.category_id is not None and not self.category_visible(
                page.category, user, now
            ):
                # A page inside a section nobody may see yet is not visible
                # material, whatever the page itself says.
                continue
            recent.append(page)
            if len(recent) >= limit:
                break
        return recent


    # ── History ──────────────────────────────────────────────────────────
    #
    # One place decides what gets archived, and it is this one. A revision
    # written from a route would be a revision missing from every other way
    # a page can be saved, and "the history is missing the edit that broke
    # it" is the only way a history can be worse than none at all.

    #: How many revisions a page keeps. A page edited every week all year
    #: fits inside this with room to spare; a page edited daily for a
    #: decade does not, and nobody has ever asked for its four-hundredth.
    REVISION_LIMIT = 200

    def save_page(self, page: Page, *, name: str, body_md: str, author=None, **rest):
        """Save a page, keeping what it said before.

        The revision is written first and only when the writing actually
        changed. Saving a page to move it between categories, or to change
        its release date, is not an edit anybody needs to undo, and
        recording it would bury the edits that matter under noise.

        Returns True when a revision was written, so a caller can tell the
        person whether their change is recoverable.
        """
        wrote_revision = False
        new_name = (name or "").strip()
        new_body = body_md or ""
        if page.name != new_name or (page.body_md or "") != new_body:
            self.revisions.create(
                page_id=page.id,
                name=page.name,
                body_md=page.body_md or "",
                author_id=getattr(author, "id", None),
                # The address is copied rather than joined, so the history
                # still says who wrote what after the account is closed.
                author_email=getattr(author, "email", None),
            )
            wrote_revision = True

        self.pages.update(page.id, name=new_name, body_md=new_body, **rest)
        if wrote_revision:
            self.revisions.trim_to(page.id, self.REVISION_LIMIT)
        return wrote_revision

    def restore_revision(self, page: Page, revision, author=None):
        """Put an old version back, keeping the one being replaced.

        Restoring is an edit like any other: it goes through save_page, so
        what the page says right now is archived before it is overwritten
        and a restore can itself be undone. A history that lost the present
        when reaching into the past would be a trap.
        """
        return self.save_page(
            page,
            name=revision.name,
            body_md=revision.body_md or "",
            author=author,
        )

    def page_history(self, page: Page, limit: int | None = None):
        return self.revisions.list_for_page(page.id, limit=limit)

    # ── Reading ──────────────────────────────────────────────────────────

    def course_by_slug(self, slug: str) -> Course | None:
        return self.repository.get_by_slug(slug)

    def newest_course(self) -> Course | None:
        return self.repository.newest()

    def category_by_slug(self, course: Course, slug: str) -> Category | None:
        return self.categories.get_by_slug(course.id, slug)

    def page_by_slug(self, course: Course, slug: str) -> Page | None:
        return self.pages.get_by_slug(course.id, slug)

    def visible_attachments(self, page: Page, user=None, now=None) -> list:
        """A page's files, or nothing when the page itself is withheld."""
        if not self.page_visible(page, user, now):
            return []
        return self.attachments.list_for_page(page.id)

    def may_read_attachment(self, media_item_id: int, user=None, now=None) -> bool:
        """Whether these bytes may be served, asked by the media feature.

        A file no page claims is refused, which keeps the deny by default
        contract intact when an attachment row is deleted and the media
        item outlives it.
        """
        attachment = self.attachments.get_by_media_item(media_item_id)
        if attachment is None:
            return False
        return self.page_visible(attachment.page, user, now)

    def search_pages(
        self, term: str, course: Course | None = None, user=None
    ) -> list[Page]:
        """Matching pages the reader is allowed to know exist.

        Filtering after the query rather than inside it means a title or a
        snippet of withheld material cannot surface in results, which is
        how the wiki this replaces leaked its unreleased pages.
        """
        term = (term or "").strip()
        if not term:
            return []
        now = _utcnow()
        found = self.pages.search(term, course.id if course else None)
        return [page for page in found if self.page_visible(page, user, now)]

    # ── Writing ──────────────────────────────────────────────────────────

    def unique_course_slug(self, base: str) -> str:
        slug, n = base, 1
        while self.repository.slug_exists(slug):
            n += 1
            slug = f"{base}-{n}"
        return slug

    def unique_category_slug(self, course_id: int, base: str) -> str:
        slug, n = base, 1
        while self.categories.get_by_slug(course_id, slug) is not None:
            n += 1
            slug = f"{base}-{n}"
        return slug

    def unique_page_slug(self, course_id: int, base: str) -> str:
        slug, n = base, 1
        while self.pages.get_by_slug(course_id, slug) is not None:
            n += 1
            slug = f"{base}-{n}"
        return slug

    def create_course(
        self,
        name: str,
        description: str = "",
        categories: list[str] | None = None,
        hidden: bool = True,
    ) -> Course:
        """Start an academic year.

        It is born hidden. A course that appeared the moment it was created
        would publish next year's material while it is still being written,
        so staff see it, fill it, and release it when it is ready.
        """
        if self.repository.name_exists(name):
            raise ValueError(f"A course named '{name}' already exists.")

        course = Course(
            name=name,
            description=description,
            slug=self.unique_course_slug(slugify(name)),
            hidden=hidden,
        )
        db.session.add(course)
        db.session.flush()

        for position, category_name in enumerate(categories or []):
            db.session.add(
                Category(
                    course_id=course.id,
                    name=category_name,
                    slug=self.unique_category_slug(course.id, slugify(category_name)),
                    position=position,
                )
            )
        db.session.commit()
        return course

    def copy_course(self, source: Course, name: str) -> Course:
        """Duplicate a year into a new one, everything withheld.

        Around thirty pages repeat every year and copying them by hand each
        September is the chore this removes. Nothing arrives released, so
        last year's dates cannot publish this year's material.
        """
        course = self.create_course(name, source.description, hidden=True)

        for category in self.categories.list_for_course(source.id):
            copy = Category(
                course_id=course.id,
                name=category.name,
                slug=self.unique_category_slug(course.id, category.slug),
                position=category.position,
                hidden=category.hidden,
            )
            db.session.add(copy)
            db.session.flush()
            for page in self.pages.list_for_category(category.id):
                db.session.add(
                    Page(
                        course_id=course.id,
                        category_id=copy.id,
                        name=page.name,
                        slug=self.unique_page_slug(course.id, page.slug),
                        body_md=page.body_md,
                        position=page.position,
                        hidden=page.hidden,
                    )
                )
        db.session.commit()
        return course

    def create_category(self, course: Course, name: str, **fields) -> Category:
        category = Category(
            course_id=course.id,
            name=name,
            slug=self.unique_category_slug(course.id, slugify(name)),
            position=self.categories.next_position(course.id),
            **fields,
        )
        db.session.add(category)
        db.session.commit()
        return category

    def create_page(
        self, course: Course, name: str, category: Category | None = None, **fields
    ) -> Page:
        page = Page(
            course_id=course.id,
            category_id=category.id if category else None,
            name=name,
            slug=self.unique_page_slug(course.id, slugify(name)),
            position=self.pages.next_position(category.id) if category else 0,
            **fields,
        )
        db.session.add(page)
        db.session.commit()
        return page

    def rename(self, item, name: str):
        """Change a title, keeping the URL it already had.

        Slugs are deliberately not regenerated: they are in slides and in
        e-mails, and a corrected typo in a title is not a reason to break
        every link to the page.
        """
        item.name = name
        db.session.commit()
        return item

    def attach_file(
        self,
        page: Page,
        file_storage,
        name: str = "",
        kind: str = KIND_FILE,
        legacy_id: int | None = None,
    ):
        """Store an uploaded file as this page's, withheld with it.

        The bytes go to the media library as a restricted item owned by
        this feature, so serving them asks page_visible first. That is what
        makes an unreleased script answer 404 to a guessed URL rather than
        merely being unlinked.

        ``kind`` says whether this is a document the reader downloads or an
        image the body embeds. Both are gated the same way; only the first
        is listed under the page.
        """
        from splent_framework.services.service_locator import get_service_class

        media = get_service_class(current_app._get_current_object(), "MediaService")()
        item = media.save_upload(
            file_storage,
            title=name or getattr(file_storage, "filename", ""),
            access="restricted",
            owner_feature="courses",
            owner_ref=f"page:{page.id}",
        )
        if item is None:
            return None

        attachment = PageAttachment(
            page_id=page.id,
            media_item_id=item.id,
            name=name or item.filename,
            kind=kind,
            legacy_id=legacy_id,
            position=len(self.attachments.list_for_page(page.id, kind=kind)),
        )
        db.session.add(attachment)
        db.session.commit()
        return attachment

    def detach_file(self, attachment) -> bool:
        """Remove a file from a page, and the stored bytes with it."""
        from splent_framework.services.service_locator import get_service_class

        media_item_id = attachment.media_item_id
        db.session.delete(attachment)
        db.session.commit()

        media = get_service_class(current_app._get_current_object(), "MediaService")()
        media.delete_item(media_item_id)
        return True

    def set_visibility(self, item, hidden: bool | None = None, publish_at=KEEP):
        """Withhold, release or schedule any of the three levels."""
        if hidden is not None:
            item.hidden = hidden
        if publish_at is not KEEP:
            item.publish_at = publish_at
        db.session.commit()
        return item


# ── Searchable material ──────────────────────────────────────────────────
#
# What this feature offers to whatever search a product installs, through
# the framework's registry. Three callables and no import of any search
# feature, so a product can install an engine, a plain search page or
# neither, and this code does not change.
#
# The split between them is the point. A document is what the material
# says; a result is what a particular reader is allowed to be told about
# it. Only the second question involves visibility, and it is asked here,
# now, per hit, because the first answer is written into an index and read
# for days afterwards.


SNIPPET_LENGTH = 200
# How much of the body to keep before the match, so the extract reads as a
# sentence the term appears in rather than starting on top of it.
SNIPPET_CONTEXT = 60

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_HTML_TAG = re.compile(r"<[^>]+>")
_LIST_BULLET = re.compile(r"^[ \t]*(?:[-+*]|\d+\.)[ \t]+", re.MULTILINE)
_MARKUP = re.compile(r"[*_~>#`|]+")
_WHITESPACE = re.compile(r"\s+")


def plain_text(body_md: str) -> str:
    """The prose inside a markdown body, near enough to read.

    Not a parser and not trying to be one. A snippet is two lines shown
    next to a title, and rendering the body properly to produce it would
    mean loading the whole markdown reader for every hit. Syntax is
    stripped rather than interpreted, which keeps a link's words and drops
    its URL, so a reader is not shown a line of raw asterisks and pipes as
    the reason a page matched.
    """
    text = _CODE_FENCE.sub(" ", body_md or "")
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _HTML_TAG.sub(" ", text)
    text = _LIST_BULLET.sub("", text)
    text = _MARKUP.sub("", text)
    return _WHITESPACE.sub(" ", text).strip()


def _snippet(text: str, term: str = "") -> str:
    """A short extract, around the match when there is one.

    A plain substring window rather than a highlighter, because whoever
    displays a result escapes it: markup put in here would either be shown
    as source or have to be trusted, and neither is worth it for a line of
    context. With no term, or with a term that only matched a title, the
    opening of the page still says what it is about.
    """
    if not text:
        return ""
    term = (term or "").strip()
    position = text.lower().find(term.lower()) if term else -1
    start = max(0, position - SNIPPET_CONTEXT) if position >= 0 else 0
    end = start + SNIPPET_LENGTH
    extract = text[start:end].strip()
    if start > 0:
        extract = f"...{extract}"
    if end < len(text):
        extract = f"{extract}..."
    return extract


def _page_url(page: Page) -> str:
    """Where a page reads, for callers holding only the page.

    The URL builder is imported here rather than at module level because
    this feature's package imports this module to expose the service, so
    the other direction has to wait until something actually asks.
    """
    from splent_io.splent_feature_courses import page_url

    return page_url(page.course, page)


def _page_result(page: Page, term: str = "") -> dict:
    """One page as a result, however it was found.

    The course and the section come with it because a wiki holding ten
    academic years has a page called "Lab 5" in every one of them, and a
    title on its own does not tell the reader which year they just found.
    """
    return {
        "title": page.name,
        "url": _page_url(page),
        "snippet": _snippet(plain_text(page.body_md), term),
        "course": page.course.name,
        "category": page.category.name if page.category is not None else "",
    }


def search_fetch():
    """Every page there is, for a search feature to index.

    Nothing about visibility is in a document, and that is deliberate:
    not hidden, not publish_at, not the course's. An index is written once
    and read for days, so an answer about who may read a page would be
    wrong from the moment staff withheld it, and a stale yes is the leak
    this whole feature exists to prevent. An index proposes candidates and
    search_resolve decides, which also means a page withheld after it was
    indexed simply stops appearing, with no reindex to remember.

    The body is stored as text rather than as markdown so that a query is
    matched against what the page says and not against its syntax.
    """
    service = CoursesService()
    for page in service.pages.iter_all():
        yield {
            "id": str(page.id),
            "title": page.name,
            "body": plain_text(page.body_md),
            "url": _page_url(page),
            "course": page.course.name,
            "category": page.category.name if page.category is not None else "",
            # Which course this page belongs to, so a reader standing in one
            # can narrow a search to it. A location and not a permission: it
            # says nothing about who may read the page, which is still asked
            # per request. Without it a narrowed search matches nothing at
            # all, and the box would answer "no results" for a course the
            # reader is looking at.
            "scope": page.course.slug,
        }


def search_resolve(doc_id, user=None) -> dict | None:
    """The page behind one candidate, if this reader may see it.

    The authority the registry promises, asked at request time and once
    per hit. It is page_visible that answers, the same call the page's own
    URL makes, so a result can never appear for material that would answer
    404 on arrival, and a title is withheld exactly as its body is.

    A primary key lookup, because a page of results asks this twenty
    times.
    """
    try:
        page_id = int(doc_id)
    except (TypeError, ValueError):
        # Ids come back from an index as strings, and an index can outlive
        # the material it was built from, so an unusable id is a candidate
        # to drop rather than an error to raise.
        return None
    service = CoursesService()
    page = service.pages.get_by_id(page_id)
    if not service.page_visible(page, user):
        return None
    return _page_result(page)


def search_resolve_many(doc_ids, user=None) -> dict:
    """A whole page of candidates at once, keeping only what may be seen.

    A search asks about far more candidates than it shows, because withheld
    material outranks visible material as often as not, and asking one page
    at a time turns that into one query instead of hundreds. The reason is
    not only speed. Answering per id makes the time a search takes depend
    on how much withheld material matched the term, and a reader who can
    measure that can ask questions about material they may not read: a term
    matching twenty unreleased pages and a term matching nothing both
    answer "no results", but they do not take the same time. One query for
    the batch, with the course and the category already joined, flattens
    that.

    Returns {doc_id: result} for the ones this reader may see, keyed by the
    id as it arrived so the caller can preserve the engine's ranking.
    """
    wanted = {}
    for doc_id in doc_ids:
        try:
            wanted[int(doc_id)] = doc_id
        except (TypeError, ValueError):
            # An index outlives the material it was built from, so an
            # unusable id is a candidate to drop, not an error to raise.
            continue
    if not wanted:
        return {}

    service = CoursesService()
    now = _utcnow()
    resolved = {}
    for page in service.pages.by_ids(list(wanted)):
        if service.page_visible(page, user, now):
            resolved[wanted[page.id]] = _page_result(page)
    return resolved


def search_find(term: str, user=None, scope: str | None = None) -> list[dict]:
    """Matching pages for a product with no engine, or one that is down.

    Exactly search_pages, presented the way the registry expects. Written
    as a call rather than as a second query on purpose: a fallback that
    filtered visibility its own way would be the one place the rule could
    drift, and it would drift silently, because the fallback only runs
    when the engine is unreachable.

    A scope is a course slug. One nobody recognises finds nothing rather
    than quietly widening to everything, which is the difference between a
    typo showing no results and a typo showing the whole site.
    """
    service = CoursesService()
    course = None
    if scope:
        course = service.course_by_slug(scope)
        if course is None:
            return []
    return [
        _page_result(page, term)
        for page in service.search_pages(term, course, user=user)
    ]


def search_scopes(user=None) -> list[dict]:
    """The courses this reader may narrow a search to, newest first.

    Only what they can already see: offering a course that answers 404 on
    arrival would confirm it exists.
    """
    return [
        {"key": course.slug, "label": course.name}
        for course in CoursesService().visible_courses(user)
    ]


def search_current_scope() -> str | None:
    """The course the reader is looking at, or nothing.

    Read here rather than by a search feature, which does not know that
    /cursos/<slug> means a course and must never learn it: these URLs are
    this feature's, built from its own configuration, and free to change
    without anything else noticing.
    """
    from flask import has_request_context, request

    if not has_request_context():
        return None
    path = (current_app.config.get("COURSES_PATH") or "").strip("/")
    if not path:
        return None
    parts = [part for part in request.path.split("/") if part]
    if len(parts) < 2 or parts[0] != path:
        return None
    slug = parts[1]
    return slug if CoursesService().course_by_slug(slug) is not None else None
