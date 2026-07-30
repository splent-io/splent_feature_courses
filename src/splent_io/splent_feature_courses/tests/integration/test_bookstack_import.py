"""Importing a BookStack wiki, and the decisions that shaped it.

These run against a hand-built source rather than a database, because what
needs pinning is not SQL: it is what happens to the material. Every case
here is something the import got wrong at some point against the real EGC
wiki, 1246 pages of it, and would get wrong again.
"""

import os

import pytest

from splent_io.splent_feature_courses.bookstack import BookStackImport
from splent_io.splent_feature_courses.models import (
    KIND_FILE,
    KIND_INLINE,
)
from splent_io.splent_feature_courses.services import CoursesService

PDF = b"%PDF-1.4 lab script"
PNG = b"\x89PNG\r\n\x1a\n fake pixels"


class FakeSource:
    """A BookStack whose answers the test writes out in full."""

    def __init__(
        self,
        books=None,
        chapters=None,
        pages=None,
        attachments=None,
        images=None,
        covers=None,
        hidden=None,
        app_url="http://localhost:8080",
    ):
        self._books = books or []
        self._chapters = chapters or []
        self._pages = pages or []
        self._attachments = attachments or []
        self._images = images or []
        self._covers = covers or {}
        self._hidden = hidden or set()
        self._app_url = app_url

    def books(self):
        return self._books

    def chapters(self):
        return self._chapters

    def pages(self):
        return self._pages

    def attachments(self):
        return self._attachments

    def gallery_images(self):
        return self._images

    def book_covers(self):
        return self._covers

    def hidden_entities(self):
        return self._hidden

    def detect_app_url(self):
        return self._app_url


def _book(id=1, slug="egc-20252026", name="EGC 2025/2026"):
    return {
        "id": id,
        "slug": slug,
        "name": name,
        "description": "Material docente",
        "created_at": None,
        "updated_at": None,
    }


def _chapter(id=10, book_id=1, slug="practicas", name="Prácticas", priority=3):
    return {
        "id": id,
        "book_id": book_id,
        "slug": slug,
        "name": name,
        "priority": priority,
    }


def _page(id=100, book_id=1, chapter_id=10, slug="lab-5", markdown="body", **extra):
    row = {
        "id": id,
        "book_id": book_id,
        "chapter_id": chapter_id,
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "priority": 1,
        "markdown": markdown,
        "html": "",
        "editor": "markdown",
        "created_at": None,
        "updated_at": None,
    }
    row.update(extra)
    return row


@pytest.fixture
def uploads(tmp_path):
    """An extracted BookStack backup, in the shape its backup really has."""
    files = tmp_path / "www" / "files" / "2026-07-Jul"
    files.mkdir(parents=True)
    (files / "abc123-pdf").write_bytes(PDF)
    gallery = tmp_path / "www" / "uploads" / "images" / "gallery" / "2026-07"
    gallery.mkdir(parents=True)
    (gallery / "diagram.png").write_bytes(PNG)
    return str(tmp_path)


def _run(source, uploads, test_app, **kwargs):
    with test_app.app_context():
        job = BookStackImport(source, uploads_root=uploads, **kwargs)
        report = job.run()
    return report


class TestStructure:
    def test_a_book_becomes_a_course_keeping_its_slug(
        self, test_client, test_app, uploads
    ):
        """The slug is the whole reason old links keep working."""
        source = FakeSource(books=[_book()], chapters=[_chapter()], pages=[_page()])

        _run(source, uploads, test_app)

        with test_app.app_context():
            course = CoursesService().repository.get_by_slug("egc-20252026")
            assert course is not None
            assert course.name == "EGC 2025/2026"
            assert course.categories[0].slug == "practicas"
            assert course.categories[0].position == 3
            assert course.pages[0].slug == "lab-5"
            assert course.pages[0].legacy_id == 100

    def test_running_it_again_updates_rather_than_duplicates(
        self, test_client, test_app, uploads
    ):
        """A migration is rehearsed, then run for real on the day."""
        source = FakeSource(books=[_book()], chapters=[_chapter()], pages=[_page()])

        _run(source, uploads, test_app)
        source._pages[0]["markdown"] = "corrected"
        _run(source, uploads, test_app)

        with test_app.app_context():
            course = CoursesService().repository.get_by_slug("egc-20252026")
            assert len(course.pages) == 1
            assert course.pages[0].body_md == "corrected"


