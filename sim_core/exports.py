"""One run, written out: a per-request CSV, a replayable JSON, an Excel workbook.

Roadmap 2.1, the export center. Everything here is a *pure function over
plain data*: the ``MetricsCollector.summary()`` dict, the config as a plain
dict (``model_dump(mode="json")``) and the collector's ``request_log()`` rows.
Same inputs in, same text or the same cells out, which is what lets the CLI
and the API call these functions verbatim and hand back the same file.

Public functions:

- :func:`to_csv` - the per-request log, one row per recorded request.
- :func:`to_json` - the summary, the config and the seed: enough to replay.
- :func:`to_xlsx` - a workbook with Summary, Per-node and Findings sheets.
- :func:`render_export` - the one dispatch every face uses, bytes out.
- :func:`spreadsheet_safe` - the guard that keeps a CSV cell from being run.

:data:`EXPORT_FORMATS` is the registry (extension, media type, tier) a caller
needs to offer and serve a format without knowing anything else about it.

This module deliberately imports **nothing** from the rest of ``sim_core``,
and ``openpyxl`` only inside :func:`to_xlsx`: importing the engine, or
exporting a CSV, never pulls a spreadsheet library in.
"""

from __future__ import annotations

import csv
import io
import json
import math
import numbers
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

# ---------------------------------------------------------------------------
# Column and sheet names - the single source of truth for every export's
# shape. A consumer's parser depends on them; change deliberately.
# ---------------------------------------------------------------------------

#: Header of the per-request CSV. Times are milliseconds here (the unit a
#: latency is read in); ``t`` is the arrival time in seconds.
CSV_COLUMNS: tuple[str, ...] = (
    "request_id",
    "t",
    "latency_ms",
    "service_ms",
    "wait_ms",
    "outcome",
    "failed",
    "sla_ok",
    "retries",
    "cache_hit",
    "failed_at",
    "path",
)

#: Sheets of the workbook, in tab order.
XLSX_SHEETS: tuple[str, ...] = ("Summary", "Per-node", "Findings")

SUMMARY_COLUMNS: tuple[str, ...] = ("Metric", "Value")

#: The first five come from the config (what was asked for), the rest from
#: the run (what happened), so a row reads left to right as cause then effect.
PER_NODE_COLUMNS: tuple[str, ...] = (
    "Node",
    "Role",
    "Capacity",
    "Service time (s)",
    "Cost per slot-hour (USD)",
    "Mean utilization",
    "P95 queue",
    "Waited requests",
    "Utilization status",
    "Verified status",
    "Verified capacity",
    "Run cost (USD)",
)

FINDINGS_COLUMNS: tuple[str, ...] = (
    "Severity",
    "Id",
    "Node",
    "Title",
    "Finding",
    "Why",
    "Impact",
    "Recommendation",
)

#: ``FINDINGS_COLUMNS`` as keys of a finding dict, in the same order.
_FINDING_KEYS: tuple[str, ...] = (
    "severity",
    "id",
    "node",
    "title",
    "text",
    "why",
    "impact",
    "recommendation",
)

#: A spreadsheet treats a cell that starts with one of these as a formula (or,
#: for tab and carriage return, lets what follows start one).
_FORMULA_LEADS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")

#: Control characters XML cannot carry at all: openpyxl refuses a cell holding
#: one, so they are dropped instead of failing the whole workbook over a name.
_UNWRITABLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

#: Widest a column is sized to; a finding's sentence is far longer than a
#: screen and is read in the cell, not across it.
_MAX_COLUMN_WIDTH = 60


# ---------------------------------------------------------------------------
# CSV: the per-request log
# ---------------------------------------------------------------------------


def spreadsheet_safe(text: str) -> str:
    """Return *text* in a form a spreadsheet shows instead of evaluating.

    A CSV has no cell types: Excel, Sheets and LibreOffice decide from the
    first character, and a node named ``=HYPERLINK(...)`` would be run on
    open. A leading single quote is the convention all three read as "this is
    text", and it keeps the name itself readable behind it.

    Only for the CSV. The workbook stores its strings as typed text cells, so
    the text there needs no change.
    """
    if text.startswith(_FORMULA_LEADS):
        return "'" + text
    return text


def _flag(value: Any) -> str:
    """A boolean as ``1``/``0``: every tool parses that, not every one ``True``."""
    return "1" if value else "0"


