"""Publish `ground_truth.json` into the Firestore Battery Registry.

    python publish_registry.py            # publish and move the pointer
    python publish_registry.py --dry-run  # print the version and change set
    python publish_registry.py --no-pointer

`ground_truth.json` stays in the repo as the reviewable source. This script is
what makes it addressable: it content-hashes the roster, writes each firm to
`rosters/{version}/firms/{crd}`, and moves `registry/current` to that version.

Deliberately dependency-free — stdlib plus a `gcloud` access token. The agent
reads Firestore with `google-cloud-firestore` under its runtime service
account; an operator script that needed the same library, application-default
credentials and a virtualenv to publish five documents would be the thing that
stops getting run.

Writes are idempotent: the version *is* the content, so republishing unchanged
data rewrites identical documents at identical paths.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "agents"))
from attest_orchestrator.registry import content_version  # noqa: E402

SOURCE = Path(__file__).parent / "agents" / "attest_orchestrator" / "ground_truth.json"
API = "https://firestore.googleapis.com/v1"


def gcloud(*args: str) -> str:
    out = subprocess.run(
        ["gcloud", *args], capture_output=True, text=True, timeout=120
    )
    if out.returncode != 0:
        sys.exit(f"gcloud {' '.join(args)} failed:\n{out.stderr.strip()}")
    return out.stdout.strip()


def encode(value):
    """Python value -> Firestore typed value.

    Stored as native fields rather than an opaque JSON blob so the roster is
    readable in the console and queryable later. `bool` is checked before `int`
    because it is a subclass of it, and floats stay floats even when integral —
    the content hash was computed over the source types.
    """
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [encode(v) for v in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {k: encode(v) for k, v in value.items()}}}
    raise TypeError(f"unencodable {type(value).__name__}: {value!r}")


def encode_fields(record: dict) -> dict:
    """A document's `fields` map — the top level of a document is a bare field
    map, not a `mapValue`, so it cannot go through `encode` directly."""
    return {k: encode(v) for k, v in record.items()}


class Firestore:
    def __init__(self, project: str, database: str, token: str):
        db = urllib.parse.quote(database, safe="")
        self.base = f"{API}/projects/{project}/databases/{db}/documents"
        self.token = token

    def _request(self, method: str, path: str, body=None):
        req = urllib.request.Request(
            f"{self.base}/{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read() or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            sys.exit(f"Firestore {method} {path} -> {exc.code}\n{detail}")

    def put(self, path: str, fields: dict) -> None:
        """PATCH with no updateMask sets the document to exactly these fields."""
        self._request("PATCH", path, {"fields": fields})

    def count(self, path: str) -> int:
        page = self._request("GET", f"{path}?pageSize=300")
        return len(page.get("documents", []))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None)
    ap.add_argument("--database", default="(default)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--no-pointer",
        action="store_true",
        help="publish the roster but leave registry/current alone",
    )
    args = ap.parse_args()

    firms = json.loads(SOURCE.read_text(encoding="utf-8"))
    version = content_version(firms)

    crds = [f["crd"] for f in firms]
    if len(set(crds)) != len(crds):
        sys.exit(f"duplicate CRDs in {SOURCE.name}: {crds}")

    print(f"roster {version} — {len(firms)} firms: {', '.join(crds)}")
    if args.dry_run:
        for f in firms:
            print(f"  would write rosters/{version}/firms/{f['crd']}  {f['name']}")
        print(
            "  would set registry/current -> " + version
            if not args.no_pointer
            else "  pointer left alone"
        )
        return 0

    project = args.project or gcloud("config", "get-value", "project")
    if not project:
        sys.exit("No project. Pass --project or run: gcloud config set project <id>")
    db = Firestore(project, args.database, gcloud("auth", "print-access-token"))

    for firm in firms:
        db.put(f"rosters/{version}/firms/{firm['crd']}", encode_fields(firm))
        print(f"  wrote firms/{firm['crd']}  {firm['name']}")

    db.put(
        f"rosters/{version}",
        encode_fields(
            {
                "roster_version": version,
                "firm_count": len(firms),
                "crds": crds,
                "source": SOURCE.name,
            }
        ),
    )

    if not args.no_pointer:
        db.put("registry/current", encode_fields({"roster_version": version}))
        print(f"  registry/current -> {version}")

    written = db.count(f"rosters/{version}/firms")
    print(f"read back {written}/{len(firms)} firms from rosters/{version}/firms")
    return 0 if written == len(firms) else 1


if __name__ == "__main__":
    raise SystemExit(main())
