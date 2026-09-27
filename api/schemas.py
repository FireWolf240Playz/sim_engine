"""Request/response schemas for the Eleven API.

Pydantic is used here strictly for *config/schema validation* — the
simulation itself never touches these models. :class:`SimulationConfig`
is reused directly from :mod:`sim_core.config`, so a request body is
validated by the exact same rules the CLI and the library use.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from sim_core import SimulationConfig


class SimulateRequest(BaseModel):
    """One simulation run.

    ``playbook`` (optional) appends a named incident's chaos events to
    ``config.chaos`` (see :mod:`sim_core.playbooks`); an
    unknown name is a 404. ``include_report_png`` returns the rendered
    report as base64 so the frontend can display it without extra calls.
    """

    config: SimulationConfig
    playbook: str | None = None
    include_report_png: bool = False


class SimulateResponse(BaseModel):
    """The run's full summary dict plus (optionally) the report PNG."""

    summary: dict[str, Any]
    report_png_b64: str | None = None


class CompareRequest(BaseModel):
    """A compare-runs request; the mode-specific fields differ per mode.

    - ``multi-cloud`` — no extra fields; the baseline plus the same
      architecture re-calibrated with AWS / Azure / GCP presets.
    - ``what-if``     — ``set``: a non-empty list of ``"path=value"``
      strings (e.g. ``"app_worker.max_capacity=20"``), each applied to a
      copy of the baseline.
    - ``sweep``       — ``param`` (a ``set_path`` path) + ``values`` (at
      least two), plus optional ``metric`` / ``threshold`` for the knee.
    """

    config: SimulationConfig
    mode: Literal["multi-cloud", "what-if", "sweep"]
    set: list[str] | None = Field(default=None, description="what-if: 'path=value' strings")
    param: str | None = Field(default=None, description="sweep: parameter path to sweep")
    values: list[float] | None = Field(default=None, description="sweep: values to try")
    metric: str = Field(default="p95_latency", description="sweep: knee metric")
    threshold: float = Field(default=0.05, description="sweep: knee improvement fraction")


class CompareResponse(BaseModel):
    """The baseline-relative diff, plus the sweep knee when the mode has one."""

    mode: str
    diff: dict[str, Any]
    knee: float | None = None


class PlaybookInfo(BaseModel):
    """One named incident: the key and a human-readable description."""

    name: str
    description: str


class PlaybookListResponse(BaseModel):
    playbooks: list[PlaybookInfo]
    count: int


class PresetListResponse(BaseModel):
    """Provider presets dumped to plain JSON (each keyed by preset name)."""

    presets: dict[str, dict[str, Any]]
    count: int


class HealthResponse(BaseModel):
    status: str
    version: str
