"""Eleven command-line interface (``python -m sim_core``).

Split by concern:

* :mod:`sim_core.cli.sim` - the ``run`` / ``demo`` simulation commands
  (demo topology, config loading, PNG + JSON outputs) and the ``compare``
  command (multi-cloud / what-if / capacity sweep, engine in
  :mod:`sim_core.compare`);
* :mod:`sim_core.cli.pricing` - the ``prices`` / ``aws-prices`` /
  ``gcp-prices`` catalog commands (price table + provider handlers).

This module is the "control file": it wires the halves into one
argparse tree and dispatches the parsed command.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from sim_core.cli import pricing, sim


def build_parser() -> argparse.ArgumentParser:
    """The ``python -m sim_core`` command-line interface."""
    parser = argparse.ArgumentParser(
        prog="eleven",
        description=(
            "Eleven: pre-deployment cloud-resilience simulator. "
            "Stress-test a cloud architecture (from a YAML/JSON config) "
            "with traffic spikes and chaos injection before deploying it."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sim.register_sim_commands(sub)
    pricing.register_pricing_commands(sub)
    return parser


#: Subcommands with their own handler (the rest go through run_simulation).
_COMMAND_HANDLERS: Dict[str, Callable[[argparse.Namespace], int]] = {
    "prices": pricing._cmd_prices,
    "aws-prices": pricing._cmd_aws_prices,
    "gcp-prices": pricing._cmd_gcp_prices,
    "compare": sim._cmd_compare,
}


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)

    handler = _COMMAND_HANDLERS.get(args.command)
    if handler is not None:
        return handler(args)

    if args.command == "demo":
        config = sim.default_config()
        report_path, json_path = sim.DEFAULT_REPORT, sim.DEFAULT_JSON
    else:  # run
        config_path = args.config
        if not Path(config_path).is_file():
            print(f"error: config file not found: {config_path}", file=sys.stderr)
            return 2
        try:
            config = sim.load_config(config_path)
        except Exception as exc:  # validation errors, bad YAML, missing file...
            print(f"error: could not load config {config_path!r}: {exc}", file=sys.stderr)
            return 2
        report_path, json_path = args.report, args.json_path

    try:
        sim.run_simulation(config, report_path, json_path)
    except Exception as exc:
        print(f"error: simulation failed: {exc}", file=sys.stderr)
        return 1
    return 0


__all__ = ["build_parser", "main"]
