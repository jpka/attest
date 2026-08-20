"""Tests for the Evidence Archive — Firestore + GCS append-only hash chain.

Mocks Firestore and GCS so the archive runs without credentials.
The archive accepts an injectable `transactional` decorator — tests pass a
fake that runs the function with a mock transaction.

The hash oracle uses independent canonical serialization (json.dumps +
hashlib.sha256) rather than calling _compute_hash, so serialization changes
cannot update both sides and pass.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from agents.attest_orchestrator.evidence_archive import (
    EvidenceArchive,
    _body,
    _compute_hash,
)


def _independent_hash(payload, prev_hash, timestamp, sequence, model_id):
    """Independent canonical serialization oracle — NOT _compute_hash."""
    canonical = json.dumps(
        {
            "payload": payload,
            "prev_hash": prev_hash,
            "timestamp": timestamp,
            "sequence": sequence,
            "model_id": model_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _mock_gcs(store: dict | None = None):
    """A bucket that actually stores what is uploaded.

    Reconciliation compares the object's bytes against the committed entry, so
    a bucket whose download_as_text() returns a MagicMock cannot exercise that
    path — it fails for the wrong reason. This keeps a dict of path -> content.
    """
    store = {} if store is None else store
    bucket = MagicMock()

    def blob_for(path):
        blob = MagicMock()
        blob.exists.side_effect = lambda: path in store

        def upload(content, **kw):
            if kw.get("if_generation_match") == 0 and path in store:
                raise RuntimeError("412 conditionNotMet")
            store[path] = content

        blob.upload_from_string.side_effect = upload
        blob.download_as_text.side_effect = lambda: store[path]
        return blob

    bucket.blob.side_effect = blob_for
    bucket._store = store
    return bucket


class FakeTxn:
    """A fake Firestore transaction that records set() calls."""

    def __init__(self, state):
        self._state = state
        self.sets = []

    def set(self, ref, value):
        self.sets.append((ref, value))
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


def _make_tx_pair():
    """Build a fake db + state with a transactional decorator."""
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

        # Non-transactional read, used by reconcile_tail. Serve it from the
        # same state the fake transaction writes to, or the recovery path
        # sees MagicMock attributes instead of the entry it just committed.
        def get(transaction=None):
            snap = MagicMock()
            key = int(seq)
            snap.exists = key in state["entries"]
            snap.to_dict = lambda: dict(state["entries"].get(key, {}))
            return snap

        ref.get = get
        return ref

    db.collection.return_value.document.side_effect = lambda name: (
        meta_ref if name == "meta" else make_entry_ref(name)
    )

    def make_txn():
        return FakeTxn(state)

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
    assert "payload" in entry
    assert entry["payload"] == "genesis"
    assert entry["model_id"] == "gemini-3.5-flash-lite"
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
    assert 1 in state["entries"]
    assert 2 in state["entries"]
    assert state["entries"][2]["prev_hash"] == first["entry_hash"]


def test_gcs_immutable_write_uses_if_generation_match():
    """GCS write must use if_generation_match=0 (immutable create)."""
    db, state, transactional = _make_tx_pair()
    bucket = MagicMock()
    blob = MagicMock()
    bucket.blob.return_value = blob

    archive = EvidenceArchive(db, bucket, transactional=transactional)
    archive.append("payload", "gemini-3.5-flash-lite")

    args, kwargs = blob.upload_from_string.call_args
    assert kwargs.get("if_generation_match") == 0


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

    expected = _independent_hash("x", "", entry["timestamp"], 1, "gemini-3.5-flash-lite")
    assert entry["entry_hash"] == expected


def test_body_includes_model_id():
    """model_id is part of the hash input."""
    body = _body("payload", "prev", "2024-01-01T00:00:00+00:00", 1, "gemini-3.5-flash-lite")
    assert "model_id" in body
    assert "gemini-3.5-flash-lite" in body


def test_model_id_changes_hash():
    """Changing model_id must change the hash."""
    hash1 = _compute_hash("payload", "", "2024-01-01T00:00:00+00:00", 1, "gemini-3.5-flash-lite")
    hash2 = _compute_hash("payload", "", "2024-01-01T00:00:00+00:00", 1, "gemini-3.1-flash-lite")
    assert hash1 != hash2


def test_payload_stored_in_entry():
    """Payload is stored in the entry so GCS object is self-verifiable."""
    db, state, transactional = _make_tx_pair()
    archive = EvidenceArchive(db, _mock_gcs(), transactional=transactional)
    entry = archive.append("verbatim-payload", "gemini-3.5-flash-lite")
    assert entry["payload"] == "verbatim-payload"
    assert archive.verify_entry(entry) is True


def test_verify_entry_detects_tamper():
    """verify_entry returns False if the entry is tampered."""
    db, state, transactional = _make_tx_pair()
    archive = EvidenceArchive(db, _mock_gcs(), transactional=transactional)
    entry = archive.append("original", "gemini-3.5-flash-lite")
    entry["payload"] = "tampered"
    assert archive.verify_entry(entry) is False


def test_verify_entry_returns_false_on_missing_fields():
    """verify_entry returns False for incomplete/malformed entries."""
    db, state, transactional = _make_tx_pair()
    archive = EvidenceArchive(db, _mock_gcs(), transactional=transactional)
    assert archive.verify_entry({}) is False
    assert archive.verify_entry({"payload": "x"}) is False
    assert archive.verify_entry(None) is False
    # CR: entry with all required fields except entry_hash
    incomplete = {
        "payload": "x",
        "prev_hash": "",
        "timestamp": "2024-01-01T00:00:00+00:00",
        "sequence": 1,
        "model_id": "gemini-3.5-flash-lite",
    }
    assert archive.verify_entry(incomplete) is False


def test_gcs_object_already_exists_idempotent():
    """If GCS object already exists with our hash, it's an idempotent retry."""
    db, state, transactional = _make_tx_pair()
    bucket = MagicMock()
    blob = MagicMock()

    blob.upload_from_string.side_effect = Exception(
        "412 PreconditionFailed: conditionNotMet"
    )

    fixed_timestamp = "2026-08-20T16:00:00+00:00"
    with patch("agents.attest_orchestrator.evidence_archive.datetime") as mock_dt:
        mock_dt.now.return_value = datetime.fromisoformat(fixed_timestamp)
        our_hash = _independent_hash("payload", "", fixed_timestamp, 1, "gemini-3.5-flash-lite")

        blob.download_as_text.return_value = json.dumps({
            "entry_hash": our_hash,
            "payload": "payload",
            "prev_hash": "",
            "timestamp": fixed_timestamp,
            "sequence": 1,
            "model_id": "gemini-3.5-flash-lite",
            "payload_sha256": hashlib.sha256(b"payload").hexdigest(),
        })
        bucket.blob.return_value = blob

        archive = EvidenceArchive(db, bucket, transactional=transactional)
        entry = archive.append("payload", "gemini-3.5-flash-lite")

    assert entry["sequence"] == 1
    assert entry["entry_hash"] == our_hash


