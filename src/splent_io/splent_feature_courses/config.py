"""
courses feature configuration.

Injects environment variables into Flask app.config.

Everything a particular subject decides lives here, not in the code: the
name a new course is called, the categories it starts with, the words the
URLs use and the timezone release dates are typed in. A wiki for another
subject is the same feature with different values.

To regenerate from source code: splent feature:inject-config splent_feature_courses
"""

import os


def _list(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def inject_config(app):
    # Release moments are stored in UTC and typed in local time. A lab at
    # 08:00 means 08:00 where the course is taught.
    timezone = os.getenv("COURSES_TIMEZONE", "Europe/Madrid")
    # URL words. They end up in slides and e-mails and are read for years,
    # so a wiki replacing an older one sets these to whatever it used to
    # serve rather than breaking every link that already exists.
    path = os.getenv("COURSES_PATH", "cursos")
    category_segment = os.getenv("COURSES_CATEGORY_SEGMENT", "categoria")
    page_segment = os.getenv("COURSES_PAGE_SEGMENT", "pagina")
    # What a new academic year is called and what it starts with. Empty
    # defaults on purpose: the subject names its own courses.
    name_prefix = os.getenv("COURSES_NAME_PREFIX", "")
    description_template = os.getenv("COURSES_DESCRIPTION_TEMPLATE", "")
    default_categories = os.getenv("COURSES_DEFAULT_CATEGORIES", "")

    app.config.update(
        {
            "COURSES_TIMEZONE": timezone,
            "COURSES_PATH": path.strip("/"),
            "COURSES_CATEGORY_SEGMENT": category_segment.strip("/"),
            "COURSES_PAGE_SEGMENT": page_segment.strip("/"),
            "COURSES_NAME_PREFIX": name_prefix,
            "COURSES_DESCRIPTION_TEMPLATE": description_template,
            "COURSES_DEFAULT_CATEGORIES": _list(default_categories),
        }
    )
