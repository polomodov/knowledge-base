from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    embedding_dimension: int = 8
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


def load_settings(config_path: str | None = None) -> Settings:
    path = Path(config_path).expanduser().resolve() if config_path else None
    data = _load_toml(path)
    arango = data.get("arangodb", {})
    embedding = data.get("embedding", {})

    password_env = arango.get("password_env", "KB_ARANGO_PASSWORD")
    return Settings(
        arango_url=os.getenv("KB_ARANGO_URL", arango.get("url", Settings.arango_url)).rstrip("/"),
        arango_database=os.getenv(
            "KB_ARANGO_DATABASE",
            arango.get("database", Settings.arango_database),
        ),
        arango_user=os.getenv("KB_ARANGO_USER", arango.get("user", Settings.arango_user)),
        arango_password=os.getenv(
            "KB_ARANGO_PASSWORD",
            os.getenv(password_env, arango.get("password", Settings.arango_password)),
        ),
        embedding_dimension=int(
            os.getenv("KB_EMBEDDING_DIMENSION", embedding.get("dimension", Settings.embedding_dimension)),
        ),
        repo_root=REPO_ROOT,
    )
