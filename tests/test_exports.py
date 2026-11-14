"""Contract for roadmap 2.1, the export center (`.agents/tasks/2.1-export-engine.md`).

Measured before this file was written (demo and a 4-node stack, seed 7):

- The per-request data is not in ``summary()``. It lives in the collector's
  records, and two things a power user asks first were not recorded at all:
  how often a request was retried, and at which node a failed one gave up.
- At the API's limit of 200,000 requests the per-request CSV is 15 MB
  (75 bytes a row) and takes 0.85 s to build, on top of a 4.8 s run.
- openpyxl stores a string that starts with ``=`` as a formula: a node named
  ``=1+1`` became a cell Excel would evaluate.
- openpyxl stamps the save time into the file, so two exports of one run
  differ in bytes (``docProps/core.xml``, zip entry times). Equal runs give
  equal cells, and that is what is pinned here.

Make these pass. Do not edit them.
"""

from __future__ import annotations

import ast
import csv
import io
import json
import math
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

from sim_core import CloudSimulator, SimulationConfig
from sim_core.exports import (
    CSV_COLUMNS,
    EXPORT_FORMATS,
    FINDINGS_COLUMNS,
    PER_NODE_COLUMNS,
    XLSX_SHEETS,
    render_export,
    spreadsheet_safe,
    to_csv,
    to_json,
    to_xlsx,
)

HOSTILE = '=HYPERLINK("http://evil.example","click")'


def _stack(
    *,
    worker: dict[str, Any] | None = None,
    worker_name: str = "worker",
    chaos: list[dict[str, Any]] | None = None,
    traffic: dict[str, Any] | None = None,
    duration: float = 60.0,
) -> SimulationConfig:
    return SimulationConfig.model_validate(
        {
            "seed": 7,
            "duration": duration,
            "sla_target": 1.0,
            "topology": {
                "nodes": [
                    {"name": "lb", "role": "load_balancer", "max_capacity": 100,
                     "service_time": 0.005, "cost_per_hour": 0.01},
                    {"name": worker_name, "role": "worker", "max_capacity": 10,
                     "service_time": 0.05, "cost_per_hour": 0.05, **(worker or {})},
                    {"name": "db", "role": "database", "max_capacity": 10,
                     "service_time": 0.05, "cost_per_hour": 0.1},
                ],
                "edges": [
                    {"source": "lb", "target": worker_name},
                    {"source": worker_name, "target": "db"},
                ],
            },
            "traffic": traffic or {"base_rps": 20.0, "duration": duration},
            "chaos": chaos or [],
        }
    )


_STARVED = {"max_capacity": 1, "service_time": 5.0, "timeout": 0.2, "retry_limit": 2,
            "retry_backoff": 0.05}
_BRIEF = {"base_rps": 5.0, "duration": 20.0}  # traffic ends early: every request finishes
_DB_DEAD = [{"event_type": "component_failure", "target": "db", "intensity": 1.0,
             "start_time": 40.0, "duration": 1000.0, "interval": 1000.0}]


def _run(
    config: SimulationConfig, *, suggest: bool = False
) -> tuple[CloudSimulator, dict[str, Any]]:
    simulator = CloudSimulator(config)
    return simulator, simulator.run(suggest=suggest)


def _export_args(config: SimulationConfig, *, suggest: bool = True) -> dict[str, Any]:
    simulator, summary = _run(config, suggest=suggest)
    return {
        "summary": summary,
        "config": config.model_dump(mode="json"),
        "request_log": simulator.collector.request_log(),
        "seed": simulator.seed,
    }


# ---------------------------------------------------------------------------
# the request log: one plain row per recorded request
# ---------------------------------------------------------------------------

ROW_KEYS = {
    "request_id", "t", "end", "latency", "service_time", "wait_time", "outcome",
    "sla_met", "retries", "cache_hit", "failed_at", "path",
}


