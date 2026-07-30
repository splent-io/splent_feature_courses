"""What a page said before somebody changed it.

A wiki without this is a wiki where one careless save destroys work nobody
can get back, and this material is written once a year by people teaching
four other things.

The properties worth pinning are not "a row was written". They are: an edit
that changed nothing leaves no trace, a restore does not lose the present,
and the history outlives the account of whoever wrote it.
"""

import pytest

from splent_framework.db import db
from splent_io.splent_feature_courses.models import Course, Page
from splent_io.splent_feature_courses.services import CoursesService


class Author:
    def __init__(self, id, email):
        self.id = id
        self.email = email


@pytest.fixture
def service():
    return CoursesService()


@pytest.fixture
def page(test_client, test_app):
    """A course with one page.

    It takes test_client so that fixture's database reset runs first.
    Without it the second test in the file inserts a course whose slug the
    first one already used, and the whole file fails on a unique
    constraint rather than on anything it is about.
    """
    with test_app.app_context():
        course = Course(name="ISIA 2027/2028", slug="isia-20272028")
        db.session.add(course)
        db.session.flush()
        item = Page(
            course_id=course.id,
            name="Teoría",
            slug="teoria",
            body_md="# Uno\n\nOriginal.",
        )
        db.session.add(item)
        db.session.commit()
        return item.id


class TestWhatGetsArchived:
    def test_an_edit_keeps_what_the_page_said_before(self, test_app, service, page):
        with test_app.app_context():
            item = db.session.get(Page, page)
            service.save_page(
                item, name="Teoría", body_md="# Dos\n\nCambiado.", author=None
            )

            history = service.page_history(db.session.get(Page, page))
            assert len(history) == 1
            # The previous body, not the new one. Storing the result of an
            # edit would make the history a list of the present.
            assert "Original." in history[0].body_md

    def test_a_rename_counts_as_an_edit(self, test_app, service, page):
        """A reader notices a renamed page as much as a rewritten one."""
        with test_app.app_context():
            item = db.session.get(Page, page)
            service.save_page(item, name="Teoría y práctica", body_md=item.body_md)

            history = service.page_history(db.session.get(Page, page))
            assert len(history) == 1
            assert history[0].name == "Teoría"

    def test_saving_without_changing_the_writing_leaves_no_trace(
        self, test_app, service, page
    ):
        """Moving a page between sections, or changing its release date, is
        not an edit anybody needs to undo. Recording it would bury the edits
        that matter under noise."""
        with test_app.app_context():
            item = db.session.get(Page, page)
            wrote = service.save_page(
                item, name=item.name, body_md=item.body_md, position=7
            )

            assert wrote is False
            assert service.page_history(db.session.get(Page, page)) == []

    def test_whitespace_around_the_title_is_not_an_edit(self, test_app, service, page):
        """The name is stripped on save, so a trailing space typed by
        accident must not manufacture a revision."""
        with test_app.app_context():
            item = db.session.get(Page, page)
            wrote = service.save_page(item, name="  Teoría  ", body_md=item.body_md)
            assert wrote is False


class TestWhoWroteIt:
    def test_the_author_is_recorded_by_address_as_well_as_id(
        self, test_app, service, page
    ):
        """Copied rather than joined: the history of a course has to outlive
        the staff list, and an account closed in three years cannot take the
        record of what a page said with it."""
        with test_app.app_context():
            item = db.session.get(Page, page)
            service.save_page(
                item,
                name=item.name,
                body_md="cambiado",
                author=Author(42, "profe@us.es"),
            )

            revision = service.page_history(db.session.get(Page, page))[0]
            assert revision.author_id == 42
            assert revision.author_email == "profe@us.es"

    def test_an_anonymous_save_is_still_recorded(self, test_app, service, page):
        with test_app.app_context():
            item = db.session.get(Page, page)
            service.save_page(item, name=item.name, body_md="cambiado", author=None)

            revision = service.page_history(db.session.get(Page, page))[0]
            assert revision.author_id is None


class TestRestoring:
    def test_it_puts_the_old_text_back(self, test_app, service, page):
        with test_app.app_context():
            item = db.session.get(Page, page)
            service.save_page(item, name=item.name, body_md="# Dos\n\nCambiado.")
            old = service.page_history(db.session.get(Page, page))[0]

            service.restore_revision(db.session.get(Page, page), old)

            assert "Original." in db.session.get(Page, page).body_md

    def test_it_does_not_lose_the_present(self, test_app, service, page):
        """A history that lost the present when reaching into the past would
        be a trap, so a restore is an edit like any other and can itself be
        undone."""
        with test_app.app_context():
            item = db.session.get(Page, page)
            service.save_page(item, name=item.name, body_md="# Dos\n\nCambiado.")
            old = service.page_history(db.session.get(Page, page))[0]

            service.restore_revision(db.session.get(Page, page), old)

            history = service.page_history(db.session.get(Page, page))
            assert len(history) == 2
            assert "Cambiado." in history[0].body_md


