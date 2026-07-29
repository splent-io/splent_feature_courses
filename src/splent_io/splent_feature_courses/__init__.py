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
    from splent_framework.assets.asset_registry import register_asset

    register_service(app, "CoursesService", CoursesService)

    # Order 100, so a skin at 200 cascades after it and recolours
    # everything. This file carries no colour of its own; it reads the
    # theme's tokens.
    register_asset(
        "css", "courses.assets", order=100, subfolder="css", filename="courses.css"
    )

    # The editor is NOT registered here. The asset registry feeds the
    # theme's public layout, and the editor belongs to the back office,
    # which the authenticated shell renders instead. hooks.py loads it
    # through layout.head.css and layout.scripts, and only on the two
    # screens that have a body to write, because a markdown editor on a
    # list of courses is three hundred kilobytes for nothing.

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
