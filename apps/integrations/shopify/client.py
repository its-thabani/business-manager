"""Shopify Admin API client.

Uses the **GraphQL** Admin API rather than REST. Shopify has restricted the REST
order endpoints for newly created apps and is steadily moving functionality to
GraphQL only, so building on GraphQL avoids a forced rewrite later. GraphQL also
lets one request fetch an order together with its line items, refunds, shipping
and fee-bearing transactions, which keeps the number of round trips low.

Authentication accepts either:

* A static Admin API access token (``shpat_…``), for custom apps created in the
  store admin.
* The app's client ID and client secret (``shpss_…``). These are exchanged for
  a short-lived access token via the client-credentials grant. That grant works
  when the app and the store belong to the same Shopify organisation.

The client ID / secret cannot be sent to the Admin API directly. They only
identify the app at the token endpoint.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urlencode

import requests
from django.conf import settings

from apps.integrations.base import (
    ApiClient,
    AuthenticationError,
    ConfigurationError,
    ConnectionCheck,
    ResponseError,
)

logger = logging.getLogger(__name__)

COST_SAFETY_FLOOR = 200
TOKEN_REFRESH_SKEW_SECONDS = 60


@dataclass
class CachedToken:
    token: str
    expires_at: float
    scope: str = ""


_TOKEN_CACHE: dict[str, CachedToken] = {}


class ShopifyError(ResponseError):
    """A GraphQL-level error returned with an HTTP 200."""


class ShopifyClient(ApiClient):
    service_name = "shopify"

    def __init__(
        self,
        *,
        store: str | None = None,
        access_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        api_version: str | None = None,
        **kwargs,
    ):
        config = settings.SHOPIFY
        self.store = (store or config["STORE"]).strip().replace("https://", "").replace("http://", "").rstrip("/")
        self._static_token = (access_token if access_token is not None else config["ACCESS_TOKEN"]).strip()
        self.client_id = (client_id if client_id is not None else config.get("CLIENT_ID") or "").strip()
        self.client_secret = (client_secret if client_secret is not None else config.get("CLIENT_SECRET") or "").strip()
        self.api_version = (api_version or config["API_VERSION"]).strip()
        self.access_token = self._static_token
        self.granted_scopes = ""
        super().__init__(f"https://{self.store}/admin/api/{self.api_version}/", **kwargs)

    def default_headers(self) -> dict[str, str]:
        return {
            **super().default_headers(),
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }

    def check_configuration(self) -> None:
        if not self.store:
            raise ConfigurationError("SHOPIFY_STORE is not set (e.g. your-store.myshopify.com)")
        if not self.store.endswith(".myshopify.com"):
            raise ConfigurationError(
                f"SHOPIFY_STORE should be the myshopify.com domain, got {self.store!r}"
            )
        if not self._static_token and not (self.client_id and self.client_secret):
            raise ConfigurationError(
                "Shopify has no credentials. Set SHOPIFY_ACCESS_TOKEN (an Admin API "
                "token starting shpat_) or both SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET "
                "so a token can be requested."
            )

    def can_exchange_token(self) -> bool:
        return bool(self.client_id and self.client_secret)

    # ------------------------------------------------------------------
    # Access token
    # ------------------------------------------------------------------

    def ensure_access_token(self, *, force: bool = False) -> str:
        """Return a usable Admin API token, exchanging credentials if needed."""
        self.check_configuration()
        if self._static_token:
            self.access_token = self._static_token
            return self.access_token

        cache_key = f"{self.store}:{self.client_id}"
        cached = _TOKEN_CACHE.get(cache_key)
        if (
            not force
            and cached
            and cached.token
            and time.time() < cached.expires_at - TOKEN_REFRESH_SKEW_SECONDS
        ):
            self.access_token = cached.token
            self.granted_scopes = cached.scope
            return self.access_token

        token, expires_in, scope = self._exchange_client_credentials()
        _TOKEN_CACHE[cache_key] = CachedToken(
            token=token,
            expires_at=time.time() + expires_in,
            scope=scope,
        )
        self.access_token = token
        self.granted_scopes = scope
        return token

    def _exchange_client_credentials(self) -> tuple[str, int, str]:
        """Trade the app client ID/secret for a 24-hour Admin API token."""
        url = f"https://{self.store}/admin/oauth/access_token"
        try:
            response = requests.post(
                url,
                data=urlencode({
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                }),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "User-Agent": "StayLit-BusinessManager/1.0",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AuthenticationError(
                f"shopify: could not reach the token endpoint ({type(exc).__name__})"
            ) from exc

        if not response.ok:
            raise AuthenticationError(self._token_error_message(response))

        try:
            body = response.json()
        except ValueError as exc:
            raise AuthenticationError("shopify: token endpoint returned a non-JSON body") from exc

        token = (body.get("access_token") or "").strip()
        if not token:
            raise AuthenticationError("shopify: token endpoint did not return an access_token")
        expires_in = int(body.get("expires_in") or 86399)
        scope = body.get("scope") or ""
        logger.info("shopify: obtained access token (expires_in=%s, scopes=%s)", expires_in, scope or "unknown")
        return token, expires_in, scope

    @staticmethod
    def _token_error_message(response: requests.Response) -> str:
        text = (response.text or "")[:400]
        lowered = text.lower()
        prefix = f"shopify: token request failed (HTTP {response.status_code})"
        if "shop_not_permitted" in lowered or "client credentials cannot" in lowered:
            return (
                f"{prefix}. The client-credentials grant only works when the app and "
                "the store belong to the same Shopify organisation in the Dev Dashboard. "
                "If this is a store custom app, create an Admin API access token (shpat_) "
                "under Settings → Apps and sales channels → Develop apps → API credentials "
                "and set SHOPIFY_ACCESS_TOKEN."
            )
        return f"{prefix}: {text or 'no body'}"

    def request(self, method, path, **kwargs):
        self.ensure_access_token()
        try:
            return super().request(method, path, **kwargs)
        except AuthenticationError:
            if self._static_token or not self.can_exchange_token():
                raise
            logger.warning("shopify: access token rejected, requesting a new one")
            self.ensure_access_token(force=True)
            return super().request(method, path, **kwargs)

    # ------------------------------------------------------------------
    # GraphQL
    # ------------------------------------------------------------------

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query and return its ``data`` payload.

        Shopify returns HTTP 200 even for query errors, so the body has to be
        inspected rather than relying on the status code.
        """
        payload = {"query": query, "variables": variables or {}}
        response = self.request("POST", "graphql.json", json=payload)
        body = response.json()

        if errors := body.get("errors"):
            messages = "; ".join(e.get("message", str(e)) for e in errors)
            if any(e.get("extensions", {}).get("code") == "THROTTLED" for e in errors):
                wait = self._throttle_wait(body)
                logger.warning("shopify: throttled, waiting %.1fs before retrying", wait)
                time.sleep(wait)
                return self.graphql(query, variables)
            raise ShopifyError(f"shopify: GraphQL error: {messages}", status=200, body=str(body)[:500])

        self._respect_cost_budget(body)
        data = body.get("data")
        if data is None:
            raise ShopifyError("shopify: GraphQL response contained no data", status=200, body=str(body)[:500])
        return data

    def paginate(
        self,
        query: str,
        *,
        connection: str,
        variables: dict | None = None,
        page_size: int = 100,
    ) -> Iterator[dict]:
        """Walk a cursor-paginated GraphQL connection, yielding each node."""
        cursor: str | None = None
        while True:
            data = self.graphql(query, {**(variables or {}), "first": page_size, "after": cursor})
            node = data
            for part in connection.split("."):
                node = node[part]

            for edge in node.get("edges", []):
                yield edge["node"]

            page_info = node.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return
            cursor = page_info.get("endCursor")
            if not cursor:
                return

    def _respect_cost_budget(self, body: dict) -> None:
        throttle = (body.get("extensions") or {}).get("cost", {}).get("throttleStatus")
        if not throttle:
            return
        available = throttle.get("currentlyAvailable", 0)
        restore_rate = throttle.get("restoreRate") or 50
        if available < COST_SAFETY_FLOOR:
            wait = (COST_SAFETY_FLOOR - available) / restore_rate
            logger.info("shopify: cost budget low (%s left), pausing %.1fs", available, wait)
            time.sleep(min(wait, 10))

    @staticmethod
    def _throttle_wait(body: dict) -> float:
        throttle = (body.get("extensions") or {}).get("cost", {}).get("throttleStatus", {})
        requested = (body.get("extensions") or {}).get("cost", {}).get("requestedQueryCost", 100)
        available = throttle.get("currentlyAvailable", 0)
        restore_rate = throttle.get("restoreRate") or 50
        return min(max((requested - available) / restore_rate, 1.0), 15.0)

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def check_connection(self) -> ConnectionCheck:
        try:
            self.check_configuration()
        except ConfigurationError as exc:
            return ConnectionCheck(
                service="Shopify",
                ok=False,
                detail=str(exc),
                hint="Set the missing value in your .env file, then re-run this command.",
            )

        try:
            self.ensure_access_token()
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            return ConnectionCheck(
                service="Shopify",
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                hint=self._diagnose(exc),
            )

        query = """
        query ShopInfo {
          shop {
            name
            myshopifyDomain
            currencyCode
            ianaTimezone
            plan { displayName }
          }
        }
        """
        try:
            shop = self.graphql(query)["shop"]
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            return ConnectionCheck(
                service="Shopify",
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                hint=self._diagnose(exc),
            )

        samples = {
            "Currency": shop["currencyCode"],
            "Timezone": shop["ianaTimezone"],
            "Plan": (shop.get("plan") or {}).get("displayName", "unknown"),
            "API version": self.api_version,
            "Auth": "static Admin API token" if self._static_token else "client-credentials grant",
        }
        if self.granted_scopes:
            samples["Granted scopes"] = self.granted_scopes
        return ConnectionCheck(
            service="Shopify",
            ok=True,
            detail=f"Connected to {shop['name']} ({shop['myshopifyDomain']})",
            samples=samples,
        )

    @staticmethod
    def _diagnose(exc: Exception) -> str:
        text = str(exc).lower()
        if "shop_not_permitted" in text or "client credentials cannot" in text:
            return (
                "Create an Admin API access token in the store (Develop apps → API credentials) "
                "and set SHOPIFY_ACCESS_TOKEN, or move the app and store into the same Dev Dashboard organisation."
            )
        if "credentials were rejected" in text or "401" in text or "403" in text:
            return (
                "The token was rejected. Confirm the app is installed on this store and has "
                "read_orders, read_products, read_customers and read_all_orders."
            )
        if "access denied" in text or "scope" in text:
            return (
                "The token is valid but lacks a required scope. Add read_orders, read_products, "
                "read_customers and read_all_orders, then reinstall or release a new app version."
            )
        if "404" in text:
            return "Check the store domain and that the API version is valid for this store."
        return "Check the store domain, API version and credentials, then try again."

    # ------------------------------------------------------------------
    # Read helpers used by the sync layer
    # ------------------------------------------------------------------

    PRODUCTS_QUERY = """
    query Products($first: Int!, $after: String, $query: String) {
      products(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
        pageInfo { hasNextPage endCursor }
        edges {
          node {
            id
            legacyResourceId
            title
            handle
            status
            productType
            vendor
            tags
            createdAt
            updatedAt
            publishedAt
            variants(first: 100) {
              edges {
                node {
                  id
                  legacyResourceId
                  title
                  sku
                  barcode
                  price
                  compareAtPrice
                  inventoryQuantity
                  inventoryItem {
                    unitCost { amount currencyCode }
                  }
                  selectedOptions { name value }
                }
              }
            }
          }
        }
      }
    }
    """

    ORDERS_QUERY = """
    query Orders($first: Int!, $after: String, $query: String) {
      orders(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
        pageInfo { hasNextPage endCursor }
        edges {
          node {
            id
            legacyResourceId
            name
            email
            createdAt
            updatedAt
            processedAt
            cancelledAt
            cancelReason
            displayFinancialStatus
            displayFulfillmentStatus
            currencyCode
            test
            sourceName
            tags
            currentTotalPriceSet { shopMoney { amount currencyCode } }
            currentSubtotalPriceSet { shopMoney { amount } }
            totalPriceSet { shopMoney { amount } }
            subtotalPriceSet { shopMoney { amount } }
            totalDiscountsSet { shopMoney { amount } }
            totalShippingPriceSet { shopMoney { amount } }
            totalTaxSet { shopMoney { amount } }
            totalRefundedSet { shopMoney { amount } }
            netPaymentSet { shopMoney { amount } }
            customer {
              id
              legacyResourceId
              displayName
              firstName
              lastName
              email
              numberOfOrders
              createdAt
            }
            shippingAddress { countryCodeV2 city zip }
            discountCodes
            lineItems(first: 100) {
              edges {
                node {
                  id
                  name
                  title
                  variantTitle
                  sku
                  quantity
                  currentQuantity
                  originalUnitPriceSet { shopMoney { amount } }
                  discountedUnitPriceSet { shopMoney { amount } }
                  totalDiscountSet { shopMoney { amount } }
                  taxLines { priceSet { shopMoney { amount } } }
                  requiresShipping
                  product { id legacyResourceId title productType }
                  variant { id legacyResourceId title sku selectedOptions { name value } }
                }
              }
            }
            transactions {
              id
              kind
              status
              gateway
              processedAt
              amountSet { shopMoney { amount } }
              fees { amount { amount } rate type }
            }
            refunds {
              id
              createdAt
              note
              totalRefundedSet { shopMoney { amount } }
              refundLineItems(first: 50) {
                edges {
                  node {
                    quantity
                    restocked
                    subtotalSet { shopMoney { amount } }
                    lineItem { id }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    def iter_products(self, *, updated_after: str | None = None, page_size: int = 50) -> Iterator[dict]:
        query = f"updated_at:>='{updated_after}'" if updated_after else None
        yield from self.paginate(
            self.PRODUCTS_QUERY, connection="products", variables={"query": query}, page_size=page_size
        )

    def iter_orders(self, *, updated_after: str | None = None, page_size: int = 25) -> Iterator[dict]:
        """Iterate orders, optionally only those changed since a timestamp.

        Filtering on ``updated_at`` rather than ``created_at`` is what makes an
        incremental sync correct: an old order whose refund status changed must be
        picked up again.
        """
        query = f"updated_at:>='{updated_after}'" if updated_after else None
        yield from self.paginate(
            self.ORDERS_QUERY, connection="orders", variables={"query": query}, page_size=page_size
        )

    def count_orders(self) -> int | None:
        """Total order count, or ``None`` if the shop does not report it."""
        try:
            data = self.graphql("query { ordersCount { count precision } }")
        except ShopifyError:
            return None
        return (data.get("ordersCount") or {}).get("count")

    def count_products(self) -> int | None:
        try:
            data = self.graphql("query { productsCount { count } }")
        except ShopifyError:
            return None
        return (data.get("productsCount") or {}).get("count")
