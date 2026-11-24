"""数据库会话与建表。"""
import sqlite3
from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.schema import CreateColumn

from app.config import settings


class Base(DeclarativeBase):
    pass


database_url = make_url(settings.database_url)
connect_args = {}
if database_url.get_backend_name() == "sqlite" and hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL"):
    # 保持 SELECT 不隐式开启事务，让需要写锁的路径可以显式开始写事务。
    connect_args["autocommit"] = sqlite3.LEGACY_TRANSACTION_CONTROL

engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)

if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON").close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def begin_write(db: Session) -> None:
    """在 SQLite 读取待修改的数据前取得写锁，PostgreSQL 由调用方锁定对应行。"""
    if db.get_bind().dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialize_schema() -> None:
    """创建数据表，并为已有的生成记录表补齐可空字段。"""
    from app.models import Generation

    with engine.begin() as conn:
        if conn.dialect.name == "sqlite":
            # 建表、检查旧字段和补列共用一个写事务，允许多个本地进程同时启动。
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        Base.metadata.create_all(conn)
        columns = {col["name"] for col in inspect(conn).get_columns("generation")}
        for name in ("usage", "changes", "options"):
            if name not in columns:
                column = CreateColumn(Generation.__table__.c[name]).compile(dialect=conn.dialect)
                guard = "IF NOT EXISTS " if conn.dialect.name == "postgresql" else ""
                conn.exec_driver_sql(f"ALTER TABLE generation ADD COLUMN {guard}{column}")
