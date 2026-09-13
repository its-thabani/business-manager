"""Inkthreadable API client.

Inkthreadable's documented API lives at ``https://www.inkthreadable.co.uk/api``
and authenticates every request with:

* ``AppId`` — the static application id (``APP-00146434``).
* ``Signature`` — ``SHA1(payload + Secret Key)`` as a 40-character hex digest.

The payload differs by method:

* GET — everything after ``?`` in the request URL, excluding ``Signature``.
* POST — the raw request body.

The secret itself never appears in the URL. Only the hash does.

There is no public product-catalogue endpoint. Supplier products and unit costs
are recovered from order line items (``pn``, ``title``, ``price``, ``options``).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterator
from urllib.parse import urlencode

from django.conf import settings

from apps.integrations.base import (
    ApiClient,
    ConfigurationError,
    ConnectionCheck,
    IntegrationError,
)
from apps.integrations.parsing import as_list, unwrap_order

logger = logging.getLogger(__name__)

# Ways of building the GET signing payload. The OpenAPI spec says "everything
# after ?" and also "Request url + Secret key". We try the documented query
# string first; the others exist so a one-character mismatch does not block us.
SIGN_QUERY = "query"
SIGN_PATH = "path"
SIGN_URL = "url"


class InkthreadableClient(ApiClient):
    service_name = "inkthreadable"

    def __init__(
        self,
        *,
        app_id: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        sign_mode: str | None = None,
        **kwargs,
    ):
        config = settings.INKTHREADABLE
        self.app_id = (app_id or config["APP_ID"]).strip()
        self.secret_key = (secret_key or config["SECRET_KEY"]).strip()
        configured = (sign_mode or config.get("AUTH_STYLE") or SIGN_QUERY).strip().lower()
        # Older .env files used header-style names. Treat anything unrecognised
        # as the documented query-string signature so a leftover value cannot
        # silently pick a scheme that will never work.
        self.sign_mode = configured if configured in {SIGN_QUERY, SIGN_PATH, SIGN_URL} else SIGN_QUERY
        default_base = base_url or config.get("BASE_URL") or "https://www.inkthreadable.co.uk/api"
        super().__init__(default_base, **kwargs)

    def check_configuration(self) -> None:
        if not self.app_id:
            raise ConfigurationError("INKTHREADABLE_APP_ID is not set (e.g. APP-00146434)")
        if not self.secret_key:
            raise ConfigurationError("INKTHREADABLE_SECRET_KEY is not set")

    def default_headers(self) -> dict[str, str]:
        return {**super().default_headers(), "Accept": "application/json"}

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    def sign(self, payload: str) -> str:
        """SHA1 hex digest of ``payload + secret``. The secret is never returned."""
        return hashlib.sha1(f"{payload}{self.secret_key}".encode("utf-8")).hexdigest()

    def signing_payload(self, path: str, query: str, *, mode: str | None = None) -> str:
        """The string that is hashed, before the secret is appended."""
        mode = mode or self.sign_mode
        if mode == SIGN_PATH:
            return f"/{path.lstrip('/')}?{query}" if query else f"/{path.lstrip('/')}"
        if mode == SIGN_URL:
            url = self.base_url + path.lstrip("/")
            return f"{url}?{query}" if query else url
        return query

    def signed_query(self, extra: dict | None = None, *, path: str = "", mode: str | None = None) -> str:
        """Return ``AppId=…&…&Signature=…`` for a GET request.

        Parameter order is the order we send them, which must match the signed
        string exactly. ``AppId`` is always first.
        """
        params: dict[str, Any] = {"AppId": self.app_id, "format": "JSON"}
        if extra:
            for key, value in extra.items():
                if value is None or value == "":
                    continue
                params[key] = value
        query = urlencode(params)
        signature = self.sign(self.signing_payload(path, query, mode=mode))
        return f"{query}&Signature={signature}"

    def _probe_orders(self, mode: str) -> tuple[bool, str, Any]:
        """One unretried GET against the listing endpoint, for discovery."""
        import requests

        query = self.signed_query({"limit": 1, "page": 1}, path="orders.php", mode=mode)
        url = self.base_url + "orders.php?" + query
        try:
            response = requests.get(url, headers=self.default_headers(), timeout=45)
        except requests.RequestException as exc:
            return False, f"{type(exc).__name__}: {exc}", None
        preview = (response.text or "").replace("\n", " ")[:180]
        if not response.ok:
            return False, f"HTTP {response.status_code} {preview}", None
        try:
            return True, "ok", response.json()
        except ValueError:
            return False, f"non-JSON response: {preview}", None

    def get_signed(self, path: str, params: dict | None = None, *, mode: str | None = None) -> Any:
        """GET an endpoint with a correctly signed query string."""
        query = self.signed_query(params, path=path, mode=mode)
        return self.get_json(f"{path.lstrip('/')}?{query}")

    def post_signed(self, path: str, body: str) -> Any:
        """POST an endpoint. The signature is of the raw body, not the query."""
        query = urlencode({"AppId": self.app_id, "Signature": self.sign(body)})
        return self.request(
            "POST",
            f"{path.lstrip('/')}?{query}",
            headers={"Content-Type": "application/json"},
        ).json()

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def check_connection(self) -> ConnectionCheck:
        try:
            self.check_configuration()
        except ConfigurationError as exc:
            return ConnectionCheck(
                service="Inkthreadable",
                ok=False,
                detail=str(exc),
                hint="Set the missing value in your .env file, then re-run this command.",
            )

        last_error = ""
        # Prefer the listing endpoint: orders/count.php has been observed to 500
        # even when the account is valid. One attempt per mode, no retries.
        for mode in dict.fromkeys((self.sign_mode, SIGN_QUERY, SIGN_PATH, SIGN_URL)):
            ok, detail, payload = self._probe_orders(mode)
            if ok:
                records = [unwrap_order(r) for r in as_list(payload) if unwrap_order(r)]
                self.sign_mode = mode
                return ConnectionCheck(
                    service="Inkthreadable",
                    ok=True,
                    detail=f"Authenticated (SHA1 {mode} signature). Listing returned {len(records)} order(s).",
                    hint=(
                        f"Add INKTHREADABLE_AUTH_STYLE={mode} to your .env so this no longer needs probing."
                        if mode != (settings.INKTHREADABLE.get("AUTH_STYLE") or "").strip()
                        else ""
                    ),
                    samples={"Signature mode": mode, "Base URL": self.base_url.rstrip("/")},
                )
            last_error = f"{mode}: {detail}"
            logger.info("inkthreadable: sign mode %s failed: %s", mode, detail)

        return ConnectionCheck(
            service="Inkthreadable",
            ok=False,
            detail=f"Could not authenticate against {self.base_url}. Last error: {last_error}",
            hint=(
                "Confirm INKTHREADABLE_APP_ID and INKTHREADABLE_SECRET_KEY, and that "
                "INKTHREADABLE_BASE_URL is https://www.inkthreadable.co.uk/api"
            ),
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def count_orders(self, **params) -> int | None:
        payload = self.get_signed("orders/count.php", params or None)
        return _extract_count(payload)

    def list_orders(self, **params) -> list[dict]:
        payload = self.get_signed("orders.php", params or None)
        return [unwrap_order(record) for record in as_list(payload) if unwrap_order(record)]

    def get_order(self, order_id: int | str) -> dict:
        payload = self.get_signed("order.php", {"id": order_id})
        if isinstance(payload, dict) and isinstance(payload.get("order"), dict):
            return payload["order"]
        if isinstance(payload, dict) and payload.get("id") is not None:
            return payload
        raise IntegrationError(f"inkthreadable: unexpected order payload for id={order_id}")

    def iter_orders(self, *, since_id: int | None = None, page_size: int = 10) -> Iterator[dict]:
        """Walk every order, page by page.

        Stops on an empty page. ``since_id`` is the incremental cursor: only
        orders at or after that id are returned.
        """
        page = 1
        seen_first: str | None = None
        while True:
            params: dict[str, Any] = {"page": page, "limit": page_size}
            if since_id is not None:
                params["since_id"] = since_id
            records = self.list_orders(**params)
            if not records:
                return
            first_id = str(records[0].get("id") or "")
            if first_id and first_id == seen_first:
                logger.warning("inkthreadable: page %s repeated earlier content; stopping", page)
                return
            seen_first = first_id
            yield from records
            if len(records) < page_size:
                return
            page += 1


def _extract_count(payload: Any) -> int | None:
    if isinstance(payload, dict) and payload.get("count") is not None:
        try:
            return int(payload["count"])
        except (TypeError, ValueError):
            return None
    if isinstance(payload, list):
        return len(payload)
    return None
