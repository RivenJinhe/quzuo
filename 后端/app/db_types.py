"""PostgreSQL 与 SQLite 的 ORM 类型和列默认值。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement
from sqlalchemy.types import TypeDecorator


# 显式写入的 Python None 仍对应 JSON null，省略可空列时保留 SQL NULL。
JSON_DOCUMENT = JSONB(none_as_null=False).with_variant(JSON(none_as_null=False), "sqlite")


class UuidList(TypeDecorator[list[uuid.UUID]]):
    """SQLite 将 UUID 列表存为 JSON 数组，读出后恢复 UUID 对象。"""

    impl = ARRAY(UUID(as_uuid=True))
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(JSON(none_as_null=True))
        return super().load_dialect_impl(dialect)

    def process_bind_param(self, value, dialect):
        if dialect.name == "sqlite" and value is not None:
            return [item.hex for item in value]
        return value

    def process_result_value(self, value, dialect):
        if dialect.name == "sqlite" and value is not None:
            return [uuid.UUID(item) for item in value]
        return value


class UtcDateTime(TypeDecorator[datetime]):
    """SQLite 以 UTC 写入无时区时间，读取时补上 UTC 时区。"""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if dialect.name == "sqlite" and value is not None:
            if value.utcoffset() is None:
                raise ValueError("SQLite timestamps require a timezone-aware datetime")
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if dialect.name == "sqlite" and value is not None:
            return value.replace(tzinfo=timezone.utc)
        return value


class _ColumnDefault(ColumnElement):
    """在建表语句中按数据库输出列默认值。"""

    inherit_cache = False

    def __init__(self, postgresql_sql: str, sqlite_sql: str):
        self.postgresql_sql = postgresql_sql
        self.sqlite_sql = sqlite_sql


@compiles(_ColumnDefault)
def _compile_column_default(element, compiler, **kw):
    if compiler.dialect.name == "sqlite":
        return element.sqlite_sql
    return element.postgresql_sql


EMPTY_JSON_OBJECT = _ColumnDefault("'{}'::jsonb", "'{}'")
EMPTY_JSON_ARRAY = _ColumnDefault("'[]'::jsonb", "'[]'")
EMPTY_UUID_LIST = _ColumnDefault("'{}'", "'[]'")
# SQLite 的时间默认值和绑定参数采用相同的六位小数格式，以便按列值排序和比较。
UTC_NOW = _ColumnDefault("now()", "(strftime('%Y-%m-%d %H:%M:%f000', 'now'))")
