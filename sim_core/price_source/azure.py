"""Azure retail price catalog (``prices.azure.com``, OData ``$filter``).

Provider API notes (verified live, 2026-09): USD only (other
``currencyCode`` filters return zero rows). All billing modes come back in
ONE item list, classified by:

* ``type == "Consumption"``        -> on-demand, ``retailPrice`` per hour;
* ``type == "Reservation"``        -> committed-use, ``retailPrice`` is the
  TOTAL prepaid for the ``reservationTerm`` ("1 Year" / "3 Years");
* ``type == "DevTestConsumption"`` -> excluded (dev/test meters).

``priceType`` / ``termType`` are NOT valid OData filters (HTTP 400), so
classification is done client-side.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import CatalogBase, PriceLookupError, ResolvedPrice
from .constants import (
    AZURE_PRICES_API,
    DEFAULT_CACHE_PATH,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TTL_SECONDS,
    HOURS_PER_YEAR,
    SUPPORTED_CURRENCY,
    _MAX_PAGES,
    _TYPE_CONSUMPTION,
    _TYPE_RESERVATION,
)


def _escape_odata(value: str) -> str:
    """Escape a string for use inside an OData ``'...'`` literal."""
    return value.replace("'", "''")


def _eq_clause(field: str, value: str) -> str:
    """Build an OData ``field eq 'value'`` clause."""
    return f"{field} eq '{_escape_odata(value)}'"


class AzureCatalog(CatalogBase):
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
        super().__init__(cache_path, ttl_seconds, timeout)

    # ------------------------------------------------------------------ #
    # Query layer (OData ``$filter``)
    # ------------------------------------------------------------------ #
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
            unit="Hour",
            on_demand_per_hour=self._hourly_rate(on_demand, None),
            reserved_1y_per_hour=(
                self._hourly_rate(reserved_1y, 1) if reserved_1y is not None else None
            ),
            reserved_3y_per_hour=(
                self._hourly_rate(reserved_3y, 3) if reserved_3y is not None else None
            ),
            source=AZURE_PRICES_API,
            fetched_at=fetched_at or datetime.now(timezone.utc),
        )

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
