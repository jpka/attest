"""Tests for the package's lazy submodule loader.

The loader exists so `scorer` is importable without `google.adk`. It is also
the piece that broke `agent.py` outright: a `from . import <name>` inside
`__getattr__` re-enters the loader during the submodule's own execution and
recurses without bound. These tests pin the behaviour that prevents that.

`agent.py` itself is not imported here — it requires `google.adk`, which is
installed by the generated Dockerfile rather than declared in requirements.
The recursion is reproduced with a stdlib-only package instead, so the test
covers the mechanism on any machine.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

import agents.attest_orchestrator as pkg


class TestLazyLoader:
    def test_scorer_importable_without_adk(self):
        """The reason the loader exists: no google.adk needed for the scorer."""
        scorer = pkg.scorer
        assert hasattr(scorer, "score_answer")

    def test_evidence_archive_importable(self):
        assert hasattr(pkg.evidence_archive, "EvidenceArchive")

    def test_memory_bank_importable(self):
        assert hasattr(pkg.memory_bank, "MemoryBank")

    def test_unknown_attribute_raises_attribute_error(self):
        missing = "nope"  # indirect so ruff sees no constant attribute access
        with pytest.raises(AttributeError, match="no attribute 'nope'"):
            getattr(pkg, missing)

    def test_resolved_submodule_is_cached_in_globals(self):
        """A resolved name must become a plain attribute.

        If it stays unbound, every later lookup re-enters __getattr__ — which
        is what made the recursion unbounded.

        The binding is cleared first because a direct
        ``importlib.import_module`` of the submodule sets the parent attribute
        as a side effect: without the reset, this passes without ever calling
        ``__getattr__``, so it would not test the loader at all.
        """
        vars(pkg).pop("registry", None)
        assert "registry" not in vars(pkg)
        resolved = pkg.registry  # goes through __getattr__
        assert resolved is sys.modules["agents.attest_orchestrator.registry"]
        assert "registry" in vars(pkg), "resolved module was not cached"

    def test_dir_lists_lazy_submodules(self):
        listed = dir(pkg)
        for name in ("agent", "evidence_archive", "memory_bank", "scorer"):
            assert name in listed


class TestLazyLoaderRecursion:
    """Regression: `from . import X` inside __getattr__ recurses forever."""

    @staticmethod
    def _run(init_body: str, tmp_path) -> str:
        p = tmp_path / "lazypkg"
        p.mkdir()
        (p / "__init__.py").write_text(textwrap.dedent(init_body))
        (p / "leaf.py").write_text("VALUE = 42\n")
        # Mirrors agent.py: a sibling import at module scope.
        (p / "consumer.py").write_text("from . import leaf\n\nRESULT = leaf.VALUE\n")
        code = (
            "import sys; sys.setrecursionlimit(200); "
            f"sys.path.insert(0, {str(tmp_path)!r});\n"
            "try:\n"
            "    from lazypkg.consumer import RESULT\n"
            "    print('OK', RESULT)\n"
            "except RecursionError:\n"
            "    print('RECURSION')\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )
        return out.stdout.strip()

    def test_from_import_pattern_recurses(self, tmp_path):
        """The broken pattern — kept as an executable explanation of the bug."""
        result = self._run(
            """
            def __getattr__(name):
                if name == "leaf":
                    from . import leaf

                    return leaf
                raise AttributeError(name)
            """,
            tmp_path,
        )
        assert result == "RECURSION", f"expected recursion, got {result!r}"

    def test_importlib_with_caching_does_not_recurse(self, tmp_path):
        """The shipped pattern must survive the same import."""
        result = self._run(
            """
            import importlib

            _LAZY = {"leaf"}


            def __getattr__(name):
                if name in _LAZY:
                    module = importlib.import_module(f".{name}", __name__)
                    globals()[name] = module
                    return module
                raise AttributeError(name)
            """,
            tmp_path,
        )
        assert result == "OK 42", f"expected clean import, got {result!r}"
