import json
import sys
from pathlib import Path

import pytest
from roster_factory import future_date, recent_date, row, write_roster

from ingest import adv_schema as adv
from ingest.anonymize import FICTIONAL
from ingest.select_firms import (
    BucketShortfallError,
    classify,
    main,
    months_since,
    run,
)

ROOT = Path(__file__).resolve().parent.parent


def test_empty_roster_exits(tmp_path):
    out = tmp_path / "out.json"
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        run(str(empty), str(out))
    header_only = tmp_path / "header_only.csv"
    write_roster(header_only, [])
    with pytest.raises(BucketShortfallError):
        run(str(header_only), str(out))


def test_header_mismatch_exits(tmp_path):
    roster = tmp_path / "roster.csv"
    out = tmp_path / "out.json"
    header = list(adv.EXPECTED_HEADERS)
    header[200] = "NOT THE PINNED COLUMN"
    write_roster(roster, [row()], header=header)
    with pytest.raises(SystemExit) as exc:
        run(str(roster), str(out))
    assert "column 200" in str(exc.value)


def test_selects_one_of_each_bucket(tmp_path):
    roster = tmp_path / "roster.csv"
    out = tmp_path / "out.json"
    rows = [
        row(crd="1000001", name="FIRM A"),
        row(crd="1000002", name="FIRM B"),
        row(crd="1000003", name="FIRM C", disciplinary=True),
        row(crd="1000004", name="FIRM D", status_date=recent_date()),
        row(crd="1000005", name="FIRM E", website_count="0"),
        row(crd="1000006", name="FIRM F", aum="50000000"),
        row(crd="1000007", name="FIRM G", status_date="01/01/2024"),
    ]
    write_roster(roster, rows)
    firms, summary = run(str(roster), str(out))
    assert [f["selection_bucket"] for f in firms] == [
        "clean", "clean", "disciplinary", "recent", "thin_web",
    ]
    assert [f["crd"] for f in firms] == [
        "1000001", "1000002", "1000003", "1000004", "1000005",
    ]
    assert summary["scanned"] == 5
    assert out.read_text(encoding="utf-8").endswith("\n")


def test_short_row_skipped_and_counted(tmp_path):
    roster = tmp_path / "roster.csv"
    out = tmp_path / "out.json"
    rows = [
        row(crd="1000006", name="TRUNCATED FIRM")[:20],
        row(crd="1000001", name="FIRM A"),
        row(crd="1000002", name="FIRM B"),
        row(crd="1000003", name="FIRM C", disciplinary=True),
        row(crd="1000004", name="FIRM D", status_date=recent_date()),
        row(crd="1000005", name="FIRM E", website_count="0"),
    ]
    write_roster(roster, rows)
    firms, summary = run(str(roster), str(out))
    assert summary["short"] == 1
    assert len(firms) == 5


def test_only_short_rows_raises_bucket_shortfall(tmp_path):
    roster = tmp_path / "roster.csv"
    out = tmp_path / "out.json"
    rows = [row(crd="1000001", name="SHORT FIRM")[:20] for _ in range(3)]
    write_roster(roster, rows)
    with pytest.raises(BucketShortfallError):
        run(str(roster), str(out))
    assert not out.exists()


def test_empty_crd_skipped(tmp_path):
    roster = tmp_path / "roster.csv"
    out = tmp_path / "out.json"
    rows = [
        row(crd="", name="NO CRD FIRM"),
        row(crd="1000001", name="FIRM A"),
        row(crd="1000002", name="FIRM B"),
        row(crd="1000003", name="FIRM C", disciplinary=True),
        row(crd="1000004", name="FIRM D", status_date=recent_date()),
        row(crd="1000005", name="FIRM E", website_count="0"),
    ]
    write_roster(roster, rows)
    firms, summary = run(str(roster), str(out))
    assert summary["no_crd"] == 1
    assert len(firms) == 5


def test_wrong_form_version_skipped(tmp_path):
    roster = tmp_path / "roster.csv"
    out = tmp_path / "out.json"
    rows = [
        row(crd="1000006", name="OLD FORM FIRM", form_version="03/2020"),
        row(crd="1000001", name="FIRM A"),
        row(crd="1000002", name="FIRM B"),
        row(crd="1000003", name="FIRM C", disciplinary=True),
        row(crd="1000004", name="FIRM D", status_date=recent_date()),
        row(crd="1000005", name="FIRM E", website_count="0"),
    ]
    write_roster(roster, rows)
    firms, summary = run(str(roster), str(out))
    assert summary["form_version"] == 1
    assert "1000006" not in [f["crd"] for f in firms]


