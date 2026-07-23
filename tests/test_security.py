from jobbot.security import redact_url_tracking_parameters, safe_log


def test_safe_log_redacts_pii_and_secrets() -> None:
    output = safe_log(
        "Contact person@example.com at 619-555-1212, 123 Main St, San Diego",
        api_key="api_key=secretvalue",
    )
    assert "person@example.com" not in output
    assert "619-555-1212" not in output
    assert "123 Main St" not in output
    assert "secretvalue" not in output


def test_tracking_query_parameters_are_removed_from_logs() -> None:
    url = (
        "https://jobs.example.test/role?id=42&utm_source=email"
        "&gclid=tracking-value&candidate=external"
    )
    redacted = redact_url_tracking_parameters(url)
    assert redacted == "https://jobs.example.test/role?id=42&candidate=external"
    output = safe_log("Fetching job URL", url=url)
    assert "utm_source" not in output
    assert "gclid" not in output
    assert "tracking-value" not in output