def test_request_log_has_one_row_per_recorded_request() -> None:
    simulator, summary = _run(_stack())
    log = simulator.collector.request_log()
    assert len(log) == summary["requests"] > 1000
    assert all(set(row) == ROW_KEYS for row in log)
    assert len({row["request_id"] for row in log}) == len(log)


def test_request_log_is_in_arrival_order() -> None:
    simulator, _ = _run(_stack())
    arrivals = [row["t"] for row in simulator.collector.request_log()]
    assert arrivals == sorted(arrivals)
    # the collector itself records at completion, which is a different order
    assert [r.start for r in simulator.collector.requests] != arrivals


def test_request_log_rows_are_plain_data() -> None:
    simulator, _ = _run(_stack())
    row = simulator.collector.request_log()[0]
    json.dumps(row, allow_nan=False)  # no dataclass, numpy scalar or NaN inside
    assert isinstance(row["path"], list)
    assert type(row["retries"]) is int and type(row["sla_met"]) is bool


def test_latency_is_service_plus_wait() -> None:
    simulator, _ = _run(_stack(worker={"max_capacity": 2}))
    log = simulator.collector.request_log()
    assert any(row["wait_time"] > 0.01 for row in log)  # this stack does queue
    for row in log:
        assert row["latency"] == pytest.approx(row["end"] - row["t"])
        assert row["wait_time"] >= 0.0
        assert row["service_time"] + row["wait_time"] == pytest.approx(row["latency"])


def test_path_is_the_nodes_served_in_order() -> None:
    simulator, _ = _run(_stack())
    by_id = {r.request_id: r for r in simulator.collector.requests}
    for row in simulator.collector.request_log():
        assert row["path"] == list(by_id[row["request_id"]].component_times)
    completed = [row for row in simulator.collector.request_log() if row["outcome"] == "completed"]
    assert {tuple(row["path"]) for row in completed} == {("lb", "worker", "db")}


def test_outcomes_add_up_to_the_summary() -> None:
    simulator, summary = _run(_stack(chaos=_DB_DEAD))
    log = simulator.collector.request_log()
    count = {name: sum(1 for row in log if row["outcome"] == name)
             for name in ("completed", "failed", "overdue")}
    assert sum(count.values()) == len(log)
    assert count["overdue"] == summary["overdue_requests"] > 0
    assert count["failed"] + count["overdue"] == summary["failed_requests"]
    assert count["completed"] == summary["requests"] - summary["failed_requests"]
    for row in log:
        if row["outcome"] == "overdue":
            assert row["end"] == 60.0 and not row["sla_met"]


def test_a_failed_request_says_where_it_gave_up_and_how_often_it_retried() -> None:
    simulator, summary = _run(_stack(worker=_STARVED, traffic=_BRIEF))
    assert summary["unfinished_requests"] == 0  # so every retry belongs to a row
    log = simulator.collector.request_log()
    failed = [row for row in log if row["outcome"] == "failed"]
    assert len(failed) > 50
    for row in failed:
        assert row["failed_at"] == "worker"
        assert row["retries"] == 2  # retry_limit: three attempts, two retries
        assert row["path"] == ["lb"]  # served by the balancer, never by the worker
    assert all(row["failed_at"] is None for row in log if row["outcome"] == "completed")
    assert sum(row["retries"] for row in log) == summary["total_retries"]


def test_a_healthy_run_has_no_retries_and_no_failure_site() -> None:
    simulator, _ = _run(_stack())
    log = simulator.collector.request_log()
    assert {row["retries"] for row in log} == {0}
    assert {row["failed_at"] for row in log} == {None}


def test_recording_retries_does_not_move_the_run() -> None:
    """The new per-request fields are bookkeeping: no extra random draw."""
    _, first = _run(_stack(worker=_STARVED, traffic=_BRIEF))
    _, again = _run(_stack(worker=_STARVED, traffic=_BRIEF))
    assert first == again
    assert first["requests"] == 72 and first["total_retries"] == 134  # seed 7, measured before 2.1


