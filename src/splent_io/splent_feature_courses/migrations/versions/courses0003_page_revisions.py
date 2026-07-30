"""courses feature: keep what a page said before it was changed.

A wiki without history is a wiki where one careless save destroys work
nobody can get back, and this material is written once a year by people
teaching four other things.

Nothing is backfilled. A revision records the state before an edit, and no
edit before this revision was ever observed, so inventing a first revision
from the current body would claim the page had been saved once when it had
been saved an unknown number of times. Pages simply start their history at
their next save.

Revision ID: courses0003
Revises: courses0002
"""

import sqlalchemy as sa
from alembic import op

revision = "courses0003"
down_revision = "courses0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "course_page_revision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=True),
        # No foreign key to the user table. This feature must not depend on
        # which account model a product installs, and a course's history has
        # to outlive the staff list: an account closed in three years cannot
        # be allowed to take the record of what a page said with it.
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("author_email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["page_id"], ["course_page.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The only query this table ever answers is "this page, newest first",
    # so the index covers both columns in that order.
    op.create_index(
        "ix_course_page_revision_page_created",
        "course_page_revision",
        ["page_id", "created_at"],
    )
    op.create_index(
        "ix_course_page_revision_author_id", "course_page_revision", ["author_id"]
    )


def downgrade():
    op.drop_index("ix_course_page_revision_author_id", "course_page_revision")
    op.drop_index("ix_course_page_revision_page_created", "course_page_revision")
    op.drop_table("course_page_revision")
