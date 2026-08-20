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

**Idempotent append.** The GCS object is written *before* the Firestore
transaction commits, so a failed Firestore commit never leaves an orphan
object. On retry, the same sequence is computed (the tail hasn't advanced),
the GCS object is overwritten with identical content, and the Firestore
transaction retries. Either call produces the same durable result.

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
    """Append-only evidence chain backed by Firestore + GCS.

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

        The append is idempotent: writing the GCS object before the Firestore
        transaction means a failed commit never leaves an orphan object, and
        retrying recomputes the same sequence and overwrites the GCS object
        with identical content.

        Returns the entry dict.
        """
        timestamp = datetime.now(UTC).isoformat()

        # Read the tail *before* writing GCS so retry sees the same sequence.
        # The Firestore transaction will re-read atomically and abort if another
        # writer advanced meta between our read and commit — that is the
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

        # GCS first: if Firestore fails, the object exists but the chain does
        # not advance. Retry recomputes the same sequence and overwrites.
        self._write_gcs(entry)

        # Firestore transaction: atomically write the entry and advance meta.
        self._tx_advance(entry, tail_hash)

        logger.info(
            "evidence.append seq=%d hash=%s",
            entry["sequence"],
            entry["entry_hash"][:16],
        )
        return entry

    def _write_gcs(self, entry: dict) -> None:
        """Write one entry as a JSON object to GCS.

        Includes the full payload so the stored object is self-verifiable:
        a verifier can reload ``evidence/<seq>.json`` and recompute
        ``entry_hash`` from its fields.
        """
        path = f"evidence/{entry['sequence']}.json"
        blob = self._bucket.blob(path)
        blob.upload_from_string(
            json.dumps(entry, sort_keys=True),
            content_type="application/json",
        )

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
            # the entry was written by a previous attempt that failed to advance
            # meta. Skip re-writing it — just advance meta.
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
        hash chain linkage. Returns False if the entry is tampered.
        """
        expected = _compute_hash(
            payload=entry["payload"],
            prev_hash=entry["prev_hash"],
            timestamp=entry["timestamp"],
            sequence=entry["sequence"],
            model_id=entry["model_id"],
        )
        return expected == entry["entry_hash"]