def test_aum_out_of_band_skipped(tmp_path):
    roster = tmp_path / "roster.csv"
    out = tmp_path / "out.json"
    rows = [
        row(crd="1000006", name="SMALL FIRM", aum="50000000"),
        row(crd="1000001", name="FIRM A"),
        row(crd="1000002", name="FIRM B"),
        row(crd="1000003", name="FIRM C", disciplinary=True),
        row(crd="1000004", name="FIRM D", status_date=recent_date()),
        row(crd="1000005", name="FIRM E", website_count="0"),
    ]
    write_roster(roster, rows)
    firms, summary = run(str(roster), str(out))
    assert summary["aum_out_of_band"] == 1
    assert "1000006" not in [f["crd"] for f in firms]


def test_duplicate_crd_skipped(tmp_path):
    roster = tmp_path / "roster.csv"
    out = tmp_path / "out.json"
    rows = [
        row(crd="1000001", name="FIRM A"),
        row(crd="1000001", name="FIRM A DUPLICATE"),
        row(crd="1000002", name="FIRM B"),
        row(crd="1000003", name="FIRM C", disciplinary=True),
        row(crd="1000004", name="FIRM D", status_date=recent_date()),
        row(crd="1000005", name="FIRM E", website_count="0"),
    ]
    write_roster(roster, rows)
    firms, _ = run(str(roster), str(out))
    crds = [f["crd"] for f in firms]
    assert crds.count("1000001") == 1
    assert len(firms) == 5


def test_future_dated_registration_not_recent():
    future = row(status_date=future_date())
    assert classify(future) is None
    assert months_since(future_date()) is None


def test_invalid_status_date_skipped(tmp_path):
    roster = tmp_path / "roster.csv"
    out = tmp_path / "out.json"
    rows = [
        row(crd="1000006", name="BAD DATE FIRM", status_date="not-a-date"),
        row(crd="1000001", name="FIRM A"),
        row(crd="1000002", name="FIRM B"),
        row(crd="1000003", name="FIRM C", disciplinary=True),
        row(crd="1000004", name="FIRM D", status_date=recent_date()),
        row(crd="1000005", name="FIRM E", website_count="0"),
    ]
    write_roster(roster, rows)
    firms, summary = run(str(roster), str(out))
    assert summary["invalid_status_date"] == 1
    assert "1000006" not in [f["crd"] for f in firms]
    assert len(firms) == 5


def test_classify_buckets():
    assert classify(row(disciplinary=True)) == "disciplinary"
    assert classify(row(status_date=recent_date())) == "recent"
    assert classify(row(website_count="0")) == "thin_web"
    assert classify(row(status_date="01/01/2010")) == "clean"


def test_partial_bucket_shortfall_exits_nonzero(tmp_path, monkeypatch):
    roster = tmp_path / "roster.csv"
    out = tmp_path / "out.json"
    write_roster(roster, [row(crd="1000001", name="FIRM A")])
    with pytest.raises(BucketShortfallError):
        run(str(roster), str(out))
    monkeypatch.setattr(sys, "argv", ["select_firms", str(roster), "--out", str(out)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_committed_ground_truth_is_fictionalized():
    path = ROOT / "agents" / "attest_orchestrator" / "ground_truth.json"
    firms = json.loads(path.read_text(encoding="utf-8"))
    assert len(firms) == len(FICTIONAL)
    fictional_crds = {f["crd"] for f in FICTIONAL}
    for i, firm in enumerate(firms):
        assert firm["crd"] in fictional_crds
        assert firm["name"] == FICTIONAL[i]["name"]


def test_committed_roster_version():
    import importlib.util

    # Load registry.py by path: importing the package would execute
    # agents/attest_orchestrator/__init__.py, which pulls in google.adk —
    # not installed in CI. registry.py itself is stdlib-only.
    registry_path = ROOT / "agents" / "attest_orchestrator" / "registry.py"
    spec = importlib.util.spec_from_file_location("registry", registry_path)
    registry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registry)

    path = ROOT / "agents" / "attest_orchestrator" / "ground_truth.json"
    firms = json.loads(path.read_text(encoding="utf-8"))
    assert registry.content_version(firms) == "32a6587ad82b"