class TestTheHistoryDoesNotGrowForever:
    def test_it_keeps_the_newest_and_drops_the_rest(self, test_app, service, page):
        with test_app.app_context():
            original = CoursesService.REVISION_LIMIT
            CoursesService.REVISION_LIMIT = 3
            try:
                for n in range(6):
                    item = db.session.get(Page, page)
                    service.save_page(item, name=item.name, body_md=f"cuerpo {n}")

                history = service.page_history(db.session.get(Page, page))
                assert len(history) == 3
                # The newest survive: the oldest copy of a page is the one
                # nobody has ever asked for.
                #
                # "cuerpo 4", not "cuerpo 5". A revision is what the page
                # said *before* a save, so the last body written is the one
                # on the page and the newest revision is the one before it.
                assert "cuerpo 4" in history[0].body_md
                assert db.session.get(Page, page).body_md == "cuerpo 5"
            finally:
                CoursesService.REVISION_LIMIT = original


class TestOneHistoryPerPage:
    def test_a_revision_is_only_reachable_through_its_own_page(
        self, test_app, service, page
    ):
        """A revision id on its own would let anybody who can edit one page
        read the history of another by guessing numbers."""
        with test_app.app_context():
            item = db.session.get(Page, page)
            service.save_page(item, name=item.name, body_md="cambiado")
            revision = service.page_history(db.session.get(Page, page))[0]

            assert service.revisions.get_for_page(page, revision.id) is not None
            assert service.revisions.get_for_page(page + 999, revision.id) is None


class TestTheVisibilityBadgeTellsTheTruth:
    """It used to ask the row, and lied in both directions.

    Upwards: a page inside a withheld course showed as Visible while every
    reader got a 404, so a teacher checking that this week's material is out
    read that badge and stopped looking. Downwards: with hidden ticked and a
    date set it announced a release that was never going to arrive, because
    hidden wins over the schedule for good.
    """

    def test_a_page_nobody_is_withholding_is_visible(self, test_app, service, page):
        with test_app.app_context():
            item = db.session.get(Page, page)
            assert service.visibility_state(item)["state"] == "visible"

    def test_hidden_never_announces_a_date(self, test_app, service, page):
        from datetime import datetime, timedelta, timezone

        with test_app.app_context():
            item = db.session.get(Page, page)
            item.hidden = True
            item.publish_at = datetime.now(timezone.utc) + timedelta(days=30)
            db.session.commit()

            status = service.visibility_state(db.session.get(Page, page))
            assert status["state"] == "hidden"
            # The date is real in the column and meaningless in fact, so it
            # must not reach the screen.
            assert status["moment"] is None

    def test_a_future_date_without_hidden_is_a_schedule(self, test_app, service, page):
        from datetime import datetime, timedelta, timezone

        with test_app.app_context():
            item = db.session.get(Page, page)
            item.publish_at = datetime.now(timezone.utc) + timedelta(days=2)
            db.session.commit()

            status = service.visibility_state(db.session.get(Page, page))
            assert status["state"] == "scheduled"
            assert status["moment"] is not None

    def test_a_released_page_inside_a_withheld_course_is_not_visible(
        self, test_app, service, page
    ):
        """The lie that mattered: readers get a 404 and the badge said
        Visible."""
        with test_app.app_context():
            item = db.session.get(Page, page)
            item.course.hidden = True
            db.session.commit()

            status = service.visibility_state(db.session.get(Page, page))
            assert status["state"] == "blocked"
            # Named, because a teacher has to know which screen to go and fix.
            assert status["blocked_by"].name == "ISIA 2027/2028"

    def test_it_names_the_nearest_thing_holding_it(self, test_app, service, page):
        """Saying the course is withheld when the section is too sends
        somebody to the wrong screen."""
        from splent_io.splent_feature_courses.models import Category

        with test_app.app_context():
            item = db.session.get(Page, page)
            category = Category(
                course_id=item.course_id,
                name="Prácticas",
                slug="practicas",
                hidden=True,
            )
            db.session.add(category)
            db.session.flush()
            item.category_id = category.id
            item.course.hidden = True
            db.session.commit()

            status = service.visibility_state(db.session.get(Page, page))
            assert status["blocked_by"].name == "Prácticas"

    def test_the_badge_agrees_with_what_a_reader_gets(self, test_app, service, page):
        """The property underneath all of the above: whenever the badge says
        visible, an anonymous reader can read it, and never otherwise."""
        from datetime import datetime, timedelta, timezone

        cases = [
            {},
            {"hidden": True},
            {"publish_at": datetime.now(timezone.utc) + timedelta(days=1)},
            {"publish_at": datetime.now(timezone.utc) - timedelta(days=1)},
        ]
        for fields in cases:
            with test_app.app_context():
                item = db.session.get(Page, page)
                item.hidden = False
                item.publish_at = None
                for key, value in fields.items():
                    setattr(item, key, value)
                db.session.commit()

                item = db.session.get(Page, page)
                says_visible = service.visibility_state(item)["state"] == "visible"
                reader_sees = service.page_visible(item, user=None)
                assert says_visible == reader_sees, fields
