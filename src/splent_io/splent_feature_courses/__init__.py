from flask import current_app

from splent_framework.blueprints.base_blueprint import create_blueprint
from splent_framework.search.search_registry import register_search_source
from splent_framework.services.file_access import register_file_access_resolver
from splent_framework.services.service_locator import register_service

from splent_io.splent_feature_courses.services import (
    CoursesService,
    search_fetch,
    search_current_scope,
    search_find,
    search_resolve,
    search_resolve_many,
    search_scopes,
)

courses_bp = create_blueprint(__name__)

# What media records on a restricted item so it knows who to ask before
# serving those bytes.
OWNER = "courses"

# What a search feature stores on every hit and derives its index name
# from. Written separately from OWNER although the word is the same: they
# answer to different registries, and this one cannot change once any
# product has indexed a page under it.
SEARCH_SOURCE = "courses"


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

    # What this feature offers to whatever search the product installs.
    # Nothing here knows whether there is an engine: a search feature asks
    # for every page when it wants to build an index, falls back to find
    # when it has none, and in both cases asks resolve again, one hit at a
    # time, before a reader is told anything exists. The label is the
    # untranslated group name, translated by whoever renders the results.
    #
    # Order 10 rather than the default: in a course wiki the material is
    # what a search is for, so pages come above whatever a smaller feature
    # contributes.
    register_search_source(
        key=SEARCH_SOURCE,
        label="Pages",
        fetch=search_fetch,
        resolve=search_resolve,
        resolve_many=search_resolve_many,
        find=search_find,
        scopes=search_scopes,
        current_scope=search_current_scope,
        order=10,
    )


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
