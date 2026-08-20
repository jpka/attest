"""Attest orchestrator package.

Submodules are exposed lazily so that importing `scorer` or `scorer_prompts`
does not require `google.adk` — the scorer is unit-testable without the agent
runtime.

The lazy loader uses ``importlib.import_module`` and caches the result in
``globals()``. Both details are load-bearing. A ``from . import <name>``
statement inside this function re-enters it: during the submodule's own
execution the package attribute is not yet bound, so the import machinery
falls back to ``__getattr__``, which imports again — unbounded recursion that
makes ``agent.py`` (which does ``from . import evidence_archive, ...`` at
module scope) impossible to import at all. Caching in ``globals()`` means a
resolved name is answered by normal attribute lookup and never re-enters here.
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import agent, evidence_archive, memory_bank  # noqa: F401

_LAZY_SUBMODULES = frozenset(
    {
        "agent",
        "evidence_archive",
        "memory_bank",
        "registry",
        "scorer",
        "scorer_prompts",
    }
)


def __getattr__(name: str):
    if name in _LAZY_SUBMODULES:
        module = importlib.import_module(f".{name}", __name__)
        # Cache so a second lookup is a plain attribute hit, not a re-entry.
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | _LAZY_SUBMODULES)
