"""Check a BookStack import against the wiki it came from.

An import that reports success has only said that it did not crash. This
re-reads the source and this product side by side and asks whether the
material actually arrived: the same courses with the same shape, every page
with its content intact down to the code blocks, every file present and the
same size on disk, every link still resolving, and, the one that matters
most, a student seeing exactly what a student saw before.

Findings carry a severity, because not everything that differs is a defect:

  FAIL  the import lost or broke something. It has to be fixed.
  WARN  worth a look and arguably faithful, such as a link that was already
        dead in the source, or something added here after the import.
  INFO  a deliberate difference, counted so that it stays visible.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field

from flask import current_app

from splent_io.splent_feature_courses.models import KIND_FILE, KIND_INLINE
from splent_io.splent_feature_courses.services import CoursesService

FAIL = "FAIL"
WARN = "WARN"
INFO = "INFO"

CODE_FENCE = re.compile(r"^\s*```", re.MULTILINE)
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
HEADING = re.compile(r"^\s*#{1,6}\s+\S", re.MULTILINE)
IMAGE_REF = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
MEDIA_REF = re.compile(r"/media/file/(\d+)")


@dataclass
class Finding:
    severity: str
    course: str
    check: str
    detail: str


@dataclass
class VerificationReport:
    findings: list[Finding] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)

    def add(self, severity: str, course: str, check: str, detail: str) -> None:
        self.findings.append(Finding(severity, course, check, detail))
        self.counts[severity] += 1
        self.counts[f"{severity}:{check}"] += 1

    @property
    def failed(self) -> bool:
        return self.counts[FAIL] > 0

    def by_check(self, severity: str) -> list[tuple[str, int]]:
        prefix = f"{severity}:"
        return sorted(
            (
                (key[len(prefix) :], count)
                for key, count in self.counts.items()
                if key.startswith(prefix)
            ),
            key=lambda pair: -pair[1],
        )


class BookStackVerification:
    """Re-read both sides and report every difference that matters."""

    def __init__(self, source, uploads_root: str, app_url: str | None = None):
        self.source = source
        self.uploads_root = uploads_root
        self.app_url = (app_url or source.detect_app_url() or "").rstrip("/")
        self.service = CoursesService()
        self.report = VerificationReport()

    # -- entry point -------------------------------------------------------

    def run(self) -> VerificationReport:
        books = self.source.books()
        chapters = self.source.chapters()
        pages = self.source.pages()

        courses = self._check_structure(books, chapters)
        page_rows = self._check_pages(books, chapters, pages, courses)
        self._check_files(page_rows)
        self._check_links(page_rows)
        self._check_visibility(books, chapters, pages, courses)
        self._check_extras(books, pages, courses)
        return self.report

    # -- structure ---------------------------------------------------------

    def _check_structure(self, books, chapters) -> dict:
        courses = {}
        for book in books:
            course = self.service.repository.get_by_slug(book["slug"])
            if course is None:
                self.report.add(
                    FAIL, book["slug"], "structure", "the course is not here at all"
                )
                continue
            courses[book["id"]] = course
            if course.name != book["name"]:
                self.report.add(
                    WARN,
                    book["slug"],
                    "structure",
                    f"named '{course.name}' here and '{book['name']}' there",
                )

        for chapter in chapters:
            course = courses.get(chapter["book_id"])
            if course is None:
                continue
            category = self.service.categories.get_by_slug(course.id, chapter["slug"])
            if category is None:
                self.report.add(
                    FAIL,
                    course.slug,
                    "structure",
                    f"category missing: {chapter['slug']}",
                )
            elif category.position != (chapter["priority"] or 0):
                self.report.add(
                    WARN,
                    course.slug,
                    "structure",
                    f"category '{chapter['slug']}' sits at {category.position}, "
                    f"not {chapter['priority']}",
                )
        return courses

    # -- pages -------------------------------------------------------------

    def _check_pages(
        self, books, chapters, pages, courses
    ) -> list[tuple[dict, object]]:
        chapter_slugs = {row["id"]: row["slug"] for row in chapters}
        matched = []

        for row in pages:
            course = courses.get(row["book_id"])
            if course is None:
                continue
            page = self.service.pages.get_by_slug(course.id, row["slug"])
            if page is None:
                self.report.add(
                    FAIL, course.slug, "coverage", f"page missing: {row['slug']}"
                )
                continue
            matched.append((row, page))

            expected_category = chapter_slugs.get(row["chapter_id"])
            actual_category = page.category.slug if page.category else None
            if expected_category != actual_category:
                self.report.add(
                    FAIL,
                    course.slug,
                    "structure",
                    f"page '{row['slug']}' sits under {actual_category}, "
                    f"not {expected_category}",
                )

            self._check_body(course.slug, row, page)
        return matched

    def _check_body(self, course_slug: str, row: dict, page) -> None:
        source = row["markdown"] or ""
        target = page.body_md or ""

        if source.strip() and not target.strip():
            self.report.add(
                FAIL, course_slug, "render", f"page '{row['slug']}' arrived empty"
            )
            return
        if not source.strip():
            self.report.add(
                INFO,
                course_slug,
                "render",
                f"page '{row['slug']}' was already empty in the source",
            )
            return

        source_fences = len(CODE_FENCE.findall(source))
        target_fences = len(CODE_FENCE.findall(target))
        if source_fences != target_fences:
            self.report.add(
                FAIL,
                course_slug,
                "code",
                f"page '{row['slug']}' has {target_fences // 2} code blocks, "
                f"not {source_fences // 2}",
            )
        elif source_fences:
            self._check_code_verbatim(course_slug, row["slug"], source, target)

        source_rows = len(TABLE_ROW.findall(source))
        target_rows = len(TABLE_ROW.findall(target))
        if source_rows != target_rows:
            self.report.add(
                FAIL,
                course_slug,
                "tables",
                f"page '{row['slug']}' has {target_rows} table rows, not {source_rows}",
            )

        source_headings = len(HEADING.findall(source))
        target_headings = len(HEADING.findall(target))
        if source_headings != target_headings:
            self.report.add(
                FAIL,
                course_slug,
                "render",
                f"page '{row['slug']}' has {target_headings} headings, "
                f"not {source_headings}",
            )

        source_images = len(IMAGE_REF.findall(source))
        target_images = len(IMAGE_REF.findall(target))
        if source_images != target_images:
            self.report.add(
                FAIL,
                course_slug,
                "images",
                f"page '{row['slug']}' embeds {target_images} images, "
                f"not {source_images}",
            )

        if self.app_url and self.app_url in target:
            # Only a path this wiki serves is evidence the rewrite missed
            # something. The same host followed by nothing, or by an
            # endpoint of the application a tutorial has the student run,
            # belongs to that application and has to survive untouched.
            own = self._own_url_pattern()
            if own is not None and own.search(target):
                self.report.add(
                    FAIL,
                    course_slug,
                    "links",
                    f"page '{row['slug']}' still points at {self.app_url} for "
                    "something this wiki serves",
                )
            else:
                self.report.add(
                    INFO,
                    course_slug,
                    "links",
                    f"page '{row['slug']}' names {self.app_url}, which here is "
                    "somebody else's local server and was left alone",
                )

    def _own_url_pattern(self):
        """The old address followed by a path this wiki actually serves."""
        if not self.app_url:
            return None
        prefixes = ["/uploads/", "/attachments/"]
        for key in ("COURSES_PATH", "ARCHIVE_PATH"):
            value = (current_app.config.get(key) or "").strip("/")
            if value:
                prefixes.append(f"/{value}/")
        alternatives = "|".join(re.escape(prefix) for prefix in prefixes)
        return re.compile(f"{re.escape(self.app_url)}(?=(?:{alternatives}))")

    def _check_code_verbatim(
        self, course_slug: str, page_slug: str, source: str, target: str
    ) -> None:
        """The first and last line of every code block, character for character.

        Counting blocks catches a converter that dropped one. It does not
        catch a converter that mangled what is inside, and code is exactly
        where a stray escape or a smart quote turns an instruction the
        student pastes into one that fails.
        """
        source_blocks = source.split("```")[1::2]
        target_blocks = target.split("```")[1::2]
        for index, (before, after) in enumerate(zip(source_blocks, target_blocks)):
            before_lines = [line for line in before.splitlines() if line.strip()]
            after_lines = [line for line in after.splitlines() if line.strip()]
            if not before_lines or not after_lines:
                continue
            if before_lines[0] != after_lines[0] or before_lines[-1] != after_lines[-1]:
                self.report.add(
                    FAIL,
                    course_slug,
                    "code",
                    f"page '{page_slug}' code block {index + 1} does not match "
                    "the source",
                )

    # -- files -------------------------------------------------------------

    def _check_files(self, page_rows) -> None:
        pages_by_source = {row["id"]: (row, page) for row, page in page_rows}

        for kind, rows, label in (
            (KIND_FILE, self.source.attachments(), "document"),
            (KIND_INLINE, self.source.gallery_images(), "image"),
        ):
            for row in rows:
                pair = pages_by_source.get(row["page_id"])
                if pair is None:
                    continue
                source_row, page = pair
                course_slug = page.course.slug

                attachment = self.service.attachments.get_by_legacy(kind, row["id"])
                if attachment is None:
                    self.report.add(
                        FAIL,
                        course_slug,
                        "attachments" if kind == KIND_FILE else "images",
                        f"{label} '{row['name']}' of page "
                        f"'{source_row['slug']}' is not here",
                    )
                    continue
                if attachment.page_id != page.id:
                    self.report.add(
                        FAIL,
                        course_slug,
                        "attachments" if kind == KIND_FILE else "images",
                        f"{label} '{row['name']}' hangs off the wrong page",
                    )
                self._check_bytes(course_slug, kind, label, row, attachment)

    def _check_bytes(self, course_slug: str, kind, label, row, attachment) -> None:
        """Same file, same size. A truncated upload is silent otherwise."""
        source_path = self._source_file(row["path"])
        if source_path is None:
            self.report.add(
                WARN,
                course_slug,
                "attachments" if kind == KIND_FILE else "images",
                f"{label} '{row['name']}' has no file in the source uploads",
            )
            return

        stored = self._stored_file(attachment.media_item_id)
        if stored is None:
            self.report.add(
                FAIL,
                course_slug,
                "attachments" if kind == KIND_FILE else "images",
                f"{label} '{row['name']}' has a record but no bytes on disk",
            )
            return

        expected, actual = os.path.getsize(source_path), os.path.getsize(stored)
        if expected != actual:
            self.report.add(
                FAIL,
                course_slug,
                "attachments" if kind == KIND_FILE else "images",
                f"{label} '{row['name']}' is {actual} bytes here and {expected} there",
            )

    def _source_file(self, path: str) -> str | None:
        from splent_io.splent_feature_courses.bookstack import BookStackImport

        finder = BookStackImport.__new__(BookStackImport)
        finder.uploads_root = self.uploads_root
        return BookStackImport._absolute(finder, path)

    def _stored_file(self, media_item_id: int) -> str | None:
        """Where media put the bytes, asked of media.

        Working it out here from the configured directory looked obvious and
        was wrong: with nothing configured the library falls back to the
        instance folder, so this reported all five hundred and fifty-nine
        files as missing while every one of them was being served.
        """
        from splent_framework.services.service_locator import get_service_class

        media = get_service_class(current_app._get_current_object(), "MediaService")()
        item = media.repository.get_by_id(media_item_id)
        if item is None:
            return None
        candidate = media.file_path(item)
        return candidate if os.path.isfile(candidate) else None

    # -- links -------------------------------------------------------------

    def _check_links(self, page_rows) -> None:
        """Every internal link still lands somewhere.

        Both wikis address a page the same way, so a link that worked there
        works here unless the page it names never arrived. A link that was
        already broken in the source is a WARN: this migration did not
        break it and fixing it is editorial work.
        """
        path = current_app.config["COURSES_PATH"]
        segment = current_app.config["COURSES_PAGE_SEGMENT"]
        pattern = re.compile(
            rf"/{re.escape(path)}/([\w-]+)/{re.escape(segment)}/([\w-]+)"
        )

        source_pages = {(row["book_id"], row["slug"]) for row, _ in page_rows}
        source_slugs = {row["slug"] for row, _ in page_rows}

        for row, page in page_rows:
            course_slug = page.course.slug
            for course_ref, page_ref in pattern.findall(page.body_md or ""):
                target_course = self.service.repository.get_by_slug(course_ref)
                if (
                    target_course is None
                    or self.service.pages.get_by_slug(target_course.id, page_ref)
                    is None
                ):
                    severity = WARN if page_ref not in source_slugs else FAIL
                    self.report.add(
                        severity,
                        course_slug,
                        "links",
                        f"page '{row['slug']}' links to {course_ref}/{page_ref}, "
                        + (
                            "which the source did not have either"
                            if severity == WARN
                            else "which should be here"
                        ),
                    )

            for media_id in MEDIA_REF.findall(page.body_md or ""):
                if self.service.attachments.get_by_media_item(int(media_id)) is None:
                    self.report.add(
                        FAIL,
                        course_slug,
                        "links",
                        f"page '{row['slug']}' points at file {media_id}, "
                        "which no page claims",
                    )
        del source_pages

    # -- visibility --------------------------------------------------------

    def _check_visibility(self, books, chapters, pages, courses) -> None:
        """What a student sees here, against what a student saw there.

        This is the check the whole wiki exists for. Everything else can be
        re-imported; material that becomes readable a year early cannot be
        taken back.
        """
        hidden = self.source.hidden_entities()
        hidden_books = {entity_id for kind, entity_id in hidden if kind == "book"}
        hidden_chapters = {entity_id for kind, entity_id in hidden if kind == "chapter"}
        hidden_pages = {entity_id for kind, entity_id in hidden if kind == "page"}

        chapter_by_id = {row["id"]: row for row in chapters}

        for book in books:
            course = courses.get(book["id"])
            if course is None:
                continue
            there = book["id"] not in hidden_books
            here = self.service.course_visible(course, None)
            if there != here:
                self.report.add(
                    FAIL,
                    course.slug,
                    "visibility",
                    "a student "
                    + ("cannot" if not here else "can")
                    + " open this course here, and "
                    + ("could" if there else "could not")
                    + " there",
                )

        for row in pages:
            course = courses.get(row["book_id"])
            if course is None:
                continue
            chapter = chapter_by_id.get(row["chapter_id"])
            there = (
                row["book_id"] not in hidden_books
                and row["id"] not in hidden_pages
                and (chapter is None or chapter["id"] not in hidden_chapters)
            )
            page = self.service.pages.get_by_slug(course.id, row["slug"])
            if page is None:
                continue
            here = self.service.page_visible(page, None)
            if there != here:
                self.report.add(
                    FAIL,
                    course.slug,
                    "visibility",
                    f"page '{row['slug']}' is "
                    + ("readable" if here else "withheld")
                    + " here and was "
                    + ("readable" if there else "withheld")
                    + " there",
                )

    # -- what is here and was not there ------------------------------------

    def _check_extras(self, books, pages, courses) -> None:
        source_course_slugs = {book["slug"] for book in books}
        for course in self.service.repository.list_newest_first():
            if course.slug not in source_course_slugs:
                self.report.add(
                    WARN,
                    course.slug,
                    "coverage",
                    "this course is here and not in the source, so it was "
                    "started after the import",
                )

        expected = {(row["book_id"], row["slug"]) for row in pages}
        for book in books:
            course = courses.get(book["id"])
            if course is None:
                continue
            for page in course.pages:
                if (book["id"], page.slug) not in expected:
                    self.report.add(
                        WARN,
                        course.slug,
                        "coverage",
                        f"page '{page.slug}' is here and not in the source",
                    )
