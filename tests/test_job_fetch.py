import socket
import ssl
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import certifi
import pytest

from jobbot.jobs.fetch import (
    JobURLFetchError,
    fetch_job_url,
    tls_configuration,
    verified_ssl_context,
)


class FakeResponse:
    def __init__(
        self,
        body: bytes = b"<html><body>Job description</body></html>",
        *,
        status: int = 200,
        content_type: str = "text/html",
    ) -> None:
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_certifi_is_default_and_verified_context_is_passed(monkeypatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    context = verified_ssl_context()
    assert tls_configuration().cafile == certifi.where()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True

    with patch("jobbot.jobs.fetch.urlopen", return_value=FakeResponse()) as opened:
        assert "Job description" in fetch_job_url("https://jobs.example.test/role")
    assert opened.call_count == 1
    assert opened.call_args.kwargs["context"].verify_mode == ssl.CERT_REQUIRED
    assert opened.call_args.kwargs["context"].check_hostname is True


def test_explicit_ssl_cert_file_is_respected(monkeypatch, tmp_path) -> None:
    custom_bundle = tmp_path / "company-ca.pem"
    custom_bundle.write_text("test bundle")
    monkeypatch.setenv("SSL_CERT_FILE", str(custom_bundle))
    fake_context = Mock(verify_mode=ssl.CERT_REQUIRED, check_hostname=True)
    with patch("jobbot.jobs.fetch.ssl.create_default_context", return_value=fake_context) as create:
        assert verified_ssl_context() is fake_context
    create.assert_called_once_with(cafile=str(custom_bundle))


def test_certificate_failure_is_descriptive_and_has_no_insecure_fallback() -> None:
    failure = URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate"))
    with patch("jobbot.jobs.fetch.urlopen", side_effect=failure) as opened:
        with pytest.raises(JobURLFetchError, match="Certificate verification failed"):
            fetch_job_url("https://jobs.example.test/role")
    assert opened.call_count == 1


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (URLError(socket.gaierror("name not known")), "DNS or network failure"),
        (URLError(socket.timeout("slow server")), "timed out"),
        (HTTPError("https://example.test", 403, "Forbidden", {}, None), "HTTP status failure"),
    ],
)
def test_network_failures_are_categorized(failure: Exception, message: str) -> None:
    with patch("jobbot.jobs.fetch.urlopen", side_effect=failure):
        with pytest.raises(JobURLFetchError, match=message):
            fetch_job_url("https://jobs.example.test/role", timeout=3)


def test_javascript_only_or_antibot_response_is_rejected() -> None:
    response = FakeResponse(b"<html>Please enable JavaScript to continue</html>")
    with patch("jobbot.jobs.fetch.urlopen", return_value=response):
        with pytest.raises(JobURLFetchError, match="anti-bot, JavaScript-only"):
            fetch_job_url("https://jobs.example.test/role")
