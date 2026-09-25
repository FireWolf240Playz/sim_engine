"""AWS list-price catalog (``pricing.us-east-1.amazonaws.com`` offer files).

Provider API notes (verified live, 2026-09): public offer files, one per
service + region::

    https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{ServiceCode}/current/{Region}/index.json

No auth, no AWS account, no charge. There is **no server-side filtering**:
each query downloads the whole offer file for that service + region
(``AWSELB`` ~19 KB, ``AmazonDynamoDB`` ~43 KB, ``AmazonElastiCache``
~2.2 MB, ``AmazonRDS`` ~27 MB, but ``AmazonEC2`` ~0.5 GB - hence the
``max_download_mb`` guard). File shape (``formatVersion: aws_v1``):
``products`` maps opaque SKU ids to ``attributes`` (instanceType, os,
regionCode, usagetype, ...), and ``terms`` groups price dimensions by
billing mode (``OnDemand``, ``Reserved`` with ``termAttributes.
leaseContractLength`` = "1 Year" / "3 Year" + ``paymentOption``).
Rates live in ``priceDimensions.<id>.pricePerUnit.USD`` with a ``unit``
(Hour, Requests, GB-Hours, ...).
"""

from __future__ import annotations

import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import CatalogBase, PriceLookupError, ResolvedPrice
from .constants import (
    AWS_PRICES_API,
    DEFAULT_CACHE_PATH,
    DEFAULT_MAX_DOWNLOAD_MB,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TTL_SECONDS,
    SUPPORTED_CURRENCY,
    _HOURLY_UNITS,
    _USER_AGENT,
)


def _usd_rate(dimension: Dict[str, Any]) -> Optional[float]:
    """Extract the USD rate of one AWS price dimension (``None`` if absent)."""
    per_unit = dimension.get("pricePerUnit")
    if not isinstance(per_unit, dict):
        return None
    raw = per_unit.get(SUPPORTED_CURRENCY)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _rate_for_term(term: Dict[str, Any]) -> Optional[Tuple[float, str]]:
    """Best (rate, unit) across one offer term's price dimensions.

    Prefers dimensions billed per hour (``Hour`` / ``Hrs`` - the cost
    model's unit); falls back to the first dimension with a USD rate
    (per-request meters, per-GB data, ...).
    """
    dimensions = term.get("priceDimensions")
    if not isinstance(dimensions, dict):
        return None
    for dimension in dimensions.values():
        if not isinstance(dimension, dict):
            continue
        unit = str(dimension.get("unit") or "")
        if unit.lower() in _HOURLY_UNITS:
            rate = _usd_rate(dimension)
            if rate is not None:
                return rate, unit
    for dimension in dimensions.values():
        if not isinstance(dimension, dict):
            continue
        rate = _usd_rate(dimension)
        if rate is not None:
            return rate, str(dimension.get("unit") or "")
    return None


def _on_demand_rate(offer: Dict[str, Any], sku: str) -> Optional[Tuple[float, str]]:
    """The on-demand rate for one SKU from ``terms.OnDemand`` (or ``None``)."""
    terms = offer.get("terms")
    entry = terms.get("OnDemand", {}).get(sku) if isinstance(terms, dict) else None
    if not isinstance(entry, dict):
        return None
    for term in entry.values():
        if isinstance(term, dict):
            rate = _rate_for_term(term)
            if rate is not None:
                return rate
    return None


def _reserved_rate(offer: Dict[str, Any], sku: str, term_years: int) -> Optional[float]:
    """Committed-use (Reserved Instance) rate for one SKU and term length.

    Matches ``termAttributes.leaseContractLength`` ("1 Year" / "3 Year") and
    prefers the ``No Upfront`` payment option (the cleanest hourly equivalent
    of a prepaid commitment). ``None`` when the SKU has no RI offering.
    """
    terms = offer.get("terms")
    reserved = terms.get("Reserved") if isinstance(terms, dict) else None
    entry = reserved.get(sku) if isinstance(reserved, dict) else None
    if not isinstance(entry, dict):
        return None
    prefix = f"{term_years} year"
    best: Optional[Tuple[int, float]] = None
    for term in entry.values():
        if not isinstance(term, dict):
            continue
        attrs = term.get("termAttributes")
        lease = str(attrs.get("leaseContractLength") or "").lower() if isinstance(attrs, dict) else ""
        if not lease.startswith(prefix):
            continue
        rate = _rate_for_term(term)
        if rate is None:
            continue
        payment = str(attrs.get("paymentOption") or "").lower()
        score = 0 if payment == "no upfront" else 1
        if best is None or score < best[0]:
            best = (score, rate[0])
    return best[1] if best is not None else None


