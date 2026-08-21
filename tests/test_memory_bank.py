"""Tests for Memory Bank — Vertex AI reasoning-engine working memory.

All tests use an injectable request stub so no credentials are required.
"""

from __future__ import annotations

import pytest

from agents.attest_orchestrator import memory_bank
from agents.attest_orchestrator.memory_bank import (
    InvalidCRDError,
    MemoryBank,
    MemoryBankConfig,
    VertexAPIError,
    VertexClient,
    _validate_crd,
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
        # Isolate the location vars: deploy.sh exports ATTEST_MODEL_LOCATION as
        # `global`, and a developer with that sourced would otherwise see this
        # assertion fail for environmental reasons.
        monkeypatch.delenv("ATTEST_MEMORY_LOCATION", raising=False)
        monkeypatch.delenv("ATTEST_MODEL_LOCATION", raising=False)
        cfg = MemoryBankConfig.from_env()
        assert cfg.project == "my-project"
        assert cfg.location == "us-central1"
        assert cfg.engine_id == ""

    def test_from_env_ignores_model_location(self, monkeypatch):
        """The engine region must not follow the subject model's location.

        ATTEST_MODEL_LOCATION is `global` in the deployed config because the
        3.x Gemini models resolve only there. Reasoning engines are regional
        and do not exist in `global`, so reading that variable would point the
        engine at a location it cannot live in.
        """
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
        monkeypatch.setenv("ATTEST_MODEL_LOCATION", "global")
        monkeypatch.delenv("ATTEST_MEMORY_LOCATION", raising=False)
        monkeypatch.delenv("ATTEST_MEMORY_ENGINE_ID", raising=False)
        cfg = MemoryBankConfig.from_env()
        assert cfg.location == "us-central1"

    def test_from_env_honours_memory_location(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
        monkeypatch.setenv("ATTEST_MEMORY_LOCATION", "us-east4")
        monkeypatch.delenv("ATTEST_MEMORY_ENGINE_ID", raising=False)
        cfg = MemoryBankConfig.from_env()
        assert cfg.location == "us-east4"


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
            bank.generate_memories(facts=["a"] * 6, scope={"crd": "900001"})  # not-a-crd

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
        assert bank.purge_memories('scope.crd="900001"', force=False) == 3  # not-a-crd

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
        assert bank.purge_memories('scope.crd="900001"', force=True) == 3  # not-a-crd

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
        assert bank.purge_memories('scope.crd="900001"', force=True) == 7  # not-a-crd

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


class TestCRDValidation:
    """CRD validation is what keeps the interpolated purge filter safe."""

    # Every literal here must be a roster CRD or an obviously synthetic one.
    # Some real SEC registrants hold very short CRDs. Any such value would be
    # functionally identical as a validation fixture, and using one would put a
    # real registrant identifier back into published history — exactly what the
    # pre-publication content sweep exists to keep out. "0" carries the same
    # "short numeric string" test intent and cannot identify a registrant,
    # because CRD numbering starts at 1. See test_no_real_crd_in_tree.py,  # not-a-crd
    # which fails the build if a real CRD reaches the working tree.
    @pytest.mark.parametrize("value", ["900001", " 900001 ", "0"])
    def test_accepts_numeric(self, value):
        assert _validate_crd(value) == value.strip()

    @pytest.mark.parametrize(
        "value",
        [
            '900001" OR scope.crd!="',  # closes the literal, widens the filter
            '900001" OR true OR scope.crd="900002',
            "900001; DROP",
            "",
            "   ",
            "abc",
            "900001.0",
            "-900001",
        ],
    )
    def test_rejects_non_numeric(self, value):
        with pytest.raises(InvalidCRDError):
            _validate_crd(value)

    def test_purge_filter_cannot_be_widened(self, monkeypatch):
        """The injection this validation exists to stop.

        A crafted CRD closing the quoted literal early would turn
        `scope.crd="X"` into a filter matching every memory. Validation must
        reject it before any request is built.
        """
        called = []

        class Boom(MemoryBank):
            def purge_memories(self, filter_expr, force=False):
                called.append((filter_expr, force))
                return 99  # not-a-crd

        monkeypatch.setattr(
            memory_bank.MemoryBank,
            "from_env",
            classmethod(lambda cls: Boom(_client({}), CONFIG)),
        )
        with pytest.raises(InvalidCRDError):
            memory_bank.purge_firm_memory('900001" OR scope.crd!="', dry_run=False)
        assert called == [], "a request was built from an invalid CRD"

    def test_tools_return_error_dict_for_invalid_crd(self, monkeypatch):
        """The agent-facing tools report the rejection instead of raising.

        A raised exception inside a tool surfaces to the model as an opaque
        failure; an error dict tells it what was wrong.
        """
        monkeypatch.setattr(
            memory_bank.MemoryBank,
            "from_env",
            classmethod(lambda cls: (_ for _ in ()).throw(AssertionError("must not build"))),
        )
        bad = 'x" OR scope.crd!="'
        assert memory_bank.recall_firm_memory(bad)["error"] == "invalid_crd"
        assert memory_bank.remember_firm_finding(bad, "A", "fact")["error"] == "invalid_crd"


class TestRecallLimitClamping:
    """`limit` arrives from the model, so it is untrusted."""

    @staticmethod
    def _capture(monkeypatch):
        seen = {}

        class Recorder(MemoryBank):
            def retrieve(self, scope, query=None, top_k=5, page_size=20, filter_groups=None):
                seen["top_k"] = top_k
                seen["page_size"] = page_size
                return []

        monkeypatch.setattr(
            memory_bank.MemoryBank,
            "from_env",
            classmethod(lambda cls: Recorder(_client({}), CONFIG)),
        )
        return seen

    @pytest.mark.parametrize(
        ("given", "expected"),
        [(5, 5), (1, 1), (20, 20), (10_000, 20), (0, 1), (-3, 1)],
    )
    def test_limit_is_clamped(self, monkeypatch, given, expected):
        seen = self._capture(monkeypatch)
        memory_bank.recall_firm_memory("900001", limit=given)
        assert seen["top_k"] == expected
        assert seen["page_size"] == expected

    def test_non_integer_limit_falls_back(self, monkeypatch):
        seen = self._capture(monkeypatch)
        memory_bank.recall_firm_memory("900001", limit="not-a-number")
        assert seen["top_k"] == 5


class TestPagination:
    """Vertex list endpoints paginate; reading page 1 only is a wrong answer."""

    def test_engine_lookup_follows_pages(self):
        cfg = MemoryBankConfig(project="p", engine_id="")
        list_url = f"{BASE}/projects/p/locations/us-central1/reasoningEngines"
        bank = MemoryBank(
            _client(
                {
                    ("GET", list_url): (
                        200,
                        {
                            "reasoningEngines": [
                                {"name": "other", "displayName": "unrelated"}
                            ],
                            "nextPageToken": "tok2",
                        },
                    ),
                    ("GET", f"{list_url}?pageToken=tok2"): (
                        200,
                        {
                            "reasoningEngines": [
                                {"name": ENGINE, "displayName": "attest-memory-bank"}
                            ]
                        },
                    ),
                }
            ),
            cfg,
        )
        # Must find the page-2 match rather than creating a duplicate engine.
        assert bank.ensure_engine() == ENGINE

    def test_list_memories_follows_pages(self):
        url = f"{BASE}/{ENGINE}/memories"
        bank = MemoryBank(
            _client(
                {
                    ("GET", url): (
                        200,
                        {
                            "memories": [{"name": "m1", "scope": {"crd": "900001"}}],
                            "nextPageToken": "tok2",
                        },
                    ),
                    ("GET", f"{url}?pageToken=tok2"): (
                        200,
                        {"memories": [{"name": "m2", "scope": {"crd": "900001"}}]},
                    ),
                }
            ),
            CONFIG,
        )
        assert [m["name"] for m in bank.list_memories()] == ["m1", "m2"]

    def test_scope_filter_applies_after_all_pages(self):
        url = f"{BASE}/{ENGINE}/memories"
        bank = MemoryBank(
            _client(
                {
                    ("GET", url): (
                        200,
                        {
                            "memories": [{"name": "m1", "scope": {"crd": "900002"}}],
                            "nextPageToken": "tok2",
                        },
                    ),
                    ("GET", f"{url}?pageToken=tok2"): (
                        200,
                        {"memories": [{"name": "m2", "scope": {"crd": "900001"}}]},
                    ),
                }
            ),
            CONFIG,
        )
        # The only match is on page 2: an empty result here would be a lie.
        found = bank.list_memories(scope={"crd": "900001"})
        assert [m["name"] for m in found] == ["m2"]


class TestPurgeIsNotAnAgentTool:
    def test_purge_absent_from_module_tool_set(self):
        """Purge must stay operator-only.

        agent.py holds the authoritative tool list, but it needs google.adk to
        import. This asserts the module-level intent that pairs with it: the
        docstring marks purge as operator-only, and the agent instruction tells
        the model it has no delete tool.
        """
        assert "not registered on the agent" in memory_bank.purge_firm_memory.__doc__
        assert "Operator use only" in memory_bank.purge_firm_memory.__doc__
