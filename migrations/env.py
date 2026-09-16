"""Run migrations against DATABASE_URL"""
from alembic import context
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, create_engine, inspect, pool

from app.database import Base
from app.config import settings
from app import models  # noqa: F401 -- register all tables
from migrations.legacy_schema import Base as LegacyBase

config = context.config


def run(connection):
    if connection.dialect.name != "sqlite":
        raise RuntimeError("These migrations currently support SQLite only.")
    if connection.in_transaction():
        raise RuntimeError("Migrations require a connection without an active transaction.")

    # SQLite table rebuilds cannot run with foreign keys enabled. This is local
    # to this connection
    foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
    connection.commit()
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    connection.commit()
    try:
        # Explicit BEGIN makes SQLite DDL transactional even with the sqlite3
        # driver's legacy transaction mode (Python 3.11+)
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
        migration_context = MigrationContext.configure(connection)
        current = migration_context.get_current_heads()
        if tables and not current:
            if not config.attributes.get("adopt_legacy"):
                raise RuntimeError(
                    "Unversioned database: back it up, then run "
                    "`uv run python -m app.migrate adopt-legacy`. "
                    "Do not use alembic stamp to bypass schema validation."
                )
            differences = compare_metadata(
                MigrationContext.configure(connection, opts={"compare_server_default": True}),
                LegacyBase.metadata,
            )
            # Autogenerate does not compare primary keys or CHECK constraints.
            inspector = inspect(connection)
            for name, table in LegacyBase.metadata.tables.items():
                if name not in tables:
                    continue
                primary_key = inspector.get_pk_constraint(name)["constrained_columns"]
                if primary_key != [column.name for column in table.primary_key]:
                    differences.append(("primary_key", name))
                expected_checks = {
                    " ".join(str(constraint.sqltext).split())
                    for constraint in table.constraints if isinstance(constraint, CheckConstraint)
                }
                actual_checks = {
                    " ".join(constraint["sqltext"].split())
                    for constraint in inspector.get_check_constraints(name)
                }
                if expected_checks != actual_checks:
                    differences.append(("check_constraints", name))
            if differences:
                raise RuntimeError(
                    "Database does not match the pre-Alembic schema; it may have "
                    "a partially applied migration.sql. No changes were applied. "
                    "Restore the pre-migration backup or reconcile the schema first. "
                    f"Differences: {differences!r}"
                )
            migration_context.stamp(
                ScriptDirectory.from_config(config), "001_existing_schema"
            )
        elif config.attributes.get("adopt_legacy") and not tables:
            raise RuntimeError("Database is empty; use `uv run alembic upgrade head`.")

        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            render_as_batch=True,
            compare_type=True,
            compare_server_default=True,
            transactional_ddl=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Foreign-key violations; migration rolled back: {violations!r}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.exec_driver_sql(f"PRAGMA foreign_keys={int(foreign_keys)}")
        connection.commit()


if context.is_offline_mode():
    raise RuntimeError("Offline SQL generation is unsupported; migrations validate existing data.")

supplied = config.attributes.get("connection")
if supplied is not None:
    run(supplied)
else:
    engine = create_engine(settings.database_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            run(connection)
    finally:
        engine.dispose()
