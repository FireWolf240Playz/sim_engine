"""Live cloud pricing via the Azure Retail Prices API (USD, no API key).

Eleven's cost model stays **offline and deterministic at run time**: this
module resolves *concrete hourly rates* from Azure's public retail-pricing
endpoint and hands them to the rest of the system as plain configuration
data. The SimPy engine itself never touches the network.

Pricing modes (real-world semantics):

* **on-demand** - pay-as-you-go sticker price, no commitment;
* **reserved 1y / 3y** - committed-use (a.k.a. "commit plan" / reservation)
  rates, discounted but prepaid for the term. ``None`` when the SKU has no
  committed-use offering (serverless services are pay-per-use only).

API schema notes (verified live against ``prices.azure.com``, 2026-09):

* The endpoint serves **USD only** (``BillingCurrency`` is always USD, and
  other ``currencyCode`` filters return zero rows) - conversion to other
  currencies is intentionally left to a future FX step.
* All billing modes come back in ONE item list, classified by:
  - ``type == "Consumption"``        -> on-demand, ``retailPrice`` per hour;
  - ``type == "Reservation"``        -> committed-use, ``retailPrice`` is the
    TOTAL prepaid for the ``reservationTerm`` ("1 Year" / "3 Years");
  - ``type == "DevTestConsumption"`` -> excluded (dev/test meters).
* ``priceType`` / ``termType`` are NOT valid OData filters on this endpoint
  (they return HTTP 400 "Invalid OData parameters supplied"), so the
  classification is done client-side.

Behaviour:

* Prices are cached to a local JSON file (default
  ``~/.eleven/prices_cache.json``) with a 7-day TTL;
* If the network is unavailable, :meth:`AzureCatalog.resolve` falls back to
  the last known good cached price and flags it ``from_cache=True``;
* Only the Python standard library is used (``urllib``) - no new dependency.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

#: Azure's public retail prices endpoint (OData-style ``$filter`` queries).
AZURE_PRICES_API: str = "https://prices.azure.com/api/retail/prices"

#: The only currency the endpoint actually serves (verified live).
SUPPORTED_CURRENCY: str = "USD"

#: Hours in one billing year (the unit of every rate in this module).
HOURS_PER_YEAR: float = 8760.0

#: Default location of the local price cache.
DEFAULT_CACHE_PATH: Path = Path.home() / ".eleven" / "prices_cache.json"

#: Prices older than this are refetched when the network is available.
DEFAULT_TTL_SECONDS: float = 7 * 24 * 3600

#: Per-request timeout for the pricing API.
DEFAULT_TIMEOUT_SECONDS: float = 20.0

_MAX_PAGES: int = 8
_MAX_CACHE_ENTRIES: int = 256
_USER_AGENT: str = "eleven-sim/0.1 (pre-deployment cloud resilience simulator)"

#: Row ``type`` values we understand.
_TYPE_CONSUMPTION: str = "Consumption"
_TYPE_RESERVATION: str = "Reservation"


class PriceLookupError(RuntimeError):
    """A price could not be resolved (bad SKU/service, empty result, network down)."""


class ResolvedPrice(BaseModel):
    """Hourly unit price for one Azure SKU (USD).

    This is *configuration-side* data (numbers, strings) - it is never used
    as live simulation state, keeping the SimPy engine pure and deterministic.

    ``reserved_*`` values are the committed-use **hourly equivalents**: the
    API reports the total prepaid for the term, and we divide by the term
    hours (1y -> 8760 h, 3y -> 26280 h). ``None`` when the SKU has no
    committed-use offering.
    """

    provider: str = "azure"
    service: str
    product_name: str
    sku: str
    region: str
    currency: str = SUPPORTED_CURRENCY
    on_demand_per_hour: float = Field(gt=0.0, description="Pay-as-you-go hourly rate.")
    reserved_1y_per_hour: Optional[float] = Field(
        default=None, ge=0.0, description="Committed-use 1-year hourly rate, if offered."
    )
    reserved_3y_per_hour: Optional[float] = Field(
        default=None, ge=0.0, description="Committed-use 3-year hourly rate, if offered."
    )
    source: str = AZURE_PRICES_API
    fetched_at: datetime
    from_cache: bool = Field(default=False, description="True when served from the local cache.")


def _escape_odata(value: str) -> str:
    """Escape a string for use inside an OData ``'...'`` literal."""
    return value.replace("'", "''")


def _eq_clause(field: str, value: str) -> str:
    """Build an OData ``field eq 'value'`` clause."""
    return f"{field} eq '{_escape_odata(value)}'"


class AzureCatalog:
    """Resolve Azure SKU prices from the public retail-prices API.

    Parameters
    ----------
    cache_path:
        Where the JSON price cache lives. Parent directories are created on
        first write.
    ttl_seconds:
        How long a cached price is considered fresh.
    timeout:
        Per-request network timeout in seconds.
    """

    def __init__(
        self,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._cache_path: Path = Path(cache_path)
        self._ttl: float = ttl_seconds
        self._timeout: float = timeout

    # ------------------------------------------------------------------ #
    # HTTP / query layer
    # ------------------------------------------------------------------ #
    def _http_get_json(self, url: str) -> Dict[str, Any]:
        """GET ``url`` and decode the JSON body (raises :class:`PriceLookupError`)."""
        request = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = response.read().decode("utf-8")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
            raise PriceLookupError(f"network request to the Azure pricing API failed: {exc}") from exc
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise PriceLookupError(
                f"expected JSON from the Azure pricing API, got: {payload[:120]!r}"
            ) from exc
        if not isinstance(data, dict):
            raise PriceLookupError(f"unexpected payload shape from the Azure pricing API: {type(data)!r}")
        return data

    def _query(self, *clauses: str) -> List[Dict[str, Any]]:
        """Run a ``$filter`` query, following ``NextPageLink`` pagination."""
        filter_expr = " and ".join(clauses)
        url = f"{AZURE_PRICES_API}?{urllib.parse.urlencode({'$filter': filter_expr})}"
        items: List[Dict[str, Any]] = []
        for _ in range(_MAX_PAGES):
            page = self._http_get_json(url)
            page_items = page.get("Items")
            if isinstance(page_items, list):
                items.extend(page_items)
            url = page.get("NextPageLink")
            if not url:
                break
        return items

    # ------------------------------------------------------------------ #
    # Row classification (the API mixes all billing modes in one list)
    # ------------------------------------------------------------------ #
    @classmethod
    def _pick(
        cls,
        items: List[Dict[str, Any]],
        *,
        row_type: str,
        term_prefix: Optional[str],
        product_contains: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Pick the cheapest qualifying hourly row for one billing mode.

        ``row_type`` is the API ``type`` field ("Consumption" / "Reservation");
        ``term_prefix`` restricts reservations by ``reservationTerm``
        ("1 year" / "3 year", case-insensitive prefix). ``product_contains``
        prefers product names containing the substring (e.g. "linux") and,
        if nothing matches, retries with the preference dropped (VM product
        names often carry no OS hint at all).
        """

        def matches(item: Dict[str, Any]) -> bool:
            if item.get("unitOfMeasure") != "1 Hour":
                return False
            if item.get("isPrimaryMeterRegion", True) is False:
                return False
            if item.get("type") != row_type:
                return False
            if not isinstance(item.get("retailPrice"), (int, float)):
                return False
            if term_prefix is not None:
                term = str(item.get("reservationTerm") or "").lower()
                if not term.startswith(term_prefix):
                    return False
            if product_contains:
                name = str(item.get("productName") or "").lower()
                if product_contains.lower() not in name:
                    return False
            return True

        candidates = [item for item in items if matches(item)]
        if not candidates and product_contains:
            return cls._pick(
                items, row_type=row_type, term_prefix=term_prefix, product_contains=None
            )
        if not candidates:
            return None
        return min(candidates, key=lambda item: float(item["retailPrice"]))

    @staticmethod
    def _hourly_rate(item: Dict[str, Any], term_years: Optional[int]) -> float:
        """Convert a row's ``retailPrice`` to a USD/hour rate.

        Consumption rows are already hourly. Reservation rows are the total
        prepaid for the whole term, so divide by the term's hours.
        """
        price = float(item["retailPrice"])
        if term_years is None:
            return price
        return price / (term_years * HOURS_PER_YEAR)

    def _classify(
        self,
        items: List[Dict[str, Any]],
        *,
        sku: str,
        service: str,
        region: str,
        product_contains: Optional[str],
        fetched_at: Optional[datetime] = None,
    ) -> Optional[ResolvedPrice]:
        """Build a :class:`ResolvedPrice` from the raw rows of ONE SKU.

        Returns ``None`` when the SKU has no usable on-demand hourly row.
        """
        on_demand = self._pick(
            items, row_type=_TYPE_CONSUMPTION, term_prefix=None, product_contains=product_contains
        )
        if on_demand is None:
            return None
        reserved_1y = self._pick(
            items, row_type=_TYPE_RESERVATION, term_prefix="1 year", product_contains=product_contains
        )
        reserved_3y = self._pick(
            items, row_type=_TYPE_RESERVATION, term_prefix="3 year", product_contains=product_contains
        )
        return ResolvedPrice(
            service=str(on_demand.get("serviceName") or service),
            product_name=str(on_demand.get("productName") or ""),
            sku=str(on_demand.get("skuName") or sku),
            region=str(on_demand.get("armRegionName") or region),
            currency=SUPPORTED_CURRENCY,
            on_demand_per_hour=self._hourly_rate(on_demand, None),
            reserved_1y_per_hour=(
                self._hourly_rate(reserved_1y, 1) if reserved_1y is not None else None
            ),
            reserved_3y_per_hour=(
                self._hourly_rate(reserved_3y, 3) if reserved_3y is not None else None
            ),
            fetched_at=fetched_at or datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------ #
    # Cache layer
    # ------------------------------------------------------------------ #
    def _load_cache(self) -> Dict[str, Any]:
        if not self._cache_path.is_file():
            return {"version": 1, "entries": {}}
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "entries": {}}
        if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
            return {"version": 1, "entries": {}}
        return data

    def _save_cache(self, data: Dict[str, Any]) -> None:
        entries = data.setdefault("entries", {})
        if len(entries) > _MAX_CACHE_ENTRIES:
            oldest_first = sorted(entries.items(), key=lambda kv: str(kv[1].get("fetched_at", "")))
            for key, _ in oldest_first[: len(entries) - _MAX_CACHE_ENTRIES]:
                entries.pop(key, None)
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            raise PriceLookupError(f"could not write the price cache at {self._cache_path!r}: {exc}") from exc

    def _is_fresh(self, cached: Dict[str, Any]) -> bool:
        try:
            fetched = datetime.fromisoformat(str(cached.get("fetched_at")))
        except ValueError:
            return False
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        return 0 <= age <= self._ttl

    @staticmethod
    def _cache_key(*parts: str) -> str:
        digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
        return digest[:32]

    def _store(self, key: str, price: ResolvedPrice) -> None:
        cache = self._load_cache()
        cache.setdefault("entries", {})[key] = {
            "fetched_at": price.fetched_at.isoformat(),
            "price": price.model_dump(mode="json"),
        }
        self._save_cache(cache)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def _fetch_one(
        self,
        sku: str,
        *,
        service: str,
        region: str,
        product_contains: Optional[str],
    ) -> ResolvedPrice:
        """Fetch all billing modes for one SKU in a single API query."""
        items = self._query(
            _eq_clause("serviceName", service),
            _eq_clause("armRegionName", region),
            _eq_clause("currencyCode", SUPPORTED_CURRENCY),
            _eq_clause("skuName", sku),
        )
        price = self._classify(
            items, sku=sku, service=service, region=region, product_contains=product_contains
        )
        if price is None:
            raise PriceLookupError(
                f"no hourly retail price found for sku={sku!r} in service={service!r} "
                f"region={region!r} (currency is fixed to {SUPPORTED_CURRENCY} by the API)"
            )
        return price

    def resolve(
        self,
        sku: str,
        *,
        service: str,
        region: str = "westeurope",
        product_contains: Optional[str] = "linux",
        refresh: bool = False,
    ) -> ResolvedPrice:
        """Resolve one SKU to concrete hourly rates (USD).

        Cache-first (TTL :attr:`_ttl`); on network failure the last cached
        price is returned flagged ``from_cache=True``; with no cache at all a
        :class:`PriceLookupError` is raised.
        """
        key = self._cache_key(sku, service, region, SUPPORTED_CURRENCY, product_contains or "")
        cached = self._load_cache().get("entries", {}).get(key)
        if cached is not None and not refresh and self._is_fresh(cached):
            return ResolvedPrice.model_validate(cached["price"])
        try:
            price = self._fetch_one(
                sku, service=service, region=region, product_contains=product_contains
            )
        except PriceLookupError as exc:
            if cached is not None:
                stale = ResolvedPrice.model_validate(cached["price"])
                stale.from_cache = True
                return stale
            raise
        self._store(key, price)
        return price

    def list_skus(
        self,
        service: str,
        *,
        region: str = "westeurope",
        sku_contains: Optional[str] = None,
        limit: int = 15,
        product_contains: Optional[str] = "linux",
    ) -> List[ResolvedPrice]:
        """List SKUs of one Azure service with all three billing modes (USD).

        One API query (plus pagination) covers the whole page because every
        billing mode comes back in the same item list. Always hits the
        network (this is the "browse the catalog" command) and refreshes the
        local cache as a side effect.
        """
        items = self._query(
            _eq_clause("serviceName", service),
            _eq_clause("armRegionName", region),
            _eq_clause("currencyCode", SUPPORTED_CURRENCY),
        )

        by_sku: Dict[str, List[Dict[str, Any]]] = {}
        skus: List[str] = []
        for item in items:
            if item.get("unitOfMeasure") != "1 Hour":
                continue
            name = str(item.get("skuName") or "")
            if not name:
                continue
            if sku_contains and sku_contains.lower() not in name.lower():
                continue
            by_sku.setdefault(name, []).append(item)
            if name not in skus:
                skus.append(name)
            if len(skus) >= limit:
                break
        if not skus:
            raise PriceLookupError(
                f"no hourly SKUs found in service={service!r} region={region!r} "
                f"sku-filter={sku_contains!r}"
            )

        now = datetime.now(timezone.utc)
        results: List[ResolvedPrice] = []
        for sku in skus:
            price = self._classify(
                by_sku[sku],
                sku=sku,
                service=service,
                region=region,
                product_contains=product_contains,
                fetched_at=now,
            )
            if price is None:
                continue
            self._store(
                self._cache_key(sku, service, region, SUPPORTED_CURRENCY, product_contains or ""),
                price,
            )
            results.append(price)
        return results


def make_catalog(
    cache_path: Optional[Path | str] = None,
    ttl_seconds: Optional[float] = None,
) -> AzureCatalog:
    """Convenience factory: ``None`` values fall back to the documented defaults."""
    kwargs: Dict[str, Any] = {}
    if cache_path is not None:
        kwargs["cache_path"] = Path(cache_path)
    if ttl_seconds is not None:
        kwargs["ttl_seconds"] = ttl_seconds
    return AzureCatalog(**kwargs)
