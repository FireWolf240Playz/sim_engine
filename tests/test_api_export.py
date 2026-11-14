"""Contract for roadmap 2.1b: ``POST /export`` and ``GET /export/formats``
(`.agents/tasks/2.1b-export-api-cli.md`).

The API keeps no run. An export therefore names the run by what produced it,
the config (with its seed) and the playbook, and the server runs it again:
the engine is deterministic, so the file describes exactly the run the caller
saw. A run id replaces the re-run when the run store lands (roadmap 3.1).

Make these pass. Do not edit them.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from test_api import _config

from api.limits import MAX_REQUESTS_PER_CALL
from api.main import create_app
from sim_core.exports import CSV_COLUMNS, XLSX_SHEETS

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _body(fmt: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"config": _config().model_dump(mode="json"), "format": fmt}
    body.update(overrides)
    return body


def _simulate(client: TestClient, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"config": _config().model_dump(mode="json"), **overrides}
    response = client.post("/simulate", json=body)
    assert response.status_code == 200
    return response.json()


def _long_config() -> dict[str, Any]:
    """The same stack for 20 s. An incident starts 5 s in at the earliest, and
    the 6 s fixture records nothing after that: its run reads the same with
    or without one."""
    config = _config().model_dump(mode="json")
    config["duration"] = 20.0
    config["traffic"]["duration"] = 20.0
    return config


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text, newline="")))


# ---------------------------------------------------------------------------
# what can be exported
# ---------------------------------------------------------------------------

def test_formats_listing(client: TestClient) -> None:
    response = client.get("/export/formats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert [f["name"] for f in payload["formats"]] == ["csv", "json", "xlsx"]
    by_name = {f["name"]: f for f in payload["formats"]}
    assert set(by_name["csv"]) == {"name", "label", "extension", "media_type", "tier"}
    assert by_name["xlsx"]["tier"] == "paid" and by_name["csv"]["tier"] == "free"
    assert by_name["xlsx"]["media_type"] == XLSX


# ---------------------------------------------------------------------------
# the three downloads
# ---------------------------------------------------------------------------

def test_csv_download_is_the_request_log_of_that_run(client: TestClient) -> None:
    response = client.post("/export", json=_body("csv"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="eleven-run-seed42.csv"'
    rows = _rows(response.text)
    assert tuple(rows[0]) == CSV_COLUMNS
    summary = _simulate(client)["summary"]
    assert len(rows) == summary["requests"] > 0
    assert sum(int(row["failed"]) for row in rows) == summary["failed_requests"]


def test_json_download_is_the_run_the_caller_saw(client: TestClient) -> None:
    response = client.post("/export", json=_body("json"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"].endswith('filename="eleven-run-seed42.json"')
    payload = response.json()
    assert payload["seed"] == 42
    assert payload["summary"] == _simulate(client)["summary"]
    assert payload["config"] == _config().model_dump(mode="json")


def test_xlsx_download_opens(client: TestClient) -> None:
    response = client.post("/export", json=_body("xlsx"))
    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX
    assert response.headers["content-disposition"].endswith('filename="eleven-run-seed42.xlsx"')
    workbook = load_workbook(io.BytesIO(response.content))
    assert tuple(workbook.sheetnames) == XLSX_SHEETS
    values = {row[0]: row[1] for row in workbook["Summary"].iter_rows(values_only=True)}
    assert values["Resilience score"] == _simulate(client)["summary"]["resilience_score"]


def test_a_playbook_is_part_of_the_run(client: TestClient) -> None:
    config = _long_config()
    plain = client.post("/export", json=_body("json", config=config)).json()
    incident = client.post(
        "/export", json=_body("json", config=config, playbook="db_failover")
    )
    assert incident.status_code == 200
    assert incident.headers["content-disposition"].endswith(
        'filename="eleven-db_failover-seed42.json"'
    )
    payload = incident.json()
    seen = _simulate(client, config=config, playbook="db_failover")["summary"]
    assert payload["summary"] == seen
    assert payload["summary"] != plain["summary"]
    # the exported config is the one that ran: the incident's events are in it
    assert len(payload["config"]["chaos"]) > len(plain["config"]["chaos"]) == 0


def test_two_exports_of_one_run_are_the_same_file(client: TestClient) -> None:
    for fmt in ("csv", "json"):
        first = client.post("/export", json=_body(fmt)).content
        assert first == client.post("/export", json=_body(fmt)).content


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------

def test_an_unknown_format_is_refused(client: TestClient) -> None:
    response = client.post("/export", json=_body("pdf"))
    assert response.status_code == 422
    assert "csv" in json.dumps(response.json())  # the answer names what exists


def test_an_unknown_playbook_is_a_404(client: TestClient) -> None:
    response = client.post("/export", json=_body("csv", playbook="nope"))
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown playbook: 'nope'"


def test_a_config_without_a_seed_cannot_be_exported(client: TestClient) -> None:
    """Without the seed the server would export some other run."""
    body = _body("csv")
    body["config"]["seed"] = None
    response = client.post("/export", json=body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "seed" in detail and "re-run" in detail


def test_an_export_counts_against_the_work_budget(client: TestClient) -> None:
    body = _body("csv")
    body["config"]["duration"] = 1200.0
    body["config"]["traffic"] = {"base_rps": 500.0, "duration": 1200.0}
    response = client.post("/export", json=body)
    assert response.status_code == 422
    assert f"{MAX_REQUESTS_PER_CALL:,}" in response.json()["detail"]


# ---------------------------------------------------------------------------
# a browser on another origin can read the file name
# ---------------------------------------------------------------------------

def test_the_file_name_header_is_exposed_to_the_frontend(client: TestClient) -> None:
    response = client.post(
        "/export", json=_body("csv"), headers={"Origin": "http://localhost:3000"}
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "content-disposition" in exposed


# ---------------------------------------------------------------------------
# /simulate is untouched
# ---------------------------------------------------------------------------

def test_simulate_keeps_its_shape(client: TestClient) -> None:
    payload = _simulate(client)
    assert set(payload) == {"summary", "report_png_b64", "chaos", "timeseries"}
