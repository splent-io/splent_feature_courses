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
