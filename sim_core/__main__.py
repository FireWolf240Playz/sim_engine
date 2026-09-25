"""Command-line entry point: ``python -m sim_core``.

Subcommands
-----------
``run``
    Run a simulation from a config file (YAML or JSON)::

        python -m sim_core run my_topology.yaml --report out.png --json summary.json

    The config file is validated with :meth:`sim_core.SimulationConfig.from_yaml`
    / :meth:`sim_core.SimulationConfig.from_json` (both the new graph topology
    and the legacy fixed-pipeline form are accepted). Produces a PNG report
    and a JSON metrics summary alongside it.

``demo``
    Run the built-in representative topology (load balancer -> worker pool ->
    read-through cache -> database) with a traffic spike and three chaos
    windows, without needing a config file::

        python -m sim_core demo

``prices``
    Browse / resolve live Azure retail prices (no API key required). Results
    are cached locally for 7 days; on network failure the last cached price
    is served and flagged::

        python -m sim_core prices list "Virtual Machines" --sku B2 --region westeurope
        python -m sim_core prices resolve "B2s v2" --service "Virtual Machines"

``aws-prices``
    Browse / resolve live AWS list prices from the public pricing (offer-file)
    API (no AWS account required). Same local caching semantics. ``--attr``
    filters are case-insensitive substring matches on product attributes::

        python -m sim_core aws-prices list AmazonElastiCache --attr family=r6g
        python -m sim_core aws-prices list AWSELB
        python -m sim_core aws-prices resolve AmazonEC2 --attr instanceType=t3.micro --attr os=Linux
        python -m sim_core aws-prices resolve AmazonRDS --attr dbInstanceClass=db.r6g.xlarge

``gcp-prices``
    Browse / resolve live GCP list prices from the Cloud Billing Pricing
    API (needs ``ELEVEN_GCP_API_KEY`` - environment variable or ``.env``
    file in the project root, key's project must have the Cloud Billing API
    enabled). ``--match`` is a case-insensitive display-name substring::

        python -m sim_core gcp-prices list "Compute Engine" --match "e2 instance"
        python -m sim_core gcp-prices resolve "Compute Engine" --match "E2 Instance Core" --region europe-west9

The implementation lives in :mod:`sim_core.cli` (``sim`` for the
simulation commands, ``pricing`` for the catalog commands); this module is
a thin shim so ``python -m sim_core`` keeps working unchanged.
"""

from __future__ import annotations

from sim_core.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
