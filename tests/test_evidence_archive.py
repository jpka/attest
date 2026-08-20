"""Tests for the Evidence Archive — Firestore + GCS append-only hash chain.

Mocks Firestore and GCS so the archive runs without credentials.
The archive accepts an injectable `transactional` decorator — tests pass a
fake that runs the function with a mock transaction.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock

import pytest

from agents.attest_orchestrator.evidence_archive import EvidenceArchive


def _mock_gcs():
    bucket = MagicMock()
    bucket.blob.return_value = MagicMock()
    return bucket


def _hash_entry(payload, prev_hash, timestamp, sequence):
    """Compute the expected entry hash the same way the archive does."""
    body = json.dumps(
        {"payload": payload, "prev_hash": prev_hash, "timestamp": timestamp, "sequence": sequence},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


class FakeTxn:
    """A fake Firestore transaction that records set() calls."""
    def __init__(self):
        self.sets = []

    def set(self, ref, value):
        self.sets.append((ref, value))


def _make_tx_pair():
    """Build a fake db + state with a transactional decorator that records writes.

    Returns (db, state, transactional) where state is a dict with
    'meta' and 'entries' keys.
    """
    db = MagicMock()
    state = {"meta": {}, "entries": {}}

    def get_meta(transaction=None):
        snap = MagicMock()
        snap.exists = bool(state["meta"])
        snap.to_dict = lambda: dict(state["meta"]) if state["meta"] else None
        return snap

    meta_ref = MagicMock()
    meta_ref.id = "meta"
    meta_ref.get = get_meta

    def make_entry_ref(seq):
        ref = MagicMock()
        ref.id = str(seq)
        return ref

    db.collection.return_value.document.side_effect = lambda name: (
        meta_ref if name == "meta" else make_entry_ref(name)
    )

    def make_txn():
        return FakeTxn()

    db.transaction = make_txn

    def transactional(fn):
        """A firestore-like transactional wrapper that replays set() calls."""
        def wrapper(txn):
            result = fn(txn)
            # Apply the writes to state
            for ref, value in txn.sets:
                if ref.id == "meta":
                    state["meta"].clear()
                    state["meta"].update(value)
                else:
                    state["entries"][int(ref.id)] = value
            return result
        return wrapper

    return db, state, transactional


def test_empty_chain_starts_at_zero():
    db = MagicMock()
    meta_ref = MagicMock()
    meta_ref.id = "meta"
    meta_ref.get.return_value.exists = False
    db.collection.return_value.document.return_value = meta_ref
    db.transaction = lambda: FakeTxn()
    archive = EvidenceArchive(db, _mock_gcs(), transactional=lambda fn: fn)
    tail_hash, seq = archive.current_tail()
    assert tail_hash == ""
    assert seq == 0


def test_append_returns_linked_entry():
    db, state, transactional = _make_tx_pair()
    archive = EvidenceArchive(db, _mock_gcs(), transactional=transactional)
    entry = archive.append("genesis", "gemini-3.5-flash-lite")

    assert entry["sequence"] == 1
    assert entry["prev_hash"] == ""
    assert "entry_hash" in entry
    assert "payload_sha256" in entry
    assert "model_id" in entry
    # Verify state was written
    assert 1 in state["entries"]
    assert state["meta"]["sequence"] == 1


def test_second_entry_links_to_first():
    db, state, transactional = _make_tx_pair()
    archive = EvidenceArchive(db, _mock_gcs(), transactional=transactional)
    first = archive.append("genesis", "gemini-3.5-flash-lite")
    second = archive.append("second", "gemini-3.5-flash-lite")

    assert first["sequence"] == 1
    assert first["prev_hash"] == ""
    assert second["sequence"] == 2
    assert second["prev_hash"] == first["entry_hash"]
    # Verify state has both entries
    assert 1 in state["entries"]
    assert 2 in state["entries"]
    assert state["entries"][2]["prev_hash"] == first["entry_hash"]


def test_gcs_write_after_tx_commit():
    db, state, transactional = _make_tx_pair()
    bucket = MagicMock()
    blob = MagicMock()
    bucket.blob.return_value = blob
    archive = EvidenceArchive(db, bucket, transactional=transactional)
    archive.append("payload", "gemini-3.5-flash-lite")

    bucket.blob.assert_called_once_with("evidence/1.json")
    blob.upload_from_string.assert_called_once()
    args, kwargs = blob.upload_from_string.call_args
    assert kwargs.get("content_type") == "application/json"


def test_hash_is_deterministic():
    """The same inputs must produce the same hash every time."""
    db, state, transactional = _make_tx_pair()
    archive = EvidenceArchive(db, _mock_gcs(), transactional=transactional)
    entry = archive.append("x", "gemini-3.5-flash-lite")

    expected = _hash_entry("x", "", entry["timestamp"], 1)
    assert entry["entry_hash"] == expected


def test_from_env_requires_bucket(monkeypatch):
    """ATTEST_EVIDENCE_BUCKET must be set."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test")
    monkeypatch.delenv("ATTEST_EVIDENCE_BUCKET", raising=False)

    # Inject fake google.cloud modules so from_env's lazy import resolves
    import sys
    import types

    fake_firestore = types.ModuleType("google.cloud.firestore")
    fake_storage = types.ModuleType("google.cloud.storage")
    # Provide stub constructors so from_env() can build without real GCP
    fake_firestore.Client = lambda **kw: MagicMock()
    fake_storage.Client = lambda **kw: MagicMock()

    old_firestore = sys.modules.get("google.cloud.firestore")
    old_storage = sys.modules.get("google.cloud.storage")
    sys.modules["google.cloud.firestore"] = fake_firestore
    sys.modules["google.cloud.storage"] = fake_storage
    try:
        with pytest.raises(RuntimeError, match="ATTEST_EVIDENCE_BUCKET"):
            EvidenceArchive.from_env()
    finally:
        if old_firestore is not None:
            sys.modules["google.cloud.firestore"] = old_firestore
        else:
            sys.modules.pop("google.cloud.firestore", None)
        if old_storage is not None:
            sys.modules["google.cloud.storage"] = old_storage
        else:
            sys.modules.pop("google.cloud.storage", None)
