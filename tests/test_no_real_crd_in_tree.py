"""The pre-publication CRD sweep, run by the build.

Every retrospective sweep this repository has run found something the previous
one's scoping had missed: first by path, then by ref set, then by the *form* the
value took -- a ``filter-repo`` pass cleared a quoted fixture literal everywhere
and left the same number untouched in a comment one line above it.

So this check is an allowlist, not a blocklist. It does not name the real
registrant CRDs it is defending against -- doing that is the exact mistake it
exists to catch, and a blocklist only ever covers the values someone already
thought of. It asserts the complementary and strictly stronger property: every
CRD-shaped literal in the tracked tree is one of ours.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SELF = Path(__file__).resolve()
REPO_ROOT = SELF.parent.parent

# Every value below is in the 9000-prefixed fictional block. That prefix is the
# invariant test_allowlist_is_entirely_fictional pins, and it is what makes the
# allowlist auditable without naming a single real registrant.

# The synthetic roster. CRDs 900001-900005 are not assigned to any registrant.
ROSTER_CRDS = {f"90000{n}" for n in range(1, 6)}

# The out-of-roster probe value, the unknown-CRD probe in local_test.py, and the
# 10-digit block the roster fixtures are built from. The fixtures were 100000x
# until CodeRabbit pointed out that seven-digit CRDs are well inside the range
# assigned to individuals -- an allowlist entry nobody had verified was
# fictional, which is the same error this module exists to catch, one level up.
SYNTHETIC_CRDS = {"900006", "900099"} | {f"900000000{n}" for n in range(1, 10)}

# Zero is not an assigned registration number -- CRD numbering starts at 1 -- so
# it cannot identify a registrant. It is the one short fixture kept, to catch a
# future minimum-length rule in _validate_crd, which today only checks isdigit.
SHORT_FIXTURES = {"0"}

# Sentinels for this module's own detection tests. Deliberately NOT allowlisted
# -- the detection tests assert they are flagged -- and deliberately not
# 9000-prefixed, so they can never be confused with a fixture value.
DETECTOR_SENTINELS = {"9999999999", "8888888888", "7777777777"}

ALLOWED = ROSTER_CRDS | SYNTHETIC_CRDS | SHORT_FIXTURES

# The heuristic form below cannot be made precise -- a line that mentions CRDs
# and carries an integer is usually a slice width, a column count or a PR
# number. Rather than drop the check to silence the noise, or leave it advisory
# where it would be ignored, an inspected line is annotated in place. The marker
# is the durable record that someone read it: a manual sweep leaves no trace and
# cannot distinguish "inspected and fine" from "missed", which is the specific
# gap that let a real CRD sit in a comment through four passes.
NOT_A_CRD = re.compile(r"(?i)\bnot-a-crd\b")

# A CRD sitting in a CRD-shaped position: JSON key, kwarg, attribute, filter.
CRD_POSITION = re.compile(
    r"""(?xi)
    (?: "crd" \s* : \s* "? (?P<a>\d+) "?
      | \bcrd \s* = \s* "? (?P<b>\d+) "?
      | \bcrd_number \s* [:=] \s* "? (?P<c>\d+) "?
      | scope\.crd \s* = \s* "? (?P<d>\d+) "?
    )
    """
)

# Any line that talks about CRDs at all. Every integer on such a line has to be
# accounted for -- this is the form that caught nothing for four passes because
# the value was prose, not data.
CRD_MENTION = re.compile(r"(?i)\bcrds?\b")
INTEGER = re.compile(r"\b\d+\b")

# Line/column references and the SEC file-number block are not CRDs.
SEC_NUMBER = re.compile(r"\b801-\d+\b")

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".zip", ".gz"}


def tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    paths = []
    for name in out.split("\0"):
        if not name:
            continue
        p = REPO_ROOT / name
        if p.suffix.lower() in SKIP_SUFFIXES or not p.is_file():
            continue
        # This module is definitionally full of CRD-shaped fixtures; scanning it
        # against its own allowlist is circular. It is covered instead by
        # test_detector_fixtures_are_synthetic below, which pins every
        # CRD-positioned value in this file to a sentinel or a roster CRD.
        if p == SELF:
            continue
        paths.append(p)
    return paths


# A scope name says CRD without word boundaries around it: TestCRDValidation.
SCOPE_NAME_CRD = re.compile(r"(?i)crd")

def _advance_bracket_state(
    line: str, depth: int, triple: str | None
) -> tuple[int, str | None]:
    """Return bracket depth and open-triple-quote state after consuming ``line``.

    Indentation only delimits scope when Python is not inside an implicit
    continuation. Without this, a value dedented inside a parenthesised literal
    pops the enclosing scope and escapes the CRD-scope heuristic entirely::

        class TestCRDValidation:
            values = (
        9999999999,
            )

    String and comment spans are skipped, so a bracket inside either does not
    move the count.
    """
    i = 0
    while i < len(line):
        if triple is not None:
            end = line.find(triple, i)
            if end == -1:
                return depth, triple
            i = end + 3
            triple = None
            continue

        char = line[i]
        if char == "#":
            break

        quote3 = line[i : i + 3]
        if quote3 in ('"""', "'''"):
            triple = quote3
            i += 3
            continue

        if char in "\"'":
            i += 1
            while i < len(line):
                if line[i] == "\\":
                    i += 2
                    continue
                if line[i] == char:
                    i += 1
                    break
                i += 1
            continue

        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        i += 1

    return depth, triple


