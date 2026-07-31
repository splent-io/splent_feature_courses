from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from splent_io.splent_feature_courses.models import (
    KIND_FILE,
    Category,
    Course,
    Page,
    PageAttachment,
    PageRevision,
)
from splent_framework.repositories.BaseRepository import BaseRepository
from sqlalchemy import func


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

    def list_recent_for_course(self, course_id: int, limit: int = 20) -> list[Page]:
        """The most recently touched pages of a course, newest first.

        Ordered by updated_at with created_at behind it, because a page
        written last week and corrected today is news again, and material
        migrated in one batch shares a creation date to the second.

        The limit is a candidate count, not an answer: whoever calls this
        still has to ask which of them this reader may see, and a page
        withheld until next Monday must not take a slot from a visible one.
        Asked for generously and cut down afterwards.
        """
        # coalesce rather than two ordering terms with NULLS LAST: MariaDB
        # rejects that syntax outright, and the fallback is what was meant
        # anyway, since a page nobody has edited was last touched when it was
        # written. It also sorts the same way on every database, which two
        # NULL-ordering rules would not.
        touched = func.coalesce(Page.updated_at, Page.created_at)
        return (
            Page.query.filter_by(course_id=course_id)
            .order_by(touched.desc(), Page.id.desc())
            .limit(limit)
            .all()
        )

    def get_by_slug(self, course_id: int, slug: str) -> Page | None:
        return Page.query.filter_by(course_id=course_id, slug=slug).first()

    def get_by_legacy_id(self, legacy_id: int) -> Page | None:
        return Page.query.filter_by(legacy_id=legacy_id).first()

    def iter_all(self, batch_size: int = 500):
        """Every page there is, oldest first, streamed rather than listed.

        A full reindex is what asks for this, and it walks a decade of
        academic years in one pass, so the rows arrive in batches instead
        of all at once. The course and the category come along in the same
        query because each document names them, and reaching for them per
        page would be two round trips times a few thousand. Both are
        many-to-one, which is the case yield_per allows.
        """
        return (
            Page.query.options(joinedload(Page.course), joinedload(Page.category))
            .order_by(Page.id)
            .yield_per(batch_size)
        )

    def by_ids(self, page_ids: list[int]) -> list[Page]:
        """Several pages in one query, with what visibility depends on.

        A page of search results asks about every candidate the engine
        proposed, most of which the reader will not be allowed to see. One
        query per candidate would be three round trips each once the course
        and the category are touched, and the time that takes would then
        depend on how much withheld material matched.
        """
        if not page_ids:
            return []
        return (
            Page.query.options(joinedload(Page.course), joinedload(Page.category))
            .filter(Page.id.in_(page_ids))
            .all()
        )

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

    def search_all(
        self, term: str = "", course_id: int | None = None, limit: int = 200
    ) -> list[Page]:
        """Every page in the wiki, filtered, newest edit first.

        The back office needs a flat list because that is how a person
        thinks of "the page about Vagrant" when they cannot remember which
        year it is under, and drilling through fourteen courses to find one
        page is the friction that makes staff stop editing.

        The term matches the title only. Searching bodies from here would
        duplicate the search feature badly: no index, no ranking, and a
        LIKE over every body in the wiki on each keystroke.
        """
        query = Page.query.options(joinedload(Page.course), joinedload(Page.category))
        if course_id:
            query = query.filter(Page.course_id == course_id)
        term = (term or "").strip()
        if term:
            query = query.filter(Page.name.ilike(f"%{term}%"))
        touched = func.coalesce(Page.updated_at, Page.created_at)
        return query.order_by(touched.desc(), Page.id.desc()).limit(limit).all()


class PageRevisionRepository(BaseRepository):
    """What a page said before each edit, newest first."""

    def __init__(self):
        super().__init__(PageRevision)

    def list_for_page(
        self, page_id: int, limit: int | None = None
    ) -> list[PageRevision]:
        query = PageRevision.query.filter_by(page_id=page_id).order_by(
            PageRevision.created_at.desc(), PageRevision.id.desc()
        )
        return query.limit(limit).all() if limit else query.all()

    def count_for_page(self, page_id: int) -> int:
        return PageRevision.query.filter_by(page_id=page_id).count()

    def get_for_page(self, page_id: int, revision_id: int) -> PageRevision | None:
        """One revision, checked against the page it is asked for.

        Both halves matter: a revision id on its own would let anybody who
        can edit one page read the history of another by guessing numbers.
        """
        return PageRevision.query.filter_by(id=revision_id, page_id=page_id).first()

    def trim_to(self, page_id: int, keep: int) -> int:
        """Drop the oldest revisions past ``keep``. Returns how many went.

        A page edited daily for a decade would otherwise carry three
        thousand copies of itself, and nobody has ever asked to see the
        four-hundredth. Kept generous, because the point of history is that
        it is there when you did not expect to need it.
        """
        stale = (
            PageRevision.query.filter_by(page_id=page_id)
            .order_by(PageRevision.created_at.desc(), PageRevision.id.desc())
            .offset(keep)
            .all()
        )
        for revision in stale:
            self.session.delete(revision)
        if stale:
            self.session.commit()
        return len(stale)


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


class WikiRepository:
    """Questions about the wiki as a whole, rather than about one course.

    Not a BaseRepository: it owns no model. It exists so the back-office
    screens that look across everything have somewhere to live that is not
    a route assembling queries by hand.
    """

    @staticmethod
    def recent_revisions(limit: int = 100) -> list[PageRevision]:
        """What has been edited lately, anywhere in the wiki."""
        return (
            PageRevision.query.options(
                joinedload(PageRevision.page).joinedload(Page.course)
            )
            .order_by(PageRevision.created_at.desc(), PageRevision.id.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def all_attachments(
        course_id: int | None = None, kind: str | None = None, limit: int = 300
    ) -> list[PageAttachment]:
        """Every file in the wiki, newest first.

        Loaded with its page and course in one go: this list is the place a
        person comes to find the file they uploaded to the wrong page, and
        the answer is useless without saying which page that is.
        """
        query = PageAttachment.query.options(
            joinedload(PageAttachment.page).joinedload(Page.course)
        ).join(Page, PageAttachment.page_id == Page.id)
        if course_id:
            query = query.filter(Page.course_id == course_id)
        if kind:
            query = query.filter(PageAttachment.kind == kind)
        return query.order_by(PageAttachment.id.desc()).limit(limit).all()
