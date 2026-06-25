from __future__ import annotations

import shutil
import subprocess
from typing import Any

from knowledge_base.config import Settings


def platform_up(settings: Settings) -> dict[str, Any]:
    command = _compose_command(settings, "up", "-d")
    if command is None:
        return _docker_unavailable()
    result = _run(command, settings)
    return {
        "status": "started" if result.returncode == 0 else "error",
        "services": {"arangodb": "starting" if result.returncode == 0 else "unknown"},
        "command": " ".join(command),
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def platform_down(settings: Settings) -> dict[str, Any]:
    command = _compose_command(settings, "down")
    if command is None:
        return _docker_unavailable()
    result = _run(command, settings)
    return {
        "status": "stopped" if result.returncode == 0 else "error",
        "command": " ".join(command),
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _compose_command(settings: Settings, *args: str) -> list[str] | None:
    compose_file = settings.repo_root / "compose" / "arangodb.compose.yml"
    env_file = settings.repo_root / "config" / "arangodb.env.example"
    compose_args = ["--env-file", str(env_file), "-f", str(compose_file), *args]
    docker = shutil.which("docker")
    if docker is not None:
        probe = subprocess.run([docker, "compose", "version"], text=True, capture_output=True, check=False)
        if probe.returncode == 0:
            return [docker, "compose", *compose_args]
    docker_compose = shutil.which("docker-compose")
    if docker_compose is not None:
        return [docker_compose, *compose_args]
    return None


def _run(command: list[str], settings: Settings) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=settings.repo_root, text=True, capture_output=True, check=False)


def _docker_unavailable() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": "Docker Compose command not found",
        "instructions": [
            "brew install colima docker docker-compose",
            "colima start --cpu 4 --memory 8 --disk 60",
            "kb platform up",
        ],
    }
