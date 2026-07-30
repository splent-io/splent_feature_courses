from datetime import datetime, timezone

from splent_framework.db import db


def _utcnow():
    return datetime.now(timezone.utc)


class VisibilityMixin:
    """Whether students can see this, and from when.

    Two independent controls, because they answer different questions.
    ``hidden`` withholds something for as long as the staff want, with no
    date in mind, and is what a course is born with. ``publish_at`` releases
    it at a moment decided in advance, which is the point of the course
    wiki: lab material appears before the session without anyone having to
    remember on the day.

    Visibility is computed per request from these two columns, so nothing
    has to run on a schedule and saving an item can never expose it by
    accident, which is the defect this replaces.
    """

    hidden = db.Column(db.Boolean, default=False, server_default="0", nullable=False)
    publish_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    def is_released(self, now=None) -> bool:
        """Whether this item's own controls allow it through.

        Says nothing about its parents. Ask the service for the answer a
        reader actually experiences.
        """
        if self.hidden:
            return False
        if self.publish_at is None:
            return True
        now = now or _utcnow()
        publish_at = self.publish_at
        if publish_at.tzinfo is None:
            # MySQL hands back naive datetimes even from a timezone-aware
            # column, and comparing one of those to an aware now raises.
            publish_at = publish_at.replace(tzinfo=timezone.utc)
        return publish_at <= now


class Course(db.Model, VisibilityMixin):
    """One academic year, an independent snapshot of the material.

    Editing this year never changes what a previous one shows, which is why
    a page reused across years is copied into each course rather than
    shared. Courses are listed newest first.
    """

    __tablename__ = "course"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, index=True, nullable=False)
    description = db.Column(db.Text, default="")
    cover_media_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)

    categories = db.relationship(
        "Category",
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="Category.position",
    )
    pages = db.relationship(
        "Page", back_populates="course", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"Course<{self.id}:{self.slug}>"


class Category(db.Model, VisibilityMixin):
    """A division of a course, such as Teoría or Prácticas.

    Categories differ from one year to the next, so they belong to a course
    and are ordered by hand rather than alphabetically.
    """

    __tablename__ = "course_category"
    __table_args__ = (
        db.UniqueConstraint("course_id", "slug", name="uq_category_slug"),
    )

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(
        db.Integer,
        db.ForeignKey("course.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)

    course = db.relationship("Course", back_populates="categories")
    pages = db.relationship(
        "Page",
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="Page.position",
    )

    def __repr__(self):
        return f"Category<{self.id}:{self.slug}>"


class Page(db.Model, VisibilityMixin):
    """A page of material, and the unit staff release on a date.

    Anything released independently needs its own page, because both the
    release date and the attachments hang off it.
    """

    __tablename__ = "course_page"
    __table_args__ = (db.UniqueConstraint("course_id", "slug", name="uq_page_slug"),)

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(
        db.Integer,
        db.ForeignKey("course.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category_id = db.Column(
        db.Integer,
        db.ForeignKey("course_category.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), nullable=False)
    # Markdown is the stored form because that is what the migrated material
    # already is, and what an editor round-trips without losing anything.
    body_md = db.Column(db.Text, default="")
    position = db.Column(db.Integer, default=0, nullable=False)
    # The identifier this page had in the wiki it came from, so links
    # written years ago in slides and e-mails keep resolving.
    legacy_id = db.Column(db.Integer, nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    course = db.relationship("Course", back_populates="pages")
    category = db.relationship("Category", back_populates="pages")
    attachments = db.relationship(
        "PageAttachment",
        back_populates="page",
        cascade="all, delete-orphan",
        order_by="PageAttachment.position",
    )
    revisions = db.relationship(
        "PageRevision",
        back_populates="page",
        cascade="all, delete-orphan",
        order_by="PageRevision.created_at.desc()",
    )

    def __repr__(self):
        return f"Page<{self.id}:{self.slug}>"


class PageRevision(db.Model):
    """What a page said before somebody changed it.

    A wiki without this is a wiki where one careless save destroys work
    nobody can get back, and the material here is written once a year by
    people teaching four other things. The editor is Wikipedia-shaped
    already; the history is the half that makes that shape safe.

    A revision is the state *before* an edit, written at save time by the
    only code that writes pages. Storing the previous body rather than a
    diff is deliberate: a body is a few kilobytes of markdown, a course has
    a few dozen pages, and a stored diff has to be replayed correctly
    forever to be worth anything, while a stored body is just the page.

    Nothing here is versioned except what a person types. Position,
    category and the release date are settings rather than writing: they
    change often, they carry no work, and recording them would bury the
    edits that matter under noise.
    """

    __tablename__ = "course_page_revision"

    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(
        db.Integer,
        db.ForeignKey("course_page.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The name and body as they stood. Both, because renaming a page is an
    # edit a reader notices as much as a rewritten paragraph.
    name = db.Column(db.String(255), nullable=False)
    body_md = db.Column(db.Text, default="")
    # Who saved over it. Nullable and never a hard foreign key constraint
    # on delete: an account can be closed years later and the history of a
    # course must survive the staff list.
    author_id = db.Column(db.Integer, nullable=True, index=True)
    author_email = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, index=True)

    page = db.relationship("Page", back_populates="revisions")

    def __repr__(self):
        return f"PageRevision<{self.id}:page {self.page_id}>"


#: A file the reader downloads, listed under the page.
KIND_FILE = "file"
#: An image the body embeds, which is not a document and is never listed.
KIND_INLINE = "inline"


class PageAttachment(db.Model):
    """A file belonging to a page, which inherits the page's visibility.

    The bytes live in the media library as a restricted item; this row is
    what tells the access resolver which page decides whether they may be
    served. An unreleased script has to answer 404 to anyone who guesses
    its URL, and that requirement is why files cannot be served statically.

    An embedded image is the same arrangement wearing a different hat. It is
    material too, so a diagram of an unreleased lab must not be reachable by
    guessing its URL either, but it is not a document and listing it under
    "Files" would be wrong. ``kind`` is that difference and nothing more:
    the access resolver treats both identically.
    """

    __tablename__ = "course_page_attachment"

    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(
        db.Integer,
        db.ForeignKey("course_page.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    media_item_id = db.Column(db.Integer, nullable=False, index=True)
    name = db.Column(db.String(255), default="")
    position = db.Column(db.Integer, default=0, nullable=False)
    kind = db.Column(
        db.String(16), default=KIND_FILE, server_default=KIND_FILE, nullable=False
    )
    # The identifier the source wiki gave this, so a body written against
    # the old URLs can be rewritten and re-running an import updates the
    # same row instead of adding another. Ids are only unique within a
    # kind: a file and an image can both be number 1 in their own table.
    legacy_id = db.Column(db.Integer, nullable=True, index=True)

    page = db.relationship("Page", back_populates="attachments")

    def __repr__(self):
        return f"PageAttachment<{self.id}:{self.media_item_id}>"
