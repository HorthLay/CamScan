import logging
import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from dotenv import load_dotenv
from models import Base

load_dotenv()

logger = logging.getLogger("camscan.db")

PRIMARY_URL = os.getenv("DATABASE_URL", "mysql+pymysql://root:password@localhost:3306/camscan")

# Supabase alternate database — used as a temp fallback when the primary is down.
SUPABASE_DATABASE_URL = (
    os.getenv("SUPABASE_DATABASE_URL") or os.getenv("SUPABASE_DB_URL") or ""
)

# Last-resort temporary database so the app never crashes on DB outage.
FALLBACK_SQLITE_URL = os.getenv("FALLBACK_SQLITE_URL", "sqlite:///./camscan_fallback.db")


def _build_engine(url: str):
    """Create an engine tuned for the target dialect."""
    engine_params = {
        "pool_pre_ping": True,
        "echo": False,
        "connect_args": {"connect_timeout": 3},
    }

    if url.startswith("sqlite"):
        engine_params["connect_args"] = {"check_same_thread": False}
        engine_params["poolclass"] = StaticPool
    elif url.startswith("postgres"):
        # Supabase/Postgres — refresh quickly (pooler closes idle connections).
        engine_params.update({
            "pool_size": 10,
            "max_overflow": 20,
            "pool_recycle": 60,
            "pool_timeout": 10,
        })
    else:
        # MySQL and friends.
        engine_params.update({
            "pool_size": 10,
            "max_overflow": 20,
            "pool_recycle": 1800,               # before MySQL idle-timeout
            "pool_timeout": 5,
            "connect_args": {"charset": "utf8mb4", "connect_timeout": 3},
        })

    return create_engine(url, **engine_params)


def _can_connect(engine) -> bool:
    """Quick round-trip check. Returns True if the database answers."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("Database unavailable (%s): %s", engine.url.database, exc)
        return False


def _select_engine():
    """Primary DB -> Supabase -> temp SQLite. The chosen engine is cached for the app lifetime."""
    primary = _build_engine(PRIMARY_URL)
    if _can_connect(primary):
        logger.info("Using primary database: %s", primary.url.database)
        return primary

    if SUPABASE_DATABASE_URL.strip():
        try:
            supabase_engine = _build_engine(SUPABASE_DATABASE_URL.strip())
        except Exception as exc:
            logger.warning("Could not build Supabase engine: %s", exc)
            supabase_engine = None
        if supabase_engine is not None and _can_connect(supabase_engine):
            logger.warning("Primary database failed -> using Supabase fallback.")
            return supabase_engine
    else:
        logger.warning("Primary database failed and SUPABASE_DATABASE_URL is not set.")

    logger.warning("All configured databases failed -> using temporary SQLite fallback: %s", FALLBACK_SQLITE_URL)
    return _build_engine(FALLBACK_SQLITE_URL)


engine = _select_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def ensure_user_columns():
    # The ALTER statements are MySQL-specific; Skip on Postgres/SQLite (schema is
    # created fresh from models there, so the columns already exist).
    if engine.dialect.name != "mysql":
        return

    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    
    with engine.begin() as connection:
        for column_info in inspector.get_columns("users"):
            col_name = column_info["name"]
            col_type = str(column_info["type"])
            
            if col_name == "note" and "enum" in col_type.lower():
                connection.execute(
                    text("ALTER TABLE users MODIFY COLUMN note VARCHAR(20) NULL")
                )
        
        missing_columns = {
            "age": "SMALLINT NULL",
            "gender": "VARCHAR(20) NULL",
            "ai_notes": "VARCHAR(255) NULL",
            "date_of_birth": "DATE NULL",
            "note": "VARCHAR(20) NULL",
        }

        for column_name, column_type in missing_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
                )


def create_tables():
    Base.metadata.create_all(bind=engine)
    ensure_user_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
