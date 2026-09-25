"""Live cloud pricing for Eleven: Azure, AWS and GCP catalogs.

Eleven's cost model stays **offline and deterministic at run time**: this
package resolves *concrete rates* from the providers' public pricing
endpoints (USD) and hands them to the rest of the system as plain
configuration data. The SimPy engine itself never touches the network.

All three catalogs share the same local JSON cache (default
``~/.eleven/prices_cache.json``, 7-day TTL) and the same
:class:`ResolvedPrice` shape, so Azure, AWS and GCP results live side by
side under non-colliding keys. Azure and AWS need no credentials at all;
GCP requires an API key (``ELEVEN_GCP_API_KEY`` env var or ``.env`` file)
but charges nothing for the lookups.

Behaviour (all providers):

* Prices are cached to the local JSON file with a 7-day TTL;
* If the network is unavailable, ``resolve`` falls back to the last known
  good cached price and flags it ``from_cache=True``;
* Only the Python standard library is used (``urllib``) - no new dependency.

Layout
------
* :mod:`~sim_core.price_source.constants` - API roots, currency defaults and
  the pagination / download / cache tuning knobs (per provider);
* :mod:`~sim_core.price_source.base` - :class:`PriceLookupError`,
  :class:`ResolvedPrice` and the shared :class:`CatalogBase` (HTTP + cache);
* :mod:`~sim_core.price_source.azure` / :mod:`~sim_core.price_source.aws` /
  :mod:`~sim_core.price_source.gcp` - one module per provider catalog, each
  with its private parsing helpers and its ``make_*_catalog`` factory.

This module is the "control file": it re-exports the full public API so
``from sim_core.price_source import ...`` keeps working exactly as before.
"""

from __future__ import annotations

from .aws import AwsCatalog, make_aws_catalog
from .base import CatalogBase, PriceLookupError, ResolvedPrice
from .constants import (
    AZURE_PRICES_API,
    AWS_PRICES_API,
    DEFAULT_CACHE_PATH,
    DEFAULT_MAX_DOWNLOAD_MB,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TTL_SECONDS,
    GCP_API_KEY_ENV,
    GCP_PRICING_API,
    HOURS_PER_YEAR,
    SUPPORTED_CURRENCY,
)
from .azure import AzureCatalog, make_catalog
from .gcp import GcpCatalog, make_gcp_catalog

__all__ = [
    # Shared constants
    "AZURE_PRICES_API",
    "AWS_PRICES_API",
    "GCP_PRICING_API",
    "GCP_API_KEY_ENV",
    "SUPPORTED_CURRENCY",
    "HOURS_PER_YEAR",
    "DEFAULT_CACHE_PATH",
    "DEFAULT_TTL_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_DOWNLOAD_MB",
    # Primitives
    "PriceLookupError",
    "ResolvedPrice",
    "CatalogBase",
    # Provider catalogs
    "AzureCatalog",
    "AwsCatalog",
    "GcpCatalog",
    # Factories
    "make_catalog",
    "make_aws_catalog",
    "make_gcp_catalog",
]
