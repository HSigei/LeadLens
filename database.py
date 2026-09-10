from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Iterator


class ConditionalWriteError(Exception):
    pass


_SCHEMA_READY = False


def _connection():
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError("psycopg is required when DATABASE_URL is configured.") from error
    return psycopg.connect(os.environ["DATABASE_URL"])


@contextmanager
def _transaction() -> Iterator[Any]:
    with _connection() as connection:
        yield connection
        connection.commit()


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _transaction() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                call_sid TEXT PRIMARY KEY,
                tenant_id TEXT,
                status TEXT,
                created_at TEXT,
                expires_at BIGINT,
                data JSONB NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS calls_tenant_created_idx ON calls (tenant_id, created_at DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS calls_status_idx ON calls (status)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at BIGINT,
                data JSONB NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS audit_tenant_created_idx ON audit_events (tenant_id, created_at DESC)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS organizations (
                tenant_id TEXT PRIMARY KEY,
                data JSONB NOT NULL
            )
            """
        )
    _SCHEMA_READY = True


class PostgresTable:
    def __init__(self, table_name: str):
        self.table_name = table_name
        ensure_schema()

    def get_item(self, *, Key: dict[str, Any], **_: Any) -> dict[str, Any]:
        key_name, key_value = next(iter(Key.items()))
        with _transaction() as connection:
            if self.table_name == "audit":
                row = connection.execute("SELECT data FROM audit_events WHERE event_id = %s", (key_value,)).fetchone()
            elif self.table_name == "organizations":
                row = connection.execute("SELECT data FROM organizations WHERE tenant_id = %s", (key_value,)).fetchone()
            else:
                row = connection.execute("SELECT data FROM calls WHERE call_sid = %s", (key_value,)).fetchone()
        return {"Item": row[0]} if row else {}

    def put_item(self, *, Item: dict[str, Any], ConditionExpression: str | None = None, **_: Any) -> None:
        if self.table_name == "audit":
            key = Item["event_id"]
            table = "audit_events"
            columns = "event_id, tenant_id, created_at, expires_at, data"
            values = (key, Item.get("tenant_id", ""), Item.get("created_at", ""), Item.get("expires_at"), json.dumps(Item))
            conflict = "event_id"
        elif self.table_name == "organizations":
            key = Item["tenant_id"]
            table = "organizations"
            columns = "tenant_id, data"
            values = (key, json.dumps(Item))
            conflict = "tenant_id"
        else:
            key = Item["call_sid"]
            table = "calls"
            columns = "call_sid, tenant_id, status, created_at, expires_at, data"
            values = (key, Item.get("tenant_id"), Item.get("status"), Item.get("created_at"), Item.get("expires_at"), json.dumps(Item))
            conflict = "call_sid"
        with _transaction() as connection:
            if ConditionExpression:
                exists = connection.execute(f"SELECT 1 FROM {table} WHERE {conflict} = %s", (key,)).fetchone()
                if exists:
                    raise ConditionalWriteError()
            placeholders = ", ".join(["%s"] * len(values))
            updates = ", ".join(f"{column}=EXCLUDED.{column}" for column in columns.split(", ")[1:])
            connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) ON CONFLICT ({conflict}) DO UPDATE SET {updates}", values)

    def update_item(self, *, Key: dict[str, Any], UpdateExpression: str, ExpressionAttributeValues: dict[str, Any], ExpressionAttributeNames: dict[str, str] | None = None, **_: Any) -> None:
        item = self.get_item(Key=Key).get("Item", {})
        item.update(Key)
        names = ExpressionAttributeNames or {}
        assignments = UpdateExpression.removeprefix("SET ").split(", ")
        for assignment in assignments:
            field, value_name = (part.strip() for part in assignment.split("=", 1))
            field = names.get(field, field)
            value = ExpressionAttributeValues[value_name]
            path = field.split(".")
            target = item
            for component in path[:-1]:
                target = target.setdefault(component, {})
            target[path[-1]] = value
        self.put_item(Item=item)

    def delete_item(self, *, Key: dict[str, Any], **_: Any) -> None:
        key_name, key_value = next(iter(Key.items()))
        table = {"audit": "audit_events", "organizations": "organizations"}.get(self.table_name, "calls")
        column = {"audit": "event_id", "organizations": "tenant_id"}.get(self.table_name, "call_sid")
        with _transaction() as connection:
            connection.execute(f"DELETE FROM {table} WHERE {column} = %s", (key_value,))

    def query(self, *, Limit: int | None = None, **kwargs: Any) -> dict[str, Any]:
        tenant_id = kwargs.get("tenant_id") or _condition_value(kwargs.get("KeyConditionExpression"))
        params: list[Any] = []
        where = ""
        if tenant_id is not None:
            where = "WHERE tenant_id = %s"
            params.append(tenant_id)
        sql = f"SELECT data FROM calls {where} ORDER BY created_at DESC"
        if Limit:
            sql += " LIMIT %s"
            params.append(Limit)
        with _transaction() as connection:
            rows = connection.execute(sql, params).fetchall()
        return {"Items": [row[0] for row in rows]}

    def scan(self, *, Limit: int | None = None, **kwargs: Any) -> dict[str, Any]:
        values = kwargs.get("ExpressionAttributeValues", {})
        status = values.get(":completed")
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status = %s"
            params.append(status)
        sql = f"SELECT data FROM calls {where} ORDER BY created_at DESC"
        if Limit:
            sql += " LIMIT %s"
            params.append(Limit)
        with _transaction() as connection:
            rows = connection.execute(sql, params).fetchall()
        return {"Items": [row[0] for row in rows]}


def _condition_value(expression: Any) -> Any:
    if expression is None:
        return None
    value = getattr(expression, "values", None)
    if value:
        return next(iter(value))
    return None