class TestVisibility:
    def test_what_the_source_withholds_arrives_withheld(
        self, test_client, test_app, uploads
    ):
        """BookStack keeps this in permission overrides, not on the entity.

        An import that read only the entities would publish next year's
        material on day one.
        """
        source = FakeSource(
            books=[_book(id=1), _book(id=2, slug="egc-20262027", name="EGC 2026/2027")],
            hidden={("book", 2)},
        )

        _run(source, uploads, test_app)

        with test_app.app_context():
            service = CoursesService()
            assert service.repository.get_by_slug("egc-20252026").hidden is False
            assert service.repository.get_by_slug("egc-20262027").hidden is True

    def test_a_second_run_does_not_undo_a_release_made_here(
        self, test_client, test_app, uploads
    ):
        source = FakeSource(
            books=[_book(id=2, slug="egc-20262027")], hidden={("book", 2)}
        )
        _run(source, uploads, test_app)

        with test_app.app_context():
            service = CoursesService()
            course = service.repository.get_by_slug("egc-20262027")
            service.set_visibility(course, hidden=False)

        _run(source, uploads, test_app)

        with test_app.app_context():
            course = CoursesService().repository.get_by_slug("egc-20262027")
            assert course.hidden is False, "the release made here was overwritten"

    def test_reset_visibility_asks_the_source_again(
        self, test_client, test_app, uploads
    ):
        source = FakeSource(
            books=[_book(id=2, slug="egc-20262027")], hidden={("book", 2)}
        )
        _run(source, uploads, test_app)

        with test_app.app_context():
            service = CoursesService()
            service.set_visibility(
                service.repository.get_by_slug("egc-20262027"), hidden=False
            )

        _run(source, uploads, test_app, reset_visibility=True)

        with test_app.app_context():
            assert (
                CoursesService().repository.get_by_slug("egc-20262027").hidden is True
            )


def _source_with_files():
    return FakeSource(
        books=[_book()],
        chapters=[_chapter()],
        pages=[
            _page(
                markdown=(
                    "See [the script](http://localhost:8080/attachments/7)\n\n"
                    "![d](http://localhost:8080/uploads/images/gallery/"
                    "2026-07/diagram.png)\n"
                )
            )
        ],
        attachments=[
            {
                "id": 7,
                "name": "lab-5.pdf",
                "path": "uploads/files/2026-07-Jul/abc123-pdf",
                "extension": "pdf",
                "page_id": 100,
                "position": 0,
            }
        ],
        images=[
            {
                "id": 3,
                "name": "diagram.png",
                "path": "/uploads/images/gallery/2026-07/diagram.png",
                "page_id": 100,
            }
        ],
    )


class TestFiles:
    def test_documents_are_listed_and_images_are_not(
        self, test_client, test_app, uploads
    ):
        """An embedded image is not a document. Offering it under "Files"
        would hand the reader a picture they are already looking at."""
        _run(_source_with_files(), uploads, test_app)

        with test_app.app_context():
            service = CoursesService()
            course = service.repository.get_by_slug("egc-20252026")
            page = course.pages[0]
            assert [a.name for a in service.attachments.list_for_page(page.id)] == [
                "lab-5.pdf"
            ]
            everything = service.attachments.list_for_page(page.id, kind=None)
            assert {a.kind for a in everything} == {KIND_FILE, KIND_INLINE}

    def test_both_are_gated_by_the_page(self, test_client, test_app, uploads):
        """The image is material too. BookStack served it statically, which
        is how a diagram of an unreleased lab leaked."""
        source = _source_with_files()
        source._hidden = {("page", 100)}

        _run(source, uploads, test_app)

        with test_app.app_context():
            service = CoursesService()
            page = service.repository.get_by_slug("egc-20252026").pages[0]
            for attachment in service.attachments.list_for_page(page.id, kind=None):
                assert (
                    service.may_read_attachment(attachment.media_item_id, None) is False
                )

    def test_the_type_of_the_file_is_declared(self, test_client, test_app, uploads):
        """Media serves what the upload declared and nothing else.

        Declaring nothing makes every PDF a download and, since the same
        response carries nosniff, stops every embedded image from rendering
        at all.
        """
        _run(_source_with_files(), uploads, test_app)

        with test_app.app_context():
            from splent_framework.services.service_locator import get_service_class
            from flask import current_app

            service = CoursesService()
            media = get_service_class(
                current_app._get_current_object(), "MediaService"
            )()
            page = service.repository.get_by_slug("egc-20252026").pages[0]
            types = {
                a.kind: media.repository.get_by_id(a.media_item_id).mime_type
                for a in service.attachments.list_for_page(page.id, kind=None)
            }
            assert types[KIND_FILE] == "application/pdf"
            assert types[KIND_INLINE] == "image/png"

    def test_the_body_points_at_the_copies(self, test_client, test_app, uploads):
        _run(_source_with_files(), uploads, test_app)

        with test_app.app_context():
            page = CoursesService().repository.get_by_slug("egc-20252026").pages[0]
            assert "/attachments/7" not in page.body_md
            assert "/uploads/images" not in page.body_md
            assert page.body_md.count("/media/file/") == 2


