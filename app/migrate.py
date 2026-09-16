import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory


def migration_config() -> Config:
    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    return config


def require_current_schema(engine) -> None:
    heads = set(ScriptDirectory.from_config(migration_config()).get_heads())
    with engine.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    if current != heads:
        raise RuntimeError(
            "Database schema is not current. Run `uv run alembic upgrade head` "
            "before starting the app. For a pre-Alembic database, back it up and "
            "run `uv run python -m app.migrate adopt-legacy` first."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["adopt-legacy"])
    parser.parse_args()
    config = migration_config()
    config.attributes["adopt_legacy"] = True
    # env.py validates the old schema before stamping. Stamping and upgrading
    # share one transaction, so a failed upgrade does not leave a false version.
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