def test_gcs_object_exists_with_different_hash_raises():
    """If GCS object exists with different hash, chain corruption is detected."""
    db, state, transactional = _make_tx_pair()
    bucket = MagicMock()
    blob = MagicMock()

    blob.upload_from_string.side_effect = Exception(
        "412 PreconditionFailed: conditionNotMet"
    )
    blob.download_as_text.return_value = json.dumps({
        "entry_hash": "different_hash",
        "payload": "other",
        "sequence": 1,
    })
    bucket.blob.return_value = blob

    archive = EvidenceArchive(db, bucket, transactional=transactional)

    with pytest.raises(RuntimeError, match="Chain corruption"):
        archive.append("payload", "gemini-3.5-flash-lite")


def test_idempotent_retry_pre_transaction_failure():
    """If Firestore tx fails before GCS write, retry produces the same sequence."""
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

    with pytest.raises(RuntimeError, match="Simulated Firestore tx failure"):
        archive.append("payload", "gemini-3.5-flash-lite")

    # GCS was NOT written because Firestore tx failed first
    blob.upload_from_string.assert_not_called()

    # Retry succeeds
    entry = archive.append("payload", "gemini-3.5-flash-lite")
    assert entry["sequence"] == 1
    assert blob.upload_from_string.call_count == 1


def test_conflict_raises():
    """If another writer advanced meta, the next append raises."""
    db, state, transactional = _make_tx_pair()

    def conflicting_transactional(fn):
        def wrapper(txn):
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