def to_csv(request_log: Iterable[Mapping[str, Any]]) -> str:
    """The per-request log as CSV text: a header, then one row per request.

    Rows keep the order they are given in (``request_log()`` hands them over
    in arrival order). Numbers are written at a fixed precision, microseconds
    for the arrival time and for the millisecond columns, so the same run is
    the same text and a diff of two exports shows real differences only.

    ``cache_hit`` is blank when the request never reached a cache: that is
    "no answer", which ``0`` would turn into a miss. Line ends are ``\\r\\n``
    (RFC 4180), so the text must be written to a file opened with
    ``newline=""`` or as bytes.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for row in request_log:
        cache_hit = row["cache_hit"]
        writer.writerow(
            (
                spreadsheet_safe(str(row["request_id"])),
                f"{row['t']:.6f}",
                f"{row['latency'] * 1000:.3f}",
                f"{row['service_time'] * 1000:.3f}",
                f"{row['wait_time'] * 1000:.3f}",
                spreadsheet_safe(str(row["outcome"])),
                _flag(row["outcome"] != "completed"),
                _flag(row["sla_met"]),
                str(int(row["retries"])),
                "" if cache_hit is None else _flag(cache_hit),
                spreadsheet_safe(str(row["failed_at"] or "")),
                spreadsheet_safe(">".join(str(name) for name in row["path"])),
            )
        )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# JSON: the whole run, replayable
# ---------------------------------------------------------------------------


def _strict(value: Any) -> Any:
    """A copy of *value* with every non-finite float, at any depth, as None.

    ``NaN`` and ``Infinity`` are not JSON; a strict parser rejects the whole
    file over one of them. Building a copy is also what keeps the caller's
    dicts untouched.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {key: _strict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strict(item) for item in value]
    return value


def to_json(
    summary: Mapping[str, Any], config: Mapping[str, Any], *, seed: int | None = None
) -> str:
    """The run as one strict JSON document: what was asked, and what came out.

    ``config`` is stored whole and unchanged in shape, so it loads back as a
    config and, with ``seed``, replays the very run ``summary`` describes.
    ``seed`` is separate from the config because a config may carry none: the
    simulator then draws one, and without it the file could not be replayed.

    Keys keep their given order (no sorting), so the same run is the same
    text. The ``eleven_export`` header names the layout's version for whoever
    reads the file later.
    """
    payload: dict[str, Any] = {
        "eleven_export": {"version": 1, "format": "json"},
        "seed": seed,
        "config": _strict(config),
        "summary": _strict(summary),
    }
    return json.dumps(payload, indent=2, allow_nan=False) + "\n"


# ---------------------------------------------------------------------------
# Excel: Summary / Per-node / Findings
# ---------------------------------------------------------------------------


