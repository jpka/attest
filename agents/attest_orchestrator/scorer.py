"""Scorer — classify an AI assistant's answer against the firm's Form ADV.

Implements the corrected v2 rubric (see scorer_prompts.py). Used as a tool on
the orchestrator agent: when the agent captures an answer about a firm, it calls
the scorer to produce a citable verdict.

Scope limit (attest-replan-0819.md, section 3, option a): Category C — fees and
minimums — returns UNVERIFIABLE by construction. Fee schedules and account
minimums live in ADV Part 2A, which the bulk roster does not contain. The scorer
names Part 2A as the missing source rather than adjudicating what it cannot source.

Every answer is screened by Model Armor before it reaches a prompt, and the
screening record travels with the verdict. See `model_armor.py` — the short
version is that an answer is untrusted third-party model output, and one that
tries to instruct the scorer is refused rather than adjudicated.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from . import model_armor, scorer_prompts

logger = logging.getLogger(__name__)

# Category C is UNVERIFIABLE by construction. See section 3 above.
CATEGORY_C = "C"

# Verdicts this module returns without asking the model, and deliberately NOT
# members of VALID_VERDICTS below: that set is the allowlist for what the
# scorer model may emit, and a model able to emit "screening failed" could
# launder an unscreened answer into the chain.
BLOCKED_VERDICT = "BLOCKED-INJECTION"
UNSCREENED_VERDICT = "ERROR-UNSCREENED"

# Valid verdict classes — anything else is ERROR-UNPARSED. Shared by every
# parse path so a model that emits "PASS" or "TRUE" cannot smuggle an
# unvetted label into the evidence chain.
VALID_VERDICTS = {
    "MATERIAL-FALSE",
    "MATERIAL-FABRICATED",
    "IMMATERIAL-FALSE",
    "UNSUPPORTED-OPINION",
    "ACCURATE",
    "ABSTAINED",
    "NOT-SURFACED",
    "UNVERIFIABLE",
    "ERROR-UNPARSED",
    "ERROR-MISSING",
}

# Same model family as premise_test.py's grader for comparability. §5.1: the
# scoring loop is high-volume, so it stays on -flash-lite. The subject model
# in the README (gemini-3.5-flash-lite) is the surveillance target; the scorer
# below is a distinct, cheaper judge — the version skew is intentional.
SCORER_MODEL = os.environ.get("ATTEST_SCORER_MODEL", "gemini-3.1-flash-lite")

# ADV sections that bear on each prompt category. Sending only the relevant
# slice halves token use and stops the grader reaching for a field with no
# bearing on the question.
CATEGORY_GROUND_TRUTH: dict[str, list[str]] = {
    "A": ["identity", "services"],
    "B": ["identity", "aum", "employees"],
    "C": ["identity", "compensation", "aum", "services"],
    "D": ["identity", "employees"],
    "E": ["identity", "services", "clients"],
    "F": ["identity"],
    "G": ["identity", "compensation", "aum", "services"],
    "H": ["identity", "disciplinary"],
}

IDENTITY_FIELDS = (
    "crd",
    "name",
    "legal_name",
    "sec_number",
    "city",
    "state",
    "website",
    "sec_registration_status",
    "sec_status_effective_date",
    "latest_adv_filing_date",
)


def _prune(value: Any) -> Any:
    """Drop empty/null leaves so the prompt carries only fields in evidence."""
    if isinstance(value, dict):
        pruned = {k: _prune(v) for k, v in value.items()}
        return {k: v for k, v in pruned.items() if v not in (None, "", {}, [])}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def ground_truth_slice(firm: dict, category: str) -> dict:
    """The slice of the ADV record that bears on this category of prompt."""
    sections = CATEGORY_GROUND_TRUTH.get(category, [])
    slice_: dict[str, Any] = {}
    for section in sections:
        if section == "identity":
            slice_.update({k: firm.get(k) for k in IDENTITY_FIELDS})
        else:
            slice_[section] = firm.get(section)
    return _prune(slice_)


def build_scorer_prompt(firm: dict, category: str, prompt: str, answer: str) -> str:
    """Build the user prompt sent to the scorer model."""
    is_discovery = category == "A"
    discovery_note = (
        "NOTE: this prompt does not name the firm. If an answer never mentions "
        "the firm, classify NOT-SURFACED.\n\n"
        if is_discovery
        else ""
    )
    return (
        f"Firm: {firm['name']} ({firm['city']}, {firm['state']})\n"
        f"Prompt category: {category}\n"
        f"Prompt asked: {prompt}\n\n"
        f"{discovery_note}"
        f"FORM ADV GROUND TRUTH (authoritative):\n"
        f"{json.dumps(ground_truth_slice(firm, category), separators=(',', ':'))}\n\n"
        f"CLASSES:\n{scorer_prompts.RUBRIC_V2}\n\n"
        f"ANSWER TO CLASSIFY:\n{answer}\n"
    )


def parse_verdict(raw: str) -> tuple[str, str]:
    """Parse the scorer's JSON response into (class, rationale).

    Tolerates truncated output: scans for the first complete verdict object
    in a possibly-truncated array rather than failing the whole response.
    """
    raw = raw.strip()

    def _valid(class_name: object) -> str:
        """Normalize and validate a verdict class. Anything not in the
        allowlist collapses to ERROR-UNPARSED."""
        if not isinstance(class_name, str):
            return "ERROR-UNPARSED"
        normalized = class_name.strip().upper()
        return normalized if normalized in VALID_VERDICTS else "ERROR-UNPARSED"

    # Direct parse.
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            verdicts = data.get("verdicts")
            if verdicts and isinstance(verdicts[0], dict):
                v = verdicts[0]
                return (
                    _valid(v.get("class", "ERROR-UNPARSED")),
                    str(v.get("rationale", "")).strip(),
                )
    except json.JSONDecodeError:
        pass

    # Salvage: scan for complete objects in a possibly-truncated array.
    decoder = json.JSONDecoder()
    cursor = raw.find("[")
    if cursor == -1:
        # No array: try the first bare object.
        start = raw.find("{")
        if start >= 0:
            try:
                obj, _ = decoder.raw_decode(raw, start)
                if isinstance(obj, dict):
                    return (
                        _valid(obj.get("class", "ERROR-UNPARSED")),
                        str(obj.get("rationale", "")).strip(),
                    )
            except json.JSONDecodeError:
                pass
    else:
        while True:
            start = raw.find("{", cursor)
            if start == -1:
                break
            try:
                obj, cursor = decoder.raw_decode(raw, start)
                if isinstance(obj, dict) and "class" in obj:
                    return (
                        _valid(obj["class"]),
                        str(obj.get("rationale", "")).strip(),
                    )
            except json.JSONDecodeError:
                break

    # Last resort: regex-extract the class value from truncated JSON. This
    # handles the case where the response was cut off mid-string so no object
    # is parseable, but the class field was written in full.
    match = re.search(r'"class"\s*:\s*"([^"]+)"', raw)
    if match:
        return (_valid(match.group(1)), "")

    return ("ERROR-UNPARSED", raw[:120].replace("\n", " "))


def _client():
    """Vertex AI genai client. Lazily imported so tool wiring tests don't need
    credentials."""
    from google import genai

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    return genai.Client(vertexai=True, project=project, location=location)


def _call_scorer(prompt: str) -> str:
    """Send the scorer prompt to Gemini. Returns raw text."""
    client = _client()
    response = client.models.generate_content(
        model=SCORER_MODEL,
        contents=prompt,
        config={
            "system_instruction": scorer_prompts.SCORER_SYSTEM_V2,
            "response_mime_type": "application/json",
            "max_output_tokens": 1000,
        },
    )
    return response.text or ""


def score_answer(
    firm: dict,
    category: str,
    prompt: str,
    answer: str,
    _call: callable = None,  # injection seam for tests
    _screen: callable = None,  # injection seam for tests
) -> dict:
    """Score one answer against the firm's ADV record.

    Returns a dict with: verdict, rationale, rubric_version, model, category,
    and armor — the Model Armor screening record for this answer.

    Category C always returns UNVERIFIABLE without a model call — that is the
    scope limit, not a fallback.

    Screening runs ahead of both, including ahead of Category C. Category C
    reaches no prompt and so carries no injection risk, but a verdict that
    carries no screening record is indistinguishable from one that was never
    screened, and the archive has to be able to tell those apart.
    """
    # Normalize and validate category. Lowercase "c" must hit the scope limit,
    # not bypass it; unknown categories fail loudly rather than reaching the
    # model with an out-of-scope question.
    if not isinstance(category, str) or not category.strip():
        return {
            "verdict": "ERROR",
            "rationale": f"Invalid category: {category!r}.",
            "rubric_version": scorer_prompts.RUBRIC_VERSION,
            "model": "validation",
            "category": str(category),
            "armor": {"state": model_armor.UNCONFIGURED, "blocked": False, "findings": {}},
        }
    category = category.strip().upper()
    if category not in CATEGORY_GROUND_TRUTH:
        return {
            "verdict": "ERROR",
            "rationale": f"Unknown category: {category!r}.",
            "rubric_version": scorer_prompts.RUBRIC_VERSION,
            "model": "validation",
            "category": category,
            "armor": {"state": model_armor.UNCONFIGURED, "blocked": False, "findings": {}},
        }

    armor = model_armor.screen_answer(answer, _screen=_screen)
    if armor.get("blocked"):
        # Two different failures, and the record must not conflate them: the
        # answer attacked the scorer, or the guardrail did not run.
        unscreened = armor.get("state") == model_armor.ERROR
        return {
            "verdict": UNSCREENED_VERDICT if unscreened else BLOCKED_VERDICT,
            "rationale": (
                "Model Armor did not screen this answer, so it was not scored: "
                f"{armor.get('detail', 'screening unavailable')}"
                if unscreened
                else (
                    "Model Armor flagged prompt injection in the captured "
                    "answer. The answer was not sent to the scorer, and no "
                    "verdict on its claims is asserted. Detected: "
                    f"{sorted(armor.get('findings', {}))}."
                )
            ),
            "rubric_version": scorer_prompts.RUBRIC_VERSION,
            "model": "model-armor",
            "category": category,
            "armor": armor,
        }

    if category == CATEGORY_C:
        return {
            "verdict": "UNVERIFIABLE",
            "rationale": (
                "Category C (fees/minimums) requires ADV Part 2A, which is not "
                "ingested. Returns UNVERIFIABLE by construction per §3 option (a)."
            ),
            "rubric_version": scorer_prompts.RUBRIC_VERSION,
            "model": "scope-limit",
            "category": category,
            "armor": armor,
        }

    scorer_prompt = build_scorer_prompt(firm, category, prompt, answer)
    call = _call or _call_scorer

    try:
        raw = call(scorer_prompt)
    except Exception as e:  # noqa: BLE001
        logger.error("scorer call failed: %s", e)
        rationale = f"{type(e).__name__}: {e}"
        if len(rationale) > 200:
            rationale = rationale[:197] + "..."
        return {
            "verdict": "ERROR",
            "rationale": rationale,
            "rubric_version": scorer_prompts.RUBRIC_VERSION,
            "model": SCORER_MODEL,
            "category": category,
            "armor": armor,
        }

    verdict, rationale = parse_verdict(raw)
    return {
        "verdict": verdict,
        "rationale": rationale,
        "rubric_version": scorer_prompts.RUBRIC_VERSION,
        "model": SCORER_MODEL,
        "category": category,
        "armor": armor,
    }
