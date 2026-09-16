import pytest
from sqlalchemy import create_engine

def test_non_test_database_is_blocked_before_file_creation(tmp_path):
    forbidden_database = tmp_path / "not-the-test-db.sqlite"
    other_engine = create_engine(f"sqlite:///{forbidden_database.as_posix()}")
    try:
        with pytest.raises(RuntimeError, match="non-test database"):
            other_engine.connect()
        assert not forbidden_database.exists()
    finally:
        other_engine.dispose()
