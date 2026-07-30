"""
Functional tests for the visibility contract, which is the whole point of
this feature.

Withheld material must be indistinguishable from material that does not
exist. Not 403, which confirms it is there; not a 500, which announces it
loudly; the same 404 a wrong URL gets. Staff, meanwhile, must see
everything, or they cannot prepare next week's session.

The scaffolded version of this file requested "/courses", a path this
feature has never served: the URL words come from configuration, and they
default to Spanish.
"""

from datetime import datetime, timedelta, timezone

import pytest

from splent_framework.db import db
from splent_io.splent_feature_auth.models import User
from splent_io.splent_feature_courses.models import Category, Course, Page


def _path(test_app, *parts) -> str:
    path = test_app.config["COURSES_PATH"]
    return "/".join([f"/{path}", *parts])


def _page_path(test_app, course, page) -> str:
    return _path(
        test_app, course.slug, test_app.config["COURSES_PAGE_SEGMENT"], page.slug
    )


def _category_path(test_app, course, category) -> str:
    return _path(
        test_app,
        course.slug,
        test_app.config["COURSES_CATEGORY_SEGMENT"],
        category.slug,
    )


@pytest.fixture
def material(test_app):
    """A released course with one page, and the staff account that owns it.

    The app context is closed before yielding on purpose. Flask reuses an
    already pushed context for a test client request, so a context held
    open across the test would serve the request from this session's
    identity map and hide any change made in between.
    """
    with test_app.app_context():
        course = Course(name="Subject 2026/2027", slug="subject-2026-2027")
        db.session.add(course)
        db.session.flush()
        category = Category(
            course_id=course.id, name="Practicals", slug="practicals", position=0
        )
        db.session.add(category)
        db.session.flush()
        page = Page(
            course_id=course.id,
            category_id=category.id,
            name="Lab 5",
            slug="lab-5",
            body_md="# Lab 5\n\nBring a laptop.",
        )
        db.session.add(page)

        staff = User(email="staff@example.com", active=True, role="staff")
        staff.set_password("1234")
        db.session.add(staff)
        db.session.commit()

        ids = {
            "course_slug": course.slug,
            "category_slug": category.slug,
            "page_slug": page.slug,
            "course_id": course.id,
            "category_id": category.id,
            "page_id": page.id,
        }

    return ids


def _as_staff(test_client):
    test_client.post(
        "/login",
        data={"email": "staff@example.com", "password": "1234"},
        follow_redirects=True,
    )


def _withhold(test_app, model, item_id, **fields):
    with test_app.app_context():
        item = db.session.get(model, item_id)
        for key, value in fields.items():
            setattr(item, key, value)
        db.session.commit()


def test_released_material_is_readable_by_anyone(test_client, test_app, material):
    with test_app.app_context():
        course = db.session.get(Course, material["course_id"])
        page = db.session.get(Page, material["page_id"])
        course_path = _path(test_app, course.slug)
        page_path = _page_path(test_app, course, page)

    assert test_client.get(course_path).status_code == 200
    response = test_client.get(page_path)
    assert response.status_code == 200
    assert b"Bring a laptop" in response.data


def test_a_hidden_course_answers_the_same_as_a_missing_one(
    test_client, test_app, material
):
    with test_app.app_context():
        course = db.session.get(Course, material["course_id"])
        course_path = _path(test_app, course.slug)
    _withhold(test_app, Course, material["course_id"], hidden=True)

    withheld = test_client.get(course_path)
    missing = test_client.get(_path(test_app, "no-such-course"))
    assert withheld.status_code == 404
    assert missing.status_code == 404
    # Byte for byte: a difference here is what tells a student the course
    # exists and is being kept from them.
    assert withheld.data == missing.data


def test_a_hidden_course_is_absent_from_the_listing(test_client, test_app, material):
    _withhold(test_app, Course, material["course_id"], hidden=True)
    response = test_client.get(_path(test_app))
    assert response.status_code == 200
    assert b"Subject 2026/2027" not in response.data


def test_a_page_scheduled_for_later_is_not_readable_yet(
    test_client, test_app, material
):
    later = datetime.now(timezone.utc) + timedelta(days=2)
    _withhold(test_app, Page, material["page_id"], publish_at=later)
    with test_app.app_context():
        course = db.session.get(Course, material["course_id"])
        page = db.session.get(Page, material["page_id"])
        page_path = _page_path(test_app, course, page)

    assert test_client.get(page_path).status_code == 404


def test_a_page_whose_moment_has_passed_is_readable(test_client, test_app, material):
    earlier = datetime.now(timezone.utc) - timedelta(minutes=1)
    _withhold(test_app, Page, material["page_id"], publish_at=earlier)
    with test_app.app_context():
        course = db.session.get(Course, material["course_id"])
        page = db.session.get(Page, material["page_id"])
        page_path = _page_path(test_app, course, page)

    assert test_client.get(page_path).status_code == 200


