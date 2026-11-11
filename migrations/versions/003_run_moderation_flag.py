"""Add optional moderator labels to runs."""
from alembic import op
import sqlalchemy as sa

revision = "003_run_moderation_flag"
down_revision = "002_ui_refresh"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("moderation_flag", sa.String(200), nullable=False, server_default=sa.text("''")))


def downgrade():
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("moderation_flag")
