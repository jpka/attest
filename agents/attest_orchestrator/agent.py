"""Attest orchestrator — deploy-path skeleton.

Purpose of this file is NOT to be the finished orchestrator. It is the thinnest
agent that exercises every moving part of the deployment so that failures show up
now rather than on Aug 27: ADK agent definition, tool calling, a Pub/Sub trigger
entry point, Cloud Trace export, and the hash-chain shape the Evidence Archive
will use.

Ground truth comes from the Battery Registry in Firestore (see `registry.py`),
content-addressed so a run can name the roster version it was scored against.
`ground_truth.json` remains in the repo as the reviewable source that
`publish_registry.py` uploads.
"""

from __future__ import annotations

import logging
import os

from google.adk.agents import Agent

from . import evidence_archive, memory_bank, registry, scorer

logger = logging.getLogger(__name__)

# §5.1: keep every high-volume loop on a -flash-lite model. The orchestrator is
# low volume, but there is no reason to burn the 20/day flash quota proving a
# deployment works.
MODEL = os.environ.get("ATTEST_MODEL", "gemini-3.5-flash-lite")


def _load_firms() -> dict[str, dict]:
    """Keyed by CRD, never by name — the roster contains distinct firms that
    share a primary business name, and name-keying silently merges them."""
    return registry.load_firms()


def get_adv_ground_truth(crd: str) -> dict:
    """Look up a firm's Form ADV record by CRD number.

    The ADV is the firm's own filing with the SEC and is authoritative. Use it as
    ground truth when checking any claim about the firm.

    Args:
        crd: The firm's CRD number, e.g. "900001". Not the firm name.

    Returns:
        The firm's ADV record, or an error dict if the CRD is not in the roster.
    """
    firm = _load_firms().get(str(crd).strip())
    if not firm:
        return {
            "error": "not_found",
            "crd": crd,
            "available": sorted(_load_firms()),
        }
    return firm


def list_covered_firms() -> list[dict]:
    """List every firm currently under surveillance, with CRD and name."""
    return [
        {"crd": f["crd"], "name": f["name"], "city": f["city"], "state": f["state"]}
        for f in _load_firms().values()
    ]


def _archive() -> evidence_archive.EvidenceArchive:
    """Build the Evidence Archive from environment.

    Lazy so importing the agent module does not require credentials.
    """
    return evidence_archive.EvidenceArchive.from_env()


def append_evidence(payload: str) -> dict:
    """Append an observation to the evidence chain and return its chain entry.

    Every entry is content-hashed and carries the previous entry's hash, so any
    retroactive edit is detectable. This is the technical expression of Rule
    204-2's requirement that electronic records be preserved in a way that
    prevents unauthorized alteration or erasure.

    The archive determines where the entry links. You cannot supply, override or
    reset the previous hash — a chain whose linkage its author controls is not a
    record.

    Args:
        payload: The verbatim observation to record.

    Returns:
        The chain entry. Persist this verbatim; never rewrite it.
    """
    return _archive().append(payload, MODEL)


root_agent = Agent(
    name="attest_orchestrator",
    model=MODEL,
    description=(
        "Surveillance orchestrator for Attest. Records what AI assistants say "
        "about SEC-registered investment advisers and checks it against the "
        "firm's own Form ADV filing."
    ),
    instruction="""You are the Attest orchestrator.

You are invoked either by an operator or by a scheduled Pub/Sub message. A Pub/Sub
invocation arrives as JSON of the form {"data": ..., "attributes": {...}}; read the
CRD out of it and proceed without asking for confirmation.

For a surveillance run:
1. Call `get_adv_ground_truth` for the CRD in question. If no CRD was supplied,
   call `list_covered_firms` and report what is covered.
2. Summarise, in at most five lines, the fields a prospect would act on: total
   AUM, employee count, main office city and state, and whether Item 11 discloses
   any disciplinary events.
3. Call `append_evidence` with that summary to write it into the chain.
4. Report the returned entry_hash.

If you are given an AI assistant's answer to classify, call `score_answer` with
the firm record, category, prompt, and answer. It returns a verdict, rationale,
rubric version, and model — cite all four when reporting the finding.

Working memory: before summarising a firm, call `recall_firm_memory` with the
CRD and, if relevant, a semantic query (e.g. "assets under management") or a
category filter. After scoring or summarising, call `remember_firm_finding`
with the CRD, category, and the verbatim finding so future runs can build on
it. Working memory is not ground truth — it is what you have previously
observed. The Battery Registry remains the source of record.

You cannot delete memories. Purging is an operator action and no tool for it is
available to you; if a firm's working memory looks wrong, report that in your
findings rather than trying to correct it.

Scope limits you must respect, because they are what keep findings defensible:
- Part 1A gives the BASIS of advisory compensation, never the rate card. Fee
  schedules and account minimums live in Part 2A, which you do not have. Never
  assert or deny a specific rate or minimum.
- Item 5.E covers advisory compensation only. A firm with registered
  representatives of a broker-dealer may legitimately earn commissions through it.
- Absence of a field is not evidence a claim is false.

Be terse. You are writing a compliance record, not talking to a customer.""",
    tools=[
        get_adv_ground_truth,
        list_covered_firms,
        append_evidence,
        scorer.score_answer,
        memory_bank.remember_firm_finding,
        memory_bank.recall_firm_memory,
    ],
)
