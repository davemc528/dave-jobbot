from jobbot.security import safe_log


def test_safe_log_redacts_pii_and_secrets() -> None:
    output = safe_log(
        "Contact person@example.com at 619-555-1212, 123 Main St, San Diego",
        api_key="api_key=secretvalue",
    )
    assert "person@example.com" not in output
    assert "619-555-1212" not in output
    assert "123 Main St" not in output
    assert "secretvalue" not in output
