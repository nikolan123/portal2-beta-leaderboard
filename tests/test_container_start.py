import sys

import pytest
from alembic import command
from sqlalchemy import text

from app import container_start
from app.migrate import migration_config, require_current_schema


@pytest.mark.parametrize("revision", [None, "002_ui_refresh", "head"])
def test_server_starts_only_after_database_is_current(monkeypatch, migration_engine, revision):
    config = migration_config()
    with migration_engine.connect() as connection:
        config.attributes["connection"] = connection
        if revision:
            command.upgrade(config, revision)
        monkeypatch.setattr(container_start, "migration_config", lambda: config)
        args = ["uvicorn", "app.main:app", "--port", "8000"]
        monkeypatch.setattr(sys, "argv", ["container_start", *args])
        calls = []

        def start(executable, passed_args):
            require_current_schema(migration_engine)
            calls.append((executable, passed_args))

        monkeypatch.setattr(container_start.os, "execvp", start)
        container_start.main()
        assert calls == [("uvicorn", args)]


def test_failed_upgrade_does_not_start_server(monkeypatch, migration_engine):
    with migration_engine.begin() as connection:
        connection.execute(text("CREATE TABLE unexpected (id INTEGER PRIMARY KEY)"))
    config = migration_config()
    with migration_engine.connect() as connection:
        config.attributes["connection"] = connection
        monkeypatch.setattr(container_start, "migration_config", lambda: config)
        monkeypatch.setattr(sys, "argv", ["container_start", "uvicorn", "app.main:app"])
        calls = []
        monkeypatch.setattr(container_start.os, "execvp", lambda *args: calls.append(args))
        with pytest.raises(RuntimeError, match="Unversioned database"):
            container_start.main()
        assert calls == []


def test_admin_command_can_run_without_automatic_upgrade(monkeypatch):
    args = ["python", "-m", "app.migrate", "adopt-legacy"]
    monkeypatch.setattr(sys, "argv", ["container_start", *args])
    calls = []
    monkeypatch.setattr(container_start.os, "execvp", lambda *args: calls.append(args))
    container_start.main()
    assert calls == [("python", args)]
