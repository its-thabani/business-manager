"""Tests for the shared HTTP client and the two service clients.

No network access: every response is stubbed, so these run anywhere and cannot
be broken by a third-party outage.
"""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest
import requests

from apps.integrations.base import (
    ApiClient,
    AuthenticationError,
    ConfigurationError,
    RateLimitError,
    ResponseError,
    TransientError,
)
from apps.integrations.inkthreadable.client import InkthreadableClient, SIGN_QUERY
from apps.integrations.parsing import as_list, unwrap_order
from apps.integrations.shopify.client import ShopifyClient, ShopifyError, _TOKEN_CACHE


def stub_response(status=200, body=None, headers=None, text=""):
    response = Mock(spec=requests.Response)
    response.status_code = status
    response.ok = 200 <= status < 300
    response.headers = headers or {}
    response.text = text or (json.dumps(body) if body is not None else "")
    response.json.return_value = body if body is not None else {}
    return response


class RecordingClient(ApiClient):
    service_name = "test"


@pytest.fixture
def no_sleep():
    """Remove backoff delays so retry tests run instantly."""
    with patch("apps.integrations.base.time.sleep") as sleep:
        yield sleep


class TestRetryBehaviour:
    def test_successful_request_is_not_retried(self, no_sleep):
        session = Mock()
        session.request.return_value = stub_response(200, {"ok": True})
        client = RecordingClient("https://example.test/", session=session)

        assert client.get_json("thing") == {"ok": True}
        assert session.request.call_count == 1
        assert no_sleep.call_count == 0

    def test_a_timeout_is_retried_then_reported(self, no_sleep):
        session = Mock()
        session.request.side_effect = requests.Timeout("too slow")
        client = RecordingClient("https://example.test/", session=session, max_attempts=3)

        with pytest.raises(TransientError):
            client.get_json("thing")
        assert session.request.call_count == 3

    def test_a_transient_failure_that_recovers_returns_the_good_response(self, no_sleep):
        session = Mock()
        session.request.side_effect = [
            stub_response(503),
            stub_response(200, {"recovered": True}),
        ]
        client = RecordingClient("https://example.test/", session=session)

        assert client.get_json("thing") == {"recovered": True}
        assert session.request.call_count == 2

    def test_rate_limit_is_reported_distinctly_once_retries_run_out(self, no_sleep):
        session = Mock()
        session.request.return_value = stub_response(429, headers={"Retry-After": "2"})
        client = RecordingClient("https://example.test/", session=session, max_attempts=2)

        with pytest.raises(RateLimitError):
            client.get_json("thing")

    def test_retry_after_header_is_honoured(self, no_sleep):
        session = Mock()
        session.request.side_effect = [
            stub_response(429, headers={"Retry-After": "7"}),
            stub_response(200, {}),
        ]
        RecordingClient("https://example.test/", session=session).get_json("thing")
        no_sleep.assert_called_once_with(7.0)

    def test_a_malformed_retry_after_falls_back_to_backoff(self, no_sleep):
        session = Mock()
        session.request.side_effect = [
            stub_response(503, headers={"Retry-After": "soon"}),
            stub_response(200, {}),
        ]
        RecordingClient("https://example.test/", session=session).get_json("thing")
        assert no_sleep.call_count == 1
        assert no_sleep.call_args[0][0] > 0

    @pytest.mark.parametrize("status", [400, 404, 422])
    def test_client_errors_are_not_retried(self, status, no_sleep):
        session = Mock()
        session.request.return_value = stub_response(status, text="nope")
        client = RecordingClient("https://example.test/", session=session)

        with pytest.raises(ResponseError) as exc_info:
            client.get_json("thing")
        assert exc_info.value.status == status
        assert session.request.call_count == 1

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failures_are_raised_immediately(self, status, no_sleep):
        session = Mock()
        session.request.return_value = stub_response(status)
        client = RecordingClient("https://example.test/", session=session)

        with pytest.raises(AuthenticationError):
            client.get_json("thing")
        assert session.request.call_count == 1

    def test_every_call_is_logged_for_reporting(self, no_sleep):
        session = Mock()
        session.request.return_value = stub_response(200, {})
        client = RecordingClient("https://example.test/", session=session)
        client.get_json("a")
        client.get_json("b")
        assert [entry.path for entry in client.call_log] == ["a", "b"]

    def test_an_empty_base_url_is_rejected_up_front(self):
        with pytest.raises(ConfigurationError):
            RecordingClient("")

    def test_backoff_is_bounded(self):
        # A long run of failures must not produce an unbounded wait.
        assert ApiClient._backoff(20) <= 8.5


