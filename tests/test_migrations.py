"""Test DB upgrades"""
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.main import app
from app.migrate import migration_config, require_current_schema
from app.models import Category
from migrations.legacy_schema import Base as LegacyBase
from fastapi.testclient import TestClient


def upgrade(engine, *, adopt=False, revision="head"):
    config = migration_config()
    config.attributes["adopt_legacy"] = adopt
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def legacy_data(engine):
    LegacyBase.metadata.create_all(engine)
    tables = LegacyBase.metadata.tables
    with engine.begin() as connection:
        connection.execute(tables["users"].insert(), [
            {"id": 11, "discord_id": "runner", "username": "Runner"},
            {"id": 12, "discord_id": "reviewer", "username": "Reviewer"},
        ])
        connection.execute(tables["user_profiles"].insert(), {"user_id": 11, "bio": "Keep me"})
        for id_, year, build, name in [
            (21, "2009", "july-2009-852-0", "July 2009 852_0"),
            (22, "2010", "february-2010-841-0", "February 2010 841_0"),
        ]:
            connection.execute(tables["categories"].insert(), {
                "id": id_, "slug": f"{year}-no-major-exploits",
                "name": f"{name} - No Major Exploits", "short_name": "No Major Exploits",
                "build_slug": build, "build_name": name,
                "rules_file": f"rules/{build}/no-major-exploits.md",
            })
        connection.execute(tables["categories"].insert(), {
            "id": 23, "slug": "custom-old", "name": "Custom Full Name",
            "short_name": "Custom", "build_slug": "custom-build", "build_name": "Custom Build",
            "rules_file": "rules/custom.md", "description": "Keep this description",
        })
        connection.execute(tables["runs"].insert(), {
            "id": 31, "user_id": 11, "category_id": 21, "time_ms": 12345,
            "video_url": "https://example.com/proof", "notes": "Keep run notes",
            "status": "approved", "reviewed_by_user_id": 12,
        })
        connection.execute(tables["audit_logs"].insert(), {
            "id": 41, "action": "approved", "run_id": 31,
            "actor_discord_id": "reviewer", "actor_name": "Reviewer",
            "runner_discord_id": "runner", "runner_name": "Runner",
            "category_name": "Historical category label", "time_ms": 12345,
        })


def snapshot(engine):
    with engine.connect() as connection:
        names = inspect(connection).get_table_names()
        return {
            "schema": connection.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
            ).fetchall(),
            "data": {name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY 1').fetchall()
                     for name in names},
        }


def assert_current_schema(engine):
    require_current_schema(engine)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection, opts={
            "compare_server_default": True,
        }), Base.metadata) == []
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []


def test_fresh_database_and_second_upgrade(migration_engine):
    upgrade(migration_engine)
    assert_current_schema(migration_engine)
    before = snapshot(migration_engine)
    upgrade(migration_engine)
    assert snapshot(migration_engine) == before
    config = migration_config()
    with migration_engine.connect() as connection:
        config.attributes["connection"] = connection
        command.check(config)


@pytest.mark.parametrize("versioned", [False, True])
def test_upgrade_preserves_data_and_matches_models(migration_engine, versioned):
    if versioned:
        upgrade(migration_engine, revision="001_existing_schema")
    legacy_data(migration_engine)
    before = snapshot(migration_engine)["data"]
    upgrade(migration_engine, adopt=not versioned)
    assert_current_schema(migration_engine)
    after = snapshot(migration_engine)["data"]
    for name in ["users", "user_profiles", "audit_logs"]:
        assert after[name] == before[name]
    assert after["runs"][0][:-1] == before["runs"][0]
    assert after["runs"][0][-1] == ""
    with migration_engine.connect() as connection:
        rows = connection.execute(text("SELECT * FROM categories ORDER BY id")).mappings().all()
        assert [(row["id"], row["build_slug"], row["slug"], row["name"]) for row in rows] == [
            (21, "july09", "nme", "No Major Exploits"),
            (22, "feb10", "nme", "No Major Exploits"),
            (23, "custom-build", "custom-old", "Custom"),
        ]
        assert rows[0]["legacy_slug"] == "2009-no-major-exploits"
        assert rows[0]["build_version"] == "852_0"
        assert rows[1]["rules_file"] == "rules/feb10/nme.md"
        assert rows[2]["legacy_slug"] == "custom-old"
        assert rows[2]["description"] == "Keep this description"
    before = snapshot(migration_engine)
    upgrade(migration_engine)
    assert snapshot(migration_engine) == before


