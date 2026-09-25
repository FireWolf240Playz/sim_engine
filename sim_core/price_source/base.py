"""Provider-agnostic pricing primitives.

* :class:`PriceLookupError` - raised when a price cannot be resolved;
* :class:`ResolvedPrice` - the normalized result shape shared by all three
  providers (configuration-side data, never live simulation state);
* :class:`CatalogBase` - the shared HTTP + local-cache plumbing that the
  Azure, AWS and GCP catalogs build on.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from .constants import _MAX_CACHE_ENTRIES, _USER_AGENT, SUPPORTED_CURRENCY


class PriceLookupError(RuntimeError):
    """A price could not be resolved (bad SKU/service, empty result, network down)."""


class ResolvedPrice(BaseModel):
    """Unit price for one cloud SKU (USD).

    This is *configuration-side* data (numbers, strings) - it is never used
    as live simulation state, keeping the SimPy engine pure and deterministic.

    ``unit`` says what the rates are expressed per: ``"Hour"`` (serverful
    instances, the default - all rates are per hour), or e.g. ``"Request"``
    / ``"GB-Hours"`` for pay-per-use serverless meters.

    ``reserved_*`` values are the committed-use **hourly equivalents**:
    Azure reports the total prepaid for the term (divided by the term hours:
    1y -> 8760 h, 3y -> 26280 h); AWS reports RI rates that are already
    hourly. ``None`` when the SKU has no committed-use offering.
    """

    provider: str = "azure"
    service: str
    product_name: str
    sku: str
    region: str
    currency: str = SUPPORTED_CURRENCY
    unit: str = Field(default="Hour", description="Billing unit the rates are expressed per.")
    on_demand_per_hour: float = Field(gt=0.0, description="On-demand rate per ``unit``.")
    reserved_1y_per_hour: Optional[float] = Field(
        default=None, ge=0.0, description="Committed-use 1-year rate, if offered."
    )
    reserved_3y_per_hour: Optional[float] = Field(
        default=None, ge=0.0, description="Committed-use 3-year rate, if offered."
    )
    source: str = Field(default="", description="Pricing endpoint / offer file the rates came from.")
    fetched_at: datetime
    from_cache: bool = Field(default=False, description="True when served from the local cache.")


class CatalogBase:
    """Shared HTTP + local-cache plumbing for the cloud price catalogs.

    Subclasses add provider-specific query logic (Azure's OData endpoint vs.
    AWS's per-service offer files vs. GCP's Cloud Billing API) but share the
    same JSON cache file and TTL semantics. Cache keys must embed the
    provider so entries never collide.
    """

    def __init__(
        self,
        cache_path: Path | str,
        ttl_seconds: float,
        timeout: float,
    ) -> None:
        self._cache_path: Path = Path(cache_path)
        self._ttl: float = ttl_seconds
        self._timeout: float = timeout

    # ------------------------------------------------------------------ #
    # HTTP layer
    # ------------------------------------------------------------------ #
    def _http_get_bytes(self, url: str) -> bytes:
        """GET ``url`` and return the raw body (raises :class:`PriceLookupError`)."""
        request = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace").strip()[:500]
            except (OSError, ValueError):
                pass
            detail = f" (API says: {body})" if body else ""
            raise PriceLookupError(
                f"the pricing API returned HTTP {exc.code} for {url}{detail}"
            ) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise PriceLookupError(f"network request to the pricing API failed: {exc}") from exc

    def _http_get_json(self, url: str) -> Dict[str, Any]:
        """GET ``url`` and decode the JSON body (raises :class:`PriceLookupError`)."""
        try:
            data = json.loads(self._http_get_bytes(url))
        except json.JSONDecodeError as exc:
            raise PriceLookupError(f"expected JSON from the pricing API at {url!r}") from exc
        if not isinstance(data, dict):
            raise PriceLookupError(f"unexpected payload shape from the pricing API: {type(data)!r}")
        return data

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
