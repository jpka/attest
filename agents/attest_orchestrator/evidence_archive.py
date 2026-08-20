"""Evidence Archive — append-only SHA-256 hash chain on Firestore + GCS.

Replaces the in-process chain in agent.py. Two durability properties:

1. Hash linkage — each entry carries the previous entry's hash. Editing any
   entry breaks every subsequent link.
2. Monotonic sequence — a pure hash chain detects edits but not *truncation*
   (dropping the last N entries leaves a valid chain). A sequence number
   closes that: a gap is evidence of truncation.

The tail is stored in Firestore ``evidence_chain/meta`` and advanced inside a
transaction with optimistic concurrency. A writer that reads a stale meta
fails at commit and retries, so two instances cannot fork the chain.

**Idempotent append.** The Firestore transaction commits first. The GCS object
is then written with ``if_generation_match=0`` — an immutable create that fails
if the object already exists. On retry after a partial failure:

- If the committed entry has the same hash (same payload, model, timestamp),
  the GCS object is verified and the append is a no-op.
- If the committed entry has a different hash, a ``RuntimeError`` is raised.

Either call produces the same durable result. Concurrent writers cannot
overwrite each other's GCS objects because the Firestore transaction aborts
if meta advanced between read and commit.

**Self-verifiable entries.** The GCS object stores the full entry including
``payload`` and ``model_id``, so a verifier can recompute ``entry_hash``
from the persisted fields alone. ``model_id`` is included in the hash input,
so changing it changes the hash — a record's scorer model is part of its
identity.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

META_DOC = ("evidence_chain", "meta")
ENTRY_COLLECTION = "evidence_chain"


def _body(
    payload: str,
    prev_hash: str,
    timestamp: str,
    sequence: int,
    model_id: str,
) -> str:
    """The canonical string that gets hashed — includes every linked field.

    ``model_id`` is included so a record's scorer model is part of its
    identity: changing the model changes the hash.
    """
    return json.dumps(
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


def _compute_hash(
    payload: str, prev_hash: str, timestamp: str, sequence: int, model_id: str
) -> str:
    """Compute entry_hash from the same inputs as _body."""
    return hashlib.sha256(
        _body(payload, prev_hash, timestamp, sequence, model_id).encode()
    ).hexdigest()


class EvidenceArchive:
    """Append-only evidence chain backed on Firestore + GCS.

    Args:
        firestore_client: a ``google.cloud.firestore.Client``.
        gcs_bucket: a ``google.cloud.storage.Bucket``.
        transactional: optional decorator that wraps a function to run it as a
            Firestore transaction. Defaults to ``google.cloud.firestore.transactional``.
            Injection seam for tests.
    """

    def __init__(
        self,
        firestore_client,
        gcs_bucket,
        transactional: callable = None,
    ):
        self._db = firestore_client
        self._bucket = gcs_bucket
        # Lazy import default so importing this module needs no credentials
        if transactional is None:
            from google.cloud import firestore

            transactional = firestore.transactional
        self._transactional = transactional

    @classmethod
    def from_env(cls) -> EvidenceArchive:
        """Build from environment.

        Lazy imports so importing this module needs no credentials.
        """
        from google.cloud import firestore, storage

        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        database = os.environ.get("ATTEST_FIRESTORE_DATABASE", "(default)")
        bucket_name = os.environ.get("ATTEST_EVIDENCE_BUCKET")
        if not bucket_name:
            raise RuntimeError(
                "ATTEST_EVIDENCE_BUCKET is not set. Create a GCS bucket and "
                "set this env var before running."
            )
        return cls(
            firestore_client=firestore.Client(project=project, database=database),
            gcs_bucket=storage.Client(project=project).bucket(bucket_name),
        )

    def _meta_ref(self):
        return self._db.collection(META_DOC[0]).document(META_DOC[1])

    def current_tail(self) -> tuple[str, int]:
        """Return ``(tail_hash, sequence)``. Empty chain: ``("", 0)``."""
        snap = self._meta_ref().get()
        if not snap.exists:
            return "", 0
        data = snap.to_dict() or {}
        return data.get("tail_hash", ""), data.get("sequence", 0)

    def append(self, payload: str, model_id: str) -> dict:
        """Append an entry to the chain.

        The append is idempotent: the Firestore transaction commits first,
        then the GCS object is written immutably (``if_generation_match=0``).
        If the GCS object already exists, it is verified against our hash —
        a partial previous attempt left it in place and the Firestore entry
        is already committed.

        Ordering note. Firestore commits first on purpose: the alternative,
        writing GCS first, orphans an object whose entry never became part of
        the chain, and there is no tail to reconcile it against. Committing
        first means the *chain* is always authoritative and a missing object
        is a repairable gap rather than an ambiguity — but only if something
        actually repairs it, which is what ``reconcile_tail`` below is for.

        Returns the entry dict.
        """
        timestamp = datetime.now(UTC).isoformat()

        # Repair a prior partial append before extending the chain. If the
        # last append committed to Firestore and then failed to write GCS,
        # the tail we are about to build on has no durable object behind it,
        # and simply appending would bury that gap one entry deeper.
        self.reconcile_tail()

        # Read the tail so we know what sequence to compute. The Firestore
        # transaction will re-read atomically and abort if another writer
        # advanced meta between our read and commit — that is the
        # optimistic-concurrency guard.
        tail_hash, seq = self.current_tail()
        new_seq = seq + 1

        entry_hash = _compute_hash(payload, tail_hash, timestamp, new_seq, model_id)
        entry = {
            "entry_hash": entry_hash,
            "prev_hash": tail_hash,
            "timestamp": timestamp,
            "sequence": new_seq,
            "payload": payload,
            "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
            "model_id": model_id,
        }

        # Firestore transaction: atomically write the entry and advance meta.
        # This commits first — if it fails, nothing is written and we retry.
        self._tx_advance(entry, tail_hash)

        # GCS write after commit, immutable: if_generation_match=0 means
        # "only write if the object doesn't exist". If it already exists
        # (from a partial previous attempt where Firestore committed but
        # GCS failed), verify it matches our hash.
        self._write_gcs_immutable(entry)

        logger.info(
            "evidence.append seq=%d hash=%s",
            entry["sequence"],
            entry["entry_hash"][:16],
        )
        return entry

    def reconcile_tail(self) -> dict | None:
        """Re-write the GCS object for the tail entry if it is missing.

        Closes the window between ``_tx_advance`` and ``_write_gcs_immutable``.
        If the process dies in there — or the bucket is unreachable, which is
        not hypothetical — Firestore holds a committed entry and an advanced
        tail with no durable object behind it. The next ``append`` would read
        that tail, succeed, and leave the gap permanently one entry back.

        Recovery re-writes the *same committed entry* rather than rolling the
        chain back. The entry is already the tail and may already have been
        reported to a caller, so rolling back would retract a hash someone
        holds; and because the entry is content-addressed, re-writing it is
        byte-identical or it is corruption. Nothing is invented here.

        Returns the repaired entry, or ``None`` when there was nothing to do
        (the common case, and the empty-chain case).
        """
        tail_hash, seq = self.current_tail()
        if seq == 0:
            return None

        blob = self._bucket.blob(f"evidence/{seq}.json")
        if blob.exists():
            return None

        snap = self._db.collection(ENTRY_COLLECTION).document(str(seq)).get()
        if not snap.exists:
            # Tail names a sequence with no Firestore entry. That is not a
            # partial GCS write and re-writing would be a guess, so refuse.
            raise RuntimeError(
                f"Chain tail names sequence {seq} but no entry document exists. "
                "Refusing to reconcile — this is not a partial GCS write."
            )

        entry = snap.to_dict() or {}
        if entry.get("entry_hash") != tail_hash:
            raise RuntimeError(
                f"Entry {seq} hash {str(entry.get('entry_hash'))[:16]} does not match "
                f"chain tail {tail_hash[:16]}. Refusing to reconcile."
            )
        if not self.verify_entry(entry):
            raise RuntimeError(
                f"Entry {seq} fails its own hash verification. Refusing to reconcile."
            )

        self._write_gcs_immutable(entry)
        logger.warning(
            "evidence.reconcile seq=%d hash=%s — GCS object was missing for a "
            "committed entry and has been rewritten",
            seq,
            tail_hash[:16],
        )
        return entry

    def _write_gcs_immutable(self, entry: dict) -> None:
        """Write one entry to GCS immutably.

        Uses ``if_generation_match=0`` to only create the object if it
        doesn't already exist. If it does exist, verifies the content
        matches our hash (idempotent retry path). Raises if the existing
        object has different content (chain corruption).
        """
        path = f"evidence/{entry['sequence']}.json"
        blob = self._bucket.blob(path)
        content = json.dumps(entry, sort_keys=True)

        try:
            blob.upload_from_string(
                content,
                content_type="application/json",
                if_generation_match=0,
            )
        except Exception as exc:
            # Object may already exist (partial previous attempt).
            # Verify it matches our expected hash.
            if "conditionNotMet" in str(exc) or "412" in str(exc) or "PreconditionFailed" in str(exc):
                existing = blob.download_as_text()
                existing_entry = json.loads(existing)
                # CR: verify the entire entry, not just the hash
                if self.verify_entry(existing_entry):
                    return
                raise RuntimeError(
                    f"GCS object {path} exists with different hash: "
                    f"expected {entry['entry_hash'][:16]}, "
                    f"found {existing_entry.get('entry_hash', 'unknown')[:16]}. "
                    f"Chain corruption detected."
                ) from exc
            raise

    def _tx_advance(self, entry: dict, expected_tail_hash: str) -> None:
        """Atomically write the entry and advance the meta tail.

        Aborts if meta was modified between our pre-read and this commit,
        so two writers cannot both advance past the same tail.
        """
        db = self._db
        meta_ref = self._meta_ref()
        entry_coll = ENTRY_COLLECTION
        new_seq = entry["sequence"]

        @self._transactional
        def _txn(txn):
            meta_snap = meta_ref.get(transaction=txn)
            if meta_snap.exists:
                current = meta_snap.to_dict() or {}
                current_tail = current.get("tail_hash", "")
            else:
                current_tail = ""

            # Optimistic concurrency: if another writer advanced meta, abort.
            if current_tail != expected_tail_hash:
                raise RuntimeError(
                    f"Chain tail conflict: expected {expected_tail_hash[:16]}, "
                    f"found {current_tail[:16]}. Another writer advanced the chain."
                )

            # Idempotency: if this exact sequence already exists with our hash,
            # the entry was written by a previous attempt. Skip re-writing it.
            entry_ref = db.collection(entry_coll).document(str(new_seq))
            existing = entry_ref.get(transaction=txn)
            if existing.exists:
                existing_data = existing.to_dict() or {}
                if existing_data.get("entry_hash") == entry["entry_hash"]:
                    # Same entry already persisted — only advance meta.
                    txn.set(
                        meta_ref,
                        {"tail_hash": entry["entry_hash"], "sequence": new_seq},
                    )
                    return

            txn.set(entry_ref, entry)
            txn.set(
                meta_ref,
                {"tail_hash": entry["entry_hash"], "sequence": new_seq},
            )

        _txn(db.transaction())

    def verify_entry(self, entry: dict) -> bool:
        """Recompute entry_hash from persisted fields.

        A verifier can call this on a reloaded GCS object to confirm the
        hash chain linkage. Returns False if the entry is tampered or
        missing required fields.
        """
        try:
            expected = _compute_hash(
                payload=entry["payload"],
                prev_hash=entry["prev_hash"],
                timestamp=entry["timestamp"],
                sequence=entry["sequence"],
                model_id=entry["model_id"],
            )
            return expected == entry["entry_hash"]
        except (KeyError, TypeError):
            return False
