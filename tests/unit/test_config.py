from pathlib import Path

import knowledge_base.config as config
from knowledge_base.config import _load_env_file, load_settings


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


def test_load_env_file_parses_and_ignores_comments(tmp_path: Path) -> None:
    env = tmp_path / "arangodb.env"
    env.write_text(
        "# comment\n\nKB_ARANGO_PASSWORD = real-secret \nKB_ARANGO_USER=root\nnot a pair\n",
        encoding="utf-8",
    )
    values = _load_env_file(env)
    assert values == {"KB_ARANGO_PASSWORD": "real-secret", "KB_ARANGO_USER": "root"}


def test_load_settings_reads_env_override_file(tmp_path: Path, monkeypatch) -> None:
    # The client must pick up config/arangodb.env so it matches the container that
    # `kb platform up` started with those credentials (PR #10 review).
    for var in ("KB_ARANGO_URL", "KB_ARANGO_DATABASE", "KB_ARANGO_USER", "KB_ARANGO_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "arangodb.env").write_text(
        "KB_ARANGO_PASSWORD=from-file\nKB_ARANGO_DATABASE=kb_override\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)

    settings = load_settings()

    assert settings.arango_password == "from-file"  # env file used when no env var / TOML
    assert settings.arango_database == "kb_override"
    assert settings.arango_user == "root"  # falls back to default when nothing sets it


def test_env_var_overrides_env_file(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "arangodb.env").write_text("KB_ARANGO_PASSWORD=from-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("KB_ARANGO_PASSWORD", "from-env")

    assert load_settings().arango_password == "from-env"