class TestShopifyConfiguration:
    def test_missing_all_credentials_is_explained(self, settings):
        settings.SHOPIFY = {
            "STORE": "scctj4-i8.myshopify.com", "ACCESS_TOKEN": "",
            "CLIENT_ID": "", "CLIENT_SECRET": "", "API_VERSION": "2025-01",
        }
        check = ShopifyClient().check_connection()
        assert not check.ok
        assert "SHOPIFY_ACCESS_TOKEN" in check.detail or "CLIENT_ID" in check.detail

    def test_client_id_and_secret_are_enough_to_pass_configuration(self, settings):
        settings.SHOPIFY = {
            "STORE": "scctj4-i8.myshopify.com", "ACCESS_TOKEN": "",
            "CLIENT_ID": "abc", "CLIENT_SECRET": "shpss_x", "API_VERSION": "2025-01",
        }
        ShopifyClient().check_configuration()

    def test_a_non_myshopify_domain_is_rejected(self, settings):
        settings.SHOPIFY = {
            "STORE": "staylit.com", "ACCESS_TOKEN": "shpat_x",
            "CLIENT_ID": "", "CLIENT_SECRET": "", "API_VERSION": "2025-01",
        }
        with pytest.raises(ConfigurationError, match="myshopify.com"):
            ShopifyClient().check_configuration()

    def test_a_protocol_prefix_in_the_store_setting_is_tolerated(self, settings):
        settings.SHOPIFY = {
            "STORE": "https://scctj4-i8.myshopify.com/", "ACCESS_TOKEN": "shpat_x",
            "CLIENT_ID": "", "CLIENT_SECRET": "", "API_VERSION": "2025-01",
        }
        client = ShopifyClient()
        assert client.store == "scctj4-i8.myshopify.com"
        assert client.base_url == "https://scctj4-i8.myshopify.com/admin/api/2025-01/"

    def test_the_token_is_sent_in_the_shopify_header(self, settings):
        settings.SHOPIFY = {
            "STORE": "s.myshopify.com", "ACCESS_TOKEN": "shpat_secret",
            "CLIENT_ID": "", "CLIENT_SECRET": "", "API_VERSION": "2025-01",
        }
        assert ShopifyClient().default_headers()["X-Shopify-Access-Token"] == "shpat_secret"


class TestShopifyTokenExchange:
    def test_a_static_token_is_used_without_calling_the_token_endpoint(self, settings):
        settings.SHOPIFY = {
            "STORE": "s.myshopify.com", "ACCESS_TOKEN": "shpat_static",
            "CLIENT_ID": "abc", "CLIENT_SECRET": "shpss_x", "API_VERSION": "2025-01",
        }
        with patch("apps.integrations.shopify.client.requests.post") as post:
            token = ShopifyClient().ensure_access_token()
        assert token == "shpat_static"
        post.assert_not_called()

    def test_client_credentials_are_exchanged_and_cached(self, settings):
        settings.SHOPIFY = {
            "STORE": "s.myshopify.com", "ACCESS_TOKEN": "",
            "CLIENT_ID": "abc", "CLIENT_SECRET": "shpss_x", "API_VERSION": "2025-01",
        }
        _TOKEN_CACHE.clear()
        response = stub_response(200, {"access_token": "exchanged", "expires_in": 86399, "scope": "read_orders"})
        with patch("apps.integrations.shopify.client.requests.post", return_value=response) as post:
            first = ShopifyClient().ensure_access_token()
            second = ShopifyClient().ensure_access_token()
        assert first == second == "exchanged"
        assert post.call_count == 1

    def test_a_rejected_grant_explains_the_organisation_rule(self, settings):
        settings.SHOPIFY = {
            "STORE": "s.myshopify.com", "ACCESS_TOKEN": "",
            "CLIENT_ID": "abc", "CLIENT_SECRET": "shpss_x", "API_VERSION": "2025-01",
        }
        _TOKEN_CACHE.clear()
        response = stub_response(400, text="Oauth error shop_not_permitted: Client credentials cannot be performed")
        with patch("apps.integrations.shopify.client.requests.post", return_value=response):
            check = ShopifyClient().check_connection()
        assert not check.ok
        assert "organisation" in check.detail.lower() or "organization" in check.detail.lower() or "shpat" in check.detail.lower()


