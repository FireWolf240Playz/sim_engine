"""The ``prices`` / ``aws-prices`` / ``gcp-prices`` catalog subcommands.

Owns the pricing-side CLI surface: the shared price-table renderer and
the three provider command handlers (Azure, AWS, GCP). The catalogs
themselves live in :mod:`sim_core.price_source`.
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Tuple

from sim_core.price_source import (
    AZURE_PRICES_API,
    AzureCatalog,
    AwsCatalog,
    GcpCatalog,
    PriceLookupError,
    ResolvedPrice,
    make_catalog,
    make_aws_catalog,
    make_gcp_catalog,
)

_HOURS_PER_MONTH: float = 730.0
_HOURS_PER_YEAR: float = 8760.0


_UNIT_SUFFIX: Dict[str, str] = {
    "hour": "/h",
    "request": "/req",
    "requests": "/req",
    "gb-hours": "/GB-h",
}


def _fmt_rate(value: Optional[float], unit: str = "Hour") -> str:
    """Format a unit rate (``n/a`` when the tier is absent).

    Hourly sub-dollar rates are rendered in cents so ``0.809`` reads as
    ``80.9¢/h``; other units keep their own suffix (``/req``, ``/GB-h``).
    """
    if value is None:
        return "n/a"
    suffix = _UNIT_SUFFIX.get(unit.lower(), "/" + unit.lower())
    if unit.lower() == "hour" and value < 1.0:
        return f"{value * 100:.1f}¢{suffix}"
    if value < 0.01:
        return f"${value:.4f}{suffix}"
    if value < 1.0:
        return f"${value:.3f}{suffix}"
    return f"${value:,.2f}{suffix}"


def _fmt_money(value: float) -> str:
    """Format a dollar amount with precision scaled to the magnitude."""
    if value < 1.0:
        return f"${value * 100:.1f}¢"
    if value < 100.0:
        return f"${value:,.2f}"
    return f"${value:,.0f}"


_PROVIDER_LABEL: Dict[str, str] = {"azure": "Azure", "aws": "AWS", "gcp": "GCP"}


def _print_price_table(
    prices: List[ResolvedPrice], *, service: str, region: str
) -> None:
    """Render resolved prices as an aligned console table (provider-aware)."""
    if not prices:
        return
    currency = prices[0].currency
    provider = _PROVIDER_LABEL.get(prices[0].provider, prices[0].provider)
    print(f"{provider} prices - service: {service} | region: {region} | currency: {currency}")
    print(
        f"{'SKU':<18} {'Product':<38} {'On-demand':>15} {'Reserved 1y':>15} {'Reserved 3y':>15}"
    )
    for price in prices:
        unit = price.unit
        product = price.product_name if len(price.product_name) <= 38 else price.product_name[:37] + "…"
        sku = price.sku if len(price.sku) <= 18 else price.sku[:17] + "…"
        print(
            f"{sku:<18} {product:<38} "
            f"{_fmt_rate(price.on_demand_per_hour, unit):>15} "
            f"{_fmt_rate(price.reserved_1y_per_hour, unit):>15} "
            f"{_fmt_rate(price.reserved_3y_per_hour, unit):>15}"
        )
    if all(p.unit.lower() in ("hour", "hrs") for p in prices):
        print(
            f"\n~ monthly / yearly (on-demand, {_HOURS_PER_MONTH:.0f} h/month, "
            f"{_HOURS_PER_YEAR:.0f} h/year):"
        )
        for price in prices:
            sku = price.sku if len(price.sku) <= 18 else price.sku[:17] + "…"
            print(
                f"  {sku:<18} {_fmt_money(price.on_demand_per_hour * _HOURS_PER_MONTH):>12}/month   "
                f"{_fmt_money(price.on_demand_per_hour * _HOURS_PER_YEAR):>12}/year"
            )
    newest = max((p.fetched_at for p in prices), default=None)
    if newest is not None:
        note = " (served from local cache)" if any(p.from_cache for p in prices) else ""
        source = prices[0].source or AZURE_PRICES_API
        print(f"\nsource: {source}")
        print(f"fetched: {newest:%Y-%m-%d %H:%M} UTC{note}")


def _parse_attr_filters(raw: List[str]) -> List[Tuple[str, str]]:
    """Parse ``--attr KEY=VALUE`` pairs (repeated) into a filter list."""
    filters: List[Tuple[str, str]] = []
    for item in raw:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise PriceLookupError(f"invalid --attr {item!r} (expected KEY=VALUE)")
        filters.append((key.strip(), value.strip()))
    return filters


def _cmd_prices(args: argparse.Namespace) -> int:
    """Dispatch the ``prices`` subcommand (list / resolve)."""
    catalog: AzureCatalog = make_catalog(args.cache_path)
    try:
        if args.prices_command == "list":
            prices = catalog.list_skus(
                args.service,
                region=args.region,
                sku_contains=args.sku,
                limit=args.limit,
                product_contains=args.product or None,
            )
            _print_price_table(prices, service=args.service, region=args.region)
        else:  # resolve
            price = catalog.resolve(
                args.sku,
                service=args.service,
                region=args.region,
                product_contains=args.product or None,
                refresh=args.refresh,
            )
            if price.from_cache:
                print("warning: fresh fetch failed - serving the last cached price", file=sys.stderr)
            _print_price_table([price], service=args.service, region=args.region)
    except PriceLookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_aws_prices(args: argparse.Namespace) -> int:
    """Dispatch the ``aws-prices`` subcommand (list / resolve)."""
    catalog: AwsCatalog = make_aws_catalog(args.cache_path, max_download_mb=args.max_mb)
    try:
        if args.aws_prices_command == "list":
            prices = catalog.list_skus(
                args.service,
                region=args.region,
                attr_filters=_parse_attr_filters(args.attr),
                limit=args.limit,
            )
        else:  # resolve
            price = catalog.resolve(
                args.service,
                region=args.region,
                attr_filters=_parse_attr_filters(args.attr),
                refresh=args.refresh,
            )
            if price.from_cache:
                print("warning: fresh fetch failed - serving the last cached price", file=sys.stderr)
            prices = [price]
        _print_price_table(prices, service=args.service, region=args.region)
    except PriceLookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_gcp_prices(args: argparse.Namespace) -> int:
    """Dispatch the ``gcp-prices`` subcommand (list / resolve)."""
    catalog: GcpCatalog = make_gcp_catalog(args.cache_path, api_key=args.api_key or None)
    try:
        if args.gcp_prices_command == "list":
            prices = catalog.list_skus(
                args.service,
                match=args.match or None,
                region=args.region or None,
                limit=args.limit,
            )
        else:  # resolve
            price = catalog.resolve(
                args.service,
                match=args.match,
                region=args.region or None,
                refresh=args.refresh,
            )
            if price.from_cache:
                print("warning: fresh fetch failed - serving the last cached price", file=sys.stderr)
            prices = [price]
        _print_price_table(prices, service=args.service, region=args.region or "all regions")
    except PriceLookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def register_pricing_commands(subparsers: argparse._SubParsersAction) -> None:
    """Add the ``prices`` / ``aws-prices`` / ``gcp-prices`` subcommands."""
    prices_p = subparsers.add_parser(
        "prices",
        help="Browse / resolve live Azure retail prices (no API key required).",
    )
    prices_sub = prices_p.add_subparsers(dest="prices_command", required=True)

    list_p = prices_sub.add_parser(
        "list",
        help="List SKUs of an Azure service with on-demand and reserved hourly rates.",
    )
    list_p.add_argument(
        "service",
        help='Azure service name, e.g. "Virtual Machines" or "Caching for Redis".',
    )
    list_p.add_argument(
        "--sku",
        default=None,
        help="Only SKUs whose name contains this substring (case-insensitive).",
    )
    list_p.add_argument("--region", default="westeurope", help="Azure region (default: westeurope).")
    list_p.add_argument("--limit", type=int, default=15, help="Max SKUs to display (default: 15).")
    list_p.add_argument(
        "--product",
        default="linux",
        help="Prefer products whose name contains this (default: linux; empty string = any).",
    )
    list_p.add_argument(
        "--cache-path",
        default=None,
        help="Path of the local price cache file (default: ~/.eleven/prices_cache.json).",
    )

    resolve_p = prices_sub.add_parser(
        "resolve",
        help="Resolve one SKU to concrete hourly rates (on-demand + reserved 1y/3y).",
    )
    resolve_p.add_argument("sku", help='SKU name, e.g. "B2s v2".')
    resolve_p.add_argument("--service", required=True, help='Azure service name, e.g. "Virtual Machines".')
    resolve_p.add_argument("--region", default="westeurope", help="Azure region (default: westeurope).")
    resolve_p.add_argument(
        "--product",
        default="linux",
        help="Prefer products whose name contains this (default: linux; empty string = any).",
    )
    resolve_p.add_argument(
        "--cache-path",
        default=None,
        help="Path of the local price cache file (default: ~/.eleven/prices_cache.json).",
    )
    resolve_p.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass the cache TTL and force a fresh fetch.",
    )

    aws_p = subparsers.add_parser(
        "aws-prices",
        help="Browse / resolve live AWS list prices (no AWS account required).",
    )
    aws_sub = aws_p.add_subparsers(dest="aws_prices_command", required=True)

    aws_list_p = aws_sub.add_parser(
        "list",
        help="List products of an AWS service with on-demand and reserved rates.",
    )
    aws_list_p.add_argument(
        "service",
        help='AWS service code, e.g. "AmazonEC2", "AmazonRDS", "AmazonElastiCache" or "AWSELB".',
    )
    aws_list_p.add_argument(
        "--attr",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Filter by product attribute, case-insensitive substring "
            "(repeatable), e.g. --attr instanceType=t3 --attr os=Linux."
        ),
    )
    aws_list_p.add_argument("--region", default="us-east-1", help="AWS region code (default: us-east-1).")
    aws_list_p.add_argument("--limit", type=int, default=15, help="Max products to display (default: 15).")
    aws_list_p.add_argument(
        "--max-mb",
        type=float,
        default=100.0,
        help=(
            "Refuse to download offer files bigger than this many MB "
            "(default: 100; 0 = no limit; AmazonEC2 is ~500 MB)."
        ),
    )
    aws_list_p.add_argument(
        "--cache-path",
        default=None,
        help="Path of the local price cache file (default: ~/.eleven/prices_cache.json).",
    )

    aws_resolve_p = aws_sub.add_parser(
        "resolve",
        help="Resolve one AWS product (by attribute filter) to concrete rates.",
    )
    aws_resolve_p.add_argument(
        "service",
        help='AWS service code, e.g. "AmazonEC2" or "AmazonRDS".',
    )
    aws_resolve_p.add_argument(
        "--attr",
        action="append",
        required=True,
        metavar="KEY=VALUE",
        help=(
            "Attribute filter, case-insensitive substring (repeatable), "
            "e.g. --attr instanceType=t3.micro --attr os=Linux. "
            "With several matches the cheapest on-demand product wins."
        ),
    )
    aws_resolve_p.add_argument("--region", default="us-east-1", help="AWS region code (default: us-east-1).")
    aws_resolve_p.add_argument(
        "--max-mb",
        type=float,
        default=100.0,
        help="Refuse to download offer files bigger than this many MB (default: 100; 0 = no limit).",
    )
    aws_resolve_p.add_argument(
        "--cache-path",
        default=None,
        help="Path of the local price cache file (default: ~/.eleven/prices_cache.json).",
    )
    aws_resolve_p.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass the cache TTL and force a fresh fetch.",
    )

    gcp_p = subparsers.add_parser(
        "gcp-prices",
        help="Browse / resolve live GCP list prices (needs ELEVEN_GCP_API_KEY).",
    )
    gcp_sub = gcp_p.add_subparsers(dest="gcp_prices_command", required=True)

    gcp_list_p = gcp_sub.add_parser(
        "list",
        help="List SKUs of a GCP service with on-demand and committed-use rates.",
    )
    gcp_list_p.add_argument(
        "service",
        help='GCP service name, e.g. "Compute Engine", "Cloud SQL" or "Cloud Memorystore".',
    )
    gcp_list_p.add_argument(
        "--match",
        default=None,
        help="Only SKUs whose display name contains this substring (case-insensitive).",
    )
    gcp_list_p.add_argument(
        "--region",
        default=None,
        help="Only SKUs sold in this region (e.g. europe-west9; default: all regions).",
    )
    gcp_list_p.add_argument("--limit", type=int, default=15, help="Max SKUs to display (default: 15).")
    gcp_list_p.add_argument(
        "--api-key",
        default=None,
        help="GCP API key (default: ELEVEN_GCP_API_KEY env var or .env file).",
    )
    gcp_list_p.add_argument(
        "--cache-path",
        default=None,
        help="Path of the local price cache file (default: ~/.eleven/prices_cache.json).",
    )

    gcp_resolve_p = gcp_sub.add_parser(
        "resolve",
        help="Resolve one GCP SKU (display-name substring) to concrete rates.",
    )
    gcp_resolve_p.add_argument(
        "service",
        help='GCP service name, e.g. "Compute Engine" or "Cloud SQL".',
    )
    gcp_resolve_p.add_argument(
        "--match",
        required=True,
        help=(
            "SKU display-name substring (case-insensitive), e.g. 'E2 Instance Core'. "
            "With several matches the cheapest on-demand one wins."
        ),
    )
    gcp_resolve_p.add_argument(
        "--region",
        default=None,
        help="Restrict to SKUs sold in this region (e.g. europe-west9).",
    )
    gcp_resolve_p.add_argument(
        "--api-key",
        default=None,
        help="GCP API key (default: ELEVEN_GCP_API_KEY env var or .env file).",
    )
    gcp_resolve_p.add_argument(
        "--cache-path",
        default=None,
        help="Path of the local price cache file (default: ~/.eleven/prices_cache.json).",
    )
    gcp_resolve_p.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass the cache TTL and force a fresh fetch.",
    )
