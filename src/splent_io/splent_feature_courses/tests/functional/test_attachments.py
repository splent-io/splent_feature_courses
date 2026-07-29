"""
Functional tests for attachment visibility.

The material is PDFs, and students guess URLs. That single fact is what
ruled out static site generators and any wiki that gates the page but not
the file, so it deserves tests of its own: a script attached to a withheld
page must answer 404 to a guess, and start serving the moment the page is
released, with no separate step to remember.

The bytes live in the media library as a restricted item owned by this
feature, and the media serving route asks may_read_attachment before
sending anything.
"""

import io

import pytest
from werkzeug.datastructures import FileStorage

from splent_framework.db import db
from splent_io.splent_feature_auth.models import User
from splent_io.splent_feature_courses.models import Category, Course, Page
from splent_io.splent_feature_courses.services import CoursesService

PDF_BYTES = b"%PDF-1.4 lab script"


@pytest.fixture
def attached(test_client, test_app):
    """A withheld page with a file hanging off it.

    It takes test_client so the database reset that fixture performs
    happens before this data is created, and closes its app context before
    returning: Flask reuses an already pushed context for test client
    requests, and a context held open would serve them from this session's
    identity map.
    """
    with test_app.app_context():
        course = Course(name="Subject 2026/2027", slug="attach-2026-2027")
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
            slug="attach-lab-5",
            hidden=True,
        )
        db.session.add(page)

        staff = User(email="attach-staff@example.com", active=True, role="staff")
        staff.set_password("1234")
        student = User(email="attach-student@example.com", active=True, role="user")
        student.set_password("1234")
        db.session.add_all([staff, student])
        db.session.commit()

        service = CoursesService()
        attachment = service.attach_file(
            page,
            FileStorage(
                stream=io.BytesIO(PDF_BYTES),
                filename="lab-5.pdf",
                content_type="application/pdf",
            ),
            name="Lab 5 script",
        )
        assert attachment is not None, "the upload has to be stored to test anything"
        ids = {"page_id": page.id, "media_item_id": attachment.media_item_id}

    return ids


def _release(test_app, page_id):
    with test_app.app_context():
        page = db.session.get(Page, page_id)
        page.hidden = False
        db.session.commit()


def _login(test_client, email):
    test_client.post(
        "/login",
        data={"email": email, "password": "1234"},
        follow_redirects=True,
    )


def test_a_withheld_page_refuses_its_file_to_a_guess(test_client, attached):
    url = f"/media/file/{attached['media_item_id']}"
    assert test_client.get(url).status_code == 404


def test_the_refusal_looks_like_a_file_that_does_not_exist(test_client, attached):
    withheld = test_client.get(f"/media/file/{attached['media_item_id']}")
    missing = test_client.get("/media/file/999999")
    assert withheld.status_code == missing.status_code == 404
    assert withheld.data == missing.data


def test_an_ordinary_account_is_refused_too(test_client, attached):
    """Having a login is not the same as being allowed to read early."""
    _login(test_client, "attach-student@example.com")
    url = f"/media/file/{attached['media_item_id']}"
    assert test_client.get(url).status_code == 404


def test_releasing_the_page_serves_the_file(test_client, test_app, attached):
    _release(test_app, attached["page_id"])
    response = test_client.get(f"/media/file/{attached['media_item_id']}")
    assert response.status_code == 200
    assert response.data == PDF_BYTES


def test_a_served_file_cannot_be_cached_or_sniffed(test_client, test_app, attached):
    _release(test_app, attached["page_id"])
    response = test_client.get(f"/media/file/{attached['media_item_id']}")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "private, no-store"


def test_staff_read_the_file_before_it_is_released(test_client, attached):
    _login(test_client, "attach-staff@example.com")
    response = test_client.get(f"/media/file/{attached['media_item_id']}")
    assert response.status_code == 200
    assert response.data == PDF_BYTES


def test_the_file_is_stored_outside_the_static_directory(test_app, attached):
    """A file under static/ is served by the web server, which never asks
    anyone whether it may be."""
    with test_app.app_context():
        from splent_io.splent_feature_media.models import MediaItem

        item = db.session.get(MediaItem, attached["media_item_id"])
        assert item.access == "restricted"
        assert item.owner_feature == "courses"
        assert not item.url.startswith("/static/")
