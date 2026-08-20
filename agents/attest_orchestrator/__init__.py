"""Attest orchestrator package.

Imports are lazy so that importing `scorer` or `scorer_prompts` does not
require `google.adk` — the scorer is unit-testable without the agent runtime.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import agent, evidence_archive  # noqa: F401


def __getattr__(name: str):
    if name == "agent":
        from . import agent

        return agent
    if name == "evidence_archive":
        from . import evidence_archive

        return evidence_archive
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
