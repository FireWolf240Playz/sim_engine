"""GCP list-price catalog (Cloud Billing Pricing API, ``cloudbilling.googleapis.com``).

Provider API notes (verified live, 2026-09): the old free pricing endpoints
are dead (404), so the Cloud Billing API is the only public pricing source
left. It needs an API key from a project where the Cloud Billing API is
enabled (no billing account needed for public SKUs):

* friendly service name -> ``services/{ID}`` via ``/v2beta/services``;
* SKUs via ``/v2beta/skus?filter=service="services/{ID}"`` (paginated;
  region comes from each SKU's ``geoTaxonomy``);
* rates via ``/v2beta/skus/{skuId}/price`` where
  ``skuPrices[].rate.tiers[0].listPrice.nanos / 1e9`` is the USD rate
  (already hourly), classified by ``consumptionModelDescription``
  ("Default" -> on-demand, "... 1 Year" / "... 3 Year" -> committed-use).

GCP prices SKUs atomically (a Compute VM = per-core + per-RAM rates).
"""

from __future__ import annotations

import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CatalogBase, PriceLookupError, ResolvedPrice
from .constants import (
    DEFAULT_CACHE_PATH,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TTL_SECONDS,
    GCP_API_KEY_ENV,
    GCP_PRICING_API,
    SUPPORTED_CURRENCY,
    _GCP_MAX_SERVICE_PAGES,
    _GCP_MAX_SKU_PAGES,
    _GCP_RESOLVE_MAX_CANDIDATES,
    _GCP_SERVICE_PAGE_SIZE,
    _GCP_SKU_PAGE_SIZE,
    _GCP_SPOT_MARKERS,
)


def _dotenv_value(name: str) -> Optional[str]:
    """Read ``name`` from the environment, falling back to a local ``.env``.

    Checks the current working directory and the project root (two levels
    up from this module: ``sim_engine/sim_core/price_source/gcp.py``) in
    that order; the first non-empty ``NAME=value`` line wins. Returns
    ``None`` when unset everywhere.
    """
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    candidates = (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].lstrip()
            key, _sep, raw = stripped.partition("=")
            if key.strip() != name:
                continue
            raw = raw.strip().strip('"').strip("'").strip()
            if raw:
                return raw
    return None


def _sku_regions(sku: Dict[str, Any]) -> List[str]:
    """Region codes a GCP SKU is sold in (from its ``geoTaxonomy``).

    Handles both shapes the API uses: ``regionalMetadata.region.region``
    (one region) and ``multiRegionalMetadata.regions[].region`` (several).
    """
    geo = sku.get("geoTaxonomy")
    if not isinstance(geo, dict):
        return []
    regions: List[str] = []
    regional = geo.get("regionalMetadata")
    if isinstance(regional, dict):
        region = regional.get("region")
        if isinstance(region, dict) and isinstance(region.get("region"), str):
            regions.append(str(region["region"]))
    multi = geo.get("multiRegionalMetadata")
    if isinstance(multi, dict):
        for entry in multi.get("regions") or []:
            if isinstance(entry, dict) and isinstance(entry.get("region"), str):
                regions.append(str(entry["region"]))
    return regions


def _normalize_unit(unit: str) -> str:
    """Map GCP unit spellings (``h``, ``hour``) to canonical unit names."""
    lowered = unit.strip().lower()
    if lowered in ("h", "hour", "hours"):
        return "Hour"
    if lowered in ("request", "requests"):
        return "Request"
    if lowered in ("gb-hour", "gb-hours"):
        return "GB-Hour"
    return unit.strip() or "Hour"


