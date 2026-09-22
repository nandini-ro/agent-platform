"""Engine/session wiring.

connect_args is SQLite-only; the branch keeps the module PostgreSQL-ready.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings

settings = get_settings()

_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(
    settings.database_url, connect_args=_connect_args, pool_pre_ping=True
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables. A real deployment would use Alembic migrations."""
    from app import models  # noqa: F401  (import registers the mappers)
    from app.db.base import Base

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Reconcile an existing database with columns added to the models.

    A stand-in for Alembic, limited to the two operations that cannot lose
    data: adding a column, and giving pre-existing rows the value that column's
    Python-side default would have produced. It never drops or retypes
    anything. Both passes are idempotent, so an interrupted run self-heals on
    the next start. Anything beyond this needs a real migration tool.
    """
    from sqlalchemy import inspect, text

    from app.db.base import Base

    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}

            for column in table.columns:
                if column.primary_key:
                    continue
                if column.name not in existing:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table.name} ADD COLUMN {column.name} "
                            f"{column.type.compile(engine.dialect)}"
                        )
                    )
                # A column added by an earlier start leaves NULLs behind, which
                # the mapped type does not allow. Columns that are nullable by
                # design have no default and are left alone.
                filler = _default_value(column)
                if filler is not None:
                    connection.execute(
                        text(
                            f"UPDATE {table.name} SET {column.name} = :value "
                            f"WHERE {column.name} IS NULL"
                        ),
                        {"value": filler},
                    )


def _default_value(column) -> object | None:
    """The literal a newly added column's rows should hold, or None to skip."""
    import json

    from sqlalchemy import JSON

    default = column.default
    if default is None:
        return None  # nullable by design; NULL is the intended value
    if getattr(default, "is_scalar", False):
        value = default.arg
        return json.dumps(value) if isinstance(column.type, JSON) else value
    # Callable defaults (list, dict, utcnow) have no scalar to copy. For the
    # JSON containers the empty value is right; a timestamp we cannot invent.
    if isinstance(column.type, JSON):
        return json.dumps([] if default.arg is list else {})
    return None
