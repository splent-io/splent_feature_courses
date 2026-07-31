"""Copying a year, which is the chore this feature exists to remove.

Around thirty pages repeat every September and copying them by hand is
what nobody has time for. The copy therefore has to be complete, because a
teacher who finds it is not has lost more time than they saved.

It was not. It walked the categories and copied their pages, which dropped
a page belonging to no section and, silently, every single attachment:
copying EGC 2024/2025 gave sixty-one pages and none of its fifty-two
files, with nothing to suggest anything was missing until a student asked
for the slides.
"""

import io

import pytest
from werkzeug.datastructures import FileStorage

from splent_framework.db import db
from splent_io.splent_feature_courses.models import (
    KIND_INLINE,
    Category,
    Course,
    Page,
)
from splent_io.splent_feature_courses.services import CoursesService


@pytest.fixture
def service():
    return CoursesService()


def upload(name, payload=b"contenido"):
    return FileStorage(
        stream=io.BytesIO(payload), filename=name, content_type="application/pdf"
    )


@pytest.fixture
def year(test_client, test_app, service):
    """A year with a section, a page in it, a page outside it, and files.

    Both shapes matter. Migrated material often belongs to no section, and
    the attachments are the part that used to disappear.
    """
    with test_app.app_context():
        course = Course(name="EGC 2024/2025", slug="egc-20242025")
        db.session.add(course)
        db.session.flush()

        section = Category(
            course_id=course.id, name="Prácticas", slug="practicas", position=0
        )
        db.session.add(section)
        db.session.flush()

        inside = Page(
            course_id=course.id,
            category_id=section.id,
            name="Lab 5",
            slug="lab-5",
            body_md="Traer portátil.",
        )
        loose = Page(
            course_id=course.id,
            category_id=None,
            name="Teoría",
            slug="teoria",
            body_md="Sin sección, como llega lo migrado.",
        )
        db.session.add_all([inside, loose])
        db.session.commit()

        service.attach_file(inside, upload("slides.pdf"), name="slides.pdf")
        service.attach_file(
            inside, upload("diagram.png"), name="diagram.png", kind=KIND_INLINE
        )
        return course.id


class TestWhatComesAcross:
    def test_every_page_does_not_only_the_filed_ones(self, test_app, service, year):
        """The one outside a section is still the course's."""
        with test_app.app_context():
            source = db.session.get(Course, year)
            copy = service.copy_course(source, "EGC 2025/2026")

            names = sorted(p.name for p in service.pages.list_for_course(copy.id))
            assert names == ["Lab 5", "Teoría"]

    def test_a_page_keeps_the_section_it_was_in(self, test_app, service, year):
        with test_app.app_context():
            source = db.session.get(Course, year)
            copy = service.copy_course(source, "EGC 2025/2026")

            pages = {p.name: p for p in service.pages.list_for_course(copy.id)}
            assert pages["Lab 5"].category.name == "Prácticas"
            assert pages["Teoría"].category_id is None
            # And into the copy's own section, not the original's.
            assert pages["Lab 5"].category.course_id == copy.id

    def test_the_files_come_too(self, test_app, service, year):
        """The whole point. Fifty-two of them went missing on a real year."""
        with test_app.app_context():
            source = db.session.get(Course, year)
            copy = service.copy_course(source, "EGC 2025/2026")

            page = next(
                p for p in service.pages.list_for_course(copy.id) if p.name == "Lab 5"
            )
            attachments = service.attachments.list_for_page(page.id, kind=None)
            assert sorted(a.name for a in attachments) == ["diagram.png", "slides.pdf"]

    def test_an_embedded_image_stays_an_embedded_image(self, test_app, service, year):
        """A picture copied as a document would appear under Files, offering
        the reader something they are already looking at."""
        with test_app.app_context():
            source = db.session.get(Course, year)
            copy = service.copy_course(source, "EGC 2025/2026")

            page = next(
                p for p in service.pages.list_for_course(copy.id) if p.name == "Lab 5"
            )
            kinds = {
                a.name: a.kind
                for a in service.attachments.list_for_page(page.id, kind=None)
            }
            assert kinds["diagram.png"] == KIND_INLINE


