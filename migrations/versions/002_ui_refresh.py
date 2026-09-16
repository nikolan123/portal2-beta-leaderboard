"""
Upgrade category URLs and run splits.

Revision ID: 002_ui_refresh
Revises: 001_existing_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "002_ui_refresh"
down_revision = "001_existing_schema"
branch_labels = None
depends_on = None

BUILDS = {
    "july-2009-852-0": ("july09", "July 2009", "852_0", "2009"),
    "february-2010-841-0": ("feb10", "February 2010", "841_0", "2010"),
}
CATEGORIES = {
    "no-major-exploits": ("nme", "No Major Exploits"),
    "oob-sla": ("oob-sla", "Out-of-Bounds (SLA)"),
    "in-bounds-no-sla": ("inbounds", "Inbounds (No SLA)"),
}


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT * FROM categories")).mappings().all()

    # Name the reflected old UNIQUE(name) so batch mode can remove it.
    # slug uniqueness is an index, not a table constraint in the old schema.
    with op.batch_alter_table(
        "categories", recreate="always",
        naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
    ) as batch:
        batch.drop_constraint("uq_categories_name", type_="unique")
        batch.drop_index("ix_categories_slug")
        batch.create_index("ix_categories_slug", ["slug"], unique=False)
        batch.add_column(sa.Column("legacy_slug", sa.String(80), nullable=False, server_default=""))
        batch.add_column(sa.Column("build_version", sa.String(80), nullable=False, server_default=""))
        batch.create_index("ix_categories_legacy_slug", ["legacy_slug"], unique=False)
        batch.drop_column("short_name")
        batch.create_unique_constraint("uq_categories_build_slug_slug", ["build_slug", "slug"])

    for row in rows:
        values = {
            "id": row["id"],
            "legacy_slug": row["slug"],
            "slug": row["slug"],
            "name": row["short_name"] or row["name"],
            "build_slug": row["build_slug"],
            "build_name": row["build_name"],
            "build_version": "",
            "rules_file": row["rules_file"],
        }
        build = BUILDS.get(row["build_slug"])
        if build:
            slug, name, version, year = build
            values.update(build_slug=slug, build_name=name, build_version=version)
            for old_suffix, (category_slug, category_name) in CATEGORIES.items():
                if row["slug"] == f"{year}-{old_suffix}":
                    values.update(slug=category_slug, name=category_name)
                old_path = f"rules/{row['build_slug']}/{old_suffix}.md"
                if row["rules_file"] == old_path:
                    values["rules_file"] = f"rules/{slug}/{category_slug}.md"
        connection.execute(sa.text(
            "UPDATE categories SET legacy_slug=:legacy_slug, slug=:slug, name=:name, "
            "build_slug=:build_slug, build_name=:build_name, build_version=:build_version, "
            "rules_file=:rules_file WHERE id=:id"
        ), values)

    op.add_column("runs", sa.Column("splits_url", sa.String(500), nullable=False, server_default=""))


def downgrade():
    raise RuntimeError("UI refresh downgrade is unsupported sorry")
