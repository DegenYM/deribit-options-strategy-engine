from __future__ import annotations

import itertools
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import requests

from .config import BotConfig
from .exceptions import AuthenticationError, ExchangeError, TransientExchangeError
from .exchange_throttle import pace_exchange_request
from .utils import format_decimal, utc_now_ms


@dataclass(frozen=True)
class _CachedAuthTokens:
    access_token: str
    refresh_token: str | None
    token_expiry_ms: int


_AUTH_TOKEN_CACHE: dict[str, _CachedAuthTokens] = {}
_AUTH_CACHE_LOCK = threading.Lock()
_INSTRUMENTS_CACHE: dict[tuple[str, str, bool], tuple[float, list[dict[str, Any]]]] = {}
_INSTRUMENTS_CACHE_LOCK = threading.Lock()

# Short-TTL cache for public, read-only macro feeds (index price / chart / DVOL).
# These are queried many times per cycle (e.g. ``_currency_index_price`` is hit
# 20+ times) and are identical for all clients, so a process-global TTL cache
# both de-duplicates redundant HTTP within a cycle and keeps valuations within a
# single snapshot consistent.
_PUBLIC_READ_CACHE: dict[str, tuple[float, Any]] = {}
_PUBLIC_READ_CACHE_LOCK = threading.Lock()


def _instruments_cache_ttl_seconds() -> float:
    raw = os.environ.get("DERIBIT_INSTRUMENTS_CACHE_TTL_SEC", "300")
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 300.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return default


def _index_price_cache_ttl_seconds() -> float:
    # Index price feeds the live spot used for equity/PnL, so keep the TTL short.
    return _env_float("DERIBIT_INDEX_PRICE_CACHE_TTL_SEC", 5.0)


def _macro_cache_ttl_seconds() -> float:
    # Index chart / DVOL are daily-resolution series; a longer window is safe.
    return _env_float("DERIBIT_MACRO_CACHE_TTL_SEC", 60.0)


def _cached_public_read(key: str, ttl: float, loader: Callable[[], Any]) -> Any:
    """Return a TTL-cached public read, deep-ish copying mutable payloads."""
    if ttl <= 0:
        return loader()
    now = time.monotonic()
    with _PUBLIC_READ_CACHE_LOCK:
        cached = _PUBLIC_READ_CACHE.get(key)
        if cached is not None and (now - cached[0]) < ttl:
            return _copy_cached(cached[1])
    value = loader()
    with _PUBLIC_READ_CACHE_LOCK:
        _PUBLIC_READ_CACHE[key] = (time.monotonic(), _copy_cached(value))
    return value


