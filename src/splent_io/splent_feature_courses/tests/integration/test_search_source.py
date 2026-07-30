"""
Integration tests for the search source this feature registers.

An index is a cache and never the authority. Whatever an engine proposes,
resolve is asked again when the results are about to be shown, and it
answers from the page's own release controls as they are at that moment.
These tests are about that second question, because it is the one that
decides whether next week's exam turns up in a search box.

Withholding has to be total. A candidate this reader may not see comes
back as None, not as a result with the body removed, because a title says
enough on its own. "June exam solutions" is the leak, not its contents.
"""

from datetime import datetime, timedelta, timezone

import pytest

from splent_framework.db import db
from splent_io.splent_feature_auth.models import User
from splent_io.splent_feature_courses.models import Category, Course, Page
from splent_io.splent_feature_courses.services import (
    plain_text,
    search_fetch,
    search_find,
    search_resolve,
)


@pytest.fixture
def material(test_client, test_app):
    """A released course with one released page, and a staff account.

    It takes test_client so that fixture's database reset happens before
    this data is created. Unlike the functional tests, nothing here goes
    through the test client, so each test opens the app context it needs
    around the call it is making.
    """
    with test_app.app_context():
        course = Course(name="Subject 2026/2027", slug="search-2026-2027")
        db.session.add(course)
        db.session.flush()
        category = Category(
            course_id=course.id, name="Practicals", slug="search-practicals", position=0
        )
        db.session.add(category)
        db.session.flush()
        page = Page(
            course_id=course.id,
            category_id=category.id,
            name="Lab 5",
            slug="search-lab-5",
            body_md="# Lab 5\n\nBring a **laptop** and read [the notes](/notes).",
        )
        db.session.add(page)

        staff = User(email="search-staff@example.com", active=True, role="staff")
        staff.set_password("1234")
        student = User(email="search-student@example.com", active=True, role="user")
        student.set_password("1234")
        db.session.add_all([staff, student])
        db.session.commit()

        ids = {
            "course_id": course.id,
            "category_id": category.id,
            "page_id": page.id,
            "staff_id": staff.id,
            "student_id": student.id,
        }

    return ids


def _withhold(test_app, model, item_id, **fields):
    with test_app.app_context():
        item = db.session.get(model, item_id)
        for key, value in fields.items():
            setattr(item, key, value)
        db.session.commit()


def _user(user_id):
    return db.session.get(User, user_id)


def test_a_released_page_resolves_for_an_anonymous_reader(test_app, material):
    with test_app.app_context():
        result = search_resolve(material["page_id"], None)

    assert result is not None
    assert result["title"] == "Lab 5"
    assert result["url"].endswith("/search-lab-5")
    assert "laptop" in result["snippet"]
    # Where it lives, because ten academic years hold ten pages called
    # Lab 5 and a title alone does not say which one this is.
    assert result["course"] == "Subject 2026/2027"
    assert result["category"] == "Practicals"


def test_a_withheld_page_resolves_to_nothing_for_an_anonymous_reader(
    test_app, material
):
    _withhold(test_app, Page, material["page_id"], hidden=True)

    with test_app.app_context():
        assert search_resolve(material["page_id"], None) is None


def test_staff_resolve_the_page_that_is_still_withheld(test_app, material):
    """Staff prepare next week's session, so they see what is queued."""
    _withhold(test_app, Page, material["page_id"], hidden=True)

    with test_app.app_context():
        result = search_resolve(material["page_id"], _user(material["staff_id"]))

    assert result is not None
    assert result["title"] == "Lab 5"


def test_an_ordinary_account_resolves_no_more_than_an_anonymous_one(test_app, material):
    """Having a login is not a way past the release date."""
    _withhold(test_app, Page, material["page_id"], hidden=True)

    with test_app.app_context():
        assert (
            search_resolve(material["page_id"], _user(material["student_id"])) is None
        )


def test_a_page_scheduled_for_later_resolves_to_nothing_yet(test_app, material):
    later = datetime.now(timezone.utc) + timedelta(days=2)
    _withhold(test_app, Page, material["page_id"], publish_at=later)

    with test_app.app_context():
        assert search_resolve(material["page_id"], None) is None


def test_a_released_page_inside_a_withheld_course_resolves_to_nothing(
    test_app, material
):
    """The branch travels with the leaf, exactly as it does on the site."""
    _withhold(test_app, Course, material["course_id"], hidden=True)

    with test_app.app_context():
        assert search_resolve(material["page_id"], None) is None


def test_a_released_page_inside_a_withheld_category_resolves_to_nothing(
    test_app, material
):
    _withhold(test_app, Category, material["category_id"], hidden=True)

    with test_app.app_context():
        assert search_resolve(material["page_id"], None) is None


def test_an_id_that_does_not_exist_resolves_to_nothing(test_app, material):
    """An index outlives the material it was built from.

    A page deleted this morning is still a candidate in an index written
    last night, and a resolver that raised on it would take the whole
    results page down instead of dropping one hit.
    """
    with test_app.app_context():
        assert search_resolve(999999, None) is None
        assert search_resolve("999999", None) is None
        assert search_resolve("not-a-page-id", None) is None
        assert search_resolve(None, None) is None


def test_a_document_says_nothing_about_who_may_read_it(test_app, material):
    """An index that recorded visibility would be wrong within the hour."""
    _withhold(test_app, Page, material["page_id"], hidden=True)

    with test_app.app_context():
        documents = list(search_fetch())

    assert len(documents) == 1
    document = documents[0]
    assert document["id"] == str(material["page_id"])
    assert document["title"] == "Lab 5"
    assert "laptop" in document["body"]
    # A withheld page is still indexed. It has to be, or releasing it
    # would need a reindex before anyone could find it.
    assert not {"hidden", "publish_at", "visible", "released"} & set(document)


def test_without_an_engine_find_answers_only_what_the_reader_may_see(
    test_app, material
):
    with test_app.app_context():
        assert [result["title"] for result in search_find("laptop", None)] == ["Lab 5"]

    _withhold(test_app, Page, material["page_id"], hidden=True)

    with test_app.app_context():
        assert search_find("laptop", None) == []
        assert [
            result["title"]
            for result in search_find("laptop", _user(material["staff_id"]))
        ] == ["Lab 5"]


def test_a_snippet_reads_as_prose_rather_than_as_markdown(test_app, material):
    with test_app.app_context():
        snippet = search_resolve(material["page_id"], None)["snippet"]

    assert "**" not in snippet
    assert "#" not in snippet
    # A link keeps its words and loses its URL, so the extract still reads.
    assert "the notes" in snippet
    assert "/notes" not in snippet


def test_plain_text_drops_the_syntax_and_keeps_the_words():
    body = (
        "## Practical 3\n\n"
        "- Install `docker` first\n"
        "- Read [the guide](https://example.org/guide)\n\n"
        "```bash\napt install docker\n```\n\n"
        "> Bring a laptop.\n"
    )
    text = plain_text(body)

    assert "Practical 3" in text
    assert "docker" in text
    assert "the guide" in text
    assert "Bring a laptop." in text
    assert "https://example.org/guide" not in text
    assert "apt install" not in text
    assert "##" not in text
