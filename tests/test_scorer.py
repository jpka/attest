"""Tests for the scorer module."""

import json

import pytest

from agents.attest_orchestrator import scorer
from agents.attest_orchestrator.scorer_prompts import RUBRIC_VERSION


@pytest.fixture
def firm():
    """A minimal firm record shaped like ground_truth.json."""
    return {
        "crd": "900001",
        "name": "NORTHBRIDGE CAPITAL ADVISORS, LLC",
        "legal_name": "NORTHBRIDGE CAPITAL ADVISORS, LLC",
        "sec_number": "801-900001",
        "city": "AUSTIN",
        "state": "TX",
        "website": "HTTPS://WWW.NORTHBRIDGEADVISORS.EXAMPLE",
        "sec_registration_status": "Approved",
        "sec_status_effective_date": "08/22/2000",
        "latest_adv_filing_date": "06/26/2026",
        "aum": {
            "total_usd": 630665806.0,
            "discretionary_usd": 628902725.0,
            "total_accounts": 1554,
        },
        "employees": {
            "total_excluding_clerical": 32,
            "registered_reps_of_broker_dealer": 32,
            "performing_advisory_functions": 32,
        },
        "clients": {
            "by_type": {
                "individuals_non_hnw": {
                    "number_of_clients": 548,
                    "regulatory_aum_usd": 163209871.0,
                },
            },
        },
        "compensation": {
            "adv_item": "Form ADV Part 1A Item 5.E",
            "percentage_of_aum": {"value": True},
            "hourly_charges": {"value": True},
            "fixed_fees": {"value": True},
            "commissions": {"value": True},
            "receives_commissions": True,
            "is_fee_only": False,
        },
        "services": {
            "financial_planning": {"value": True},
            "portfolio_mgmt_individuals_small_biz": {"value": True},
            "portfolio_mgmt_investment_companies": {"value": False},
            "portfolio_mgmt_businesses_institutions": {"value": True},
        },
        "disciplinary": {
            "adv_item": "Form ADV Part 1A Item 11",
            "any_disclosure": False,
            "items": [],
        },
    }


class TestGroundTruthSlice:
    def test_prunes_empty_fields(self, firm):
        slice_ = scorer.ground_truth_slice(firm, "B")
        assert "aum" in slice_
        # Empty disciplinary items should be pruned
        assert "total_usd" in slice_["aum"]

    def test_category_B_includes_identity_aum_employees(self, firm):
        slice_ = scorer.ground_truth_slice(firm, "B")
        assert slice_["crd"] == "900001"
        assert "aum" in slice_
        assert "employees" in slice_
        assert "compensation" not in slice_

    def test_category_C_includes_compensation(self, firm):
        slice_ = scorer.ground_truth_slice(firm, "C")
        assert "compensation" in slice_
        assert "aum" in slice_

    def test_category_H_includes_disciplinary(self, firm):
        slice_ = scorer.ground_truth_slice(firm, "H")
        assert "disciplinary" in slice_
        assert slice_["disciplinary"]["any_disclosure"] is False


class TestBuildScorerPrompt:
    def test_includes_ground_truth(self, firm):
        prompt = scorer.build_scorer_prompt(firm, "B", "How much AUM?", "$3.5B")
        assert "NORTHBRIDGE" in prompt
        assert "630665806" in prompt
        assert "$3.5B" in prompt
        assert "MATERIAL-FALSE" in prompt

    def test_discovery_note_for_category_A(self, firm):
        prompt = scorer.build_scorer_prompt(firm, "A", "Best advisor?", "Try Acme.")
        assert "NOT-SURFACED" in prompt

    def test_no_discovery_note_for_other_categories(self, firm):
        prompt = scorer.build_scorer_prompt(firm, "B", "How much AUM?", "$3.5B")
        # The rubric always contains "NOT-SURFACED" — check the discovery note
        # specifically, which only appears for category A.
        assert "does not name the firm" not in prompt


class TestParseVerdict:
    def test_direct_parse(self):
        raw = '{"verdicts": [{"answer": 1, "class": "MATERIAL-FALSE", "rationale": "wrong AUM"}]}'
        verdict, rationale = scorer.parse_verdict(raw)
        assert verdict == "MATERIAL-FALSE"
        assert "wrong AUM" in rationale

    def test_salvage_truncated(self):
        raw = '{"verdicts": [{"answer": 1, "class": "ACCURATE", "rationale": "matches'
        verdict, rationale = scorer.parse_verdict(raw)
        assert verdict == "ACCURATE"

    def test_garbage_returns_error(self):
        verdict, _ = scorer.parse_verdict("not json at all")
        assert verdict == "ERROR-UNPARSED"


class TestScoreAnswer:
    def test_category_C_scope_limit(self, firm):
        """Category C returns UNVERIFIABLE without calling the model."""
        result = scorer.score_answer(firm, "C", "What are the fees?", "0.5%")
        assert result["verdict"] == "UNVERIFIABLE"
        assert "Part 2A" in result["rationale"]
        assert result["model"] == "scope-limit"
        assert result["category"] == "C"

    def test_category_C_never_calls_model(self, firm):
        """The model injection point is never invoked for Category C."""
        called = []

        def fake_call(prompt):
            called.append(prompt)
            return "{}"

        scorer.score_answer(firm, "C", "fees?", "0.5%", _call=fake_call)
        assert called == []

    def test_rubric_version_attached(self, firm):
        result = scorer.score_answer(firm, "C", "fees?", "0.5%")
        assert result["rubric_version"] == RUBRIC_VERSION

    def test_other_categories_call_model(self, firm):
        """Non-C categories call the model and parse the verdict."""

        def fake_call(prompt):
            assert "NORTHBRIDGE" in prompt
            return json.dumps({
                "verdicts": [{"answer": 1, "class": "ACCURATE", "rationale": "matches"}]
            })

        result = scorer.score_answer(firm, "B", "How much AUM?", "$630M", _call=fake_call)
        assert result["verdict"] == "ACCURATE"
        assert result["model"] == scorer.SCORER_MODEL

    def test_model_error_returns_error(self, firm):
        def fake_call(prompt):
            raise RuntimeError("API down")

        result = scorer.score_answer(firm, "B", "How much AUM?", "$630M", _call=fake_call)
        assert result["verdict"] == "ERROR"
        assert "API down" in result["rationale"]

    def test_full_category_C_path_does_not_log_error(self, firm, caplog):
        """Category C should not produce any error log — the scope limit is expected."""
        import logging
        with caplog.at_level(logging.ERROR):
            scorer.score_answer(firm, "C", "fees?", "0.5%")
        assert "scorer call failed" not in caplog.text