def _product_label(attributes: Dict[str, Any]) -> str:
    """Human-readable label for an AWS product entry (SKUs are opaque ids)."""
    for key in ("groupDescription", "instanceType", "usagetype"):
        value = attributes.get(key)
        if isinstance(value, str) and value:
            return value
    value = attributes.get("location")
    return value if isinstance(value, str) else ""


def _attr_matches(attributes: Dict[str, Any], filters: List[Tuple[str, str]]) -> bool:
    """Case-insensitive substring match on product attribute values.

    ``[("instanceType", "t3")]`` matches ``t3.micro``, ``t3a.medium`` etc.
    """
    lowered = {str(k).lower(): str(v).lower() for k, v in attributes.items()}
    for key, value in filters:
        haystack = lowered.get(key.lower())
        if haystack is None or value.lower() not in haystack:
            return False
    return True


class AwsCatalog(CatalogBase):
    """Resolve AWS list prices from the public pricing (offer-file) API.

    No AWS account or API key is required: the endpoint serves static JSON
    offer files per service + region. Because there is no server-side
    filtering, a query downloads the WHOLE offer file for that service +
    region; ``max_download_mb`` refuses files beyond the guard (default
    100 MB - ``AmazonEC2`` is ~0.5 GB and needs ``--max-mb`` raised).

    Parameters
    ----------
    cache_path:
        Where the JSON price cache lives (shared with :class:`AzureCatalog`).
    ttl_seconds:
        How long a cached price is considered fresh.
    timeout:
        Per-request network timeout in seconds.
    max_download_mb:
        Refuse to download offer files bigger than this (0 disables the guard).
    """

    def __init__(
        self,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_download_mb: float = DEFAULT_MAX_DOWNLOAD_MB,
    ) -> None:
        super().__init__(cache_path, ttl_seconds, timeout)
        self._max_download_bytes = (
            int(max_download_mb * 1024 * 1024) if max_download_mb and max_download_mb > 0 else None
        )

    @staticmethod
    def _offer_url(service: str, region: str) -> str:
        return f"{AWS_PRICES_API}/offers/v1.0/aws/{service}/current/{region}/index.json"

    # ------------------------------------------------------------------ #
    # Fetch layer (whole offer file per service + region)
    # ------------------------------------------------------------------ #
    def _head_content_length(self, url: str) -> Optional[int]:
        """Best-effort ``Content-Length`` via HEAD (``None`` when unknown)."""
        request = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": _USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                value = response.headers.get("Content-Length")
                return int(value) if value else None
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            return None

    def _fetch_offer(self, service: str, region: str) -> Dict[str, Any]:
        """Download the offer file for ``service`` + ``region`` (size-guarded)."""
        url = self._offer_url(service, region)
        length = self._head_content_length(url)
        if self._max_download_bytes is not None and length and length > self._max_download_bytes:
            raise PriceLookupError(
                f"the AWS offer file for {service!r} / {region} is {length / (1024 * 1024):.0f} MB, "
                f"above the {self._max_download_bytes / (1024 * 1024):.0f} MB download guard - "
                f"re-run with --max-mb {int(length / (1024 * 1024)) + 100} to allow it"
            )
        data = self._http_get_json(url)
        if not isinstance(data.get("products"), dict) or not isinstance(data.get("terms"), dict):
            raise PriceLookupError(
                f"unexpected offer file shape from {url!r} (missing products/terms) - "
                f"check the service code and region"
            )
        return data

    # ------------------------------------------------------------------ #
    # Classification
    # ------------------------------------------------------------------ #
    def _build_price(
        self,
        offer: Dict[str, Any],
        sku: str,
        product: Dict[str, Any],
        *,
        service: str,
        region: str,
        fetched_at: datetime,
    ) -> Optional[ResolvedPrice]:
        """Build a :class:`ResolvedPrice` for one SKU (``None`` without on-demand)."""
        attributes = product.get("attributes")
        attributes = attributes if isinstance(attributes, dict) else {}
        rate = _on_demand_rate(offer, sku)
        if rate is None:
            return None
        on_demand, unit = rate
        return ResolvedPrice(
            provider="aws",
            service=str(attributes.get("servicecode") or service),
            product_name=_product_label(attributes),
            sku=sku,
            region=str(attributes.get("regionCode") or region),
            currency=SUPPORTED_CURRENCY,
            unit=unit,
            on_demand_per_hour=on_demand,
            reserved_1y_per_hour=_reserved_rate(offer, sku, 1),
            reserved_3y_per_hour=_reserved_rate(offer, sku, 3),
            source=self._offer_url(service, region),
            fetched_at=fetched_at,
        )

    def _cache_key_for(
        self,
        service: str,
        region: str,
        filters: List[Tuple[str, str]],
        kind: str,
    ) -> str:
        filter_part = ";".join(f"{k}={v}" for k, v in sorted(filters))
        return self._cache_key("aws", kind, service, region, SUPPORTED_CURRENCY, filter_part)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def resolve(
        self,
        service: str,
        *,
        region: str = "us-east-1",
        attr_filters: Optional[List[Tuple[str, str]]] = None,
        refresh: bool = False,
    ) -> ResolvedPrice:
        """Resolve one AWS product (by attribute filter) to concrete rates (USD).

        ``attr_filters`` are case-insensitive substring matches on product
        attributes, e.g. ``[("instanceType", "t3.micro"), ("os", "Linux")]``.
        When several SKUs match, the cheapest on-demand one wins.

        Cache-first (TTL :attr:`_ttl`); on network failure the last cached
        price for this exact query is returned flagged ``from_cache=True``;
        with no cache at all a :class:`PriceLookupError` is raised.
        """
        filters = list(attr_filters or [])
        key = self._cache_key_for(service, region, filters, "resolve")
        cached = self._load_cache().get("entries", {}).get(key)
        if cached is not None and not refresh and self._is_fresh(cached):
            return ResolvedPrice.model_validate(cached["price"])
        try:
            offer = self._fetch_offer(service, region)
            now = datetime.now(timezone.utc)
            best: Optional[ResolvedPrice] = None
            for sku, product in offer["products"].items():
                if not isinstance(product, dict):
                    continue
                attributes = product.get("attributes")
                if not isinstance(attributes, dict) or not _attr_matches(attributes, filters):
                    continue
                price = self._build_price(
                    offer, sku, product, service=service, region=region, fetched_at=now
                )
                if price is None:
                    continue
                if best is None or price.on_demand_per_hour < best.on_demand_per_hour:
                    best = price
            if best is None:
                wanted = ", ".join(f"{k}~{v}" for k, v in filters) or "(any)"
                raise PriceLookupError(
                    f"no AWS product matched {wanted} in service={service!r} region={region!r}"
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
        region: str = "us-east-1",
        attr_filters: Optional[List[Tuple[str, str]]] = None,
        limit: int = 15,
    ) -> List[ResolvedPrice]:
        """List products of one AWS service with all three billing modes (USD).

        ``attr_filters`` are case-insensitive substring matches on product
        attributes (e.g. ``[("instanceType", "t3")]``). Always downloads the
        offer file (this is the "browse the catalog" command) and refreshes
        the local cache as a side effect.
        """
        filters = list(attr_filters or [])
        offer = self._fetch_offer(service, region)
        now = datetime.now(timezone.utc)
        results: List[ResolvedPrice] = []
        for sku, product in offer["products"].items():
            if not isinstance(product, dict):
                continue
            attributes = product.get("attributes")
            if not isinstance(attributes, dict) or not _attr_matches(attributes, filters):
                continue
            price = self._build_price(
                offer, sku, product, service=service, region=region, fetched_at=now
            )
            if price is None:
                continue
            self._store(self._cache_key("aws", "sku", service, region, sku, SUPPORTED_CURRENCY), price)
            results.append(price)
            if len(results) >= limit:
                break
        if not results:
            wanted = ", ".join(f"{k}~{v}" for k, v in filters) or "(any)"
            raise PriceLookupError(
                f"no AWS products matched {wanted} in service={service!r} region={region!r}"
            )
        return results


def make_aws_catalog(
    cache_path: Optional[Path | str] = None,
    ttl_seconds: Optional[float] = None,
    max_download_mb: Optional[float] = None,
) -> AwsCatalog:
    """Convenience factory: ``None`` values fall back to the documented defaults."""
    kwargs: Dict[str, Any] = {}
    if cache_path is not None:
        kwargs["cache_path"] = Path(cache_path)
    if ttl_seconds is not None:
        kwargs["ttl_seconds"] = ttl_seconds
    if max_download_mb is not None:
        kwargs["max_download_mb"] = max_download_mb
    return AwsCatalog(**kwargs)
