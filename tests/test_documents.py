"""DocumentStore CRUD tests (pure Python, uses tmp dirs)."""

import pytest

from querynest.core.exceptions import DocumentNotFoundError
from querynest.core.models import DocumentMetadata
from querynest.storage.document_store import DocumentStore


def _store(tmp_path):
    return DocumentStore(storage_dir=str(tmp_path / "qn_store"))


def _meta(doc_id="doc1", name="paper.pdf", ftype="pdf"):
    return DocumentMetadata(
        document_id=doc_id, filename=name, file_type=ftype, parser="mineru",
        parse_method="auto",
    )


def test_upsert_and_get(tmp_path):
    store = _store(tmp_path)
    store.upsert(_meta())
    row = store.get_document("doc1")
    assert row["filename"] == "paper.pdf"
    assert row["document_id"] == "doc1"


def test_upsert_saves_source_text(tmp_path):
    store = _store(tmp_path)
    store.upsert(_meta(), content="hello world body text")
    assert "hello" in store.read_source("doc1")


def test_list_documents(tmp_path):
    store = _store(tmp_path)
    store.upsert(_meta("a"))
    store.upsert(_meta("b", "b.pdf"))
    assert {d["document_id"] for d in store.list_documents()} == {"a", "b"}


def test_document_exists(tmp_path):
    store = _store(tmp_path)
    assert store.document_exists("nope") is False
    store.upsert(_meta())
    assert store.document_exists("doc1") is True


def test_document_status(tmp_path):
    store = _store(tmp_path)
    store.upsert(_meta())
    assert store.document_status("doc1") == "ready"


def test_delete_document(tmp_path):
    store = _store(tmp_path)
    store.upsert(_meta())
    assert store.delete_document("doc1") is True
    assert store.document_exists("doc1") is False
    assert store.delete_document("doc1") is False


def test_get_missing_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(DocumentNotFoundError):
        store.get_document("missing")


def test_persistence_across_reopen(tmp_path):
    path = str(tmp_path / "qn_store")
    s1 = DocumentStore(storage_dir=path)
    s1.upsert(_meta())
    s2 = DocumentStore(storage_dir=path)
    assert s2.document_exists("doc1") is True


def test_upsert_keeps_created_at(tmp_path):
    store = _store(tmp_path)
    store.upsert(_meta("a"))
    store.upsert(_meta("a"))
    row = store.get_document("a")
    assert row["created_at"]