class TestAppendEvidenceToolSignature:
    """The tool must not let the model supply chain linkage.

    An earlier version of `append_evidence` accepted `prev_hash` as a tool
    argument. That let the model fork or reset the chain by passing a stale
    value — undetectable downstream, and precisely the failure the Evidence
    Archive exists to prevent. The fix was to have the tool read the tail
    itself; nothing asserted it stayed that way, so this is that assertion.

    Read with `ast` rather than by importing: `agent.py` needs `google.adk`,
    which the generated Dockerfile installs and CI does not, so an
    import-based check here would be skipped on exactly the machines that run
    it. Re-adding the parameter is a source-level mistake and this catches it
    at the source.
    """

    def _signature_params(self) -> list[str]:
        import ast
        import pathlib

        src = pathlib.Path("agents/attest_orchestrator/agent.py").read_text()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == "append_evidence":
                args = node.args
                assert not args.vararg, "no *args on a tool signature"
                assert not args.kwarg, "no **kwargs on a tool signature"
                return [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
        raise AssertionError("append_evidence not found in agent.py")

    def test_takes_only_payload(self):
        params = self._signature_params()
        assert params == ["payload"], (
            f"append_evidence must take only 'payload'; got {params}. "
            "Chain linkage is read from the tail, never supplied by the caller."
        )

    def test_prev_hash_is_not_a_parameter(self):
        assert "prev_hash" not in self._signature_params(), (
            "prev_hash is back as a tool argument — the model can fork or reset "
            "the chain by passing a stale value."
        )



class TestPostCommitGCSFailureIsRecoverable:
    """The window between the Firestore commit and the GCS write.

    `append` commits the entry and advances the tail, then writes the object.
    If the second step fails — process death, or a bucket that is not there —
    Firestore holds a committed entry with no durable object, and the *next*
    append reads that advanced tail, succeeds, and buries the gap one entry
    deeper where nothing will look for it.

    Not hypothetical. `deploy.sh infra` gained the bucket-creation step days
    after `infra` had last been run, so the deployed archive spent four days
    committing to Firestore and 404ing on GCS. Entries 1 and 2 of the live
    chain exist in Firestore with no object, permanently, because nothing
    reconciled before entry 3 was appended.

    `reconcile_tail` closes it: re-write the same committed entry, never roll
    the chain back. The entry may already have been reported to a caller, so
    retracting its hash is worse than repairing it.
    """

    def test_gcs_failure_after_commit_leaves_a_committed_entry(self):
        """Establish the hazard: the commit survives, the object does not."""
        db, state, transactional = _make_tx_pair()
        bucket = MagicMock()
        blob = MagicMock()
        blob.exists.return_value = False
        blob.upload_from_string.side_effect = RuntimeError("bucket does not exist")
        bucket.blob.return_value = blob
        archive = EvidenceArchive(db, bucket, transactional=transactional)

        with pytest.raises(RuntimeError, match="bucket does not exist"):
            archive.append("genesis", "gemini-3.5-flash-lite")

        # Firestore committed even though the caller saw an exception.
        assert 1 in state["entries"]
        assert state["meta"]["sequence"] == 1

    def test_next_append_reconciles_the_missing_object(self):
        store = {}
        db, _state, transactional = _make_tx_pair()
        archive = EvidenceArchive(db, _mock_gcs(store), transactional=transactional)

        first = archive.append("genesis", "gemini-3.5-flash-lite")
        # Simulate the post-commit failure: entry 1 committed, object lost.
        del store["evidence/1.json"]

        second = archive.append("second", "gemini-3.5-flash-lite")

        assert "evidence/1.json" in store, "entry 1's object was not rewritten"
        assert second["prev_hash"] == first["entry_hash"]
        assert second["sequence"] == 2

    def test_reconcile_is_a_noop_when_the_object_is_present(self):
        db, _state, transactional = _make_tx_pair()
        archive = EvidenceArchive(db, _mock_gcs(), transactional=transactional)
        archive.append("genesis", "gemini-3.5-flash-lite")

        assert archive.reconcile_tail() is None

    def test_reconcile_is_a_noop_on_an_empty_chain(self):
        db = MagicMock()
        meta_ref = MagicMock()
        meta_ref.id = "meta"
        meta_ref.get.return_value.exists = False
        db.collection.return_value.document.return_value = meta_ref
        archive = EvidenceArchive(db, _mock_gcs(), transactional=lambda fn: fn)

        assert archive.reconcile_tail() is None

    def test_reconcile_rejects_a_divergent_object_at_the_expected_path(self):
        """An object at the right path is not proof of the right object.

        A self-valid entry for some *other* payload can sit at
        evidence/{seq}.json. Accepting it because the path is occupied would
        report a successful repair while Firestore and GCS disagree about what
        entry 1 says — which is the one thing the archive exists to rule out.
        """
        store = {}
        db, _state, transactional = _make_tx_pair()
        archive = EvidenceArchive(db, _mock_gcs(store), transactional=transactional)
        archive.append("genesis", "gemini-3.5-flash-lite")

        # Replace the object with a different, internally consistent entry.
        ts = "2026-08-20T16:00:00+00:00"
        other = {
            "entry_hash": _independent_hash("tampered", "", ts, 1, "m"),
            "prev_hash": "",
            "timestamp": ts,
            "sequence": 1,
            "payload": "tampered",
            "payload_sha256": hashlib.sha256(b"tampered").hexdigest(),
            "model_id": "m",
        }
        store["evidence/1.json"] = json.dumps(other, sort_keys=True)

        with pytest.raises(RuntimeError, match="does not match the committed entry"):
            archive.reconcile_tail()

    def test_reconcile_rejects_an_entry_whose_sequence_disagrees(self):
        """Document ID and entry sequence must agree.

        `verify_entry` hashes `entry["sequence"]`, never the document ID, so a
        self-valid entry stored at document 1 while claiming sequence 2 passes
        both the tail-hash and self-verification checks. The object path is
        derived from the entry, so repairing it would write evidence/2.json,
        leave evidence/1.json absent, and report success.
        """
        db, state, transactional = _make_tx_pair()
        archive = EvidenceArchive(db, _mock_gcs(), transactional=transactional)

        ts = "2026-08-20T16:00:00+00:00"
        # Self-consistent entry that declares sequence 2 ...
        entry = {
            "entry_hash": _independent_hash("payload", "", ts, 2, "m"),
            "prev_hash": "",
            "timestamp": ts,
            "sequence": 2,
            "payload": "payload",
            "payload_sha256": hashlib.sha256(b"payload").hexdigest(),
            "model_id": "m",
        }
        # ... stored at document 1, with the tail naming sequence 1.
        state["entries"][1] = entry
        state["meta"] = {"tail_hash": entry["entry_hash"], "sequence": 1}

        with pytest.raises(RuntimeError, match="declares sequence 2"):
            archive.reconcile_tail()

    def test_reconcile_refuses_when_the_entry_document_is_absent(self):
        """A tail naming a sequence with no entry is not a partial write.

        Re-writing would mean inventing the entry's contents. An archive that
        fabricates a record to close a gap is worse than one that reports it.
        """
        db, state, transactional = _make_tx_pair()
        archive = EvidenceArchive(db, _mock_gcs(), transactional=transactional)
        state["meta"] = {"tail_hash": "a" * 64, "sequence": 1}

        with pytest.raises(RuntimeError, match="no entry document exists"):
            archive.reconcile_tail()


def test_precondition_failure_with_different_self_valid_content_raises():
    """A 412 does not license accepting whatever is already there.

    `if_generation_match=0` fails when an object exists. The handler then reads
    it to decide whether this is an idempotent retry. `verify_entry` alone only
    proves that object is internally consistent — a self-valid entry for a
    different payload passes it, so the retry would be reported as successful
    while GCS holds something else. The entries themselves must match.
    """
    db, _state, transactional = _make_tx_pair()
    ts = "2026-08-20T16:00:00+00:00"
    other = {
        "entry_hash": _independent_hash("different", "", ts, 1, "m"),
        "prev_hash": "",
        "timestamp": ts,
        "sequence": 1,
        "payload": "different",
        "payload_sha256": hashlib.sha256(b"different").hexdigest(),
        "model_id": "m",
    }
    # Pre-seed the path so the immutable write hits its precondition.
    store = {"evidence/1.json": json.dumps(other, sort_keys=True)}
    archive = EvidenceArchive(db, _mock_gcs(store), transactional=transactional)

    with pytest.raises(RuntimeError, match="exists with different content"):
        archive.append("payload", "gemini-3.5-flash-lite")