class GcpCatalog(CatalogBase):
    """Resolve GCP list prices from the Cloud Billing Pricing API (v2beta).

    Unlike Azure/AWS, GCP's public pricing endpoints require an API key:
    set :data:`sim_core.price_source.constants.GCP_API_KEY_ENV` (environment
    variable or ``.env`` file in the project root) to a key created in a
    Google Cloud project where the *Cloud Billing API*
    (``cloudbilling.googleapis.com``) is enabled. No billing account is
    needed for the public SKU/price endpoints, and GCP charges nothing for
    these lookups.

    GCP prices SKUs atomically - a Compute VM is sold as per-core and
    per-GB-RAM rates, Cloud SQL as per-vCPU-hour and per-GB-storage-hour -
    so a :class:`ResolvedPrice` exposes exactly the atomic rate it was
    resolved for (a machine's full price is vCPU x core-rate + GB x
    RAM-rate, which the caller composes).

    Parameters
    ----------
    cache_path:
        Where the JSON price cache lives (shared with the other catalogs).
    ttl_seconds:
        How long a cached price is considered fresh.
    timeout:
        Per-request network timeout in seconds.
    api_key:
        Explicit GCP API key (overrides environment / ``.env`` lookup).
    """

    def __init__(
        self,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        api_key: Optional[str] = None,
    ) -> None:
        super().__init__(cache_path, ttl_seconds, timeout)
        self._api_key: Optional[str] = api_key

    # ------------------------------------------------------------------ #
    # Auth + HTTP
    # ------------------------------------------------------------------ #
    def _key(self) -> str:
        """Resolve the GCP API key (constructor arg > env > ``.env`` file)."""
        if self._api_key:
            return self._api_key
        resolved = _dotenv_value(GCP_API_KEY_ENV)
        if not resolved:
            raise PriceLookupError(
                f"GCP API key not found - set {GCP_API_KEY_ENV} in the environment "
                "or in a .env file in the project root (the key's project must "
                "have the Cloud Billing API enabled)"
            )
        return resolved

    def _get(self, path: str, **params: str) -> Dict[str, Any]:
        """GET a GCP API ``path`` (API key attached) and return the JSON body."""
        query = dict(params)
        query["key"] = self._key()
        url = f"{GCP_PRICING_API}{path}?{urllib.parse.urlencode(query)}"
        return self._http_get_json(url)

    # ------------------------------------------------------------------ #
    # Discovery (services + SKUs)
    # ------------------------------------------------------------------ #
    def _list_services(self) -> List[Dict[str, Any]]:
        """All publicly listed GCP services (paginated), name + displayName."""
        services: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        for _ in range(_GCP_MAX_SERVICE_PAGES):
            params: Dict[str, str] = {"pageSize": str(_GCP_SERVICE_PAGE_SIZE)}
            if page_token is not None:
                params["pageToken"] = page_token
            page = self._get("/v2beta/services", **params)
            items = page.get("services")
            if isinstance(items, list):
                services.extend(item for item in items if isinstance(item, dict))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return services

    def _service_ref(self, service: str) -> Tuple[str, str]:
        """Resolve a GCP service to ``(display_name, 'services/{ID}')``.

        Accepts an exact display name ("Compute Engine"), a
        case-insensitive substring ("compute", "memorystore"), or an
        explicit ``services/{ID}`` reference. Exact display-name matches
        win; otherwise the first substring match in catalog order is used.
        The mapping is cached (service IDs are stable) so repeated lookups
        skip the ~18-page services scan.
        """
        if service.startswith("services/"):
            return service, service
        map_key = self._cache_key("gcp", "service", service.lower())
        cached_entry = self._load_cache().get("entries", {}).get(map_key)
        if cached_entry is not None and self._is_fresh(cached_entry):
            cached_ref = str(cached_entry.get("ref") or "")
            if cached_ref:
                return str(cached_entry.get("display") or service), cached_ref
        services = self._list_services()
        wanted = service.lower()
        chosen: Optional[Dict[str, Any]] = None
        partial: Optional[Dict[str, Any]] = None
        for svc in services:
            display = str(svc.get("displayName") or "")
            ref = str(svc.get("name") or "")
            if not display or not ref:
                continue
            if display.lower() == wanted:
                chosen = svc
                break
            if partial is None and wanted in display.lower():
                partial = svc
        if chosen is None:
            chosen = partial
        if chosen is None:
            names = sorted(
                {
                    str(s.get("displayName"))
                    for s in services
                    if s.get("displayName")
                    and wanted in str(s.get("displayName")).lower()
                }
            )
            hint = f" (closest names: {', '.join(names[:5])})" if names else ""
            raise PriceLookupError(f"GCP service {service!r} not found{hint}")
        display = str(chosen.get("displayName"))
        ref = str(chosen.get("name"))
        cache = self._load_cache()
        cache.setdefault("entries", {})[map_key] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "display": display,
            "ref": ref,
        }
        self._save_cache(cache)
        return display, ref

    def _list_skus(
        self,
        service_ref: str,
        *,
        match: Optional[str] = None,
        region: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """List a service's GCP SKUs (paginated), with client-side filters.

        The API only filters by service, so ``match`` (case-insensitive
        ``displayName`` substring) and ``region`` (must appear in the SKU's
        ``geoTaxonomy``) are applied here. ``limit`` stops pagination as
        soon as enough SKUs are collected (GCP scans can be slow - Compute
        Engine has 10k+ SKUs).
        """
        wanted = match.lower() if match else None
        region_l = region.lower() if region else None
        filter_expr = f'service="{service_ref}"'
        skus: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        for _ in range(_GCP_MAX_SKU_PAGES):
            params: Dict[str, str] = {
                "pageSize": str(_GCP_SKU_PAGE_SIZE),
                "filter": filter_expr,
            }
            if page_token is not None:
                params["pageToken"] = page_token
            page = self._get("/v2beta/skus", **params)
            items = page.get("skus")
            reached_limit = False
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                name_l = str(item.get("displayName") or "").lower()
                if wanted is not None and wanted not in name_l:
                    continue
                if any(marker in name_l for marker in _GCP_SPOT_MARKERS):
                    continue
                if region_l is not None and region_l not in _sku_regions(item):
                    continue
                skus.append(item)
                if limit is not None and len(skus) >= limit:
                    reached_limit = True
                    break
            page_token = page.get("nextPageToken") if not reached_limit else None
            if page_token is None:
                break
        return skus

    # ------------------------------------------------------------------ #
    # Price classification
    # ------------------------------------------------------------------ #
    @staticmethod
    def _sku_price_rate(entry: Dict[str, Any]) -> Optional[Tuple[float, str]]:
        """(USD rate, raw unit) of one ``skuPrices[]`` entry, first tier.

        ``rate.tiers[0].listPrice.nanos / 1e9`` is the USD rate (GCP rates
        are already hourly - no term conversion, unlike Azure).
        """
        if entry.get("valueType") != "rate":
            return None
        rate = entry.get("rate")
        if not isinstance(rate, dict):
            return None
        tiers = rate.get("tiers")
        if not isinstance(tiers, list) or not tiers or not isinstance(tiers[0], dict):
            return None
        list_price = tiers[0].get("listPrice")
        if not isinstance(list_price, dict):
            return None
        nanos = list_price.get("nanos")
        if not isinstance(nanos, (int, float)) or isinstance(nanos, bool):
            return None
        unit_info = rate.get("unitInfo")
        unit = str(unit_info.get("unit") or "") if isinstance(unit_info, dict) else ""
        return float(nanos) / 1e9, unit

    def _build_price(
        self,
        sku: Dict[str, Any],
        *,
        service: str,
        region: Optional[str],
        fetched_at: datetime,
    ) -> Optional[ResolvedPrice]:
        """Fetch one SKU's price and build a :class:`ResolvedPrice`.

        ``skuPrices[]`` is classified by ``consumptionModelDescription``:
        "Default" -> on-demand, "... 1 Year" / "... 3 Year" -> committed
        use (CUD / savings-plan). Returns ``None`` when the SKU has no
        usable on-demand rate.
        """
        sku_id = str(sku.get("skuId") or "")
        if not sku_id:
            return None
        body = self._get(f"/v2beta/skus/{sku_id}/price")
        entries: List[Tuple[str, float, str]] = []
        for entry in body.get("skuPrices") or []:
            if not isinstance(entry, dict):
                continue
            parsed = self._sku_price_rate(entry)
            if parsed is None:
                continue
            rate, unit = parsed
            desc = str(entry.get("consumptionModelDescription") or "").lower()
            entries.append((desc, rate, unit))
        if not entries:
            return None

        def pick(*needles: str) -> Optional[float]:
            rates = [rate for desc, rate, _ in entries if any(n in desc for n in needles)]
            return min(rates) if rates else None

        on_demand = pick("default")
        if on_demand is None:
            # Fallback: the cheapest rate that is not a 1y/3y commitment.
            non_commitment = [
                rate
                for desc, rate, _ in entries
                if "1 year" not in desc and "3 year" not in desc
            ]
            on_demand = min(non_commitment) if non_commitment else min(r for _, r, _ in entries)
        if on_demand <= 0:
            return None
        reserved_1y = pick("1 year")
        reserved_3y = pick("3 year")
        regions = _sku_regions(sku)
        return ResolvedPrice(
            provider="gcp",
            service=service,
            product_name=str(sku.get("displayName") or sku_id),
            sku=sku_id,
            region=regions[0] if regions else (region or "global"),
            currency=str(body.get("currencyCode") or SUPPORTED_CURRENCY),
            unit=_normalize_unit(entries[0][2]),
            on_demand_per_hour=on_demand,
            reserved_1y_per_hour=reserved_1y,
            reserved_3y_per_hour=reserved_3y,
            source=f"{GCP_PRICING_API}/v2beta/skus/{sku_id}/price",
            fetched_at=fetched_at,
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def resolve(
        self,
        service: str,
        *,
        match: str,
        region: Optional[str] = None,
        refresh: bool = False,
    ) -> ResolvedPrice:
        """Resolve one GCP SKU to concrete rates (USD).

        Scans the service's SKUs for ones whose ``displayName`` contains
        ``match`` (case-insensitive) and, when ``region`` is given, are
        sold in that region; the cheapest on-demand match wins (among the
        first ``_GCP_RESOLVE_MAX_CANDIDATES`` matches, since GCP catalog
        scans are slow and a specific machine + region rarely has more
        than a handful of matches).

        Cache-first (TTL :attr:`_ttl`); on network failure the last cached
        price is returned flagged ``from_cache=True``; with no cache at all
        a :class:`PriceLookupError` is raised.
        """
        key = self._cache_key(
            "gcp", "resolve", service.lower(), match.lower(), region or "", SUPPORTED_CURRENCY
        )
        cached = self._load_cache().get("entries", {}).get(key)
        if cached is not None and not refresh and self._is_fresh(cached):
            return ResolvedPrice.model_validate(cached["price"])
        try:
            display, service_ref = self._service_ref(service)
            candidates = self._list_skus(
                service_ref, match=match, region=region, limit=_GCP_RESOLVE_MAX_CANDIDATES
            )
            if not candidates:
                raise PriceLookupError(
                    f"no GCP SKU of {display!r} matched {match!r}"
                    + (f" in region={region!r}" if region else "")
                )
            now = datetime.now(timezone.utc)
            best: Optional[ResolvedPrice] = None
            for sku in candidates:
                price = self._build_price(sku, service=display, region=region, fetched_at=now)
                if price is None:
                    continue
                if best is None or price.on_demand_per_hour < best.on_demand_per_hour:
                    best = price
            if best is None:
                raise PriceLookupError(
                    f"GCP SKUs of {display!r} matched {match!r} but none had a "
                    f"usable on-demand rate"
                )
        except PriceLookupError as exc:
            if cached is not None:
                stale = ResolvedPrice.model_validate(cached["price"])
                stale.from_cache = True
                return stale
            raise
        self._store(key, best)
        return best

    def list_skus(
        self,
        service: str,
        *,
        match: Optional[str] = None,
        region: Optional[str] = None,
        limit: int = 15,
    ) -> List[ResolvedPrice]:
        """List SKUs of one GCP service with all three billing modes (USD).

        ``match`` is a case-insensitive ``displayName`` substring, ``region``
        restricts to SKUs sold in that region. Always hits the network (this
        is the "browse the catalog" command) and refreshes the local cache
        as a side effect.
        """
        display, service_ref = self._service_ref(service)
        skus = self._list_skus(service_ref, match=match, region=region, limit=limit)
        if not skus:
            raise PriceLookupError(
                f"no GCP SKUs found in service={display!r}"
                + (f" region={region!r}" if region else "")
                + f" match={match or '(any)'!r}"
            )
        now = datetime.now(timezone.utc)
        results: List[ResolvedPrice] = []
        for sku in skus:
            price = self._build_price(sku, service=display, region=region, fetched_at=now)
            if price is None:
                continue
            self._store(
                self._cache_key("gcp", "sku", service_ref, str(sku.get("skuId") or "")),
                price,
            )
            results.append(price)
        if not results:
            raise PriceLookupError(
                f"no GCP SKUs with usable rates in service={display!r}"
                + (f" region={region!r}" if region else "")
            )
        return results


def make_gcp_catalog(
    cache_path: Optional[Path | str] = None,
    ttl_seconds: Optional[float] = None,
    api_key: Optional[str] = None,
) -> GcpCatalog:
    """Convenience factory: ``None`` values fall back to the documented defaults."""
    kwargs: Dict[str, Any] = {}
    if cache_path is not None:
        kwargs["cache_path"] = Path(cache_path)
    if ttl_seconds is not None:
        kwargs["ttl_seconds"] = ttl_seconds
    if api_key is not None:
        kwargs["api_key"] = api_key
    return GcpCatalog(**kwargs)
