"""Run the whole path locally before spending a Cloud Build minute on it.

    pip install google-adk
    export GOOGLE_API_KEY=<your AI Studio key>
    python local_test.py

Exercises, in order:
  1. agent + tool loading
  2. the Pub/Sub trigger route, with a real push envelope
  3. one live model call

If this passes, a Cloud Run failure is an infrastructure problem, not an agent
problem — which is the whole point of running it.
"""

from __future__ import annotations

import base64
import inspect
import json
import os
import sys

FAILED = False


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILED
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILED = True


def main() -> int:
    if not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        print("Set GOOGLE_API_KEY (AI Studio) or configure Vertex auth first.")
        return 2

    # ADK reads agents from a directory, so the package must be importable.
    sys.path.insert(0, "agents")

    from attest_orchestrator.agent import (  # noqa: E402
        append_evidence,
        get_adv_ground_truth,
        root_agent,
    )

    check("agent loads", root_agent.name == "attest_orchestrator", root_agent.model)
    check("tools attached", len(root_agent.tools) == 3)

    firm = get_adv_ground_truth("900001")
    check(
        "ground truth by CRD",
        firm.get("disciplinary", {}).get("any_disclosure") is True,
        f"{firm.get('name')} — Item 11 discloses "
        f"{len(firm.get('disciplinary', {}).get('items', []))} items",
    )
    check("unknown CRD handled", get_adv_ground_truth("9999").get("error") == "not_found")

    first = append_evidence("genesis")
    second = append_evidence("second")
    check(
        "hash chain links",
        first["prev_hash"] == "" and second["prev_hash"] == first["entry_hash"],
        first["entry_hash"][:16],
    )
    # The linkage must not be reachable from the model. If `prev_hash` ever comes
    # back as a parameter, the agent can fork or reset the chain by passing a
    # stale value and nothing downstream can tell.
    check(
        "chain linkage not caller-supplied",
        "prev_hash" not in inspect.signature(append_evidence).parameters,
    )

    from fastapi.testclient import TestClient  # noqa: E402
    from google.adk.cli.fast_api import get_fast_api_app  # noqa: E402

    app = get_fast_api_app(agents_dir="agents", web=False, trigger_sources=["pubsub"])
    routes = {r.path for r in app.routes}
    check(
        "pubsub trigger registered",
        "/apps/{app_name}/trigger/pubsub" in routes,
    )

    envelope = {
        "message": {
            "data": base64.b64encode(json.dumps({"crd": "900001"}).encode()).decode(),
            "attributes": {"battery_version": "4ea67a1f35e1"},
            "messageId": "local-1",
        },
        "subscription": "projects/local/subscriptions/attest-runs-push",
    }
    client = TestClient(app, raise_server_exceptions=False)
    print("\n--- live model call via the Pub/Sub route (this costs tokens) ---")
    resp = client.post("/apps/attest_orchestrator/trigger/pubsub", json=envelope)
    check(
        "end-to-end trigger",
        resp.status_code == 200,
        f"HTTP {resp.status_code} {resp.text[:200]}",
    )
    if resp.status_code != 200:
        print(
            "\nA 500 here is what Pub/Sub sees as a nack, so it will retry.\n"
            "429 with 'PerDay' in the body means that model's free-tier daily\n"
            "quota is spent — switch ATTEST_MODEL or attach billing (§5.1)."
        )

    print()
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
