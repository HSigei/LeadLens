from types import SimpleNamespace

import core
import storage


def test_storage_client_uses_r2_endpoint_when_configured(monkeypatch):
    captured = {}
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setattr(storage.boto3, "client", lambda service, **kwargs: captured.update(service=service, **kwargs))
    storage.storage_client()
    assert captured == {"service": "s3", "region_name": "us-east-1", "endpoint_url": "https://example.r2.cloudflarestorage.com"}


def test_postgres_table_factories_work_when_database_url_is_set(monkeypatch):
    table = SimpleNamespace()
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/test")
    monkeypatch.setattr(core, "PostgresTable", lambda name: table)
    assert core.calls_table() is table
    assert core.audit_table() is table
    assert core.organizations_table() is table