class TestShopifyGraphql:
    @pytest.fixture
    def client(self, settings):
        settings.SHOPIFY = {
            "STORE": "s.myshopify.com", "ACCESS_TOKEN": "shpat_x",
            "CLIENT_ID": "", "CLIENT_SECRET": "", "API_VERSION": "2025-01",
        }
        return ShopifyClient(session=Mock())

    def test_a_graphql_error_returned_with_http_200_is_raised(self, client):
        client.session.request.return_value = stub_response(
            200, {"errors": [{"message": "Field 'nope' doesn't exist"}]}
        )
        with pytest.raises(ShopifyError, match="doesn't exist"):
            client.graphql("query { nope }")

    def test_a_throttle_error_waits_and_retries(self, client, no_sleep):
        throttled = stub_response(200, {
            "errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
            "extensions": {"cost": {"requestedQueryCost": 100,
                                    "throttleStatus": {"currentlyAvailable": 0, "restoreRate": 50}}},
        })
        client.session.request.side_effect = [throttled, stub_response(200, {"data": {"shop": {"name": "S"}}})]

        assert client.graphql("query { shop { name } }")["shop"]["name"] == "S"
        assert no_sleep.called

    def test_a_low_cost_budget_causes_a_proactive_pause(self, client, no_sleep):
        client.session.request.return_value = stub_response(200, {
            "data": {"shop": {"name": "S"}},
            "extensions": {"cost": {"throttleStatus": {"currentlyAvailable": 10, "restoreRate": 50}}},
        })
        client.graphql("query { shop { name } }")
        assert no_sleep.called

    def test_pagination_follows_cursors_and_stops(self, client):
        page_one = stub_response(200, {"data": {"orders": {
            "pageInfo": {"hasNextPage": True, "endCursor": "cur1"},
            "edges": [{"node": {"name": "#1"}}, {"node": {"name": "#2"}}],
        }}})
        page_two = stub_response(200, {"data": {"orders": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [{"node": {"name": "#3"}}],
        }}})
        client.session.request.side_effect = [page_one, page_two]

        names = [n["name"] for n in client.paginate("query{}", connection="orders")]
        assert names == ["#1", "#2", "#3"]

    def test_pagination_stops_if_a_cursor_is_missing_despite_hasnextpage(self, client):
        # Guards against an endless loop if the API reports inconsistent paging.
        client.session.request.return_value = stub_response(200, {"data": {"orders": {
            "pageInfo": {"hasNextPage": True, "endCursor": None},
            "edges": [{"node": {"name": "#1"}}],
        }}})
        assert len(list(client.paginate("query{}", connection="orders"))) == 1


class TestInkthreadableConfiguration:
    @pytest.fixture(autouse=True)
    def configured(self, settings):
        settings.INKTHREADABLE = {
            "APP_ID": "APP-00146434",
            "SECRET_KEY": "secret",
            "BASE_URL": "https://www.inkthreadable.co.uk/api",
            "AUTH_STYLE": "query",
        }

    def test_missing_app_id_is_reported(self, settings):
        settings.INKTHREADABLE = {**settings.INKTHREADABLE, "APP_ID": ""}
        check = InkthreadableClient().check_connection()
        assert not check.ok
        assert "INKTHREADABLE_APP_ID" in check.detail

    def test_the_secret_never_appears_in_the_signed_query(self):
        query = InkthreadableClient().signed_query(path="orders.php")
        assert "secret" not in query
        assert query.startswith("AppId=APP-00146434")
        assert "Signature=" in query

    def test_the_signature_is_a_40_character_hex_digest(self):
        client = InkthreadableClient()
        signature = client.sign("AppId=APP-00146434")
        assert len(signature) == 40
        assert signature == client.sign("AppId=APP-00146434")
        assert signature != client.sign("AppId=OTHER")

    def test_query_mode_signs_only_the_query_string(self):
        client = InkthreadableClient(sign_mode=SIGN_QUERY)
        assert client.signing_payload("orders.php", "AppId=APP-00146434") == "AppId=APP-00146434"

    def test_an_unknown_auth_style_falls_back_to_query(self, settings):
        settings.INKTHREADABLE = {**settings.INKTHREADABLE, "AUTH_STYLE": "headers"}
        assert InkthreadableClient().sign_mode == SIGN_QUERY

    def test_a_successful_listing_is_reported(self):
        with patch("requests.get", return_value=stub_response(200, [{"order": {"id": 1}}])):
            check = InkthreadableClient().check_connection()
        assert check.ok
        assert "1 order" in check.detail

    def test_a_rejected_request_is_reported(self):
        with patch("requests.get", return_value=stub_response(401, text="denied")):
            check = InkthreadableClient().check_connection()
        assert not check.ok


class TestResponseShapeDetection:
    @pytest.mark.parametrize(
        "payload,expected",
        [
            ([{"a": 1}, {"b": 2}], 2),
            ({"data": [{"a": 1}]}, 1),
            ({"items": [{"a": 1}, {"b": 2}]}, 2),
            ({"results": []}, 0),
            ({"products": [{"a": 1}]}, 1),
            ({"id": 5, "title": "Tee"}, 1),  # a single bare record
        ],
    )
    def test_records_are_found_regardless_of_wrapper(self, payload, expected):
        assert len(as_list(payload)) == expected

    def test_an_inkthreadable_wrapped_order_is_unwrapped(self):
        assert unwrap_order({"order": {"id": 9, "status": "paid"}})["id"] == 9
        assert unwrap_order({"id": 9})["id"] == 9
