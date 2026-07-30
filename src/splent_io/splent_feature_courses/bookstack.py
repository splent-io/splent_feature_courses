"""Import a BookStack wiki into courses.

BookStack is where a lot of course material already lives, and its model
lines up with this one almost exactly: a book is a course, a chapter is a
category, a page is a page. What does not line up is where the bytes live
and how withholding works, and those are the two things this module is
careful about.

The source is read from a **database**, not the REST API. A migration that
matters is run from the dump taken at the moment of the cutover, and a dump
is a database; asking for an API means the old instance still has to be
running, with a token, reachable from here, at the exact moment you want to
switch over. Reading the dump also sees deleted rows for what they are and
never pages through anything.

Nothing is destructive and nothing is a one-shot: every row is matched by
what identifies it in the source (a slug, or a kind and an id), so the
import can be rehearsed as often as needed and run again on the day.

Three decisions worth stating, because they are choices and not mechanics:

  * **Embedded images become restricted files too.** BookStack serves them
    statically, so today a diagram belonging to an unreleased lab is
    readable by anyone who guesses its URL. Here they go through the same
    resolver as the documents, which is the whole reason this wiki exists.
  * **Visibility is only set when a row is created.** Re-running an import
    must not undo a release someone made in the meantime. ``reset_visibility``
    asks for the source's answer again, on purpose.
  * **Nothing is dropped silently.** A reference that cannot be resolved is
    reported as a problem rather than left pointing at a dead URL or quietly
    removed.
"""

from __future__ import annotations

import mimetypes
import os
import re
from collections import Counter
from dataclasses import dataclass, field

from splent_framework.db import db

from splent_io.splent_feature_courses.models import (
    KIND_FILE,
    KIND_INLINE,
    Category,
    Course,
    Page,
)
from splent_io.splent_feature_courses.services import CoursesService


# ---------------------------------------------------------------------------
# Reading the source
# ---------------------------------------------------------------------------


