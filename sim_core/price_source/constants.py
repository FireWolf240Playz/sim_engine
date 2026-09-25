"""Shared constants for the cloud price catalogs (Azure, AWS, GCP).

Provider API roots, the shared currency/unit defaults, and the tuning
knobs (cache size, pagination caps, download guards) that keep the
catalogs' network use bounded. Grouped by provider; the public names are
part of the :mod:`sim_core.price_source` API surface (re-exported by the
package :mod:`__init__`), the underscored ones are internal tuning knobs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

# ---------------------------------------------------------------------- #
# Shared defaults (all providers)
# ---------------------------------------------------------------------- #

#: The currency both providers are read from (USD only, by design).
SUPPORTED_CURRENCY: str = "USD"

#: Hours in one billing year (the unit of every reserved-term conversion).
HOURS_PER_YEAR: float = 8760.0

#: Default location of the local price cache (shared by all providers).
DEFAULT_CACHE_PATH: Path = Path.home() / ".eleven" / "prices_cache.json"

#: Prices older than this are refetched when the network is available.
DEFAULT_TTL_SECONDS: float = 7 * 24 * 3600

#: Per-request timeout for the pricing APIs (the AWS offer files can be large).
DEFAULT_TIMEOUT_SECONDS: float = 120.0

#: Maximum number of cached entries before the oldest ones are evicted.
_MAX_CACHE_ENTRIES: int = 256

#: User-Agent string sent with the pricing API requests.
_USER_AGENT: str = "eleven-sim/0.1 (pre-deployment cloud resilience simulator)"

# ---------------------------------------------------------------------- #
# Azure (prices.azure.com, OData ``$filter`` queries)
# ---------------------------------------------------------------------- #

#: Azure's public retail prices endpoint (OData-style ``$filter`` queries).
AZURE_PRICES_API: str = "https://prices.azure.com/api/retail/prices"

#: Maximum number of pages of an Azure ``$filter`` query to follow.
_MAX_PAGES: int = 8

#: Azure row ``type`` values we understand.
_TYPE_CONSUMPTION: str = "Consumption"
_TYPE_RESERVATION: str = "Reservation"

# ---------------------------------------------------------------------- #
# AWS (pricing.us-east-1.amazonaws.com, per-service offer files)
# ---------------------------------------------------------------------- #

#: AWS public pricing (offer-file) API root.
AWS_PRICES_API: str = "https://pricing.us-east-1.amazonaws.com"

#: Guard for the whole-file AWS downloads (AmazonEC2 alone is ~0.5 GB).
DEFAULT_MAX_DOWNLOAD_MB: float = 100.0

#: The unit spellings AWS uses for hourly billing (varies by service file).
_HOURLY_UNITS: frozenset = frozenset({"hour", "hrs"})

# ---------------------------------------------------------------------- #
# GCP (cloudbilling.googleapis.com, Cloud Billing API v2beta)
# ---------------------------------------------------------------------- #

#: GCP Cloud Billing Pricing API (v2beta) root.
GCP_PRICING_API: str = "https://cloudbilling.googleapis.com"

#: Environment variable (or ``.env`` file entry) holding the GCP API key.
GCP_API_KEY_ENV: str = "ELEVEN_GCP_API_KEY"

#: GCP pagination caps (keep runaway scans bounded - Compute Engine alone
#: has 10k+ SKUs and its page scan is slow).
_GCP_SERVICE_PAGE_SIZE: int = 100
_GCP_SKU_PAGE_SIZE: int = 1000
_GCP_MAX_SERVICE_PAGES: int = 40
_GCP_MAX_SKU_PAGES: int = 60
_GCP_RESOLVE_MAX_CANDIDATES: int = 50

#: Spot/preemptible SKUs are a different product tier (reclaimable VMs), not
#: a cheaper on-demand rate - excluded so "cheapest wins" stays comparable
#: with Azure/AWS (whose public pricing APIs also omit spot pricing).
_GCP_SPOT_MARKERS: Tuple[str, ...] = ("spot", "preemptible")
