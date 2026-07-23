from __future__ import annotations

import os
import socket
import ssl
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

DEFAULT_REQUEST_TIMEOUT = 20.0


class JobURLFetchError(RuntimeError):
    """A categorized, user-actionable job URL retrieval failure."""


@dataclass(frozen=True)
class TLSConfiguration:
    cafile: str
    source: str


def tls_configuration() -> TLSConfiguration:
    configured = os.getenv("SSL_CERT_FILE")
    if configured:
        return TLSConfiguration(cafile=configured, source="SSL_CERT_FILE")
    return TLSConfiguration(cafile=certifi.where(), source="certifi")


def verified_ssl_context() -> ssl.SSLContext:
    configuration = tls_configuration()
    context = ssl.create_default_context(cafile=configuration.cafile)
    # These are assertions, not overrides: fail closed if a runtime returns an unsafe context.
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise JobURLFetchError("TLS configuration error: certificate verification is not enabled")
    return context


def configured_request_timeout(explicit: float | None = None) -> float:
    if explicit is not None:
        timeout = explicit
    else:
        raw = os.getenv("JOBBOT_REQUEST_TIMEOUT", str(DEFAULT_REQUEST_TIMEOUT))
        try:
            timeout = float(raw)
        except ValueError as exc:
            raise JobURLFetchError(
                "Invalid JOBBOT_REQUEST_TIMEOUT: expected a positive number of seconds"
            ) from exc
    if timeout <= 0:
        raise JobURLFetchError("Request timeout must be greater than zero seconds")
    return timeout


def _looks_javascript_only_or_blocked(body: str, content_type: str) -> bool:
    lowered = body.casefold()
    markers = (
        "enable javascript",
        "javascript is required",
        "verify you are human",
        "checking your browser",
        "cf-chl-",
        "captcha",
        "access denied",
    )
    non_html = content_type and "html" not in content_type.casefold()
    return non_html or any(marker in lowered for marker in markers)


def fetch_job_url(url: str, *, timeout: float | None = None) -> str:
    request = Request(url, headers={"User-Agent": "dave-jobbot/0.1 (manual review)"})
    context = verified_ssl_context()
    request_timeout = configured_request_timeout(timeout)
    try:
        with urlopen(  # noqa: S310 - HTTPS is verified by the explicit SSL context.
            request,
            timeout=request_timeout,
            context=context,
        ) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise JobURLFetchError(f"HTTP status failure: server returned HTTP {status}")
            content_type = response.headers.get("Content-Type", "")
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise JobURLFetchError(
            f"HTTP status failure: server returned HTTP {exc.code} {exc.reason}"
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise JobURLFetchError(f"Request timed out after {request_timeout:g} seconds") from exc
    except ssl.SSLCertVerificationError as exc:
        raise JobURLFetchError(
            "Certificate verification failed. Check SSL_CERT_FILE if you use a custom CA "
            "bundle, then run `jobbot doctor` to inspect TLS paths."
        ) from exc
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError) or (
            isinstance(reason, ssl.SSLError)
            and "certificate verify failed" in str(reason).casefold()
        ):
            raise JobURLFetchError(
                "Certificate verification failed. Check SSL_CERT_FILE if you use a custom CA "
                "bundle, then run `jobbot doctor` to inspect TLS paths."
            ) from exc
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise JobURLFetchError(f"Request timed out after {request_timeout:g} seconds") from exc
        raise JobURLFetchError(f"DNS or network failure: {reason}") from exc
    if _looks_javascript_only_or_blocked(body, content_type):
        raise JobURLFetchError(
            "The server returned an anti-bot, JavaScript-only, or non-HTML response. "
            "Save the rendered job description locally and retry with `jobbot job add --file`."
        )
    return body
