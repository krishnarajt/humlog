from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os
from pathlib import Path

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://humlog:humlog@localhost:5432/humlog"
)

# Handle postgres:// vs postgresql:// (for compatibility)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update(
        {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_recycle": 300,  # Recycle connections every 5 minutes
        }
    )

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency for getting database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations():
    """Run Alembic migrations to the latest revision."""
    from alembic import command
    from alembic.config import Config

    project_root = Path(__file__).resolve().parents[2]
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    # script_location in alembic.ini is relative to the working directory. The
    # app runs migrations itself at startup, so pin it to an absolute path and
    # stop caring what the process was launched from.
    alembic_cfg.set_main_option("script_location", str(project_root / "alembic"))
    command.upgrade(alembic_cfg, "head")


def init_db(use_alembic: bool | None = None):
    """Initialize database tables.

    Alembic by default: the app runs `alembic upgrade head` on startup, so a
    deploy that adds a migration applies it without a separate step. Set
    USE_ALEMBIC_MIGRATIONS=false to fall back to create_all(), which builds the
    current schema but records no revision.
    """
    import app.db.models  # noqa: F401  (registers every model on Base)

    if use_alembic is None:
        use_alembic = os.getenv("USE_ALEMBIC_MIGRATIONS", "true").lower() == "true"

    if use_alembic:
        run_migrations()
    else:
        Base.metadata.create_all(bind=engine)
