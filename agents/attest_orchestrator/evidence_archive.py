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

The entry payload is written to GCS after the transaction commits, so the
chain never advances without a durable object backing it.
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


def _body(payload: str, prev_hash: str, timestamp: str, sequence: int) -> str:
    """The canonical string that gets hashed — includes every linked field."""
    return json.dumps(
        {
            "payload": payload,
            "prev_hash": prev_hash,
            "timestamp": timestamp,
            "sequence": sequence,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


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

        The Firestore transaction reads the tail, computes the next entry,
        writes it, and advances the meta — all atomically. If another writer
        modified meta between read and commit, Firestore aborts and the
        transactional wrapper retries with a fresh tail.

        GCS write happens after the transaction commits, so the chain never
        advances without a durable object backing it.

        Returns the entry dict.
        """
        timestamp = datetime.now(UTC).isoformat()
        entry = self._tx_advance(payload, model_id, timestamp)
        self._write_gcs(entry)
        logger.info(
            "evidence.append seq=%d hash=%s",
            entry["sequence"],
            entry["entry_hash"][:16],
        )
        return entry

    def _write_gcs(self, entry: dict) -> None:
        """Write one entry as a JSON object to GCS."""
        path = f"evidence/{entry['sequence']}.json"
        blob = self._bucket.blob(path)
        blob.upload_from_string(
            json.dumps(entry, sort_keys=True),
            content_type="application/json",
        )

    def _tx_advance(self, payload: str, model_id: str, timestamp: str) -> dict:
        """Run the Firestore transaction that advances the chain."""
        db = self._db
        meta_ref = self._meta_ref()
        entry_coll = ENTRY_COLLECTION

        @self._transactional
        def _txn(txn):
            meta_snap = meta_ref.get(transaction=txn)
            if meta_snap.exists:
                data = meta_snap.to_dict() or {}
                tail_hash = data.get("tail_hash", "")
                seq = data.get("sequence", 0)
            else:
                tail_hash, seq = "", 0

            new_seq = seq + 1
            body = _body(payload, tail_hash, timestamp, new_seq)
            entry_hash = hashlib.sha256(body.encode()).hexdigest()
            entry = {
                "entry_hash": entry_hash,
                "prev_hash": tail_hash,
                "timestamp": timestamp,
                "sequence": new_seq,
                "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "model_id": model_id,
            }

            entry_ref = db.collection(entry_coll).document(str(new_seq))
            txn.set(entry_ref, entry)
            txn.set(
                meta_ref,
                {"tail_hash": entry_hash, "sequence": new_seq},
            )

            return entry

        return _txn(db.transaction())
