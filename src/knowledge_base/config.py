from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knowledge_base.constants import VECTOR_DIMENSION

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback
    tomllib = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    arango_url: str = "http://127.0.0.1:8529"
    arango_database: str = "knowledge_base"
    arango_user: str = "root"
    arango_password: str = "knowledge-base-dev"
    embedding_dimension: int = VECTOR_DIMENSION
    embedding_provider: str = "hash"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    retrieval_min_similarity: float = 0.0
    repo_root: Path = REPO_ROOT


def _load_toml(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    if tomllib is None:
        raise RuntimeError("TOML config requires Python 3.11+")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _load_env_file(path: Path) -> dict[str, str]:
    # Parse the gitignored config/arangodb.env (simple KEY=VALUE lines) so the client uses
    # the same credentials `kb platform up` started the container with (finding #20 / PR #10).
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def load_settings(config_path: str | None = None) -> Settings:
    path = Path(config_path).expanduser().resolve() if config_path else None
    data = _load_toml(path)
    arango = data.get("arangodb", {})
    embedding = data.get("embedding", {})
    retrieval = data.get("retrieval", {})
    env_file = _load_env_file(REPO_ROOT / "config" / "arangodb.env")

    def resolve(key: str, toml_value: Any, default: Any) -> Any:
        # process env var > TOML (--config) > config/arangodb.env override > default
        if os.getenv(key) is not None:
            return os.getenv(key)
        if toml_value is not None:
            return toml_value
        if key in env_file:
            return env_file[key]
        return default

    password_env = arango.get("password_env", "KB_ARANGO_PASSWORD")
    password = os.getenv("KB_ARANGO_PASSWORD")
    if password is None and password_env != "KB_ARANGO_PASSWORD":
        password = os.getenv(password_env)
    if password is None:
        password = arango.get("password")
    if password is None:
        password = env_file.get("KB_ARANGO_PASSWORD")
    if password is None:
        password = Settings.arango_password

    return Settings(
        arango_url=resolve("KB_ARANGO_URL", arango.get("url"), Settings.arango_url).rstrip("/"),
        arango_database=resolve("KB_ARANGO_DATABASE", arango.get("database"), Settings.arango_database),
        arango_user=resolve("KB_ARANGO_USER", arango.get("user"), Settings.arango_user),
        arango_password=password,
        embedding_dimension=int(resolve("KB_EMBEDDING_DIMENSION", embedding.get("dimension"), Settings.embedding_dimension)),
        embedding_provider=resolve("KB_EMBEDDING_PROVIDER", embedding.get("provider"), Settings.embedding_provider),
        embedding_model=resolve("KB_EMBEDDING_MODEL", embedding.get("model"), Settings.embedding_model),
        retrieval_min_similarity=float(
            resolve("KB_RETRIEVAL_MIN_SIMILARITY", retrieval.get("min_similarity"), Settings.retrieval_min_similarity)
        ),
        repo_root=REPO_ROOT,
    )
