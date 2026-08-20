"""Tests for Memory Bank — Vertex AI reasoning-engine working memory.

All tests use an injectable request stub so no credentials are required.
"""

from __future__ import annotations

import pytest

from agents.attest_orchestrator.memory_bank import (
    MemoryBank,
    MemoryBankConfig,
    VertexAPIError,
    VertexClient,
)


def _stub_request(responses: dict[tuple[str, str], tuple[int, dict]]):
    """Build a request callable that returns canned responses by (method, url)."""

    def request(method: str, url: str, body: dict | None):
        key = (method, url)
        if key not in responses:
            return 404, {"error": {"code": 404, "message": f"no stub for {method} {url}"}}
        return responses[key]

    return request


def _client(responses) -> VertexClient:
    return VertexClient(_stub_request(responses))


CONFIG = MemoryBankConfig(project="p", location="us-central1", engine_id="123")
ENGINE = "projects/p/locations/us-central1/reasoningEngines/123"
BASE = "https://us-central1-aiplatform.googleapis.com/v1beta1"


class TestMemoryBankConfig:
    def test_engine_raises_without_id(self):
        cfg = MemoryBankConfig(project="p")
        with pytest.raises(RuntimeError, match="ATTEST_MEMORY_ENGINE_ID"):
            _ = cfg.engine

    def test_from_env_defaults(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
        monkeypatch.delenv("ATTEST_MEMORY_ENGINE_ID", raising=False)
        cfg = MemoryBankConfig.from_env()
        assert cfg.project == "my-project"
        assert cfg.location == "us-central1"
        assert cfg.engine_id == ""


class TestEnsureEngine:
    def test_existing_engine_returns_name(self):
        bank = MemoryBank(
            _client({("GET", f"{BASE}/{ENGINE}"): (200, {"name": ENGINE})}),
            CONFIG,
        )
        assert bank.ensure_engine() == ENGINE

    def test_reuses_engine_with_matching_display_name(self):
        cfg = MemoryBankConfig(project="p", engine_id="")
        bank = MemoryBank(
            _client(
                {
                    (
                        "GET",
                        f"{BASE}/projects/p/locations/us-central1/reasoningEngines",
                    ): (200, {"reasoningEngines": [{"name": ENGINE, "displayName": "attest-memory-bank"}]}),
                }
            ),
            cfg,
        )
        assert bank.ensure_engine() == ENGINE

    def test_creates_engine_when_none_exist(self):
        cfg = MemoryBankConfig(project="p", engine_id="")
        op_name = "projects/p/locations/us-central1/operations/op1"
        created = ENGINE
        bank = MemoryBank(
            _client(
                {
                    (
                        "GET",
                        f"{BASE}/projects/p/locations/us-central1/reasoningEngines",
                    ): (200, {}),
                    ("POST", f"{BASE}/projects/p/locations/us-central1/reasoningEngines"): (
                        200,
                        {"name": op_name},
                    ),
                    (
                        "GET",
                        f"https://us-central1-aiplatform.googleapis.com/v1beta1/{op_name}",
                    ): (
                        200,
                        {"done": True, "response": {"name": created}},
                    ),
                }
            ),
            cfg,
        )
        assert bank.ensure_engine() == created


class TestCreateMemory:
    def test_returns_memory(self):
        op_name = "projects/p/locations/us-central1/operations/op1"
        mem_name = f"{ENGINE}/memories/999"
        bank = MemoryBank(
            _client(
                {
                    ("POST", f"{BASE}/{ENGINE}/memories"): (200, {"name": op_name}),
                    ("GET", f"https://us-central1-aiplatform.googleapis.com/v1beta1/{op_name}"): (
                        200,
                        {"done": True, "response": {"name": mem_name}},
                    ),
                }
            ),
            CONFIG,
        )
        result = bank.create_memory(
            fact="CRD 900001 has AUM 412M",
            scope={"crd": "900001"},
            metadata={"category": {"stringValue": "A"}},
        )
        assert result["name"] == mem_name


class TestGenerateMemories:
    def test_rejects_more_than_five_facts(self):
        bank = MemoryBank(_client({}), CONFIG)
        with pytest.raises(ValueError, match="at most 5"):
            bank.generate_memories(facts=["a"] * 6, scope={"crd": "900001"})

    def test_returns_generated_memories(self):
        op_name = "projects/p/locations/us-central1/operations/op1"
        bank = MemoryBank(
            _client(
                {
                    ("POST", f"{BASE}/{ENGINE}/memories:generate"): (200, {"name": op_name}),
                    ("GET", f"https://us-central1-aiplatform.googleapis.com/v1beta1/{op_name}"): (
                        200,
                        {
                            "done": True,
                            "response": {
                                "generatedMemories": [
                                    {"memory": {"name": "m1"}, "action": "CREATED"}
                                ]
                            },
                        },
                    ),
                }
            ),
            CONFIG,
        )
        result = bank.generate_memories(facts=["fact one"], scope={"crd": "900001"})
        assert len(result) == 1
        assert result[0]["action"] == "CREATED"


class TestRetrieve:
    def test_similarity_search(self):
        bank = MemoryBank(
            _client(
                {
                    ("POST", f"{BASE}/{ENGINE}/memories:retrieve"): (
                        200,
                        {"retrievedMemories": [{"memory": {"name": "m1"}, "distance": 0.1}]},
                    )
                }
            ),
            CONFIG,
        )
        result = bank.retrieve(scope={"crd": "900001"}, query="AUM")
        assert len(result) == 1
        assert result[0]["distance"] == 0.1

    def test_simple_retrieval_with_filter(self):
        bank = MemoryBank(
            _client(
                {
                    ("POST", f"{BASE}/{ENGINE}/memories:retrieve"): (
                        200,
                        {"retrievedMemories": [{"memory": {"name": "m1"}}]},
                    )
                }
            ),
            CONFIG,
        )
        result = bank.retrieve(
            scope={"crd": "900001"},
            filter_groups=[
                {"filters": [{"key": "category", "op": "EQUAL", "value": {"stringValue": "A"}}]}
            ],
        )
        assert len(result) == 1


class TestPurge:
    def test_dry_run_returns_count(self):
        op_name = "projects/p/locations/us-central1/operations/op1"
        bank = MemoryBank(
            _client(
                {
                    ("POST", f"{BASE}/{ENGINE}/memories:purge"): (200, {"name": op_name}),
                    ("GET", f"https://us-central1-aiplatform.googleapis.com/v1beta1/{op_name}"): (
                        200,
                        {"done": True, "response": {"purgeCount": 3}},
                    ),
                }
            ),
            CONFIG,
        )
        assert bank.purge_memories('scope.crd="900001"', force=False) == 3

    def test_force_deletes(self):
        op_name = "projects/p/locations/us-central1/operations/op1"
        bank = MemoryBank(
            _client(
                {
                    ("POST", f"{BASE}/{ENGINE}/memories:purge"): (200, {"name": op_name}),
                    ("GET", f"https://us-central1-aiplatform.googleapis.com/v1beta1/{op_name}"): (
                        200,
                        {"done": True, "response": {"purgeCount": 3}},
                    ),
                }
            ),
            CONFIG,
        )
        assert bank.purge_memories('scope.crd="900001"', force=True) == 3

    def test_inline_done_operation_is_not_polled(self):
        """Purge returns an already-done operation in the POST response.

        Polling such an operation by GET returns 500 from the live service, so
        the client must read the inline result. Only the POST is stubbed here:
        any GET attempt falls through to the stub's 404 and fails the test.
        """
        op_name = "projects/p/locations/us-central1/operations/op1"
        bank = MemoryBank(
            _client(
                {
                    ("POST", f"{BASE}/{ENGINE}/memories:purge"): (
                        200,
                        {"name": op_name, "done": True, "response": {"purgeCount": 7}},
                    ),
                }
            ),
            CONFIG,
        )
        assert bank.purge_memories('scope.crd="900001"', force=True) == 7

    def test_inline_done_error_raises(self):
        """An inline-done operation carrying an error must not be treated as success."""
        bank = MemoryBank(
            _client(
                {
                    ("POST", f"{BASE}/{ENGINE}/memories:purge"): (
                        200,
                        {
                            "name": "op1",
                            "done": True,
                            "error": {"code": 400, "message": "bad filter"},
                        },
                    ),
                }
            ),
            CONFIG,
        )
        with pytest.raises(VertexAPIError):
            bank.purge_memories("bogus", force=True)


class TestVertexAPIError:
    def test_raises_on_error_status(self):
        client = VertexClient(_stub_request({}))
        with pytest.raises(VertexAPIError, match="Vertex API 404"):
            client.call("GET", "https://example.com/missing")
