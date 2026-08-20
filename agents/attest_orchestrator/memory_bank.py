"""Memory Bank — Vertex AI reasoning-engine working memory for the orchestrator.

Attest's ground truth is the Battery Registry: versioned, content-addressed,
immutable under its own version. Working memory is the complement — what the
orchestrator has observed about a firm across runs, searchable by meaning and
filterable by metadata, with consolidation handled by the service.

Both the generation and similarity-search models are pinned at engine creation.
A reasoning engine whose `contextSpec.memoryBankConfig` lacks
`similaritySearchConfig.embeddingModel` provisions fine but cannot answer a
similarity query — the failure shows up at query time, not deploy time. This
module treats that config as mandatory rather than optional.

All Vertex calls go through a thin HTTP client with an injectable ``request``
callable, so unit tests run without credentials and without the client library.
The module is import-safe: importing it does not require ``google.auth``.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LOCATION = "us-central1"
GENERATION_MODEL = "gemini-2.5-flash"
EMBEDDING_MODEL = "text-embedding-005"

# The models Memory Bank runs on are *platform* models, not the subject model
# the battery measures. They are pinned here, separate from ATTEST_MODEL, so a
# subject-model bump cannot silently change how memories are generated or
# embedded.


@dataclass(frozen=True)
class MemoryBankConfig:
    """Engine identity, resolved from env or defaults."""

    project: str
    location: str = DEFAULT_LOCATION
    engine_id: str = ""
    display_name: str = "attest-memory-bank"

    @property
    def parent(self) -> str:
        return f"projects/{self.project}/locations/{self.location}"

    @property
    def engine(self) -> str:
        if not self.engine_id:
            raise RuntimeError(
                "ATTEST_MEMORY_ENGINE_ID is not set. Provision an engine first: "
                "./deploy.sh memory"
            )
        return f"{self.parent}/reasoningEngines/{self.engine_id}"

    @property
    def generation_model(self) -> str:
        return (
            f"projects/{self.project}/locations/{self.location}"
            f"/publishers/google/models/{GENERATION_MODEL}"
        )

    @property
    def embedding_model(self) -> str:
        return (
            f"projects/{self.project}/locations/{self.location}"
            f"/publishers/google/models/{EMBEDDING_MODEL}"
        )

    @classmethod
    def from_env(cls) -> MemoryBankConfig:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set")
        return cls(
            project=project,
            location=os.environ.get("ATTEST_MODEL_LOCATION", DEFAULT_LOCATION),
            engine_id=os.environ.get("ATTEST_MEMORY_ENGINE_ID", "").strip(),
        )


class VertexClient:
    """Thin authorized HTTP client for the Vertex AI REST API.

    Args:
        request: injectable ``(method, url, body) -> (status, dict)`` callable.
            In production this is ``google.auth.transport.requests.AuthorizedSession``;
            in tests it is a stub.
    """

    def __init__(self, request: Callable[[str, str, dict | None], tuple[int, dict]]):
        self._request = request

    @classmethod
    def from_env(cls) -> VertexClient:
        """Build with Application Default Credentials. Lazy import."""
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        session = AuthorizedSession(credentials)

        def _request(method: str, url: str, body: dict | None):
            resp = session.request(
                method,
                url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=120,
            )
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            return resp.status_code, payload

        return cls(_request)

    def base(self, location: str) -> str:
        return f"https://{location}-aiplatform.googleapis.com/v1beta1"

    def call(
        self,
        method: str,
        url: str,
        body: dict | None = None,
    ) -> dict:
        status, payload = self._request(method, url, body)
        if status >= 400:
            raise VertexAPIError(status, payload)
        return payload


class VertexAPIError(RuntimeError):
    def __init__(self, status: int, payload: dict):
        self.status = status
        self.payload = payload
        message = payload.get("error", {}).get("message", "")
        super().__init__(f"Vertex API {status}: {message}")


def _poll_operation(
    client: VertexClient,
    op: dict,
    location: str,
    timeout_s: int = 300,
) -> dict:
    """Wait for a Vertex LRO to finish. Returns the completed operation.

    Several Memory Bank methods (``memories.create``, ``memories.purge``)
    return an operation that is **already** ``done`` in the POST response. That
    is not a fast path to optimise — polling those operation names by GET
    returns 500 rather than the completed operation, so an unconditional poll
    turns a successful call into an error. Check ``done`` before polling.

    Transient 5xx on the poll itself is retried: a failed status read does not
    mean the operation failed.
    """
    if op.get("done"):
        if "error" in op:
            raise VertexAPIError(op["error"].get("code", 500), op)
        return op

    op_name = op["name"]
    deadline = time.monotonic() + timeout_s
    url = f"https://{location}-aiplatform.googleapis.com/v1beta1/{op_name}"
    transient_errors = 0
    while time.monotonic() < deadline:
        try:
            polled = client.call("GET", url)
        except VertexAPIError as exc:
            if exc.status >= 500 and transient_errors < 5:
                transient_errors += 1
                logger.warning(
                    "LRO poll transient %d (attempt %d)", exc.status, transient_errors
                )
                time.sleep(3)
                continue
            raise
        if polled.get("done"):
            if "error" in polled:
                raise VertexAPIError(polled["error"].get("code", 500), polled)
            return polled
        time.sleep(3)
    raise TimeoutError(f"LRO {op_name} did not finish within {timeout_s}s")


class MemoryBank:
    """Working memory scoped to a single reasoning engine.

    Every method is idempotent where the service allows it: ``purge_memories``
    is a dry-run preview by default, and ``generate_memories`` consolidates
    against existing memories rather than duplicating them.
    """

    def __init__(self, client: VertexClient, config: MemoryBankConfig):
        self._client = client
        self._config = config

    @classmethod
    def from_env(cls) -> MemoryBank:
        return cls(VertexClient.from_env(), MemoryBankConfig.from_env())

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------

    def ensure_engine(self) -> str:
        """Return the engine resource name, creating it if necessary.

        Idempotent: if ``config.engine_id`` is set the engine is fetched and
        returned; if unset, an engine matching ``display_name`` is reused when
        found, otherwise created.
        """
        base = self._client.base(self._config.location)

        if self._config.engine_id:
            engine_name = self._config.engine
            self._client.call("GET", f"{base}/{engine_name}")
            logger.info("memory_bank.engine exists: %s", engine_name)
            return engine_name

        # Look up by display name before creating.
        existing = self._client.call(
            "GET", f"{base}/{self._config.parent}/reasoningEngines"
        ).get("reasoningEngines", [])
        for e in existing:
            if e.get("displayName") == self._config.display_name:
                logger.info("memory_bank.engine reused: %s", e["name"])
                return e["name"]

        logger.info("memory_bank.engine creating: %s", self._config.display_name)
        body = {
            "displayName": self._config.display_name,
            "description": "Attest surveillance working memory (Memory Bank).",
            "contextSpec": {
                "memoryBankConfig": {
                    "generationConfig": {"model": self._config.generation_model},
                    "similaritySearchConfig": {
                        "embeddingModel": self._config.embedding_model
                    },
                }
            },
        }
        op = self._client.call(
            "POST",
            f"{self._client.base(self._config.location)}/{self._config.parent}/reasoningEngines",
            body,
        )
        op = _poll_operation(self._client, op, self._config.location)
        created = op.get("response", {})
        engine_name = created.get("name", "")
        if not engine_name:
            raise RuntimeError(f"Engine creation returned no name: {op}")
        logger.info("memory_bank.engine created: %s", engine_name)
        return engine_name

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create_memory(
        self,
        fact: str,
        scope: dict[str, str],
        metadata: dict[str, dict[str, Any]] | None = None,
    ) -> dict:
        """Write one explicit memory. Does not consolidate — use for verbatim
        observations where the exact wording is the record.

        Returns the created Memory resource.
        """
        body: dict[str, Any] = {"fact": fact, "scope": scope}
        if metadata:
            body["metadata"] = metadata
        op = self._client.call(
            "POST",
            f"{self._client.base(self._config.location)}/{self._config.engine}/memories",
            body,
        )
        op = _poll_operation(self._client, op, self._config.location)
        return op.get("response", {})

    def generate_memories(
        self,
        facts: list[str],
        scope: dict[str, str],
        metadata: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict]:
        """Consolidate facts into the bank. The service deduplicates and merges
        against existing memories.

        Returns the generated-memory records (each with ``action``: CREATED,
        UPDATED, or DELETED).
        """
        if len(facts) > 5:
            raise ValueError("generate_memories accepts at most 5 facts per call")
        body: dict[str, Any] = {
            "scope": scope,
            "directMemoriesSource": {
                "directMemories": [{"fact": f} for f in facts]
            },
        }
        if metadata:
            body["metadata"] = metadata
        op = self._client.call(
            "POST",
            f"{self._client.base(self._config.location)}/{self._config.engine}/memories:generate",
            body,
        )
        op = _poll_operation(self._client, op, self._config.location)
        return op.get("response", {}).get("generatedMemories", [])

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def retrieve(
        self,
        scope: dict[str, str],
        query: str | None = None,
        top_k: int = 5,
        page_size: int = 20,
        filter_groups: list[dict] | None = None,
    ) -> list[dict]:
        """Retrieve memories by semantic similarity (if ``query``) or by
        recency (simple retrieval). ``filter_groups`` applies metadata filters
        in addition to scope.
        """
        body: dict[str, Any] = {"scope": scope}
        if query:
            body["similaritySearchParams"] = {"searchQuery": query, "topK": top_k}
        else:
            body["simpleRetrievalParams"] = {"pageSize": page_size}
        if filter_groups:
            body["filterGroups"] = filter_groups
        return self._client.call(
            "POST",
            f"{self._client.base(self._config.location)}/{self._config.engine}/memories:retrieve",
            body,
        ).get("retrievedMemories", [])

    def list_memories(self, scope: dict[str, str] | None = None) -> list[dict]:
        """List memories. If ``scope`` is given, filters client-side."""
        memories = self._client.call(
            "GET",
            f"{self._client.base(self._config.location)}/{self._config.engine}/memories",
        ).get("memories", [])
        if scope is None:
            return memories
        return [m for m in memories if m.get("scope") == scope]

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def purge_memories(
        self,
        filter_expr: str,
        force: bool = False,
    ) -> int:
        """Purge memories matching a filter expression.

        With ``force=False`` (the default) this is a dry run that returns the
        count without deleting. Pass ``force=True`` to actually remove them.
        """
        op = self._client.call(
            "POST",
            f"{self._client.base(self._config.location)}/{self._config.engine}/memories:purge",
            {"filter": filter_expr, "force": force},
        )
        op = _poll_operation(self._client, op, self._config.location)
        return int(op.get("response", {}).get("purgeCount", 0))

    def delete_memory(self, memory_name: str) -> None:
        """Delete a single memory by its full resource name."""
        self._client.call(
            "DELETE",
            f"{self._client.base(self._config.location)}/{memory_name}",
        )


# ----------------------------------------------------------------------
# Agent-facing tools
# ----------------------------------------------------------------------


def remember_firm_finding(
    crd: str,
    category: str,
    fact: str,
    roster_version: str = "",
) -> dict:
    """Store a firm-specific finding in working memory, consolidating with
    what is already known. Use this after scoring or summarising a run.

    Args:
        crd: The firm's CRD number, e.g. "900001".
        category: Battery category the finding belongs to (e.g. "A", "B", "C").
        fact: The verbatim observation to remember.
        roster_version: The roster version the finding was scored against.

    Returns:
        The generated-memory records, each with an action (CREATED / UPDATED).
    """
    bank = MemoryBank.from_env()
    metadata: dict[str, dict[str, Any]] = {
        "crd": {"stringValue": str(crd).strip()},
        "category": {"stringValue": category},
    }
    if roster_version:
        metadata["roster_version"] = {"stringValue": roster_version}
    return {
        "results": bank.generate_memories(
            facts=[fact],
            scope={"crd": str(crd).strip()},
            metadata=metadata,
        )
    }


def recall_firm_memory(
    crd: str,
    query: str = "",
    category: str = "",
    limit: int = 5,
) -> dict:
    """Retrieve what the orchestrator already knows about a firm.

    Args:
        crd: The firm's CRD number, e.g. "900001".
        query: Semantic search query. If empty, returns the most recent memories.
        category: Optional battery category to filter by.
        limit: Maximum memories to return.

    Returns:
        A list of retrieved memories, each with ``memory`` and optional
        ``distance``.
    """
    bank = MemoryBank.from_env()
    scope = {"crd": str(crd).strip()}
    filter_groups = None
    if category:
        filter_groups = [
            {
                "filters": [
                    {
                        "key": "category",
                        "op": "EQUAL",
                        "value": {"stringValue": category},
                    }
                ]
            }
        ]
    results = bank.retrieve(
        scope=scope,
        query=query or None,
        top_k=limit,
        page_size=limit,
        filter_groups=filter_groups,
    )
    return {"memories": results}


def purge_firm_memory(crd: str, dry_run: bool = True) -> dict:
    """Preview or execute deletion of all memories for a firm.

    Args:
        crd: The firm's CRD number, e.g. "900001".
        dry_run: When True (default), returns the count that would be deleted
            without deleting. Set False to actually purge.

    Returns:
        The purge count.
    """
    bank = MemoryBank.from_env()
    count = bank.purge_memories(
        filter_expr=f'scope.crd="{str(crd).strip()}"',
        force=not dry_run,
    )
    return {"purge_count": count, "dry_run": dry_run}