SCOPE_DECL = re.compile(
    r"^(?P<indent>\s*)(?:class|(?:async\s+)?def)\s+(?P<name>\w+)"
)


def scan(text: str) -> list[tuple[int, str, str]]:
    """Return (lineno, form, value) for every CRD-shaped literal not allowlisted.

    Two forms. ``crd-position`` is precise and always fatal. ``crd-mention`` is
    heuristic: it fires on any integer sharing a line with the word CRD, or
    sitting anywhere inside a class or function whose *name* says CRD -- the
    fixture that started all of this was a bare parametrize list whose line
    never used the word, inside a class called TestCRDValidation. A line-local
    check could not have seen it. Heuristic hits are silenced per line with a
    ``not-a-crd`` marker, which records that someone read it.
    """
    findings: list[tuple[int, str, str]] = []
    scopes: list[tuple[int, str]] = []
    depth = 0
    triple: str | None = None

    for lineno, line in enumerate(text.splitlines(), start=1):
        continued = depth > 0 or triple is not None
        depth, triple = _advance_bracket_state(line, depth, triple)
        # Pop on any dedent, not only when the next declaration appears: a
        # module-level statement after a CRD-named class is out of that scope
        # and must not inherit it.
        if line.strip() and not continued:
            indent = len(line) - len(line.lstrip())
            while scopes and scopes[-1][0] >= indent:
                scopes.pop()
            decl = SCOPE_DECL.match(line)
            if decl:
                scopes.append((indent, decl.group("name")))

        for m in CRD_POSITION.finditer(line):
            value = next(g for g in m.groups() if g is not None)
            if value not in ALLOWED:
                findings.append((lineno, "crd-position", value))

        in_crd_scope = any(SCOPE_NAME_CRD.search(name) for _, name in scopes)
        if (CRD_MENTION.search(line) or in_crd_scope) and not NOT_A_CRD.search(line):
            stripped = SEC_NUMBER.sub("", line)
            for value in INTEGER.findall(stripped):
                if value not in ALLOWED:
                    findings.append((lineno, "crd-mention", value))

    return findings


def test_no_unallowlisted_crd_in_tracked_tree() -> None:
    failures: list[str] = []
    for path in tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, form, value in scan(text):
            rel = path.relative_to(REPO_ROOT).as_posix()
            failures.append(f"{rel}:{lineno} [{form}] {value!r}")

    assert not failures, (
        "CRD-shaped literals that are not on the synthetic allowlist.\n"
        "Every CRD in published material must come from the 900001-900005 roster "
        "or be an obviously synthetic fixture value. If one of these is genuinely "
        "not a CRD, add it to SYNTHETIC_CRDS with a reason.\n\n  "
        + "\n  ".join(failures)
    )


# The check has to fail on the thing it was written for. Both forms, explicitly,
# so that a future refactor that quietly stops matching is caught here rather
# than by the next person running a manual sweep.


@pytest.mark.parametrize(
    "sample",
    [
        '    scope={"crd": "9999999999"},',
        "    crd=8888888888",
        "    crd_number: 7777777777",
    ],
)
def test_detects_crd_position_form(sample: str) -> None:
    assert scan(sample), f"crd-position form not detected: {sample!r}"


def test_detects_bare_integer_on_a_crd_line() -> None:
    """The regression case: the comment that survived the filter-repo pass."""
    sample = "    # A real registrant CRD (9999999999, 8888888888) would be fine here"
    found = {value for _, form, value in scan(sample) if form == "crd-mention"}
    assert found == {"9999999999", "8888888888"}, found