class TestRewritingIsNotBlunt:
    """Both of these corrupted real pages of the EGC wiki."""

    def test_code_is_left_exactly_as_written(self, test_client, test_app, uploads):
        """A Vagrant tutorial tells the student to open their own
        application at localhost:8080. That is not this wiki."""
        body = (
            "Open your app:\n\n"
            "```\n"
            "curl http://localhost:8080/api/hello\n"
            "```\n\n"
            "and `http://localhost:8080/status` too.\n"
        )
        source = FakeSource(
            books=[_book()], chapters=[_chapter()], pages=[_page(markdown=body)]
        )

        _run(source, uploads, test_app)

        with test_app.app_context():
            page = CoursesService().repository.get_by_slug("egc-20252026").pages[0]
            assert "curl http://localhost:8080/api/hello" in page.body_md
            assert "`http://localhost:8080/status`" in page.body_md

    def test_an_address_that_is_not_a_wiki_path_survives(
        self, test_client, test_app, uploads
    ):
        """Twenty-five references in the real corpus name the host with
        nothing after it. Stripping it turned "<http://localhost:8080>"
        into "<>"."""
        body = "Visit <http://localhost:8080> and then /cursos elsewhere.\n"
        source = FakeSource(
            books=[_book()], chapters=[_chapter()], pages=[_page(markdown=body)]
        )

        _run(source, uploads, test_app)

        with test_app.app_context():
            page = CoursesService().repository.get_by_slug("egc-20252026").pages[0]
            assert "<http://localhost:8080>" in page.body_md

    def test_a_link_to_this_wiki_loses_the_old_host(
        self, test_client, test_app, uploads
    ):
        body = "See [last year](http://localhost:8080/cursos/egc-20242025/pagina/x).\n"
        source = FakeSource(
            books=[_book()], chapters=[_chapter()], pages=[_page(markdown=body)]
        )

        _run(source, uploads, test_app)

        with test_app.app_context():
            page = CoursesService().repository.get_by_slug("egc-20252026").pages[0]
            assert "(/cursos/egc-20242025/pagina/x)" in page.body_md


class TestDryRun:
    def test_it_writes_nothing(self, test_client, test_app, uploads):
        source = FakeSource(books=[_book()], chapters=[_chapter()], pages=[_page()])

        report = _run(source, uploads, test_app, dry_run=True)

        assert report.created["pages"] == 1
        with test_app.app_context():
            assert CoursesService().repository.get_by_slug("egc-20252026") is None

    def test_it_still_resolves_references(self, test_client, test_app, uploads):
        """A rehearsal that reported every link as unresolvable would hide
        the ones that really are."""
        source = _source_with_files()

        report = _run(source, uploads, test_app, dry_run=True)

        assert report.problems == []
        assert report.rewritten_links["attachments"] == 1
        assert report.rewritten_links["images"] == 1


class TestMissingFiles:
    def test_a_file_the_backup_does_not_have_is_reported(
        self, test_client, test_app, uploads
    ):
        source = FakeSource(
            books=[_book()],
            chapters=[_chapter()],
            pages=[_page()],
            attachments=[
                {
                    "id": 9,
                    "name": "gone.pdf",
                    "path": "uploads/files/2026-07-Jul/missing-pdf",
                    "extension": "pdf",
                    "page_id": 100,
                    "position": 0,
                }
            ],
        )

        report = _run(source, uploads, test_app)

        assert any("gone.pdf" in problem for problem in report.problems)


class TestBackupLayout:
    def test_documents_are_found_where_the_backup_puts_them(self, uploads):
        """The database says uploads/files/... and the backup writes
        www/files/..., which is not the same path."""
        finder = BookStackImport.__new__(BookStackImport)
        finder.uploads_root = uploads

        found = BookStackImport._absolute(
            finder, "uploads/files/2026-07-Jul/abc123-pdf"
        )

        assert found is not None
        assert os.path.isfile(found)