def _cell_value(value: Any) -> Any:
    """*value* as something a workbook cell can hold, numbers kept as numbers.

    A non-finite float has no cell form (the file would not open), and "no
    value" is an empty cell, never a ``0`` someone would then average.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        return number if math.isfinite(number) else None
    return _UNWRITABLE.sub("", str(value))


def _summary_rows(summary: Mapping[str, Any], seed: int | None) -> list[tuple[str, Any]]:
    """The headline numbers, verdict first, then volume, latency and cost."""
    explanation = summary.get("score_explanation") or {}
    projection = summary.get("cost_extrapolation") or {}
    return [
        ("Resilience score", summary.get("resilience_score")),
        ("Verdict", explanation.get("band")),
        ("Verdict headline", summary.get("verdict_headline")),
        ("Cost grade", summary.get("cost_grade")),
        ("Seed", seed),
        ("Requests", summary.get("requests")),
        ("Failed requests", summary.get("failed_requests")),
        ("Unfinished requests", summary.get("unfinished_requests")),
        ("Overdue requests", summary.get("overdue_requests")),
        ("Completion rate", summary.get("completion_rate")),
        ("SLA compliance", summary.get("sla_compliance")),
        ("SLA target (s)", summary.get("sla_target")),
        ("Average latency (s)", summary.get("avg_latency")),
        ("P50 latency (s)", summary.get("p50_latency")),
        ("P95 latency (s)", summary.get("p95_latency")),
        ("P99 latency (s)", summary.get("p99_latency")),
        ("Total retries", summary.get("total_retries")),
        ("Cache hit rate", summary.get("cache_hit_rate")),
        ("Total cost (USD)", summary.get("total_cost")),
        ("Provisioned cost (USD)", summary.get("cost_base")),
        ("Metered cost (USD)", summary.get("cost_metered")),
        ("Monthly cost, same load 24/7 (USD)", projection.get("month")),
        ("Yearly cost, same load 24/7 (USD)", projection.get("year")),
    ]


def _per_node_rows(summary: Mapping[str, Any], config: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    """One row per configured node, in config order.

    The config is the source of the rows, not the sizing block: a node the
    run has no numbers for (an idle run) still gets its row, with empty cells.
    """
    sizing = summary.get("component_sizing") or {}
    costs = summary.get("cost_breakdown_by_component") or {}
    nodes = (config.get("topology") or {}).get("nodes") or []
    rows: list[tuple[Any, ...]] = []
    for node in nodes:
        name = node.get("name")
        info = sizing.get(name) or {}
        rows.append(
            (
                name,
                node.get("role"),
                node.get("max_capacity"),
                node.get("service_time"),
                node.get("cost_per_hour"),
                info.get("mean_utilization"),
                info.get("p95_queue"),
                info.get("waited_requests"),
                info.get("status"),
                info.get("verified_status"),
                info.get("verified_capacity"),
                costs.get(name),
            )
        )
    return rows


def _findings_rows(summary: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    """One row per finding, in the order the summary lists them (worst first)."""
    return [
        tuple(finding.get(key) for key in _FINDING_KEYS)
        for finding in summary.get("findings") or []
    ]


def _fill_sheet(sheet: Any, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    """Write *header* and *rows* into an openpyxl worksheet.

    Every string is forced to a text cell after it is assigned: openpyxl
    stores one that starts with ``=`` as a formula, and a node name or a
    finding's sentence is user-controlled text that must never be evaluated.
    The text itself is not changed, so what the sheet shows is the real name.
    """
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    widths = [len(label) for label in header]
    for row_index, row in enumerate((header, *rows), start=1):
        for column_index, raw in enumerate(row, start=1):
            value = _cell_value(raw)
            cell = sheet.cell(row=row_index, column=column_index)
            cell.value = value
            if isinstance(value, str):
                cell.data_type = "s"
            if value is not None and column_index <= len(widths):
                widths[column_index - 1] = max(widths[column_index - 1], len(str(value)))
    bold = Font(bold=True)
    for column_index, width in enumerate(widths, start=1):
        sheet.cell(row=1, column=column_index).font = bold
        letter = get_column_letter(column_index)
        sheet.column_dimensions[letter].width = min(width + 2, _MAX_COLUMN_WIDTH)
    # The header stays in view however far down the sheet is scrolled.
    sheet.freeze_panes = "A2"


def to_xlsx(
    summary: Mapping[str, Any], config: Mapping[str, Any], *, seed: int | None = None
) -> bytes:
    """The run as an Excel workbook: Summary, Per-node and Findings sheets.

    Numbers are stored as numbers so a spreadsheet can compute with them;
    a value the run does not have is an empty cell. The same run gives the
    same cells, but not the same bytes: the file format carries its own save
    time.

    ``openpyxl`` is imported here, not at module level, so only a caller that
    asks for a workbook pays for it.
    """
    from openpyxl import Workbook

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = XLSX_SHEETS[0]
    _fill_sheet(summary_sheet, SUMMARY_COLUMNS, _summary_rows(summary, seed))
    _fill_sheet(
        workbook.create_sheet(XLSX_SHEETS[1]), PER_NODE_COLUMNS, _per_node_rows(summary, config)
    )
    _fill_sheet(workbook.create_sheet(XLSX_SHEETS[2]), FINDINGS_COLUMNS, _findings_rows(summary))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# The format registry and the one dispatch every face uses
# ---------------------------------------------------------------------------


class ExportFormat(NamedTuple):
    """What a caller needs to offer and serve one format."""

    name: str
    label: str
    extension: str
    media_type: str
    #: ``"free"`` or ``"paid"``. A label only: nothing here enforces it.
    tier: str
    #: False when the format is built from the request log alone, so a caller
    #: can skip the work that only the summary needs.
    uses_summary: bool


EXPORT_FORMATS: dict[str, ExportFormat] = {
    "csv": ExportFormat(
        name="csv",
        label="CSV (per-request log)",
        extension="csv",
        media_type="text/csv; charset=utf-8",
        tier="free",
        uses_summary=False,
    ),
    "json": ExportFormat(
        name="json",
        label="JSON (summary + config, replayable)",
        extension="json",
        media_type="application/json",
        tier="free",
        uses_summary=True,
    ),
    "xlsx": ExportFormat(
        name="xlsx",
        label="Excel workbook",
        extension="xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        tier="paid",
        uses_summary=True,
    ),
}


def render_export(
    fmt: str,
    *,
    summary: Mapping[str, Any],
    config: Mapping[str, Any],
    request_log: Iterable[Mapping[str, Any]],
    seed: int | None = None,
) -> bytes:
    """Render one run in format *fmt*, as the bytes of the file.

    The single dispatch the CLI and the API share, so a format cannot be
    offered by one and built differently by the other. Text formats are
    UTF-8. An unknown format is a :class:`ValueError` that names the valid
    ones, ready to show to whoever asked.
    """
    if fmt == "csv":
        return to_csv(request_log).encode("utf-8")
    if fmt == "json":
        return to_json(summary, config, seed=seed).encode("utf-8")
    if fmt == "xlsx":
        return to_xlsx(summary, config, seed=seed)
    raise ValueError(f"unknown export format {fmt!r}; expected one of: {', '.join(EXPORT_FORMATS)}")
