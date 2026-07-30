from __future__ import annotations

from sqlalchemy import or_

from splent_io.splent_feature_courses.models import (
    KIND_FILE,
    Category,
    Course,
    Page,
    PageAttachment,
)
from splent_framework.repositories.BaseRepository import BaseRepository


class CourseRepository(BaseRepository):
    def __init__(self):
        super().__init__(Course)

    def list_newest_first(self) -> list[Course]:
        """Newest course first.

        The names sort chronologically on their own (EGC 2013/2014 through
        EGC 2025/2026), so ordering by name descending keeps working every
        September with nothing to maintain.
        """
        return Course.query.order_by(Course.name.desc()).all()

    def get_by_slug(self, slug: str) -> Course | None:
        return Course.query.filter_by(slug=slug).first()

    def newest(self) -> Course | None:
        return Course.query.order_by(Course.name.desc()).first()

    def slug_exists(self, slug: str) -> bool:
        return Course.query.filter_by(slug=slug).first() is not None

    def name_exists(self, name: str) -> bool:
        return Course.query.filter_by(name=name).first() is not None


class CategoryRepository(BaseRepository):
    def __init__(self):
        super().__init__(Category)

    def list_for_course(self, course_id: int) -> list[Category]:
        return (
            Category.query.filter_by(course_id=course_id)
            .order_by(Category.position, Category.id)
            .all()
        )

    def get_by_slug(self, course_id: int, slug: str) -> Category | None:
        return Category.query.filter_by(course_id=course_id, slug=slug).first()

    def next_position(self, course_id: int) -> int:
        last = (
            Category.query.filter_by(course_id=course_id)
            .order_by(Category.position.desc())
            .first()
        )
        return (last.position + 1) if last else 0


class PageRepository(BaseRepository):
    def __init__(self):
        super().__init__(Page)

    def list_for_category(self, category_id: int) -> list[Page]:
        return (
            Page.query.filter_by(category_id=category_id)
            .order_by(Page.position, Page.id)
            .all()
        )

    def list_for_course(self, course_id: int) -> list[Page]:
        return (
            Page.query.filter_by(course_id=course_id)
            .order_by(Page.position, Page.id)
            .all()
        )

    def get_by_slug(self, course_id: int, slug: str) -> Page | None:
        return Page.query.filter_by(course_id=course_id, slug=slug).first()

    def get_by_legacy_id(self, legacy_id: int) -> Page | None:
        return Page.query.filter_by(legacy_id=legacy_id).first()

    def next_position(self, category_id: int) -> int:
        last = (
            Page.query.filter_by(category_id=category_id)
            .order_by(Page.position.desc())
            .first()
        )
        return (last.position + 1) if last else 0

    def search(self, term: str, course_id: int | None = None) -> list[Page]:
        """Candidate matches by name or body.

        Deliberately unfiltered by visibility: the caller applies that, so
        there is one place where the rule lives and no query can forget it.
        """
        pattern = f"%{term}%"
        query = Page.query.filter(
            or_(Page.name.ilike(pattern), Page.body_md.ilike(pattern))
        )
        if course_id is not None:
            query = query.filter(Page.course_id == course_id)
        return query.order_by(Page.position, Page.id).all()


class PageAttachmentRepository(BaseRepository):
    def __init__(self):
        super().__init__(PageAttachment)

    def list_for_page(
        self, page_id: int, kind: str | None = KIND_FILE
    ) -> list[PageAttachment]:
        """This page's attachments, downloadable files by default.

        The default is the listing every caller wanted before embedded
        images existed: showing one under "Files" would offer the reader a
        picture they are already looking at. Pass ``kind=None`` for
        everything, which is what an importer and a delete need.
        """
        query = PageAttachment.query.filter_by(page_id=page_id)
        if kind is not None:
            query = query.filter(PageAttachment.kind == kind)
        return query.order_by(PageAttachment.position, PageAttachment.id).all()

    def get_by_media_item(self, media_item_id: int) -> PageAttachment | None:
        return PageAttachment.query.filter_by(media_item_id=media_item_id).first()

    def get_by_legacy(self, kind: str, legacy_id: int) -> PageAttachment | None:
        """The row a previous import created for that source record.

        Scoped by kind because the source numbers its files and its images
        separately, so attachment 1 and image 1 are different things.
        """
        return PageAttachment.query.filter_by(kind=kind, legacy_id=legacy_id).first()
