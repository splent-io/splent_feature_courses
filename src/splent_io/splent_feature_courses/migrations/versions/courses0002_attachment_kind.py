"""courses feature: tell a downloadable file from an embedded image.

Both are a page's material and both must answer 404 while the page is
withheld, so they are the same restricted media item gated by the same
resolver. They differ in one respect only: a document belongs under
"Files" and an image the reader is already looking at does not.

Existing rows are files. Nothing embedded images before this revision, so
the backfill is the server default and no data has to be inspected.

Revision ID: courses0002
Revises: courses0001
"""

import sqlalchemy as sa
from alembic import op

revision = "courses0002"
down_revision = "courses0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "course_page_attachment",
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="file",
        ),
    )
    # legacy_id is only unique within a kind, since the source numbers its
    # files and its images in separate tables, so the index that an import
    # looks rows up by covers both columns.
    op.create_index(
        "ix_course_page_attachment_kind_legacy",
        "course_page_attachment",
        ["kind", "legacy_id"],
    )


def downgrade():
    op.drop_index(
        "ix_course_page_attachment_kind_legacy",
        table_name="course_page_attachment",
    )
    op.drop_column("course_page_attachment", "kind")
