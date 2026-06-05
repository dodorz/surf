import surf


def test_network_diagnostic_reports_dns_pollution(monkeypatch):
    monkeypatch.setattr(surf, "_get_local_dns_addresses", lambda hostname: ["199.59.148.102"])
    monkeypatch.setattr(surf, "_resolve_host_via_google_doh", lambda hostname, timeout=10: ["104.18.22.18", "104.18.23.18"])

    message = surf._diagnose_network_fetch_failure(
        "https://www.theatlantic.com/philosophy/2026/06/no-artificial-intelligence-is-not-conscious/687378/",
        Exception("SSLEOFError: [SSL: UNEXPECTED_EOF_WHILE_READING]"),
    )

    assert message is not None
    assert "Possible DNS pollution or network interception" in message
    assert "199.59.148.102" in message
    assert "104.18.22.18" in message
