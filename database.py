import os
from pathlib import Path

from sqlalchemy import create_engine, Engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from models import Base
from dotenv import load_dotenv

# Load .env from the project directory (same folder as this file), not from cwd.
# Otherwise `python bot.py` run from another directory misses TELEGRAM_TOKEN / DATABASE_URL.
load_dotenv(Path(__file__).resolve().parent / ".env")

# Database URL: handles the 'postgres://' vs 'postgresql://' issue
def _default_sqlite_url() -> str:
    """Prefer project dir; if not writable (e.g. HF Space with wrong image perms), use /tmp."""
    project_dir = Path(__file__).resolve().parent
    db_in_project = project_dir / "lottery.db"
    try:
        if os.access(project_dir, os.W_OK):
            return f"sqlite:///{db_in_project.as_posix()}"
    except OSError:
        pass
    tmp_root = Path(os.getenv("TMPDIR") or "/tmp")
    fallback = tmp_root / "lottery.db"
    print(f"database: using writable SQLite path {fallback} (project dir not writable)")
    return f"sqlite:///{fallback.as_posix()}"


database_url: str = os.getenv("DATABASE_URL") or _default_sqlite_url()
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

# Connection arguments (SSL is usually required for Neon.tech)
connect_args: dict = {}
engine_kwargs: dict = {"connect_args": connect_args}

if database_url.startswith("postgresql"):
    connect_args["sslmode"] = "require"
    # Neon closes idle connections; avoid stale pooled connections
    engine_kwargs.update(
        pool_pre_ping=True,
        pool_recycle=280,
        pool_size=5,
        max_overflow=5,
    )

engine: Engine = create_engine(database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine)


def _ensure_user_last_activity_column() -> None:
    """Add users.last_activity_at if missing (existing SQLite/Postgres DBs)."""
    try:
        insp = inspect(engine)
        if "users" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("users")}
        if "last_activity_at" in cols:
            return
        dialect = engine.dialect.name
        if dialect == "sqlite":
            ddl = "ALTER TABLE users ADD COLUMN last_activity_at DATETIME"
        else:
            ddl = "ALTER TABLE users ADD COLUMN last_activity_at TIMESTAMP WITH TIME ZONE"
        with engine.begin() as conn:
            conn.execute(text(ddl))
        print("Migration: added users.last_activity_at")
    except Exception as e:
        print(f"Migration note (last_activity_at): {e}")


def _ensure_telegram_id_bigint_columns() -> None:
    """Upgrade Telegram ID columns to BIGINT on PostgreSQL (for larger account IDs)."""
    if engine.dialect.name != "postgresql":
        return

    targets = [
        ("tickets", "user_id"),
        ("transactions", "user_id"),
        ("users", "id"),
    ]
    try:
        with engine.begin() as conn:
            for table_name, column_name in targets:
                data_type = conn.execute(
                    text(
                        """
                        SELECT data_type
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = :table_name
                          AND column_name = :column_name
                        """
                    ),
                    {"table_name": table_name, "column_name": column_name},
                ).scalar()

                if not data_type:
                    continue
                if data_type == "bigint":
                    continue
                if data_type == "integer":
                    conn.execute(
                        text(
                            f'ALTER TABLE "{table_name}" '
                            f'ALTER COLUMN "{column_name}" TYPE BIGINT'
                        )
                    )
                    print(f"Migration: upgraded {table_name}.{column_name} to BIGINT")
                else:
                    print(
                        f"Migration note (telegram_id bigint): "
                        f"{table_name}.{column_name} is {data_type}, left unchanged"
                    )
    except Exception as e:
        print(f"Migration note (telegram_id bigint): {e}")


def init_db() -> None:
    Base.metadata.create_all(engine)
    _ensure_user_last_activity_column()
    _ensure_telegram_id_bigint_columns()
    print("Database initialized.")


def get_session() -> Session:
    """Return a new Session. Caller must close() it (e.g. in a finally block)."""
    return SessionLocal()
