"""CLI error-path tests: bad input must exit cleanly with a message, never a traceback.

Run with::

    pytest tests/test_cli.py

The happy paths live next to their engine modules (``test_compare.py``,
``test_playbooks.py``, ``test_profile.py``). This file pins the user-error
branches, which no simulation result exercises.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_core.cli import main
from sim_core.cli.sim import default_config


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "topology.json"
    default_config().to_json(str(path))
    return path


def test_compare_missing_config_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.yaml"
    rc = main(["compare", "multi-cloud", "--topology", str(missing)])
    assert rc == 2
    assert "config file not found" in capsys.readouterr().err


def test_compare_what_if_malformed_set_exits_2(
    config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "compare",
            "what-if",
            "--topology",
            str(config_path),
            "--set",
            "no-equals-sign",
            "--json",
            str(tmp_path / "out.json"),
        ]
    )
    assert rc == 2
    assert "--set expects PATH=VALUE" in capsys.readouterr().err


def test_compare_sweep_empty_values_exits_2(
    config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "compare",
            "sweep",
            "--topology",
            str(config_path),
            "--param",
            "app_worker.max_capacity",
            "--values",
            " , ",
            "--json",
            str(tmp_path / "out.json"),
        ]
    )
    assert rc == 2
    assert "--values needs at least one value" in capsys.readouterr().err
