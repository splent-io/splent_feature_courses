"""Forms for the course back-office.

Every screen that writes goes through a FlaskForm, so each one carries a
CSRF token: the actions here withhold and release material, and a link in
an e-mail must not be able to publish next year's exam on a logged-in
member of staff's behalf.

Release moments are typed as a naive wall clock in the course's timezone
and stored as aware UTC. The forms carry the wall clock; routes.py does
the conversion in one place so the two directions cannot drift apart.
"""

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import (
    BooleanField,
    DateTimeLocalField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

# What a datetime-local input submits. Browsers append the seconds only
# once the user touches them, so both shapes have to parse.
DATETIME_LOCAL_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]


def optional_int(value):
    """Coerce a select value to an id, treating the blank option as none."""
    if value in (None, "", "None"):
        return None
    return int(value)


class VisibilityForm(FlaskForm):
    """The two controls a course, a category and a page all share.

    They are declared once because staff learn them once: the checkbox
    withholds something for as long as they want, the date releases it on
    its own. Both empty means visible now.
    """

    hidden = BooleanField(_l("Hidden"))
    publish_at = DateTimeLocalField(
        _l("Publish at"),
        format=DATETIME_LOCAL_FORMATS,
        validators=[Optional()],
    )


class NewCourseForm(FlaskForm):
    """Starting an academic year.

    No release date here on purpose. A course is born hidden and is filled
    before anyone sees it, so scheduling it is a decision for later, on the
    edit screen, once there is something to release.
    """

    name = StringField(_l("Name"), validators=[DataRequired(), Length(max=255)])
    description = TextAreaField(_l("Description"), validators=[Optional()])
    categories = TextAreaField(_l("Categories"), validators=[Optional()])
    hidden = BooleanField(_l("Hidden"), default=True)
    submit = SubmitField(_l("Create course"))


class CourseForm(VisibilityForm):
    """Editing an academic year.

    The name is editable, the URL is not: a course slug ends up in slides
    and e-mails that are read for years, so renaming fixes a typo without
    breaking a link.
    """

    name = StringField(_l("Name"), validators=[DataRequired(), Length(max=255)])
    description = TextAreaField(_l("Description"), validators=[Optional()])
    submit = SubmitField(_l("Save course"))


class CopyCourseForm(FlaskForm):
    """Duplicating a year into the next one."""

    name = StringField(
        _l("Name of the new course"), validators=[DataRequired(), Length(max=255)]
    )
    submit = SubmitField(_l("Copy course"))


class CategoryForm(VisibilityForm):
    """A division of a course, such as Teoría or Prácticas."""

    name = StringField(_l("Name"), validators=[DataRequired(), Length(max=255)])
    position = IntegerField(_l("Position"), validators=[Optional(), NumberRange(min=0)])
    submit = SubmitField(_l("Save category"))


class PageForm(VisibilityForm):
    """A page of material.

    ``preview`` and ``submit`` are two buttons on one form: previewing
    posts the same fields back and re-renders them with the body rendered
    beside the textarea, so what staff check is the markdown pipeline the
    readers get, not a second implementation of it in the browser.
    """

    name = StringField(_l("Title"), validators=[DataRequired(), Length(max=255)])
    category_id = SelectField(
        _l("Category"), coerce=optional_int, validators=[Optional()]
    )
    body_md = TextAreaField(_l("Body"), validators=[Optional()])
    position = IntegerField(_l("Position"), validators=[Optional(), NumberRange(min=0)])
    preview = SubmitField(_l("Preview"))
    submit = SubmitField(_l("Save page"))


class AttachmentForm(FlaskForm):
    """A file to hang off a page.

    The upload is stored as restricted media owned by this feature, so it
    is withheld exactly as long as its page is. That is the requirement
    that made static file serving impossible: the material is PDFs, and
    students guess URLs.
    """

    file = FileField(
        _l("File"),
        validators=[FileRequired(_l("Choose a file to upload."))],
    )
    name = StringField(_l("Display name"), validators=[Optional(), Length(max=255)])
    submit = SubmitField(_l("Attach file"))


class ConfirmForm(FlaskForm):
    """The token behind a one-click action such as deleting a course."""

    submit = SubmitField(_l("Confirm"))
