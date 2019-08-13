"""add contributor

Revision ID: 53647ffeb3c6
Revises: 4db0c66c3caf
Create Date: 2019-08-13 12:36:25.829578

"""

# revision identifiers, used by Alembic.
revision = "53647ffeb3c6"
down_revision = "4db0c66c3caf"

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from kirin.core import model


def upgrade():
    op.create_table(
        "contributor",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("coverage", sa.Text(), nullable=False),
        sa.Column("token", sa.Text(), nullable=True),
        sa.Column("feed_url", sa.Text(), nullable=True),
        sa.Column("connector_type", model.Db_connector_type, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "real_time_update",
        sa.Column("contributor_id", postgresql.UUID(), sa.ForeignKey("contributor.id"), nullable=True),
    )

    op.add_column(
        "trip_update",
        sa.Column("contributor_id", postgresql.UUID(), sa.ForeignKey("contributor.id"), nullable=True),
    )


def downgrade():
    op.drop_column("real_time_update", "contributor_id")
    op.drop_column("trip_update", "contributor_id")
    op.drop_table("contributor")
