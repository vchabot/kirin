"""
Remove unused column real_time_update.received_at

Revision ID: 1f13d13948dd
Revises: 7bfe6fc8271d
Create Date: 2020-02-13 12:41:32.412507

"""
from __future__ import absolute_import, print_function, unicode_literals, division

# revision identifiers, used by Alembic.
revision = "1f13d13948dd"
down_revision = "7bfe6fc8271d"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.drop_column("real_time_update", "received_at")


def downgrade():
    op.add_column("real_time_update", sa.Column("received_at", sa.DateTime(), nullable=True))