def test_new_constraints_are_enforced(migration_engine):
    legacy_data(migration_engine)
    upgrade(migration_engine, adopt=True)
    with migration_engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE categories SET build_slug='july09' WHERE id=22"))
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE runs SET splits_url=NULL WHERE id=31"))
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE runs SET time_ms=0 WHERE id=31"))
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE runs SET category_id=999 WHERE id=31"))


def test_unversioned_database_requires_explicit_adoption(migration_engine):
    legacy_data(migration_engine)
    before = snapshot(migration_engine)
    with pytest.raises(RuntimeError, match="Unversioned database"):
        upgrade(migration_engine)
    assert snapshot(migration_engine) == before


@pytest.mark.parametrize("statement", [
    "ALTER TABLE categories ADD COLUMN legacy_slug VARCHAR(80) DEFAULT ''",
    "ALTER TABLE runs ADD COLUMN splits_url VARCHAR(500) DEFAULT ''",
    "DROP INDEX ix_categories_slug",
])
def test_partial_or_unexpected_schema_is_rejected(migration_engine, statement):
    legacy_data(migration_engine)
    with migration_engine.begin() as connection:
        connection.exec_driver_sql(statement)
    before = snapshot(migration_engine)
    with pytest.raises(RuntimeError, match="does not match"):
        upgrade(migration_engine, adopt=True)
    assert snapshot(migration_engine) == before


def test_failure_after_table_rebuild_rolls_back_data_schema_and_stamp(migration_engine):
    legacy_data(migration_engine)
    with migration_engine.begin() as connection:
        connection.execute(LegacyBase.metadata.tables["categories"].insert(), {
            "id": 24, "slug": "nme", "name": "Conflicting custom category",
            "build_slug": "july09", "build_name": "Custom July",
        })
    before = snapshot(migration_engine)
    with pytest.raises(IntegrityError):
        upgrade(migration_engine, adopt=True)
    assert snapshot(migration_engine) == before
    with migration_engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_foreign_key_check_failure_rolls_back(migration_engine):
    legacy_data(migration_engine)
    with migration_engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("UPDATE runs SET category_id=999 WHERE id=31")
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
    before = snapshot(migration_engine)
    with pytest.raises(RuntimeError, match="Foreign-key violations"):
        upgrade(migration_engine, adopt=True)
    assert snapshot(migration_engine) == before


def test_startup_check_rejects_empty_and_old_databases(migration_engine):
    with pytest.raises(RuntimeError, match="schema is not current"):
        require_current_schema(migration_engine)
    upgrade(migration_engine, revision="001_existing_schema")
    with pytest.raises(RuntimeError, match="schema is not current"):
        require_current_schema(migration_engine)


def test_upgraded_categories_keep_legacy_routes_and_seed_ids(migration_engine):
    from app.database import get_db
    import app.main as main_module
    from sqlalchemy.orm import sessionmaker
    from unittest.mock import patch

    legacy_data(migration_engine)
    upgrade(migration_engine, adopt=True)
    sessions = sessionmaker(bind=migration_engine)

    def get_migrated_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = get_migrated_db
    try:
        with patch.object(main_module, "engine", migration_engine), patch.object(main_module, "SessionLocal", sessions):
            with TestClient(app) as client:
                response = client.get("/category/2009-no-major-exploits", follow_redirects=False)
                assert response.status_code == 301
                assert response.headers["location"] == "/july09/nme"
                assert client.get("/july09/nme").status_code == 200
                assert client.get("/category/2009-no-major-exploits/rules").status_code == 200
                assert client.get("/runs/31").status_code == 200
                assert client.get("/category/custom-old", follow_redirects=False).headers["location"] == "/custom-build/custom-old"
            # A second startup must not duplicate the migrated category.
            with TestClient(app):
                with sessions() as session:
                    categories = session.scalars(select(Category).where(Category.build_slug == "july09", Category.slug == "nme")).all()
                    assert [category.id for category in categories] == [21]
    finally:
        del app.dependency_overrides[get_db]
