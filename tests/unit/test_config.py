from knowledge_base.config import load_settings


def test_load_settings_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("KB_ARANGO_URL", "http://example.test:8529/")
    monkeypatch.setenv("KB_ARANGO_DATABASE", "kb_test")
    monkeypatch.setenv("KB_ARANGO_USER", "tester")
    monkeypatch.setenv("KB_ARANGO_PASSWORD", "secret")

    settings = load_settings()

    assert settings.arango_url == "http://example.test:8529"
    assert settings.arango_database == "kb_test"
    assert settings.arango_user == "tester"
    assert settings.arango_password == "secret"
