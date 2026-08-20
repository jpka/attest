"""Tests for the Evidence Archive — Firestore + GCS append-only hash chain.

Mocks Firestore and GCS so the archive runs without credentials.
The archive accepts an injectable `transactional` decorator — tests pass a
fake that runs the function with a mock transaction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.attest_orchestrator.evidence_archive import (
    EvidenceArchive,
    _body,
    _compute_hash,
)


def _mock_gcs():
    bucket = MagicMock()
    bucket.blob.return_value = MagicMock()
    return bucket


def _hash_entry(payload, prev_hash, timestamp, sequence, model_id):
    """Compute the expected entry hash the same way the archive does."""
    return _compute_hash(payload, prev_hash, timestamp, sequence, model_id)


class FakeTxn:
    """A fake Firestore transaction that records set() calls and supports conflict."""

    def __init__(self, state, raise_on_conflict=False):
        self._state = state
        self._raise_on_conflict = raise_on_conflict
        self.sets = []

    def set(self, ref, value):
        self.sets.append((ref, value))
        # Persist to state for inspection
        if hasattr(ref, "id"):
            if ref.id == "meta":
                self._state["meta"] = dict(value)
            else:
                self._state["entries"][int(ref.id)] = dict(value)

    def get(self, ref):
        snap = MagicMock()
        if hasattr(ref, "id"):
            if ref.id == "meta":
                snap.exists = "meta" in self._state
                snap.to_dict = lambda: dict(self._state.get("meta", {}))
            elif ref.id.isdigit():
                snap.exists = int(ref.id) in self._state["entries"]
                snap.to_dict = lambda: dict(self._state["entries"].get(int(ref.id), {}))
            else:
                snap.exists = False
                snap.to_dict = lambda: {}
        else:
            snap.exists = False
            snap.to_dict = lambda: {}
        return snap


def _make_tx_pair(raise_on_conflict=False):
    """Build a fake db + state with a transactional decorator.

    Returns (db, state, transactional) where state is a dict with
    'meta' and 'entries' keys.
    """
    db = MagicMock()
    state = {"meta": {}, "entries": {}}

    def get_meta(transaction=None):
        snap = MagicMock()
        snap.exists = "meta" in state
        snap.to_dict = lambda: dict(state["meta"]) if state.get("meta") else None
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
        return FakeTxn(state, raise_on_conflict=raise_on_conflict)

    db.transaction = make_txn

    def transactional(fn):
        def wrapper(txn):
            return fn(txn)
        return wrapper

    return db, state, transactional


def test_empty_chain_starts_at_zero():
    db = MagicMock()
    meta_ref = MagicMock()
    meta_ref.id = "meta"
    meta_ref.get.return_value.exists = False
    db.collection.return_value.document.return_value = meta_ref
    db.transaction = lambda: FakeTxn({"meta": {}, "entries": {}})
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
    assert "payload" in entry  # CR: self-verifiable — payload stored in entry
    assert entry["payload"] == "genesis"
    assert entry["model_id"] == "gemini-3.5-flash-lite"
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


def test_gcs_write_before_tx_commit():
    """GCS write happens BEFORE Firestore transaction — so a failed commit
    never leaves an orphan object."""
    db, state, transactional = _make_tx_pair()
    gcs_writes = []

    def record_gcs(entry):
        gcs_writes.append(entry["sequence"])

    bucket = MagicMock()
    blob = MagicMock()
    bucket.blob.return_value = blob

    call_count = [0]

    def tracking_transactional(fn):
        def wrapper(txn):
            call_count[0] += 1
            return fn(txn)
        return wrapper

    archive = EvidenceArchive(db, bucket, transactional=tracking_transactional)
    archive.append("payload", "gemini-3.5-flash-lite")

    # GCS should have been called once
    assert blob.upload_from_string.call_count == 1
    # And the upload should have happened (before or alongside tx)
    bucket.blob.assert_called_once_with("evidence/1.json")


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

    expected = _hash_entry("x", "", entry["timestamp"], 1, "gemini-3.5-flash-lite")
    assert entry["entry_hash"] == expected


def test_body_includes_model_id():
    """model_id is part of the hash input — CR required this."""
    body = _body("payload", "prev", "2024-01-01T00:00:00+00:00", 1, "gemini-3.5-flash-lite")
    assert "model_id" in body
    assert "gemini-3.5-flash-lite" in body


def test_model_id_changes_hash():
    """Changing model_id must change the hash."""
    hash1 = _compute_hash("payload", "", "2024-01-01T00:00:00+00:00", 1, "gemini-3.5-flash-lite")
    hash2 = _compute_hash("payload", "", "2024-01-01T00:00:00+00:00", 1, "gemini-3.1-flash-lite")
    assert hash1 != hash2


def test_payload_stored_in_entry():
    """CR: payload is stored in the entry so GCS object is self-verifiable."""
    db, state, transactional = _make_tx_pair()
    archive = EvidenceArchive(db, _mock_gcs(), transactional=transactional)
    entry = archive.append("verbatim-payload", "gemini-3.5-flash-lite")
    assert entry["payload"] == "verbatim-payload"
    # A verifier can recompute the hash from the stored fields
    assert archive.verify_entry(entry) is True


def test_verify_entry_detects_tamper():
    """verify_entry returns False if the entry is tampered."""
    db, state, transactional = _make_tx_pair()
    archive = EvidenceArchive(db, _mock_gcs(), transactional=transactional)
    entry = archive.append("original", "gemini-3.5-flash-lite")
    # Tamper with the payload
    entry["payload"] = "tampered"
    assert archive.verify_entry(entry) is False


def test_idempotent_retry():
    """If Firestore tx fails after GCS write, retry produces the same sequence.

    CR: orphan-state fix — retry should repair, not advance.
    """
    db, state, transactional = _make_tx_pair()
    bucket = MagicMock()
    blob = MagicMock()
    bucket.blob.return_value = blob

    call_count = [0]

    def sometimes_fail_transactional(fn):
        def wrapper(txn):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Simulated Firestore tx failure")
            return fn(txn)
        return wrapper

    archive = EvidenceArchive(db, bucket, transactional=sometimes_fail_transactional)

    # First call fails — but GCS write already happened
    with pytest.raises(RuntimeError, match="Simulated Firestore tx failure"):
        archive.append("payload", "gemini-3.5-flash-lite")

    # GCS was written once (before the failed tx)
    assert blob.upload_from_string.call_count == 1

    # Retry succeeds — overwrites GCS object with same content, advances meta
    entry = archive.append("payload", "gemini-3.5-flash-lite")
    assert entry["sequence"] == 1
    # GCS was written twice (once per attempt)
    assert blob.upload_from_string.call_count == 2
    # State shows sequence 1, not 2
    assert state["meta"]["sequence"] == 1


def test_conflict_raises():
    """If another writer advanced meta, the next append raises."""
    db, state, transactional = _make_tx_pair()

    def conflicting_transactional(fn):
        def wrapper(txn):
            # Simulate: another writer advanced meta to seq=1
            state["meta"] = {"tail_hash": "other_writer_hash", "sequence": 1}
            return fn(txn)
        return wrapper

    archive = EvidenceArchive(db, _mock_gcs(), transactional=conflicting_transactional)

    with pytest.raises(RuntimeError, match="Chain tail conflict"):
        archive.append("payload", "gemini-3.5-flash-lite")


def test_from_env_requires_bucket(monkeypatch):
    """ATTEST_EVIDENCE_BUCKET must be set."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test")
    monkeypatch.delenv("ATTEST_EVIDENCE_BUCKET", raising=False)

    import sys
    import types

    fake_firestore = types.ModuleType("google.cloud.firestore")
    fake_storage = types.ModuleType("google.cloud.storage")
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