# ---------------------------------------------------------------------------
# CSV: the per-request log
# ---------------------------------------------------------------------------

def _read(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text, newline="")))


def test_csv_columns_are_the_documented_ones() -> None:
    assert CSV_COLUMNS == (
        "request_id", "t", "latency_ms", "service_ms", "wait_ms", "outcome",
        "failed", "sla_ok", "retries", "cache_hit", "failed_at", "path",
    )
    simulator, _ = _run(_stack())
    header = to_csv(simulator.collector.request_log()).splitlines()[0]
    assert header == ",".join(CSV_COLUMNS)


def test_csv_has_one_row_per_request() -> None:
    """Roadmap accept: row count == completed + failed requests."""
    simulator, summary = _run(_stack(chaos=_DB_DEAD))
    rows = _read(to_csv(simulator.collector.request_log()))
    assert len(rows) == summary["requests"]
    assert sum(int(row["failed"]) for row in rows) == summary["failed_requests"]
    assert sum(int(row["sla_ok"]) for row in rows) == round(
        summary["sla_compliance"] * summary["requests"]
    )


def test_csv_values_follow_the_log() -> None:
    simulator, _ = _run(_stack(worker=_STARVED, traffic=_BRIEF))
    log = simulator.collector.request_log()
    for row, cells in zip(log, _read(to_csv(log)), strict=True):
        assert cells["request_id"] == row["request_id"]
        assert float(cells["t"]) == pytest.approx(row["t"], abs=1e-6)
        assert float(cells["latency_ms"]) == pytest.approx(row["latency"] * 1000, abs=1e-3)
        assert float(cells["service_ms"]) == pytest.approx(row["service_time"] * 1000, abs=1e-3)
        assert float(cells["wait_ms"]) == pytest.approx(row["wait_time"] * 1000, abs=1e-3)
        assert cells["outcome"] == row["outcome"]
        assert cells["failed"] == ("0" if row["outcome"] == "completed" else "1")
        assert cells["sla_ok"] == ("1" if row["sla_met"] else "0")
        assert int(cells["retries"]) == row["retries"]
        assert cells["failed_at"] == (row["failed_at"] or "")
        assert cells["path"] == ">".join(row["path"])


def test_csv_cache_hit_is_blank_when_no_cache_was_reached() -> None:
    simulator, _ = _run(_stack())  # no cache in this stack
    cells = _read(to_csv(simulator.collector.request_log()))
    assert {row["cache_hit"] for row in cells} == {""}
    rows = [
        {"request_id": "a", "t": 0.0, "end": 1.0, "latency": 1.0, "service_time": 1.0,
         "wait_time": 0.0, "outcome": "completed", "sla_met": True, "retries": 0,
         "cache_hit": hit, "failed_at": None, "path": ["c"]}
        for hit in (True, False, None)
    ]
    assert [cells["cache_hit"] for cells in _read(to_csv(rows))] == ["1", "0", ""]


def test_csv_is_the_same_text_for_the_same_run() -> None:
    first = to_csv(_run(_stack(chaos=_DB_DEAD))[0].collector.request_log())
    again = to_csv(_run(_stack(chaos=_DB_DEAD))[0].collector.request_log())
    assert first == again
    assert first.endswith("\r\n") and "\r\r\n" not in first  # RFC 4180 line ends


def test_csv_of_no_requests_is_just_the_header() -> None:
    assert to_csv([]) == ",".join(CSV_COLUMNS) + "\r\n"


def test_csv_neutralises_a_node_name_a_spreadsheet_would_run() -> None:
    config = _stack(worker=_STARVED, worker_name=HOSTILE, traffic=_BRIEF)
    simulator, _ = _run(config)
    rows = _read(to_csv(simulator.collector.request_log()))
    failed_at = {cells["failed_at"] for cells in rows} - {""}
    assert failed_at == {"'" + HOSTILE}  # the name survives, behind a quote
    for cells in rows:
        for value in cells.values():
            assert not value.startswith(("=", "+", "-", "@", "\t", "\r"))


