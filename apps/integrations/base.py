"""Shared HTTP client behaviour for external APIs.

Both suppliers of data — Shopify and Inkthreadable — are third-party services
that will occasionally rate-limit, time out or return an error. The rules applied
here are the same for both:

* Retry only what is worth retrying: timeouts, connection errors, 429 and 5xx.
  A 400 or 404 is a bug or a genuine absence and retrying it just wastes time.
* Honour a ``Retry-After`` header when the server sends one, rather than guessing.
* Back off exponentially with jitter, so repeated failures do not turn into a
  tight loop.
* Never write a credential to the log. Errors are logged with the URL path and
  status only.
* Raise a typed exception so callers can distinguish a misconfiguration from a
  transient outage.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_ATTEMPTS = 4


class IntegrationError(Exception):
    """Base class for all external integration failures."""


class ConfigurationError(IntegrationError):
    """A credential or setting is missing or malformed.

    Raised before any network call, because retrying cannot help.
    """


class AuthenticationError(IntegrationError):
    """The service rejected our credentials (401/403)."""


class RateLimitError(IntegrationError):
    """The service is rate-limiting us and retries were exhausted."""


class TransientError(IntegrationError):
    """A temporary failure that did not resolve within the retry budget."""


class ResponseError(IntegrationError):
    """The service returned an error we should not retry."""

    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class RequestLog:
    """A record of every call made, for the Data Health page."""

    method: str
    path: str
    status: int | None = None
    attempts: int = 1
    duration_ms: int = 0
    error: str = ""


class ApiClient:
    """A small, dependency-light HTTP client with sane retry behaviour."""

    #: Prefixed to log messages so failures are attributable.
    service_name = "api"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        session: requests.Session | None = None,
    ):
        if not base_url:
            raise ConfigurationError(f"{self.service_name}: no base URL configured")
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.session = session or requests.Session()
        self.call_log: list[RequestLog] = []

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def default_headers(self) -> dict[str, str]:
        """Headers sent with every request, including authentication."""
        return {"Accept": "application/json", "User-Agent": "StayLit-BusinessManager/1.0"}

    def check_configuration(self) -> None:
        """Raise ``ConfigurationError`` if the client cannot possibly work."""

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        headers: dict | None = None,
    ) -> requests.Response:
        """Perform a request, retrying transient failures."""
        self.check_configuration()

        url = urljoin(self.base_url, path.lstrip("/"))
        merged = {**self.default_headers(), **(headers or {})}
        record = RequestLog(method=method.upper(), path=path)
        started = time.monotonic()
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            record.attempts = attempt
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=merged,
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                logger.warning(
                    "%s: %s %s failed on attempt %s/%s (%s)",
                    self.service_name, method, path, attempt, self.max_attempts, type(exc).__name__,
                )
                if attempt == self.max_attempts:
                    break
                time.sleep(self._backoff(attempt))
                continue

            record.status = response.status_code

            if response.status_code in {401, 403}:
                self._finish(record, started)
                raise AuthenticationError(
                    f"{self.service_name}: credentials were rejected "
                    f"(HTTP {response.status_code} on {path}). Check the values in your .env file."
                )

            if response.status_code in RETRYABLE_STATUS:
                last_error = ResponseError(
                    f"HTTP {response.status_code}", status=response.status_code, body=response.text[:500]
                )
                if attempt == self.max_attempts:
                    break
                delay = self._retry_after(response) or self._backoff(attempt)
                logger.warning(
                    "%s: %s %s returned %s; retrying in %.1fs (attempt %s/%s)",
                    self.service_name, method, path, response.status_code, delay, attempt, self.max_attempts,
                )
                time.sleep(delay)
                continue

            if not response.ok:
                self._finish(record, started)
                raise ResponseError(
                    f"{self.service_name}: {method} {path} returned HTTP {response.status_code}",
                    status=response.status_code,
                    body=response.text[:500],
                )

            self._finish(record, started)
            return response

        # Retry budget exhausted.
        self._finish(record, started, error=str(last_error))
        status = getattr(last_error, "status", None)
        if status == 429:
            raise RateLimitError(
                f"{self.service_name}: still rate-limited after {self.max_attempts} attempts on {path}"
            ) from last_error
        raise TransientError(
            f"{self.service_name}: {method} {path} failed after {self.max_attempts} attempts "
            f"({last_error})"
        ) from last_error

    def get_json(self, path: str, *, params: dict | None = None) -> Any:
        return self.request("GET", path, params=params).json()

    def post_json(self, path: str, payload: Any, *, params: dict | None = None) -> Any:
        return self.request("POST", path, json=payload, params=params).json()

    # ------------------------------------------------------------------

    def _finish(self, record: RequestLog, started: float, *, error: str = "") -> None:
        record.duration_ms = int((time.monotonic() - started) * 1000)
        record.error = error
        self.call_log.append(record)

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        """Seconds to wait, if the server told us."""
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with jitter, capped so a run cannot stall for long."""
        return min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)


@dataclass
class ConnectionCheck:
    """The result of testing whether an integration is usable.

    Deliberately returns a structured result rather than raising, so a test
    command can report on several integrations in one pass and a Data Health page
    can show the state of each.
    """

    service: str
    ok: bool
    detail: str = ""
    hint: str = ""
    samples: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        lines = [f"{'PASS' if self.ok else 'FAIL'}  {self.service}"]
        if self.detail:
            lines.append(f"      {self.detail}")
        if self.hint:
            lines.append(f"      Hint: {self.hint}")
        for key, value in self.samples.items():
            lines.append(f"      {key}: {value}")
        return "\n".join(lines)