class BookStackSource:
    """Read-only access to a BookStack database.

    Written against the schema of BookStack 26.x, where the three entity
    types share one ``entities`` table and their bodies and descriptions
    hang off it. Earlier releases kept ``books``, ``chapters`` and ``pages``
    apart; that shape is detected and refused with a clear message rather
    than half-read.
    """

    def __init__(self, url: str):
        from sqlalchemy import create_engine

        self.url = url
        self._engine = create_engine(url)
        self._check_schema()

    def _rows(self, sql: str, **params) -> list[dict]:
        from sqlalchemy import text

        with self._engine.connect() as connection:
            result = connection.execute(text(sql), params)
            return [dict(row._mapping) for row in result]

    def _check_schema(self) -> None:
        from sqlalchemy import inspect

        tables = set(inspect(self._engine).get_table_names())
        missing = {"entities", "entity_page_data"} - tables
        if missing:
            raise RuntimeError(
                "This does not look like a BookStack 26.x database: it has no "
                f"{', '.join(sorted(missing))} table. An older BookStack keeps "
                "books, chapters and pages in separate tables and needs to be "
                "upgraded before it can be imported."
            )

    # -- entities ----------------------------------------------------------

    def books(self) -> list[dict]:
        return self._rows(
            """
            SELECT b.id, b.name, b.slug, b.created_at, b.updated_at,
                   COALESCE(d.description, '') AS description
            FROM entities b
            LEFT JOIN entity_container_data d
                   ON d.entity_id = b.id AND d.entity_type = 'book'
            WHERE b.type = 'book' AND b.deleted_at IS NULL
            ORDER BY b.name
            """
        )

    def chapters(self) -> list[dict]:
        return self._rows(
            """
            SELECT c.id, c.book_id, c.name, c.slug, c.priority
            FROM entities c
            WHERE c.type = 'chapter' AND c.deleted_at IS NULL
            ORDER BY c.book_id, c.priority, c.id
            """
        )

    def pages(self) -> list[dict]:
        """Live, non-draft pages with their markdown.

        A draft is a page its author never saved, and a template is a
        skeleton for new pages. Neither is material, so neither travels.
        """
        return self._rows(
            """
            SELECT p.id, p.book_id, p.chapter_id, p.name, p.slug, p.priority,
                   p.created_at, p.updated_at,
                   d.markdown, d.html, d.editor
            FROM entities p
            JOIN entity_page_data d ON d.page_id = p.id
            WHERE p.type = 'page' AND p.deleted_at IS NULL
              AND d.draft = 0 AND d.template = 0
            ORDER BY p.book_id, p.chapter_id, p.priority, p.id
            """
        )

    # -- files -------------------------------------------------------------

    def attachments(self) -> list[dict]:
        """Uploaded documents belonging to a live page.

        External attachments are links someone typed, not bytes we hold, so
        they stay in the body and are not imported as files.
        """
        return self._rows(
            """
            SELECT a.id, a.name, a.path, a.extension, a.uploaded_to AS page_id,
                   a.`order` AS position
            FROM attachments a
            JOIN entities p ON p.id = a.uploaded_to
                           AND p.type = 'page' AND p.deleted_at IS NULL
            WHERE a.external = 0
            ORDER BY a.uploaded_to, a.`order`, a.id
            """
        )

    def gallery_images(self) -> list[dict]:
        return self._rows(
            """
            SELECT i.id, i.name, i.path, i.uploaded_to AS page_id
            FROM images i
            JOIN entities p ON p.id = i.uploaded_to
                           AND p.type = 'page' AND p.deleted_at IS NULL
            WHERE i.type = 'gallery'
            ORDER BY i.id
            """
        )

    def book_covers(self) -> dict[int, dict]:
        """The cover image of each live book, by book id."""
        rows = self._rows(
            """
            SELECT d.entity_id AS book_id, i.id, i.name, i.path
            FROM entity_container_data d
            JOIN images i ON i.id = d.image_id
            JOIN entities b ON b.id = d.entity_id
                           AND b.type = 'book' AND b.deleted_at IS NULL
            WHERE d.entity_type = 'book' AND d.image_id IS NOT NULL
            """
        )
        return {row["book_id"]: row for row in rows}

    # -- visibility --------------------------------------------------------

    def hidden_entities(self) -> set[tuple[str, int]]:
        """(type, id) of everything the source withholds from the public.

        BookStack records this as permission overrides rather than as a flag
        on the entity, and role 0 is the fallback that applies to everyone
        with no more specific rule. A row that denies it ``view`` is the
        withheld material, which is why an import that only read the
        entities would publish next year's exam on day one.
        """
        rows = self._rows(
            """
            SELECT entity_type, entity_id
            FROM entity_permissions
            WHERE role_id = 0 AND view = 0
            """
        )
        return {(row["entity_type"], row["entity_id"]) for row in rows}

    # -- conveniences ------------------------------------------------------

    def detect_app_url(self) -> str | None:
        """The address the source called itself, read from its own bodies.

        Markdown written in BookStack embeds absolute URLs, so the bodies
        carry the answer and nobody has to remember it. Asking the settings
        table does not work: the URL lives in the environment, not the
        database.
        """
        rows = self._rows(
            """
            SELECT d.markdown
            FROM entity_page_data d
            JOIN entities p ON p.id = d.page_id
                           AND p.type = 'page' AND p.deleted_at IS NULL
            WHERE d.markdown REGEXP 'https?://[^/]+/(uploads|attachments)/'
            LIMIT 50
            """
        )
        pattern = re.compile(r"(https?://[^/\s)\"'<>]+)/(?:uploads|attachments)/")
        counts: Counter[str] = Counter()
        for row in rows:
            counts.update(pattern.findall(row["markdown"] or ""))
        return counts.most_common(1)[0][0] if counts else None


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass
class ImportReport:
    """What the import did, in enough detail to be believed."""

    created: Counter = field(default_factory=Counter)
    updated: Counter = field(default_factory=Counter)
    reused: Counter = field(default_factory=Counter)
    rewritten_links: Counter = field(default_factory=Counter)
    problems: list[str] = field(default_factory=list)

    def problem(self, message: str) -> None:
        self.problems.append(message)

    @property
    def ok(self) -> bool:
        return not self.problems


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------


