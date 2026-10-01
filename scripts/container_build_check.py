"""Check container build definitions without pulling or building images.

This is deliberately a source-level gate.  It catches accidental build-context
leaks, unlocked application dependencies, privileged runtime users, missing
health checks, and Compose dependency/port drift.  It does not claim that a
Docker image was built; use ``docker compose build`` separately when registry
access is available.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise RuntimeError(f"{label} is missing {needle!r}")


def validate_sources(root: Path = ROOT) -> dict[str, Any]:
    backend = _read(root / "backend" / "Dockerfile")
    web = _read(root / "apps" / "web" / "Dockerfile")
    nginx = _read(root / "apps" / "web" / "nginx.conf")
    ignore_lines = {
        line.strip()
        for line in _read(root / ".dockerignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    lock_lines = [
        line.strip()
        for line in _read(root / "backend" / "requirements.lock").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    _require(backend, "COPY backend/requirements.lock", "backend Dockerfile")
    _require(backend, "pip install --no-cache-dir -r /app/backend/requirements.lock", "backend Dockerfile")
    _require(backend, "USER app", "backend Dockerfile")
    _require(backend, "--uid 10001", "backend Dockerfile")
    _require(backend, "HEALTHCHECK", "backend Dockerfile")
    _require(backend, "EXPOSE 8000", "backend Dockerfile")
    _require(web, "COPY apps/web/package.json apps/web/package-lock.json", "web Dockerfile")
    _require(web, "npm ci", "web Dockerfile")
    _require(web, "USER nginx", "web Dockerfile")
    _require(web, "rm -f /etc/nginx/conf.d/default.conf", "web Dockerfile")
    _require(web, "HEALTHCHECK", "web Dockerfile")
    _require(web, "EXPOSE 8080", "web Dockerfile")
    _require(nginx, "listen 8080", "nginx config")
    _require(nginx, "pid /tmp/nginx.pid", "nginx config")
    compose_source = _read(root / "docker-compose.yml")
    _require(compose_source, "context: .", "Compose build context")

    if any("pip install" in line and "requirements.lock" not in line for line in backend.splitlines()):
        raise RuntimeError("backend Dockerfile has an install command that does not use requirements.lock")
    if any(line.startswith(("FROM", "COPY")) and "latest" in line.lower() for line in (*backend.splitlines(), *web.splitlines())):
        raise RuntimeError("container sources must not use a latest image tag")
    if not any(line.startswith("==") or "==" in line for line in lock_lines):
        raise RuntimeError("backend requirements.lock has no pinned versions")
    if any("==" not in line and not line.startswith("-") for line in lock_lines):
        raise RuntimeError("backend requirements.lock contains an unpinned requirement")
    required_ignores = {".env.*", "!.env.example", "**/node_modules", "output", "evals"}
    missing_ignores = sorted(required_ignores - ignore_lines)
    if missing_ignores:
        raise RuntimeError(f".dockerignore is missing required entries: {', '.join(missing_ignores)}")

    return {
        "backend_locked_dependencies": True,
        "web_locked_dependencies": True,
        "backend_non_root": True,
        "web_non_root": True,
        "backend_healthcheck": True,
        "web_healthcheck": True,
        "build_context_hygiene": True,
    }


def render_compose(root: Path = ROOT, runner: Runner = subprocess.run) -> dict[str, Any]:
    env = os.environ.copy()
    env["POSTGRES_PASSWORD"] = "local-container-check-only"
    result = runner(
        ["docker", "compose", "config", "--format", "json"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "docker compose config failed").strip()
        raise RuntimeError(f"Compose config failed: {detail[:500]}")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Compose config did not return JSON") from exc


def validate_compose(config: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    services = config.get("services") or {}
    required = {"postgres", "api", "worker", "web"}
    if not required.issubset(services):
        raise RuntimeError(f"Compose is missing services: {', '.join(sorted(required - set(services)))}")

    for name in ("api", "worker", "web"):
        build = services[name].get("build") or {}
        context = str(build.get("context", "")).replace("\\", "/").rstrip("/")
        dockerfile = root / str(build.get("dockerfile", ""))
        if not context or context.endswith("/..") or "/.." in context:
            raise RuntimeError(f"{name} build context must be a repository path")
        if not dockerfile.is_file():
            raise RuntimeError(f"{name} Dockerfile does not exist: {dockerfile}")

    api = services["api"]
    worker = services["worker"]
    web = services["web"]
    if (worker.get("depends_on") or {}).get("api", {}).get("condition") != "service_healthy":
        raise RuntimeError("worker must wait for a healthy API")
    if (web.get("depends_on") or {}).get("api", {}).get("condition") != "service_healthy":
        raise RuntimeError("web must wait for a healthy API")
    if "healthcheck" not in api or "healthcheck" not in web:
        raise RuntimeError("API and Web must define Compose health checks")
    web_ports = web.get("ports") or []
    if not any(port.get("host_ip") == "127.0.0.1" and str(port.get("target")) == "8080" for port in web_ports):
        raise RuntimeError("web must publish container port 8080 on loopback")
    if "upgrade head" not in json.dumps(api.get("command", [])):
        raise RuntimeError("API startup must migrate to Alembic head")

    return {
        "compose_services": sorted(required),
        "api_migrates_to_head": True,
        "worker_waits_for_healthy_api": True,
        "web_waits_for_healthy_api": True,
        "web_publishes_8080_loopback": True,
        "images_built": False,
    }


def check(root: Path = ROOT, runner: Runner = subprocess.run) -> dict[str, Any]:
    report = validate_sources(root)
    report.update(validate_compose(render_compose(root, runner), root))
    return report


def main() -> int:
    try:
        report = check()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=True, sort_keys=True))
        return 1
    report["status"] = "passed"
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