def test_allowlisted_values_do_not_trip_it() -> None:
    clean = [
        '    scope={"crd": "900001"},',
        '    @pytest.mark.parametrize("value", ["900001", " 900001 ", "7"])',
        "    # Every CRD literal here must be a roster CRD or synthetic.",
        "    # SEC number 801-900012 belongs to a fictional firm.",
    ]
    for line in clean:
        assert not scan(line), f"false positive on: {line!r}"


def test_detects_bare_fixture_inside_a_crd_named_scope() -> None:
    """The original miss: a parametrize list whose line never says "CRD".

    This is the exact shape of the fixture that reached published history and
    survived a filter-repo pass. Nothing on the offending line identifies the
    value as a CRD; only the enclosing class name does, so a line-local check
    reports clean on it.
    """
    sample = "\n".join(
        [
            "class TestCRDValidation:",
            '    @pytest.mark.parametrize("value", ["900001", "9999999999"])',
            "    def test_accepts_numeric(self, value):",
            "        assert _validate_crd(value) == value",
        ]
    )
    found = {value for _, form, value in scan(sample) if form == "crd-mention"}
    assert found == {"9999999999"}, found


def test_crd_named_scope_does_not_leak_past_dedent() -> None:
    sample = "\n".join(
        [
            "class TestCRDValidation:",
            "    pass",
            "",
            "class TestSomethingElse:",
            "    LIMIT = 9999999999",
        ]
    )
    assert not scan(sample)


def test_detector_fixtures_are_synthetic() -> None:
    """This file is skipped by the tree scan, so pin its own fixtures here.

    Non-circular on purpose: the expectation is a literal set, not the
    allowlist the scan uses. A real CRD dropped into these fixtures fails here.
    """
    source = SELF.read_text(encoding="utf-8")
    values = {
        next(g for g in m.groups() if g is not None)
        for m in CRD_POSITION.finditer(source)
    }
    permitted = DETECTOR_SENTINELS | ROSTER_CRDS
    assert values <= permitted, values - permitted


def test_allowlist_is_entirely_fictional() -> None:
    """Pin the allowlist itself, which nothing else checks.

    The 100000x fixture block sat here until review pointed out that
    seven-digit CRDs are well inside the range assigned to individuals. An
    allowlist entry nobody verified was fictional is the same failure this
    module exists to catch, one level up -- so the invariant is mechanical:
    every allowlisted value carries the 9000 fictional prefix.
    """
    unexplained = {v for v in ALLOWED if not v.startswith("9000")} - SHORT_FIXTURES
    assert not unexplained, unexplained
    assert all(v.startswith("9000") for v in ROSTER_CRDS | SYNTHETIC_CRDS)
    # The detector's own sentinels must never become allowlisted, or the
    # detection tests would silently stop detecting.
    assert not (ALLOWED & DETECTOR_SENTINELS)


def test_module_level_statement_after_crd_scope_is_not_inherited() -> None:
    """A dedent ends the scope even when no new declaration follows it.

    The first version popped the scope stack only when the next class or def
    appeared, so a module-level constant after a CRD-named class inherited that
    scope and failed the build for no reason.
    """
    sample = "\n".join(
        [
            "class TestCRDValidation:",
            "    pass",
            "",
            "TIMEOUT_SECONDS = 30",
        ]
    )
    assert not scan(sample)


def test_async_def_opens_a_crd_scope() -> None:
    """``async def`` declares a scope too; the first SCOPE_DECL missed it."""
    sample = "\n".join(
        [
            "async def fetch_crd_record(client):",
            '    return await client.get("9999999999")',
        ]
    )
    found = {value for _, form, value in scan(sample) if form == "crd-mention"}
    assert found == {"9999999999"}, found


def test_scope_survives_an_implicit_continuation() -> None:
    """A dedent inside brackets is not a dedent.

    Introduced by the fix for the previous case: popping scope on physical
    indentation alone let a value dedented inside a parenthesised literal
    escape the CRD-scope heuristic completely.
    """
    sample = "\n".join(
        [
            "class TestCRDValidation:",
            "    values = (",
            "9999999999,",
            "    )",
        ]
    )
    found = {value for _, form, value in scan(sample) if form == "crd-mention"}
    assert found == {"9999999999"}, found


def test_brackets_inside_strings_and_comments_do_not_hold_scope_open() -> None:
    for sample in (
        'class TestCRDValidation:\n    s = "("\n\nTIMEOUT_SECONDS = 30',
        "class TestCRDValidation:\n    pass  # (\n\nTIMEOUT_SECONDS = 30",
        'class TestCRDValidation:\n    """doc (unclosed"""\n\nTIMEOUT_SECONDS = 30',
    ):
        assert not scan(sample), sample