def test_a_released_page_inside_a_withheld_category_stays_withheld(
    test_client, test_app, material
):
    """Sensitive material is scheduled on the branch, not leaf by leaf."""
    _withhold(test_app, Category, material["category_id"], hidden=True)
    with test_app.app_context():
        course = db.session.get(Course, material["course_id"])
        page = db.session.get(Page, material["page_id"])
        category = db.session.get(Category, material["category_id"])
        page_path = _page_path(test_app, course, page)
        category_path = _category_path(test_app, course, category)

    assert test_client.get(page_path).status_code == 404
    assert test_client.get(category_path).status_code == 404


def test_a_released_page_inside_a_withheld_course_stays_withheld(
    test_client, test_app, material
):
    _withhold(test_app, Course, material["course_id"], hidden=True)
    with test_app.app_context():
        course = db.session.get(Course, material["course_id"])
        page = db.session.get(Page, material["page_id"])
        page_path = _page_path(test_app, course, page)

    assert test_client.get(page_path).status_code == 404


def test_the_old_search_url_sends_readers_to_the_one_search_box(
    test_client, test_app, material
):
    """Courses used to answer searches itself, over its own material only.

    A product now has one box for everything, so this feature answers by
    handing the reader over rather than keeping a second, narrower search
    alive beside it: two boxes disagreeing about what exists is worse than
    either. Old links and bookmarks still work, which is why the route is a
    redirect and not a 404.

    The guarantee this test used to make, that a withheld page never shows
    up in results, did not move with the route: it was never the searching
    that enforced it. Visibility is decided by this feature, at request time,
    once per candidate, through the resolver it registers, and an index is
    only ever allowed to propose. Pinned where that decision lives, in
    splent_feature_search: test_a_stale_index_does_not_leak_a_withheld_title.
    """
    later = datetime.now(timezone.utc) + timedelta(days=2)
    _withhold(test_app, Page, material["page_id"], publish_at=later)

    response = test_client.get(_path(test_app, "search"), query_string={"q": "Lab"})

    assert response.status_code == 302
    assert b"Lab 5" not in response.data


def test_staff_read_what_is_still_withheld(test_client, test_app, material):
    _withhold(test_app, Course, material["course_id"], hidden=True)
    with test_app.app_context():
        course = db.session.get(Course, material["course_id"])
        page = db.session.get(Page, material["page_id"])
        course_path = _path(test_app, course.slug)
        page_path = _page_path(test_app, course, page)

    _as_staff(test_client)
    assert test_client.get(course_path).status_code == 200
    assert test_client.get(page_path).status_code == 200


def test_an_ordinary_account_reads_no_more_than_an_anonymous_one(
    test_client, test_app, material
):
    """A student account is not a way past the release date."""
    with test_app.app_context():
        student = User(email="student@example.com", active=True, role="user")
        student.set_password("1234")
        db.session.add(student)
        db.session.commit()
        course = db.session.get(Course, material["course_id"])
        course_path = _path(test_app, course.slug)
    _withhold(test_app, Course, material["course_id"], hidden=True)

    test_client.post(
        "/login",
        data={"email": "student@example.com", "password": "1234"},
        follow_redirects=True,
    )
    assert test_client.get(course_path).status_code == 404


def test_the_back_office_refuses_an_ordinary_account(test_client, test_app, material):
    with test_app.app_context():
        student = User(email="student2@example.com", active=True, role="user")
        student.set_password("1234")
        db.session.add(student)
        db.session.commit()

    test_client.post(
        "/login",
        data={"email": "student2@example.com", "password": "1234"},
        follow_redirects=True,
    )
    assert test_client.get("/admin/courses").status_code == 403


def test_the_live_preview_renders_exactly_what_a_reader_gets(
    test_client, test_app, material
):
    """The editor's preview asks the server, and the server answers with the
    same pipeline the public page uses: highlighting, plugins and the
    allowlist included. A client-side preview was almost the published
    page, and almost is what an editor cannot check against."""
    _as_staff(test_client)

    response = test_client.post(
        "/admin/courses/preview",
        data={"body_md": "``` bash\nvagrant up  # arranca\n```\n<script>x</script>"},
    )

    assert response.status_code == 200
    assert b"tok-" in response.data
    assert b"<script" not in response.data


def test_the_preview_is_not_for_readers(test_client, test_app, material):
    """It renders arbitrary markdown on demand, so it belongs to the people
    who can already write pages and to nobody else."""
    response = test_client.post(
        "/admin/courses/preview", data={"body_md": "# x"}
    )
    assert response.status_code in (302, 401, 403)
