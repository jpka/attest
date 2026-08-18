"""Attest orchestrator — deploy-path skeleton.

Purpose of this file is NOT to be the finished orchestrator. It is the thinnest
agent that exercises every moving part of the deployment so that failures show up
now rather than on Aug 27: ADK agent definition, tool calling, a Pub/Sub trigger
entry point, Cloud Trace export, and the hash-chain shape the Evidence Archive
will use.

Ground truth is read from a bundled JSON file here. Swap `_load_firms` for a
Firestore read in the Aug 18-19 phase; nothing else in this file needs to change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from google.adk.agents import Agent

logger = logging.getLogger(__name__)

# §5.1: keep every high-volume loop on a -flash-lite model. The orchestrator is
# low volume, but there is no reason to burn the 20/day flash quota proving a
# deployment works.
MODEL = os.environ.get("ATTEST_MODEL", "gemini-3.5-flash-lite")

GROUND_TRUTH_PATH = Path(__file__).parent / "ground_truth.json"


@lru_cache(maxsize=1)
def _load_firms() -> dict[str, dict]:
    """Keyed by CRD, never by name — the roster contains distinct firms that
    share a primary business name, and name-keying silently merges them."""
    firms = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    return {f["crd"]: f for f in firms}


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


def append_evidence(payload: str, prev_hash: str = "") -> dict:
    """Append an observation to the evidence chain and return its chain entry.

    Every entry is content-hashed and carries the previous entry's hash, so any
    retroactive edit is detectable. This is the technical expression of Rule
    204-2's requirement that electronic records be preserved in a way that
    prevents unauthorized alteration or erasure.

    Args:
        payload: The verbatim observation to record.
        prev_hash: The `entry_hash` of the previous entry. Empty for the first.

    Returns:
        The chain entry. Persist this verbatim; never rewrite it.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    body = json.dumps(
        {"payload": payload, "prev_hash": prev_hash, "timestamp": timestamp},
        sort_keys=True,
        separators=(",", ":"),
    )
    entry_hash = hashlib.sha256(body.encode()).hexdigest()
    entry = {
        "entry_hash": entry_hash,
        "prev_hash": prev_hash,
        "timestamp": timestamp,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "model_id": MODEL,
    }
    # Aug 24-25 replaces this with an append-only Firestore write + GCS object.
    logger.info("evidence.append %s", json.dumps(entry))
    return entry


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

Scope limits you must respect, because they are what keep findings defensible:
- Part 1A gives the BASIS of advisory compensation, never the rate card. Fee
  schedules and account minimums live in Part 2A, which you do not have. Never
  assert or deny a specific rate or minimum.
- Item 5.E covers advisory compensation only. A firm with registered
  representatives of a broker-dealer may legitimately earn commissions through it.
- Absence of a field is not evidence a claim is false.

Be terse. You are writing a compliance record, not talking to a customer.""",
    tools=[get_adv_ground_truth, list_covered_firms, append_evidence],
)
