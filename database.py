from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator


class ConditionalWriteError(Exception):
    pass


_SCHEMA_READY = False
_FACTS_SCHEMA_READY = False
_DOCUMENTS_SCHEMA_READY = False


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


def ensure_facts_schema() -> None:
    global _FACTS_SCHEMA_READY
    if _FACTS_SCHEMA_READY:
        return
    with _transaction() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_facts (
                tenant_id TEXT NOT NULL,
                fact_type TEXT NOT NULL,
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, fact_type, fact_key)
            )
            """
        )
    _FACTS_SCHEMA_READY = True


def upsert_fact(tenant_id: str, fact_type: str, fact_key: str, fact_value: str) -> None:
    ensure_facts_schema()
    with _transaction() as connection:
        connection.execute(
            """
            INSERT INTO tenant_facts (tenant_id, fact_type, fact_key, fact_value, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, fact_type, fact_key) DO UPDATE SET fact_value=EXCLUDED.fact_value, updated_at=EXCLUDED.updated_at
            """,
            (tenant_id, fact_type, fact_key, fact_value, datetime.now(UTC).isoformat()),
        )


def get_facts(tenant_id: str) -> list[dict[str, Any]]:
    ensure_facts_schema()
    with _transaction() as connection:
        rows = connection.execute(
            "SELECT fact_type, fact_key, fact_value, updated_at FROM tenant_facts WHERE tenant_id = %s ORDER BY fact_type, fact_key",
            (tenant_id,),
        ).fetchall()
    return [{"fact_type": row[0], "fact_key": row[1], "fact_value": row[2], "updated_at": row[3]} for row in rows]


def delete_fact(tenant_id: str, fact_type: str, fact_key: str) -> None:
    ensure_facts_schema()
    with _transaction() as connection:
        connection.execute(
            "DELETE FROM tenant_facts WHERE tenant_id = %s AND fact_type = %s AND fact_key = %s",
            (tenant_id, fact_type, fact_key),
        )


def ensure_documents_schema() -> None:
    global _DOCUMENTS_SCHEMA_READY
    if _DOCUMENTS_SCHEMA_READY:
        return
    with _transaction() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_documents (
                tenant_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, document_id, chunk_index)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS tenant_documents_tenant_idx ON tenant_documents (tenant_id)")
    _DOCUMENTS_SCHEMA_READY = True


def insert_document_chunk(tenant_id: str, document_id: str, chunk_index: int, chunk_text: str) -> None:
    ensure_documents_schema()
    with _transaction() as connection:
        connection.execute(
            """
            INSERT INTO tenant_documents (tenant_id, document_id, chunk_index, chunk_text, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, document_id, chunk_index) DO UPDATE SET chunk_text=EXCLUDED.chunk_text, created_at=EXCLUDED.created_at
            """,
            (tenant_id, document_id, chunk_index, chunk_text, datetime.now(UTC).isoformat()),
        )


def search_document_chunks(tenant_id: str, query: str, limit: int = 4) -> list[dict[str, Any]]:
    ensure_documents_schema()
    with _transaction() as connection:
        rows = connection.execute(
            """
            SELECT document_id, chunk_index, chunk_text
            FROM tenant_documents
            WHERE tenant_id = %s AND to_tsvector('english', chunk_text) @@ plainto_tsquery('english', %s)
            ORDER BY ts_rank(to_tsvector('english', chunk_text), plainto_tsquery('english', %s)) DESC
            LIMIT %s
            """,
            (tenant_id, query, query, limit),
        ).fetchall()
    return [{"document_id": row[0], "chunk_index": row[1], "chunk_text": row[2]} for row in rows]