@pytest.mark.parametrize(
    ("text", "safe"),
    [
        ("worker", "worker"),
        ("=1+1", "'=1+1"),
        ("+1", "'+1"),
        ("-db", "'-db"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tcmd", "'\tcmd"),
        ("\rcmd", "'\rcmd"),
        ("a=b", "a=b"),
        ("", ""),
    ],
)
def test_spreadsheet_safe(text: str, safe: str) -> None:
    assert spreadsheet_safe(text) == safe


# ---------------------------------------------------------------------------
# JSON: the whole run, replayable
# ---------------------------------------------------------------------------

def test_json_carries_the_summary_the_config_and_the_seed() -> None:
    args = _export_args(_stack(chaos=_DB_DEAD))
    text = to_json(args["summary"], args["config"], seed=args["seed"])
    payload = json.loads(text)
    assert payload["eleven_export"] == {"version": 1, "format": "json"}
    assert payload["seed"] == 7
    assert payload["summary"] == json.loads(json.dumps(args["summary"]))
    # the config in the file is a config: it loads, and replays the same run
    replay = CloudSimulator(SimulationConfig.model_validate(payload["config"])).run()
    assert replay == args["summary"]
    assert text.endswith("\n")


def test_json_is_strict_and_the_same_text_for_the_same_run() -> None:
    args = _export_args(_stack())
    text = to_json(args["summary"], args["config"], seed=args["seed"])
    assert text == to_json(args["summary"], args["config"], seed=args["seed"])
    odd = {**args["summary"], "p95_latency": math.inf, "avg_latency": math.nan}
    strict = json.loads(to_json(odd, args["config"]), parse_constant=pytest.fail)
    assert strict["summary"]["p95_latency"] is None
    assert strict["summary"]["avg_latency"] is None
    assert strict["seed"] is None


def test_json_does_not_touch_its_inputs() -> None:
    args = _export_args(_stack())
    before = json.dumps([args["summary"], args["config"]], sort_keys=True)
    to_json(args["summary"], args["config"], seed=1)
    to_xlsx(args["summary"], args["config"], seed=1)
    assert json.dumps([args["summary"], args["config"]], sort_keys=True) == before


# ---------------------------------------------------------------------------
# Excel: Summary / Per-node / Findings
# ---------------------------------------------------------------------------

def _sheets(data: bytes) -> dict[str, list[tuple[Any, ...]]]:
    workbook = load_workbook(io.BytesIO(data))
    return {ws.title: [tuple(row) for row in ws.iter_rows(values_only=True)] for ws in workbook}


def test_xlsx_opens_with_the_three_sheets() -> None:
    """Roadmap accept: the workbook opens and its sheet names are correct."""
    args = _export_args(_stack(chaos=_DB_DEAD))
    data = to_xlsx(args["summary"], args["config"], seed=args["seed"])
    assert data[:2] == b"PK"
    assert XLSX_SHEETS == ("Summary", "Per-node", "Findings")
    assert tuple(_sheets(data)) == XLSX_SHEETS


def test_xlsx_summary_sheet_states_the_headline_numbers() -> None:
    args = _export_args(_stack(chaos=_DB_DEAD))
    summary = args["summary"]
    rows = _sheets(to_xlsx(summary, args["config"], seed=args["seed"]))["Summary"]
    assert rows[0][:2] == ("Metric", "Value")
    values = {row[0]: row[1] for row in rows[1:]}
    assert values["Resilience score"] == summary["resilience_score"]
    assert values["Verdict"] == summary["score_explanation"]["band"]
    assert values["Cost grade"] == summary["cost_grade"]
    assert values["Seed"] == 7
    assert values["Requests"] == summary["requests"]
    assert values["Failed requests"] == summary["failed_requests"]
    assert values["Completion rate"] == pytest.approx(summary["completion_rate"])
    assert values["SLA compliance"] == pytest.approx(summary["sla_compliance"])
    assert values["SLA target (s)"] == 1.0
    assert values["P95 latency (s)"] == pytest.approx(summary["p95_latency"])
    assert values["Total cost (USD)"] == pytest.approx(summary["total_cost"])
    assert values["Monthly cost, same load 24/7 (USD)"] == pytest.approx(
        summary["cost_extrapolation"]["month"]
    )
    # numbers are numbers, so a spreadsheet can compute with them
    assert type(values["Requests"]) is int
    assert isinstance(values["Resilience score"], float)


def test_xlsx_per_node_sheet_has_one_row_per_node() -> None:
    config = _stack(chaos=_DB_DEAD)
    args = _export_args(config)
    summary = args["summary"]
    rows = _sheets(to_xlsx(summary, args["config"], seed=args["seed"]))["Per-node"]
    assert rows[0] == PER_NODE_COLUMNS
    assert PER_NODE_COLUMNS[:5] == (
        "Node", "Role", "Capacity", "Service time (s)", "Cost per slot-hour (USD)",
    )
    for label in ("Mean utilization", "P95 queue", "Waited requests", "Utilization status",
                  "Verified status", "Verified capacity", "Run cost (USD)"):
        assert label in PER_NODE_COLUMNS
    assert [row[0] for row in rows[1:]] == ["lb", "worker", "db"]  # config order
    db = dict(zip(PER_NODE_COLUMNS, rows[3], strict=True))
    sizing = summary["component_sizing"]["db"]
    assert (db["Role"], db["Capacity"], db["Service time (s)"]) == ("database", 10, 0.05)
    assert db["Cost per slot-hour (USD)"] == 0.1
    assert db["Mean utilization"] == pytest.approx(sizing["mean_utilization"])
    assert db["Waited requests"] == sizing["waited_requests"]
    assert db["Utilization status"] == sizing["status"]
    assert db["Verified status"] == sizing["verified_status"]
    assert db["Verified capacity"] == sizing["verified_capacity"]
    assert db["Run cost (USD)"] == pytest.approx(summary["cost_breakdown_by_component"]["db"])


def test_xlsx_findings_sheet_lists_every_finding() -> None:
    args = _export_args(_stack(chaos=_DB_DEAD))
    summary = args["summary"]
    rows = _sheets(to_xlsx(summary, args["config"], seed=args["seed"]))["Findings"]
    assert rows[0] == FINDINGS_COLUMNS == (
        "Severity", "Id", "Node", "Title", "Finding", "Why", "Impact", "Recommendation",
    )
    assert len(rows) - 1 == len(summary["findings"]) > 0
    for cells, finding in zip(rows[1:], summary["findings"], strict=True):
        assert cells[0] == finding["severity"] and cells[1] == finding["id"]
        assert cells[2] == finding.get("node")
        assert cells[4] == finding["text"]


def test_xlsx_works_without_a_plan_and_without_requests() -> None:
    """A verification run carries no verified labels; an idle run no numbers."""
    args = _export_args(_stack(), suggest=False)
    rows = _sheets(to_xlsx(args["summary"], args["config"]))["Per-node"]
    db = dict(zip(PER_NODE_COLUMNS, rows[3], strict=True))
    assert db["Verified status"] is None and db["Utilization status"] is not None

    quiet = _stack(traffic={"base_rps": 0.001, "duration": 1.0}, duration=5.0)
    args = _export_args(quiet)
    assert args["summary"]["requests"] == 0
    sheets = _sheets(to_xlsx(args["summary"], args["config"], seed=args["seed"]))
    values = {row[0]: row[1] for row in sheets["Summary"][1:]}
    assert values["Requests"] == 0 and values["Resilience score"] is None
    assert len(sheets["Per-node"]) == 4 and len(sheets["Findings"]) == 1  # headers stay


def test_xlsx_never_stores_a_formula() -> None:
    """The hostile name reaches three sheets: as a node, in a finding's node
    column, and at the start of that finding's sentence."""
    config = _stack(worker=_STARVED, worker_name=HOSTILE)  # pinned all run: a weak-link finding
    args = _export_args(config)
    named = [f for f in args["summary"]["findings"] if f.get("node") == HOSTILE]
    assert named and named[0]["text"].startswith(HOSTILE)
    workbook = load_workbook(io.BytesIO(to_xlsx(args["summary"], args["config"])))
    seen = 0
    for sheet in workbook:
        for row in sheet.iter_rows():
            for cell in row:
                assert cell.data_type != "f", (sheet.title, cell.coordinate, cell.value)
                if isinstance(cell.value, str) and HOSTILE in cell.value:
                    seen += 1
    assert seen >= 3
    assert workbook["Per-node"]["A3"].value == HOSTILE  # stored as text, unchanged


def test_xlsx_has_the_same_cells_for_the_same_run() -> None:
    args = _export_args(_stack(chaos=_DB_DEAD))
    first = _sheets(to_xlsx(args["summary"], args["config"], seed=args["seed"]))
    again = _sheets(to_xlsx(args["summary"], args["config"], seed=args["seed"]))
    assert first == again


# ---------------------------------------------------------------------------
# the format registry and the one dispatch every face uses
# ---------------------------------------------------------------------------

def test_the_registry_names_three_formats_and_their_tier() -> None:
    assert list(EXPORT_FORMATS) == ["csv", "json", "xlsx"]
    assert {name: f.extension for name, f in EXPORT_FORMATS.items()} == {
        "csv": "csv", "json": "json", "xlsx": "xlsx",
    }
    assert EXPORT_FORMATS["csv"].media_type == "text/csv; charset=utf-8"
    assert EXPORT_FORMATS["json"].media_type == "application/json"
    assert EXPORT_FORMATS["xlsx"].media_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    # roadmap 2.1: free = CSV + JSON, paid = Excel (the split is the founder's to confirm)
    assert {name: f.tier for name, f in EXPORT_FORMATS.items()} == {
        "csv": "free", "json": "free", "xlsx": "paid",
    }
    # the CSV needs no summary, so a caller can skip the Fix-it plan for it
    assert {name: f.uses_summary for name, f in EXPORT_FORMATS.items()} == {
        "csv": False, "json": True, "xlsx": True,
    }
    assert all(f.name == name and f.label for name, f in EXPORT_FORMATS.items())


def test_render_export_is_each_format_as_bytes() -> None:
    args = _export_args(_stack(chaos=_DB_DEAD))
    assert render_export("csv", **args) == to_csv(args["request_log"]).encode("utf-8")
    assert render_export("json", **args) == to_json(
        args["summary"], args["config"], seed=args["seed"]
    ).encode("utf-8")
    assert _sheets(render_export("xlsx", **args)) == _sheets(
        to_xlsx(args["summary"], args["config"], seed=args["seed"])
    )
    with pytest.raises(ValueError, match=r"unknown export format 'pdf'.*csv, json, xlsx"):
        render_export("pdf", **args)


# ---------------------------------------------------------------------------
# the module stays a pure one
# ---------------------------------------------------------------------------

def test_exports_imports_nothing_from_the_engine_and_openpyxl_only_lazily() -> None:
    """Same rule as score / findings / profile: plain dicts in, text or bytes
    out. openpyxl loads inside ``to_xlsx``, so importing the engine (or
    exporting a CSV) never pulls it in."""
    source = Path(__file__).resolve().parents[1] / "sim_core" / "exports.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            top_level.add((node.module or "").split(".")[0])
    assert "sim_core" not in top_level
    assert "openpyxl" not in top_level
    assert "pandas" not in top_level and "numpy" not in top_level
