"""Attest orchestrator package.

Imports are lazy so that importing `scorer` or `scorer_prompts` does not
require `google.adk` — the scorer is unit-testable without the agent runtime.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import agent  # noqa: F401


def __getattr__(name: str):
    if name == "agent":
        from . import agent

        return agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