class TestTheCopyIsItsOwn:
    def test_the_files_are_not_shared_with_the_original(self, test_app, service, year):
        """detach_file deletes the stored bytes with the attachment, so a
        shared item would mean removing a file from this year quietly
        removing it from last year as well."""
        with test_app.app_context():
            source = db.session.get(Course, year)
            copy = service.copy_course(source, "EGC 2025/2026")

            original = next(
                p for p in service.pages.list_for_course(source.id) if p.name == "Lab 5"
            )
            duplicate = next(
                p for p in service.pages.list_for_course(copy.id) if p.name == "Lab 5"
            )
            originals = {
                a.media_item_id
                for a in service.attachments.list_for_page(original.id, kind=None)
            }
            copies = {
                a.media_item_id
                for a in service.attachments.list_for_page(duplicate.id, kind=None)
            }
            assert originals and copies
            assert originals.isdisjoint(copies)

    def test_removing_a_file_from_the_copy_leaves_the_original_alone(
        self, test_app, service, year
    ):
        with test_app.app_context():
            source = db.session.get(Course, year)
            copy = service.copy_course(source, "EGC 2025/2026")

            duplicate = next(
                p for p in service.pages.list_for_course(copy.id) if p.name == "Lab 5"
            )
            service.detach_file(
                service.attachments.list_for_page(duplicate.id, kind=None)[0]
            )

            original = next(
                p for p in service.pages.list_for_course(source.id) if p.name == "Lab 5"
            )
            assert len(service.attachments.list_for_page(original.id, kind=None)) == 2

    def test_nothing_arrives_released(self, test_app, service, year):
        """Last year's dates must not publish this year's material."""
        with test_app.app_context():
            source = db.session.get(Course, year)
            copy = service.copy_course(source, "EGC 2025/2026")
            assert copy.hidden is True

    def test_the_legacy_identifier_is_not_duplicated(self, test_app, service, year):
        """It names one page in the wiki this came from, and two rows
        claiming it would make a lookup by it ambiguous forever."""
        with test_app.app_context():
            source = db.session.get(Course, year)
            page = service.pages.list_for_course(source.id)[0]
            page.legacy_id = 4321
            db.session.commit()

            copy = service.copy_course(db.session.get(Course, year), "EGC 2025/2026")
            assert all(
                p.legacy_id is None for p in service.pages.list_for_course(copy.id)
            )


class TestSayingWhatHappened:
    def test_the_report_counts_what_came_across(self, test_app, service, year):
        with test_app.app_context():
            source = db.session.get(Course, year)
            service.copy_course(source, "EGC 2025/2026")

            assert service.last_copy_report["pages"] == 2
            assert service.last_copy_report["files"] == 2
            assert service.last_copy_report["missing"] == []

    def test_a_file_whose_bytes_are_gone_is_named_not_swallowed(
        self, test_app, service, year, monkeypatch
    ):
        """The rest of the copy is worth having, and a name in a report is a
        problem somebody can act on. A silent partial copy is not."""
        with test_app.app_context():
            from splent_framework.services.service_locator import get_service_class

            media_class = get_service_class(test_app, "MediaService")
            monkeypatch.setattr(
                media_class, "file_path", lambda self, item: "/nowhere/gone.pdf"
            )

            source = db.session.get(Course, year)
            copy = service.copy_course(source, "EGC 2025/2026")

            assert service.last_copy_report["files"] == 0
            assert sorted(service.last_copy_report["missing"]) == [
                "diagram.png",
                "slides.pdf",
            ]
            # And the pages still arrived.
            assert len(service.pages.list_for_course(copy.id)) == 2
