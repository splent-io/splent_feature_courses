from flask import current_app

from splent_framework.blueprints.base_blueprint import create_blueprint
from splent_framework.services.file_access import register_file_access_resolver
from splent_framework.services.service_locator import register_service

from splent_io.splent_feature_courses.services import CoursesService

courses_bp = create_blueprint(__name__)

# What media records on a restricted item so it knows who to ask before
# serving those bytes.
OWNER = "courses"


def _may_read(item, user) -> bool:
    return CoursesService().may_read_attachment(item.id, user)


def init_feature(app):
    register_service(app, "CoursesService", CoursesService)

    # Attachments are stored as restricted media, whose bytes are refused
    # unless the feature owning them allows the read. A lab script
    # therefore answers 404 until its page is released, to someone
    # guessing the URL exactly as to someone following a link.
    register_file_access_resolver(OWNER, _may_read)


def course_url(course) -> str:
    return f"/{current_app.config['COURSES_PATH']}/{course.slug}"


def category_url(course, category) -> str:
    path = current_app.config["COURSES_PATH"]
    segment = current_app.config["COURSES_CATEGORY_SEGMENT"]
    return f"/{path}/{course.slug}/{segment}/{category.slug}"


def page_url(course, page) -> str:
    path = current_app.config["COURSES_PATH"]
    segment = current_app.config["COURSES_PAGE_SEGMENT"]
    return f"/{path}/{course.slug}/{segment}/{page.slug}"


def inject_context_vars(app):
    return {
        "course_url": course_url,
        "category_url": category_url,
        "page_url": page_url,
    }
