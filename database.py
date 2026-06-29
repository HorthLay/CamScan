import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from models import Base

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://root:password@localhost:3306/camscan"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def ensure_user_columns():
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    missing_columns = {
        "age": "SMALLINT NULL",
        "gender": "VARCHAR(20) NULL",
        "ai_notes": "VARCHAR(255) NULL",
    }

    with engine.begin() as connection:
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
