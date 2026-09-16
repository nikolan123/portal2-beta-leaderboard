"""Create the schema used before managed migrations.

Revision ID: 001_existing_schema
"""
from alembic import op

from migrations.legacy_schema import Base

revision = "001_existing_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(op.get_bind(), checkfirst=False)


def downgrade():
    raise RuntimeError("Destructive downgrade is unsupported; restore a database backup.")
