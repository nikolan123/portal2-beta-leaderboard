"""Apply migrations before starting the container's web server."""
import os
import sys

from alembic import command

from .migrate import migration_config


def main() -> None:
    args = sys.argv[1:]
    if not args:
        raise SystemExit("No container command provided.")
    if args[0] == "uvicorn":
        print("Applying pending database migrations...", flush=True)
        command.upgrade(migration_config(), "head")
    os.execvp(args[0], args)


if __name__ == "__main__":
    main()
