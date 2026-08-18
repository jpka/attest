"""Battery Registry — content-addressed ground truth in Firestore.

The roster the agent checks claims against is not a file it happens to ship
with; it is a *versioned* artefact, and every surveillance run has to be able
to say which version it ran against. That is the whole reason this is not a
JSON read.

Versioning reuses the scheme already in `premise_test.py`:

    sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:12]

Deliberately the same twelve-character content hash, not a second scheme that
happens to look similar. A roster version and a `BATTERY_VERSION` are directly
comparable strings, and a run records both.

Layout:

    rosters/{version}                 metadata: firm count, published_at
    rosters/{version}/firms/{crd}     one firm, native fields + content_sha256
    registry/current                  pointer: {"roster_version": ...}

Content addressing means republishing identical data is a no-op that lands on
the same document paths, and any edit to the source produces a different
version rather than mutating one in place.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache

DATABASE = os.environ.get("ATTEST_FIRESTORE_DATABASE", "(default)")

# Pinning this makes a run reproducible: it fixes the ground truth the run was
# scored against, even after a newer roster is published. Unset means "whatever
# registry/current points at", which is right for a scheduled run and wrong for
# re-scoring an old one.
PINNED_VERSION = os.environ.get("ATTEST_ROSTER_VERSION", "").strip()

POINTER_PATH = ("registry", "current")


def content_version(obj) -> str:
    """The project's one content-hash scheme. Same as `BATTERY_VERSION`."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True).encode()
    ).hexdigest()[:12]


def _client():
    # Imported lazily so that merely importing the agent module does not require
    # credentials — `local_test.py` checks tool wiring without touching GCP.
    from google.cloud import firestore

    return firestore.Client(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"), database=DATABASE
    )


def current_version(db=None) -> str:
    """The roster version this process should read.

    Raises rather than guessing. A surveillance agent that silently falls back
    to some other roster is worse than one that stops: the run would look
    normal and be scored against ground truth nobody chose.
    """
    if PINNED_VERSION:
        return PINNED_VERSION
    db = db or _client()
    snap = db.collection(POINTER_PATH[0]).document(POINTER_PATH[1]).get()
    if not snap.exists:
        raise RuntimeError(
            "No roster published: registry/current is missing. "
            "Run `python publish_registry.py` first, or pin "
            "ATTEST_ROSTER_VERSION."
        )
    version = (snap.to_dict() or {}).get("roster_version")
    if not version:
        raise RuntimeError("registry/current exists but has no roster_version")
    return version


@lru_cache(maxsize=1)
def load_firms() -> dict[str, dict]:
    """Every firm in the current roster, keyed by CRD.

    Keyed by CRD, never by name — the SEC roster contains distinct firms that
    share a primary business name, and name-keying silently merges them.

    Cached for the life of the process. A roster is immutable under its own
    version, so the only thing that can change underneath this is the pointer,
    and picking that up mid-run is exactly what we do not want.
    """
    db = _client()
    version = current_version(db)
    docs = (
        db.collection("rosters")
        .document(version)
        .collection("firms")
        .stream()
    )
    firms = {d.id: d.to_dict() for d in docs}
    if not firms:
        raise RuntimeError(
            f"Roster {version} has no firms. Publish it before running."
        )
    return firms


def roster_version() -> str:
    """The version `load_firms()` actually read, for recording on a run."""
    return current_version()
