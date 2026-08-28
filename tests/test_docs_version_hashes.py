"""The documented roster version, checked against the one the code computes.

The published docs stated roster ``32a6587ad82b`` for a week after the roster
stopped hashing to it. That value was the skeleton's, from the initial commit;
the committed ``ground_truth.json`` had moved and the prose had not. Firestore
was right and the README was wrong, which is the harder direction to notice --
nothing fails, and the provenance paragraph goes on claiming a superseded
version regenerates identically.

This is the fourth appearance of one shape in this repo: the GCS bucket, the
registry pointer, the Memory Bank engine id, and now the documented hash. Each
was a value that had to be updated by remembering to update it. So this is a
check rather than a habit.

It is written as an allowlist, the same way ``test_no_real_crd_in_tree`` is. It
does not name the superseded hash -- a blocklist only covers the values someone
already thought of, and the next stale one will be different. It asserts the
complementary property: every version-shaped literal in the published docs is a
version this repo currently produces.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agents.attest_orchestrator.registry import content_version
from agents.attest_orchestrator.scorer_prompts import RUBRIC_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every file that states the roster version in prose a reader is expected to
# trust. docs/architecture.html is here because it is the diagram, which is
# read far more often than the paragraph that explains it.
DOCS_STATING_THE_ROSTER = (
    "README.md",
    "ingest/README.md",
    "docs/architecture.html",
)

# The project's content-hash scheme is twelve lowercase hex characters. Short
# git SHAs in these files are seven, so they do not collide with this shape.
VERSION_SHAPE = re.compile(r"\b[0-9a-f]{12}\b")


def roster_version() -> str:
    """What ``publish_registry.py`` would publish from the committed roster."""
    roster = json.loads(
        (REPO_ROOT / "agents/attest_orchestrator/ground_truth.json").read_text()
    )
    return content_version(roster)


def battery_version() -> str:
    """The battery version, read from the literal the smoke path sends.

    The battery itself lives in the private research repo, so this value cannot
    be recomputed here. Reading the literal still beats hardcoding it twice.
    """
    source = (REPO_ROOT / "local_test.py").read_text()
    match = re.search(r'"battery_version":\s*"([0-9a-f]{12})"', source)
    assert match, "local_test.py no longer carries a battery_version literal"
    return match.group(1)


def current_versions() -> set[str]:
    """Every twelve-hex version this repo legitimately produces today."""
    return {roster_version(), RUBRIC_VERSION, battery_version()}


@pytest.mark.parametrize("relative_path", DOCS_STATING_THE_ROSTER)
def test_docs_state_the_current_roster_version(relative_path: str) -> None:
    """Each doc names the roster the committed ground truth actually hashes to.

    Presence matters as much as correctness. A doc that quietly stopped
    mentioning the version would pass a staleness check that only compared the
    hashes it happened to find.
    """
    text = (REPO_ROOT / relative_path).read_text()
    expected = roster_version()
    assert expected in text, (
        f"{relative_path} does not state the current roster version {expected}. "
        "If the roster changed, update the prose in the same commit."
    )


@pytest.mark.parametrize("relative_path", DOCS_STATING_THE_ROSTER)
def test_docs_carry_no_superseded_version(relative_path: str) -> None:
    """No version-shaped literal in the docs names something we no longer build."""
    text = (REPO_ROOT / relative_path).read_text()
    known = current_versions()
    found = set(VERSION_SHAPE.findall(text))
    stale = found - known
    assert not stale, (
        f"{relative_path} names {sorted(stale)}, which no longer corresponds to "
        f"anything this repo produces. Current versions are {sorted(known)}."
    )


def test_the_three_versions_are_distinct() -> None:
    """Guards the check above from passing because two constants collapsed.

    If the roster and rubric versions ever coincided, an allowlist built from
    both would stop discriminating between them.
    """
    assert len(current_versions()) == 3