def _copy_cached(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def reset_public_read_cache() -> None:
    """Clear the macro read cache (intended for tests)."""
    with _PUBLIC_READ_CACHE_LOCK:
        _PUBLIC_READ_CACHE.clear()


class DeribitClient:
    """Deribit JSON-RPC over HTTP client.

    - Uses POST + JSON-RPC body for all requests so order parameters never appear in the URL.
    - OAuth2 client_credentials auth; tokens are cached and refreshed before expiry.
    - Idempotent reads retry on transient errors; non-idempotent mutations retry at most once
      on pure connection errors and never on 5xx/timeout/429 (caller must reconcile).
    """

    RETRYABLE_STATUS_CODES = {408, 425, 500, 502, 503, 504, 520, 521, 522, 523, 524}
    RATE_LIMIT_STATUS = 429
    IDEMPOTENT_RETRY_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
    AUTH_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
    UNSAFE_CONNECTION_RETRIES = 1
    TOKEN_REFRESH_SAFETY_SECONDS = 30

    # JSON-RPC methods that mutate exchange state; must NOT be blindly retried.
    _UNSAFE_METHODS = frozenset(
        {
            "private/buy",
            "private/sell",
            "private/close_position",
            "private/cancel",
            "private/cancel_all",
            "private/cancel_all_by_currency",
            "private/cancel_all_by_instrument",
            "private/cancel_by_label",
            "private/edit",
            "private/edit_by_label",
            "private/create_combo",
            "private/submit_transfer_between_subaccounts",
        }
    )

    def __init__(self, config: BotConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expiry_ms: int = 0
        self._id_counter = itertools.count(1)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _require_credentials(self) -> None:
        if not self.config.has_private_credentials:
            raise AuthenticationError("Private Deribit method requires DERIBIT_CLIENT_ID and DERIBIT_CLIENT_SECRET")

    def _token_expired(self) -> bool:
        if not self._access_token:
            return True
        safety_ms = self.TOKEN_REFRESH_SAFETY_SECONDS * 1000
        return utc_now_ms() + safety_ms >= self._token_expiry_ms

    def _auth_cache_key(self) -> str:
        return (
            f"{self.config.rest_base_url}\0{self.config.client_id.strip().lower()}\0{self.config.client_secret.strip()}"
        )

    def _hydrate_auth_from_cache(self) -> bool:
        if not self.config.has_private_credentials:
            return False
        key = self._auth_cache_key()
        with _AUTH_CACHE_LOCK:
            cached = _AUTH_TOKEN_CACHE.get(key)
        if cached is None:
            return False
        safety_ms = self.TOKEN_REFRESH_SAFETY_SECONDS * 1000
        if utc_now_ms() + safety_ms >= cached.token_expiry_ms:
            return False
        self._access_token = cached.access_token
        self._refresh_token = cached.refresh_token
        self._token_expiry_ms = cached.token_expiry_ms
        return True

    def _publish_auth_to_cache(self) -> None:
        if not self.config.has_private_credentials or not self._access_token:
            return
        entry = _CachedAuthTokens(
            access_token=self._access_token,
            refresh_token=self._refresh_token,
            token_expiry_ms=self._token_expiry_ms,
        )
        with _AUTH_CACHE_LOCK:
            _AUTH_TOKEN_CACHE[self._auth_cache_key()] = entry

    def _invalidate_auth_cache(self) -> None:
        if not self.config.has_private_credentials:
            return
        with _AUTH_CACHE_LOCK:
            _AUTH_TOKEN_CACHE.pop(self._auth_cache_key(), None)

    def _clear_auth_tokens(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self._token_expiry_ms = 0
        self._invalidate_auth_cache()

    def _parse_jsonrpc_payload(
        self,
        response: requests.Response,
        method_name: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Return ``(payload, jsonrpc_error)`` when the body is valid JSON-RPC."""
        try:
            payload = self._parse_jsonrpc(response, method_name)
        except ExchangeError:
            return None, None
        error = payload.get("error")
        json_error = error if isinstance(error, dict) and error else None
        return payload, json_error

    def _should_retry_private_auth(
        self,
        *,
        private: bool,
        attempt: int,
        attempts: int,
        status: int,
        json_error: dict[str, Any] | None,
    ) -> bool:
        if not private or attempt >= attempts - 1:
            return False
        if status == 401:
            return True
        if json_error is not None:
            return isinstance(self._classify_error(method_name="", error=json_error), AuthenticationError)
        return False

    def _ensure_access_token(self) -> str:
        if not self._token_expired():
            assert self._access_token is not None
            return self._access_token
        self._require_credentials()
        if self._hydrate_auth_from_cache() and not self._token_expired():
            assert self._access_token is not None
            return self._access_token

        if self._refresh_token:
            try:
                self._refresh_access_token()
                assert self._access_token is not None
                return self._access_token
            except (AuthenticationError, ExchangeError, TransientExchangeError):
                self._access_token = None
                self._refresh_token = None

        result = self._call_auth(
            {
                "grant_type": "client_credentials",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
            }
        )
        self._apply_auth_result(result)
        assert self._access_token is not None
        return self._access_token

    def _refresh_access_token(self) -> None:
        assert self._refresh_token is not None
        result = self._call_auth(
            {
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            }
        )
        self._apply_auth_result(result)

    def _apply_auth_result(self, result: dict[str, Any]) -> None:
        access_token = result.get("access_token") if isinstance(result, dict) else None
        if not isinstance(access_token, str) or not access_token:
            raise AuthenticationError("Deribit auth response missing access_token")
        refresh_token = result.get("refresh_token") if isinstance(result, dict) else None
        expires_in = result.get("expires_in") if isinstance(result, dict) else None
        try:
            ttl_seconds = int(expires_in) if expires_in is not None else 0
        except (TypeError, ValueError):
            ttl_seconds = 0
        self._access_token = access_token
        self._refresh_token = refresh_token if isinstance(refresh_token, str) else None
        self._token_expiry_ms = utc_now_ms() + max(ttl_seconds, 0) * 1000
        self._publish_auth_to_cache()

    def _call_auth(self, params: dict[str, Any]) -> dict[str, Any]:
        url = self.config.rest_base_url + "/public/auth"
        attempts = len(self.AUTH_RETRY_BACKOFF_SECONDS) + 1
        for attempt in range(attempts):
            payload = self._jsonrpc_body("public/auth", params)
            response = self._post_raw(url, payload, headers={})
            status = response.status_code
            if status == self.RATE_LIMIT_STATUS:
                if attempt < attempts - 1:
                    wait = self._retry_after_seconds(response)
                    if wait is None:
                        wait = self.AUTH_RETRY_BACKOFF_SECONDS[attempt]
                    time.sleep(wait)
                    continue
                raise TransientExchangeError("public/auth rate limited (HTTP 429)")
            if status in self.RETRYABLE_STATUS_CODES:
                if attempt < attempts - 1:
                    time.sleep(self.AUTH_RETRY_BACKOFF_SECONDS[attempt])
                    continue
                raise TransientExchangeError(f"public/auth HTTP {status}")
            if status >= 400:
                raise AuthenticationError(f"public/auth failed: HTTP {status} {response.text}")
            data = self._parse_jsonrpc(response, "public/auth")
            result = data.get("result")
            if not isinstance(result, dict):
                raise AuthenticationError("public/auth returned no result")
            return result
        raise TransientExchangeError("public/auth failed after retries")

    # ------------------------------------------------------------------
    # Core request primitives
    # ------------------------------------------------------------------

    def _jsonrpc_body(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": next(self._id_counter),
            "method": method,
        }
        if params is not None:
            body["params"] = self._normalize_params(params) or {}
        else:
            body["params"] = {}
        return body

    def _normalize_params(self, params: dict[str, Any] | None) -> dict[str, Any] | None:
        if params is None:
            return None
        normalized: dict[str, Any] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                normalized[key] = value
            elif isinstance(value, Decimal):
                normalized[key] = format_decimal(value, 8)
            else:
                normalized[key] = value
        return normalized

    def _headers(self, *, private: bool) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if private:
            token = self._ensure_access_token()
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _post_raw(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str],
    ) -> requests.Response:
        pace_exchange_request(self.config.client_id or None)
        return self.session.post(
            url,
            json=payload,
            headers=headers,
            timeout=self.config.request_timeout_seconds,
        )

    def _parse_jsonrpc(self, response: requests.Response, method_name: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise ExchangeError(f"{method_name} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ExchangeError(f"{method_name} returned non-object JSON body")
        if payload.get("jsonrpc") != "2.0":
            raise ExchangeError(f"{method_name} missing jsonrpc=2.0 field")
        if "id" not in payload:
            # Deribit HTTP error envelopes (e.g. session_not_found on HTTP 400) omit jsonrpc id.
            if not isinstance(payload.get("error"), dict):
                raise ExchangeError(f"{method_name} missing jsonrpc id field")
        return payload

    @staticmethod
    def _retry_after_seconds(response: requests.Response) -> float | None:
        header = response.headers.get("Retry-After") if hasattr(response, "headers") else None
        if header is None:
            return None
        try:
            return max(float(header), 0.0)
        except (TypeError, ValueError):
            return None

    def _classify_error(self, method_name: str, error: dict[str, Any]) -> Exception:
        code = error.get("code")
        message = error.get("message")
        data = error.get("data")
        text = f"{method_name} failed: code={code} message={message} data={data}"
        try:
            code_int = int(code) if code is not None else 0
        except (TypeError, ValueError):
            code_int = 0
        auth_codes = {13009, 13010, 13004, 13007, 13008}
        transient_codes = {10028, 10040, 10041, 10042, 10043, 10044, 10066}
        if code_int in auth_codes:
            return AuthenticationError(text)
        if code_int in transient_codes:
            return TransientExchangeError(text)
        return ExchangeError(text)

    # ------------------------------------------------------------------
    # Idempotent (safe) request path — retries on transient failures.
    # ------------------------------------------------------------------

    def _idempotent_request(
        self,
        method_name: str,
        *,
        params: dict[str, Any] | None = None,
        private: bool = False,
    ) -> Any:
        url = f"{self.config.rest_base_url}/{method_name}"
        attempts = len(self.IDEMPOTENT_RETRY_BACKOFF_SECONDS) + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            body = self._jsonrpc_body(method_name, params)
            try:
                response = self._post_raw(url, body, headers=self._headers(private=private))
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt < attempts - 1:
                    time.sleep(self.IDEMPOTENT_RETRY_BACKOFF_SECONDS[attempt])
                    continue
                raise TransientExchangeError(f"{method_name} failed after retries: {exc}") from exc

            status = response.status_code
            payload, json_error = self._parse_jsonrpc_payload(response, method_name)
            if status == self.RATE_LIMIT_STATUS:
                if attempt < attempts - 1:
                    wait = self._retry_after_seconds(response)
                    if wait is None:
                        wait = self.IDEMPOTENT_RETRY_BACKOFF_SECONDS[attempt]
                    time.sleep(wait)
                    continue
                raise TransientExchangeError(f"{method_name} rate limited: HTTP 429")
            if status in self.RETRYABLE_STATUS_CODES:
                if attempt < attempts - 1:
                    time.sleep(self.IDEMPOTENT_RETRY_BACKOFF_SECONDS[attempt])
                    continue
                raise TransientExchangeError(f"{method_name} retryable failure: HTTP {status}")
            if self._should_retry_private_auth(
                private=private,
                attempt=attempt,
                attempts=attempts,
                status=status,
                json_error=json_error,
            ):
                # Deribit often returns HTTP 400 + JSON-RPC 13009 (session_not_found) for stale tokens.
                self._clear_auth_tokens()
                continue
            if status >= 400:
                if json_error is not None:
                    raise self._classify_error(method_name, json_error)
                raise ExchangeError(f"{method_name} failed: HTTP {status} {response.text}")

            if payload is None:
                payload = self._parse_jsonrpc(response, method_name)
            if json_error is not None:
                raise self._classify_error(method_name, json_error)
            return payload.get("result")

        raise TransientExchangeError(f"{method_name} failed after retries: {last_error}")

    # ------------------------------------------------------------------
    # Non-idempotent (unsafe) request path — retries only on connection errors, at most once.
    # ------------------------------------------------------------------

    def _unsafe_request(
        self,
        method_name: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.config.rest_base_url}/{method_name}"
        attempts = self.UNSAFE_CONNECTION_RETRIES + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            body = self._jsonrpc_body(method_name, params)
            try:
                response = self._post_raw(url, body, headers=self._headers(private=True))
            except requests.exceptions.ConnectionError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    time.sleep(0.25)
                    continue
                raise TransientExchangeError(
                    f"{method_name} connection failed after {attempt + 1} attempts: {exc}"
                ) from exc
            except requests.exceptions.Timeout as exc:
                # Non-idempotent: order may have been accepted. Caller must reconcile.
                raise TransientExchangeError(f"{method_name} timed out; reconcile required: {exc}") from exc
            except requests.exceptions.RequestException as exc:
                raise TransientExchangeError(f"{method_name} request error: {exc}") from exc

            status = response.status_code
            if status == self.RATE_LIMIT_STATUS:
                raise TransientExchangeError(f"{method_name} rate limited: HTTP 429 (no auto-retry for unsafe calls)")
            if status in self.RETRYABLE_STATUS_CODES:
                raise TransientExchangeError(f"{method_name} server error HTTP {status}; reconcile required")
            if status >= 400:
                raise ExchangeError(f"{method_name} failed: HTTP {status} {response.text}")
            payload = self._parse_jsonrpc(response, method_name)
            error = payload.get("error")
            if isinstance(error, dict) and error:
                raise self._classify_error(method_name, error)
            return payload.get("result")

        raise TransientExchangeError(f"{method_name} failed: {last_error}")

    def _request(
        self,
        method_name: str,
        *,
        params: dict[str, Any] | None = None,
        private: bool = False,
    ) -> Any:
        if method_name in self._UNSAFE_METHODS:
            return self._unsafe_request(method_name, params=params)
        return self._idempotent_request(method_name, params=params, private=private)

    # ------------------------------------------------------------------
    # Public API wrappers
    # ------------------------------------------------------------------

    def ping(self) -> Any:
        return self._request("public/test")

    def get_instruments(self, currency: str, *, kind: str = "option", expired: bool = False) -> list[dict[str, Any]]:
        key = (currency.upper(), str(kind), bool(expired))
        ttl = _instruments_cache_ttl_seconds()
        if ttl > 0:
            now = time.monotonic()
            with _INSTRUMENTS_CACHE_LOCK:
                cached = _INSTRUMENTS_CACHE.get(key)
                if cached is not None and (now - cached[0]) < ttl:
                    return list(cached[1])
        result = self._request(
            "public/get_instruments",
            params={"currency": currency.upper(), "kind": kind, "expired": expired},
        )
        rows = result or []
        if ttl > 0:
            with _INSTRUMENTS_CACHE_LOCK:
                _INSTRUMENTS_CACHE[key] = (time.monotonic(), list(rows))
        return rows

    def get_instrument(self, instrument_name: str) -> dict[str, Any]:
        return (
            self._request(
                "public/get_instrument",
                params={"instrument_name": instrument_name},
            )
            or {}
        )

    def get_tradingview_chart_data(
        self,
        instrument_name: str,
        *,
        start_timestamp: int,
        end_timestamp: int,
        resolution: str,
    ) -> dict[str, Any]:
        """Public OHLCV data in TradingView format.

        Deribit returns an object with arrays (ticks/open/high/low/close/volume)
        and a `status` field.
        """
        result = self._request(
            "public/get_tradingview_chart_data",
            params={
                "instrument_name": instrument_name,
                "start_timestamp": int(start_timestamp),
                "end_timestamp": int(end_timestamp),
                "resolution": str(resolution),
            },
        )
        return result or {}

    def get_order_book(self, instrument_name: str, *, depth: int = 1) -> dict[str, Any]:
        return self._request("public/get_order_book", params={"instrument_name": instrument_name, "depth": depth}) or {}

    def get_book_summary_by_currency(self, currency: str, *, kind: str = "option") -> list[dict[str, Any]]:
        """Per-instrument quote summary (bid/ask/mark/underlying/OI) for a whole currency in one call.

        Lacks order-book depth amounts and greeks, so it is only a liquidity
        prefilter, not a replacement for ``get_order_book``.
        """
        return (
            self._request(
                "public/get_book_summary_by_currency",
                params={"currency": currency.upper(), "kind": kind},
            )
            or []
        )

    def get_index_price(self, index_name: str) -> dict[str, Any]:
        def _load() -> dict[str, Any]:
            return self._request("public/get_index_price", params={"index_name": index_name}) or {}

        return _cached_public_read(f"index_price:{index_name}", _index_price_cache_ttl_seconds(), _load)

    def get_index_chart_data(self, index_name: str, *, range_name: str = "1d") -> list[list[Any]]:
        def _load() -> list[list[Any]]:
            result = self._request(
                "public/get_index_chart_data", params={"index_name": index_name, "range": range_name}
            )
            return result or []

        return _cached_public_read(f"index_chart:{index_name}:{range_name}", _macro_cache_ttl_seconds(), _load)

    def get_volatility_index_data(
        self,
        currency: str,
        *,
        start_timestamp: int,
        end_timestamp: int,
        resolution: str = "1D",
    ) -> dict[str, Any]:
        def _load() -> dict[str, Any]:
            return (
                self._request(
                    "public/get_volatility_index_data",
                    params={
                        "currency": currency.upper(),
                        "start_timestamp": start_timestamp,
                        "end_timestamp": end_timestamp,
                        "resolution": resolution,
                    },
                )
                or {}
            )

        # Bucket the request window to the TTL so near-simultaneous calls in the
        # same cycle (which pass slightly different now()-based timestamps for the
        # same logical window) share one cache entry.
        ttl = _macro_cache_ttl_seconds()
        bucket_ms = int(ttl * 1000) or 1
        start_bucket = int(start_timestamp) // bucket_ms
        end_bucket = int(end_timestamp) // bucket_ms
        key = f"dvol:{currency.upper()}:{resolution}:{start_bucket}:{end_bucket}"
        return _cached_public_read(key, ttl, _load)

    def get_funding_chart_data(self, instrument_name: str, *, length: str = "8h") -> dict[str, Any]:
        return (
            self._request(
                "public/get_funding_chart_data",
                params={"instrument_name": instrument_name, "length": length},
            )
            or {}
        )

    def get_account_summaries(self, *, extended: bool = False) -> list[dict[str, Any]]:
        result = self._request(
            "private/get_account_summaries",
            params={"extended": extended},
            private=True,
        )
        if isinstance(result, dict):
            summaries = result.get("summaries")
            if isinstance(summaries, list):
                return [item for item in summaries if isinstance(item, dict)]
            return []
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []

    def _get_transaction_log_page(
        self,
        *,
        currency: str,
        start_timestamp: int,
        end_timestamp: int,
        count: int = 100,
        subaccount_id: int | None = None,
        query: str | None = None,
        continuation: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "currency": currency.upper(),
            "start_timestamp": int(start_timestamp),
            "end_timestamp": int(end_timestamp),
            "count": int(count),
        }
        if subaccount_id is not None:
            params["subaccount_id"] = int(subaccount_id)
        if query is not None and str(query).strip():
            params["query"] = str(query).strip()
        if continuation is not None:
            params["continuation"] = int(continuation)
        result = self._request(
            "private/get_transaction_log",
            params=params,
            private=True,
        )
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {"logs": [item for item in result if isinstance(item, dict)]}
        return {"logs": []}

    def get_transaction_log(
        self,
        *,
        currency: str,
        start_timestamp: int,
        end_timestamp: int,
        count: int = 100,
        subaccount_id: int | None = None,
        query: str | None = None,
        continuation: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch the transaction log window for a currency.

        Returns the raw ``logs`` list. Types of interest for external cash-flow
        reconciliation are ``deposit``, ``withdrawal``, and ``transfer`` (see
        ``EXTERNAL_FLOW_TRANSACTION_TYPES`` in ``models.py``). Ordinary trading
        entries like ``trade``, ``settlement``, ``delivery``, etc. are returned
        too — the caller is responsible for filtering.

        Pass ``query`` (e.g. ``\"trade\"``) to use Deribit's server-side filter
        (see ``private/get_transaction_log`` docs).
        """
        page = self._get_transaction_log_page(
            currency=currency,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            count=count,
            subaccount_id=subaccount_id,
            query=query,
            continuation=continuation,
        )
        logs = page.get("logs")
        if isinstance(logs, list):
            return [item for item in logs if isinstance(item, dict)]
        return []

    def iter_transaction_log(
        self,
        *,
        currency: str,
        start_timestamp: int,
        end_timestamp: int,
        count: int = 100,
        subaccount_id: int | None = None,
        query: str | None = None,
    ):
        """Yield all transaction-log rows in ``[start, end]``, following ``continuation``."""
        continuation: int | None = None
        while True:
            page = self._get_transaction_log_page(
                currency=currency,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                count=count,
                subaccount_id=subaccount_id,
                query=query,
                continuation=continuation,
            )
            logs = page.get("logs")
            rows = [item for item in logs if isinstance(item, dict)] if isinstance(logs, list) else []
            yield from rows
            next_token = page.get("continuation")
            if not rows or next_token in (None, "", 0):
                break
            continuation = int(next_token)

    def get_open_orders(self, *, kind: str = "any") -> list[dict[str, Any]]:
        params = {"kind": kind} if kind else None
        result = self._request("private/get_open_orders", params=params, private=True)
        return result or []

    def get_open_orders_by_label(self, currency: str, label: str) -> list[dict[str, Any]]:
        result = self._request(
            "private/get_open_orders_by_label",
            params={"currency": currency.upper(), "label": label},
            private=True,
        )
        return result or []

    def get_positions(self, *, currency: str = "any", kind: str | None = "any") -> list[dict[str, Any]]:
        # Deribit ``kind`` filter only accepts future|option|future_combo|option_combo.
        # Passing ``kind=any`` can yield empty or incomplete results on some accounts; omit to fetch all kinds.
        params: dict[str, Any] = {"currency": currency}
        if kind is not None and str(kind).strip().lower() not in {"", "any"}:
            params["kind"] = kind
        result = self._request("private/get_positions", params=params, private=True)
        return result or []

    def get_order_state(self, order_id: str) -> dict[str, Any]:
        return self._request("private/get_order_state", params={"order_id": order_id}, private=True) or {}

    def get_order_state_by_label(self, currency: str, label: str) -> dict[str, Any]:
        return (
            self._request(
                "private/get_order_state_by_label",
                params={"currency": currency.upper(), "label": label},
                private=True,
            )
            or {}
        )

    def get_user_trades_by_order(self, order_id: str, *, historical: bool = False) -> list[dict[str, Any]]:
        result = self._request(
            "private/get_user_trades_by_order",
            params={"order_id": order_id, "historical": historical},
            private=True,
        )
        return result or []

    def get_user_trades_by_currency(
        self,
        currency: str,
        *,
        kind: str | None = None,
        start_id: str | None = None,
        end_id: str | None = None,
        count: int = 10,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
        sorting: str | None = None,
        historical: bool = False,
        subaccount_id: int | None = None,
    ) -> dict[str, Any]:
        """Latest fills for instruments in ``currency`` (see Deribit ``private/get_user_trades_by_currency``).

        Pass ``subaccount_id`` when authenticating as the **main** account to read a subaccount's trades.
        Subaccount API keys already scope to that account; ``subaccount_id`` is usually omitted then.
        """
        params: dict[str, Any] = {
            "currency": currency.upper(),
            "count": max(1, min(int(count), 1000)),
            "historical": bool(historical),
        }
        if kind is not None and str(kind).strip():
            params["kind"] = str(kind).strip()
        if start_id is not None and str(start_id).strip():
            params["start_id"] = str(start_id).strip()
        if end_id is not None and str(end_id).strip():
            params["end_id"] = str(end_id).strip()
        if start_timestamp is not None:
            params["start_timestamp"] = int(start_timestamp)
        if end_timestamp is not None:
            params["end_timestamp"] = int(end_timestamp)
        if sorting is not None and str(sorting).strip():
            params["sorting"] = str(sorting).strip()
        if subaccount_id is not None:
            params["subaccount_id"] = int(subaccount_id)
        result = self._request(
            "private/get_user_trades_by_currency",
            params=params,
            private=True,
        )
        if isinstance(result, list):
            return {"trades": result, "has_more": False}
        if isinstance(result, dict):
            return result
        return {"trades": [], "has_more": False}

    def get_user_trades_by_instrument(
        self,
        instrument_name: str,
        *,
        start_seq: int | None = None,
        end_seq: int | None = None,
        count: int = 10,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
        sorting: str | None = None,
        historical: bool = False,
        subaccount_id: int | None = None,
    ) -> dict[str, Any]:
        """Fills for one contract (``private/get_user_trades_by_instrument``).

        Use this for a single option/future/perp name, e.g. ``BTC_USDC-27MAR26-90000-P``.
        """
        params: dict[str, Any] = {
            "instrument_name": str(instrument_name).strip(),
            "count": max(1, min(int(count), 1000)),
            "historical": bool(historical),
        }
        if start_seq is not None:
            params["start_seq"] = int(start_seq)
        if end_seq is not None:
            params["end_seq"] = int(end_seq)
        if start_timestamp is not None:
            params["start_timestamp"] = int(start_timestamp)
        if end_timestamp is not None:
            params["end_timestamp"] = int(end_timestamp)
        if sorting is not None and str(sorting).strip():
            params["sorting"] = str(sorting).strip()
        if subaccount_id is not None:
            params["subaccount_id"] = int(subaccount_id)
        result = self._request(
            "private/get_user_trades_by_instrument",
            params=params,
            private=True,
        )
        if isinstance(result, list):
            return {"trades": result, "has_more": False}
        if isinstance(result, dict):
            return result
        return {"trades": [], "has_more": False}

    def create_combo(self, trades: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("private/create_combo", params={"trades": trades}) or {}

    def place_order(
        self,
        *,
        direction: str,
        instrument_name: str,
        amount: Decimal | str,
        label: str,
        order_type: str = "limit",
        price: Decimal | str | None = None,
        time_in_force: str | None = None,
        post_only: bool | None = None,
        reject_post_only: bool | None = None,
        reduce_only: bool | None = None,
        trigger_price: Decimal | str | None = None,
        trigger: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        method = "private/buy" if direction.lower() == "buy" else "private/sell"
        if not client_order_id:
            client_order_id = f"{label}-{utc_now_ms()}"
        params: dict[str, Any] = {
            "instrument_name": instrument_name,
            "amount": amount,
            "label": label,
            "type": order_type,
            "client_order_id": client_order_id,
        }
        if price is not None:
            params["price"] = price
        if time_in_force is not None:
            params["time_in_force"] = time_in_force
        if post_only is not None:
            params["post_only"] = post_only
        if reject_post_only is not None:
            params["reject_post_only"] = reject_post_only
        if reduce_only is not None:
            params["reduce_only"] = reduce_only
        if trigger_price is not None:
            params["trigger_price"] = trigger_price
        if trigger is not None:
            params["trigger"] = trigger
        return self._request(method, params=params) or {}

    def place_buy_order(self, **kwargs: Any) -> dict[str, Any]:
        return self.place_order(direction="buy", **kwargs)

    def place_sell_order(self, **kwargs: Any) -> dict[str, Any]:
        return self.place_order(direction="sell", **kwargs)

    def close_position(
        self,
        instrument_name: str,
        *,
        order_type: str = "market",
        price: Decimal | str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"instrument_name": instrument_name, "type": order_type}
        if price is not None:
            params["price"] = price
        return self._request("private/close_position", params=params) or {}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self._request("private/cancel", params={"order_id": order_id}) or {}

    def edit_order(
        self,
        order_id: str,
        *,
        amount: Decimal | str | None = None,
        price: Decimal | str | None = None,
        post_only: bool | None = None,
        reject_post_only: bool | None = None,
        advanced: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"order_id": order_id}
        if amount is not None:
            params["amount"] = amount
        if price is not None:
            params["price"] = price
        if post_only is not None:
            params["post_only"] = post_only
        if reject_post_only is not None:
            params["reject_post_only"] = reject_post_only
        if advanced is not None:
            params["advanced"] = advanced
        return self._request("private/edit", params=params) or {}

    def cancel_all(self, *, kind: str = "any") -> Any:
        return self._request("private/cancel_all", params={"kind": kind}) or {}

    def get_subaccounts(self, *, with_portfolio: bool = False) -> list[dict[str, Any]]:
        result = self._request(
            "private/get_subaccounts",
            params={"with_portfolio": bool(with_portfolio)},
            private=True,
        )
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []

    def submit_transfer_between_subaccounts(
        self,
        *,
        currency: str,
        amount: Decimal | str,
        destination: int,
        source: int | None = None,
        nonce: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "currency": currency.upper(),
            "amount": amount,
            "destination": int(destination),
        }
        if source is not None:
            params["source"] = int(source)
        if nonce is not None and str(nonce).strip():
            params["nonce"] = str(nonce).strip()
        return self._request("private/submit_transfer_between_subaccounts", params=params, private=True) or {}
