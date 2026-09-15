from contextlib import contextmanager

import database


class FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return FakeCursor(self.rows)


def fake_transaction(connection):
    @contextmanager
    def _transaction():
        yield connection

    return _transaction


def test_upsert_fact_uses_on_conflict_update(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(database, "_FACTS_SCHEMA_READY", True)
    monkeypatch.setattr(database, "_transaction", fake_transaction(connection))

    database.upsert_fact("tenant-a", "hours", "monday_hours", "9am-5pm")

    sql, params = connection.executed[-1]
    assert "ON CONFLICT (tenant_id, fact_type, fact_key) DO UPDATE" in sql
    assert params[:4] == ("tenant-a", "hours", "monday_hours", "9am-5pm")


def test_get_facts_filters_by_tenant_id(monkeypatch):
    rows = [("hours", "monday_hours", "9am-5pm", "2026-01-01T00:00:00+00:00")]
    connection = FakeConnection(rows=rows)
    monkeypatch.setattr(database, "_FACTS_SCHEMA_READY", True)
    monkeypatch.setattr(database, "_transaction", fake_transaction(connection))

    facts = database.get_facts("tenant-a")

    sql, params = connection.executed[-1]
    assert "WHERE tenant_id = %s" in sql
    assert params == ("tenant-a",)
    assert facts == [{"fact_type": "hours", "fact_key": "monday_hours", "fact_value": "9am-5pm", "updated_at": "2026-01-01T00:00:00+00:00"}]


def test_delete_fact_scopes_to_tenant_type_and_key(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(database, "_FACTS_SCHEMA_READY", True)
    monkeypatch.setattr(database, "_transaction", fake_transaction(connection))

    database.delete_fact("tenant-a", "hours", "monday_hours")

    sql, params = connection.executed[-1]
    assert "DELETE FROM tenant_facts" in sql
    assert params == ("tenant-a", "hours", "monday_hours")


def test_ensure_facts_schema_creates_table_once(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(database, "_FACTS_SCHEMA_READY", False)
    monkeypatch.setattr(database, "_transaction", fake_transaction(connection))

    database.ensure_facts_schema()
    database.ensure_facts_schema()

    assert len(connection.executed) == 1
    assert "CREATE TABLE IF NOT EXISTS tenant_facts" in connection.executed[0][0]


def test_insert_document_chunk_uses_on_conflict_update(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(database, "_DOCUMENTS_SCHEMA_READY", True)
    monkeypatch.setattr(database, "_transaction", fake_transaction(connection))

    database.insert_document_chunk("tenant-a", "doc-1", 0, "chunk text")

    sql, params = connection.executed[-1]
    assert "ON CONFLICT (tenant_id, document_id, chunk_index) DO UPDATE" in sql
    assert params[:4] == ("tenant-a", "doc-1", 0, "chunk text")


def test_search_document_chunks_scopes_to_tenant_id(monkeypatch):
    rows = [("doc-1", 0, "matching chunk text")]
    connection = FakeConnection(rows=rows)
    monkeypatch.setattr(database, "_DOCUMENTS_SCHEMA_READY", True)
    monkeypatch.setattr(database, "_transaction", fake_transaction(connection))

    results = database.search_document_chunks("tenant-a", "opening hours", limit=4)

    sql, params = connection.executed[-1]
    assert "WHERE tenant_id = %s AND to_tsvector" in sql
    assert "ORDER BY ts_rank" in sql
    assert params == ("tenant-a", "opening hours", "opening hours", 4)
    assert results == [{"document_id": "doc-1", "chunk_index": 0, "chunk_text": "matching chunk text"}]


def test_ensure_documents_schema_creates_table_once(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(database, "_DOCUMENTS_SCHEMA_READY", False)
    monkeypatch.setattr(database, "_transaction", fake_transaction(connection))

    database.ensure_documents_schema()
    database.ensure_documents_schema()

    assert len(connection.executed) == 2
    assert "CREATE TABLE IF NOT EXISTS tenant_documents" in connection.executed[0][0]

