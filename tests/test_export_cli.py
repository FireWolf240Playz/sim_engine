"""Contract for roadmap 2.1b: ``eleven run ... --export csv xlsx``
(`.agents/tasks/2.1b-export-api-cli.md`).

The CLI stays free and writes every format: it already holds the run, so
nothing is simulated twice. Files are ``<base>.<ext>``, base ``eleven_export``
unless ``--export-base`` says otherwise.

Make these pass. Do not edit them.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from sim_core.cli import main
from sim_core.cli.sim import default_config
from sim_core.exports import CSV_COLUMNS, XLSX_SHEETS


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """The CLI demo topology, shortened so the suite stays quick."""
    config = default_config().model_copy(update={"seed": 3, "duration": 30.0})
    path = tmp_path / "topology.json"
    config.to_json(str(path))
    return path


def _run(config_path: Path, tmp_path: Path, *extra: str) -> int:
    return main(
        [
            "run", str(config_path),
            "--report", str(tmp_path / "report.png"),
            "--json", str(tmp_path / "summary.json"),
            *extra,
        ]
    )


def _rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8"), newline="")))


def test_run_writes_each_requested_format(
    config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "out" / "run1"  # the directory does not exist yet
    rc = _run(config_path, tmp_path, "--export", "csv", "xlsx", "json", "--export-base", str(base))
    assert rc == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    rows = _rows(base.with_suffix(".csv"))
    assert tuple(rows[0]) == CSV_COLUMNS
    assert len(rows) == summary["requests"] > 0

    workbook = load_workbook(base.with_suffix(".xlsx"))
    assert tuple(workbook.sheetnames) == XLSX_SHEETS

    exported = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    assert exported["seed"] == 3
    assert exported["summary"] == summary  # the run on screen, not a second one

    out = capsys.readouterr().out
    for suffix in (".csv", ".xlsx", ".json"):
        assert f"Export written to: {base.with_suffix(suffix)}" in out


def test_the_csv_on_disk_has_clean_line_ends(config_path: Path, tmp_path: Path) -> None:
    """Text mode on Windows turns the CSV's ``\\r\\n`` into ``\\r\\r\\n``,
    which a spreadsheet shows as a blank row after every request."""
    base = tmp_path / "log"
    assert _run(config_path, tmp_path, "--export", "csv", "--export-base", str(base)) == 0
    raw = base.with_suffix(".csv").read_bytes()
    assert b"\r\r\n" not in raw
    assert raw.count(b"\r\n") == raw.count(b"\n") > 1


def test_no_export_flag_writes_no_export(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _run(config_path, tmp_path) == 0
    assert not list(tmp_path.glob("eleven_export.*"))


def test_the_default_base_is_eleven_export_in_the_working_directory(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _run(config_path, tmp_path, "--export", "json") == 0
    assert (tmp_path / "eleven_export.json").is_file()


def test_an_unknown_format_is_a_usage_error(
    config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        _run(config_path, tmp_path, "--export", "pdf")
    assert raised.value.code == 2
    assert "invalid choice: 'pdf'" in capsys.readouterr().err


def test_a_playbook_run_exports_the_incident(config_path: Path, tmp_path: Path) -> None:
    base = tmp_path / "incident"
    rc = _run(
        config_path, tmp_path, "--playbook", "db_failover",
        "--export", "json", "--export-base", str(base),
    )
    assert rc == 0
    exported = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    # db_failover appends two events to whatever chaos the config already has
    assert len(exported["config"]["chaos"]) == len(default_config().chaos) + 2


def test_a_multi_seed_run_exports_its_typical_run(
    config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "typical"
    rc = _run(
        config_path, tmp_path, "--seeds", "3",
        "--export", "csv", "json", "--export-base", str(base),
    )
    assert rc == 0
    profile = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    exported = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    by_seed = {run["seed"]: run["summary"] for run in profile["runs"]}
    assert exported["seed"] in by_seed
    assert exported["summary"] == by_seed[exported["seed"]]
    assert len(_rows(base.with_suffix(".csv"))) == exported["summary"]["requests"]
    out = capsys.readouterr().out
    for suffix in (".csv", ".json"):
        path = base.with_suffix(suffix)
        assert f"Export (typical run, seed {exported['seed']}) written to: {path}" in out


def test_demo_takes_the_export_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # demo writes its report and summary here too
    assert main(["demo", "--export", "csv", "--export-base", str(tmp_path / "demo")]) == 0
    assert len(_rows(tmp_path / "demo.csv")) > 0