class BookStackImport:
    """Copy a BookStack wiki into this product's courses."""

    def __init__(
        self,
        source: BookStackSource,
        uploads_root: str,
        app_url: str | None = None,
        reset_visibility: bool = False,
        dry_run: bool = False,
    ):
        self.source = source
        self.uploads_root = uploads_root
        self.app_url = (app_url or source.detect_app_url() or "").rstrip("/")
        self.reset_visibility = reset_visibility
        self.dry_run = dry_run

        self.service = CoursesService()
        self.report = ImportReport()

        self._courses: dict[int, Course] = {}
        self._categories: dict[int, Category] = {}
        self._pages: dict[int, Page] = {}
        # (kind, source id) -> media item id, and the path an image was
        # stored under, so a body can be rewritten to point at the copy.
        self._media: dict[tuple[str, int], int] = {}
        self._image_paths: dict[str, int] = {}

    # -- entry point -------------------------------------------------------

    def run(self) -> ImportReport:
        hidden = self.source.hidden_entities()
        self._import_courses(hidden)
        self._import_categories(hidden)
        self._import_pages(hidden)
        self._import_files()
        self._rewrite_bodies()
        if self.dry_run:
            # Everything above went through the session so the report is
            # about what would really happen. None of it is kept.
            db.session.rollback()
        else:
            db.session.commit()
        return self.report

    # -- structure ---------------------------------------------------------

    def _import_courses(self, hidden: set[tuple[str, int]]) -> None:
        covers = self.source.book_covers()
        for row in self.source.books():
            course = self.service.repository.get_by_slug(row["slug"])
            creating = course is None
            if creating:
                course = Course(slug=row["slug"])
                db.session.add(course)
            course.name = row["name"]
            course.description = row["description"] or ""
            if row.get("created_at"):
                course.created_at = row["created_at"]
            if creating or self.reset_visibility:
                course.hidden = ("book", row["id"]) in hidden
            db.session.flush()

            self._courses[row["id"]] = course
            self.report.created["courses"] += 1 if creating else 0
            self.report.updated["courses"] += 0 if creating else 1

            cover = covers.get(row["id"])
            if cover is not None and not course.cover_media_id:
                media_id = self._store(cover["path"], cover["name"], public=True)
                if media_id:
                    course.cover_media_id = media_id
                    db.session.flush()

    def _import_categories(self, hidden: set[tuple[str, int]]) -> None:
        for row in self.source.chapters():
            course = self._courses.get(row["book_id"])
            if course is None:
                # A chapter whose book is gone cannot be placed. It is not an
                # error in the source, it is a chapter of a deleted book.
                continue
            category = self.service.categories.get_by_slug(course.id, row["slug"])
            creating = category is None
            if creating:
                category = Category(course_id=course.id, slug=row["slug"])
                db.session.add(category)
            category.name = row["name"]
            category.position = row["priority"] or 0
            if creating or self.reset_visibility:
                category.hidden = ("chapter", row["id"]) in hidden
            db.session.flush()

            self._categories[row["id"]] = category
            self.report.created["categories"] += 1 if creating else 0
            self.report.updated["categories"] += 0 if creating else 1

    def _import_pages(self, hidden: set[tuple[str, int]]) -> None:
        for row in self.source.pages():
            course = self._courses.get(row["book_id"])
            if course is None:
                continue
            category = self._categories.get(row["chapter_id"])

            page = self.service.pages.get_by_slug(course.id, row["slug"])
            creating = page is None
            if creating:
                page = Page(course_id=course.id, slug=row["slug"])
                db.session.add(page)
            page.name = row["name"]
            page.category_id = category.id if category else None
            page.position = row["priority"] or 0
            page.legacy_id = row["id"]
            # The body is written here as it came, and rewritten once the
            # files it references exist and have addresses.
            page.body_md = row["markdown"] or ""
            if row.get("created_at"):
                page.created_at = row["created_at"]
            if row.get("updated_at"):
                page.updated_at = row["updated_at"]
            if creating or self.reset_visibility:
                page.hidden = ("page", row["id"]) in hidden
            db.session.flush()

            self._pages[row["id"]] = page
            self.report.created["pages"] += 1 if creating else 0
            self.report.updated["pages"] += 0 if creating else 1

            if not (row["markdown"] or "").strip():
                self.report.problem(
                    f"page '{row['slug']}' in '{course.slug}' has an empty body "
                    "in the source"
                )

    # -- files -------------------------------------------------------------

    def _import_files(self) -> None:
        for row in self.source.attachments():
            self._import_one(
                row, kind=KIND_FILE, label=f"attachment {row['id']} ({row['name']})"
            )
        for row in self.source.gallery_images():
            media_id = self._import_one(
                row, kind=KIND_INLINE, label=f"image {row['id']} ({row['name']})"
            )
            if media_id is not None:
                self._image_paths[self._normalise_path(row["path"])] = media_id

    def _import_one(self, row: dict, kind: str, label: str) -> int | None:
        page = self._pages.get(row["page_id"])
        if page is None:
            return None

        existing = self.service.attachments.get_by_legacy(kind, row["id"])
        if existing is not None:
            self._media[(kind, row["id"])] = existing.media_item_id
            self.report.reused[kind] += 1
            return existing.media_item_id

        absolute = self._absolute(row["path"])
        if absolute is None:
            self.report.problem(f"{label}: no file at {row['path']} under the uploads")
            return None
        if self.dry_run:
            # Register the address this file would get, so the rewrite below
            # is exercised too. Without it a rehearsal reports every single
            # reference as unresolvable, which is the opposite of the truth
            # and hides the ones that really are.
            self.report.created[kind] += 1
            self._media[(kind, row["id"])] = 0
            return 0

        from werkzeug.datastructures import FileStorage

        with open(absolute, "rb") as stream:
            attachment = self.service.attach_file(
                page,
                FileStorage(
                    stream=stream,
                    filename=os.path.basename(row["name"]),
                    content_type=self._content_type(row["name"], absolute),
                ),
                name=row["name"],
                kind=kind,
                legacy_id=row["id"],
            )
        if attachment is None:
            self.report.problem(f"{label}: the media library refused it")
            return None

        self._media[(kind, row["id"])] = attachment.media_item_id
        self.report.created[kind] += 1
        return attachment.media_item_id

    def _store(self, path: str, name: str, public: bool) -> int | None:
        """Put a file in the media library outside the page mechanism.

        Only course covers take this route. They are decoration shown in a
        listing rather than material, so they are public like every other
        image the site chose to show, and a course nobody may see is not in
        the listing at all.
        """
        absolute = self._absolute(path)
        if absolute is None or self.dry_run:
            if absolute is None:
                self.report.problem(f"cover: no file at {path} under the uploads")
            return None

        from flask import current_app
        from splent_framework.services.service_locator import get_service_class
        from werkzeug.datastructures import FileStorage

        media = get_service_class(current_app._get_current_object(), "MediaService")()
        with open(absolute, "rb") as stream:
            item = media.save_upload(
                FileStorage(
                    stream=stream,
                    filename=os.path.basename(path),
                    content_type=self._content_type(path, absolute),
                ),
                title=name,
                access="public" if public else "restricted",
            )
        if item is None:
            self.report.problem(f"cover {name}: the media library refused it")
            return None
        self.report.created["covers"] += 1
        return item.id

    def _absolute(self, path: str) -> str | None:
        """Where a source path lands under the extracted uploads.

        The database says ``uploads/files/...`` for a document and
        ``/uploads/images/...`` for an image, but a BookStack backup does
        not lay them out that way: it wraps everything in ``www/`` and puts
        the documents at ``www/files/`` rather than under ``www/uploads/``.
        Every shape is tried, so pointing at the extracted directory is
        enough and nobody has to know which of them their backup used.
        """
        relative = path.lstrip("/")
        candidates = [relative, f"www/{relative}"]
        if relative.startswith("uploads/"):
            bare = relative[len("uploads/") :]
            candidates += [bare, f"www/{bare}"]
        for candidate in candidates:
            absolute = os.path.join(self.uploads_root, candidate)
            if os.path.isfile(absolute):
                return absolute
        return None

    @staticmethod
    def _content_type(name: str, absolute: str) -> str:
        """What this file is, so it can be served as itself.

        Media stores whatever the upload declared and the protected route
        serves that, dropping anything outside its inline-safe set to an
        octet-stream attachment. An import that declares nothing therefore
        turns every PDF into a download and, because the same response
        carries nosniff, stops every embedded image from rendering at all.

        BookStack does not store the type. It keeps the original file name,
        which carries the extension, while the path on disk ends in
        "-pdf" and would guess nothing.
        """
        guessed, _ = mimetypes.guess_type(name)
        if not guessed:
            guessed, _ = mimetypes.guess_type(absolute)
        return guessed or "application/octet-stream"

    @staticmethod
    def _normalise_path(path: str) -> str:
        return "/" + path.lstrip("/")

    # -- bodies ------------------------------------------------------------

    def _rewrite_bodies(self) -> None:
        """Point every reference at its copy here.

        Four kinds of reference appear in these bodies and each is handled
        differently: an image and a document move and must be re-addressed,
        a link to another page keeps working because both wikis use the
        same URL shape, and a link to the frozen archive is not ours to
        touch. What is left of the old absolute address is stripped, so the
        wiki stops naming a host it no longer runs on.
        """
        attachment_pattern = re.compile(
            rf"(?:{re.escape(self.app_url)})?/attachments/(\d+)"
            if self.app_url
            else r"/attachments/(\d+)"
        )
        image_pattern = re.compile(
            rf"(?:{re.escape(self.app_url)})?(/uploads/images/[^\s)\"'<>]+)"
            if self.app_url
            else r"(/uploads/images/[^\s)\"'<>]+)"
        )

        for source_id, page in self._pages.items():
            body = page.body_md or ""
            original = body

            def replace_attachment(match, page=page):
                media_id = self._media.get((KIND_FILE, int(match.group(1))))
                if media_id is None:
                    self.report.problem(
                        f"page '{page.slug}': link to attachment "
                        f"{match.group(1)}, which the source does not have"
                    )
                    return match.group(0)
                self.report.rewritten_links["attachments"] += 1
                return f"/media/file/{media_id}"

            def replace_image(match, page=page):
                media_id = self._resolve_image(match.group(1))
                if media_id is None:
                    self.report.problem(
                        f"page '{page.slug}': image {match.group(1)}, which the "
                        "source does not have"
                    )
                    return match.group(0)
                self.report.rewritten_links["images"] += 1
                return f"/media/file/{media_id}"

            def rewrite(text):
                text = attachment_pattern.sub(replace_attachment, text)
                text = image_pattern.sub(replace_image, text)
                if self.app_url:
                    before = text
                    text = self._own_url_pattern().sub("", text)
                    if text != before:
                        self.report.rewritten_links["absolute"] += 1
                return text

            body = self._outside_code(body, rewrite)

            if body != original:
                page.body_md = body
                db.session.flush()

    # A fenced block, then an inline span. Splitting on this leaves the code
    # at the odd indices, which is what makes it easy to leave alone.
    CODE_SEGMENT = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)

    @classmethod
    def _outside_code(cls, text: str, rewrite) -> str:
        """Apply a rewrite to the prose and never to the code.

        A tutorial that tells the student to open their own application at
        an address is code as far as this is concerned: the wiki that ran at
        localhost:8080 is not the Vagrant box that also does, and rewriting
        one broke the instructions for the other. Anything inside backticks
        is text to copy, so it travels exactly as it was written.
        """
        parts = cls.CODE_SEGMENT.split(text)
        return "".join(
            part if index % 2 else rewrite(part) for index, part in enumerate(parts)
        )

    def _own_url_pattern(self):
        """The old address, but only where a path this wiki serves follows.

        The address is not proof of ownership. Twenty-five references in this
        corpus name the same host with nothing after it, and a handful more
        name endpoints of an application the student is building, all of them
        in tutorials about running something locally. Stripping the host
        everywhere turned "<http://localhost:8080>" into "<>".
        """
        from flask import current_app

        prefixes = ["/uploads/", "/attachments/"]
        for key in ("COURSES_PATH", "ARCHIVE_PATH"):
            value = (current_app.config.get(key) or "").strip("/")
            if value:
                prefixes.append(f"/{value}/")
        alternatives = "|".join(re.escape(prefix) for prefix in prefixes)
        return re.compile(f"{re.escape(self.app_url)}(?=(?:{alternatives}))")

    def _resolve_image(self, path: str) -> int | None:
        """The media item for an image path, thumbnails included.

        BookStack rewrites an image to a scaled copy when it is inserted at
        a size, and the scaled copy is not a row of its own: it lives beside
        the original under a thumbs directory. The original is what we hold,
        so a body pointing at a thumbnail resolves to it.
        """
        normalised = self._normalise_path(path)
        if normalised in self._image_paths:
            return self._image_paths[normalised]
        without_thumb = re.sub(r"/thumbs-\d+-\d+/", "/", normalised)
        return self._image_paths.get(without_thumb)
