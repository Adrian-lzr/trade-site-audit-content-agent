"""Validate the rendered local Docker Compose production boundaries.

This check renders both the default and ``demo`` profiles without starting
containers or building images. It verifies loopback-only ports, health-gated
startup, and the fixture's API network namespace.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _render(profile: str | None, runner: Runner) -> dict[str, Any]:
    command = ["docker", "compose"]
    if profile:
        command.extend(["--profile", profile])
    command.extend(["config", "--format", "json"])
    env = os.environ.copy()
    env["POSTGRES_PASSWORD"] = "local-readiness-config-only"
    result = runner(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "docker compose config failed").strip()
        raise RuntimeError(f"Compose {profile or 'default'} config failed: {detail[:500]}")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Compose {profile or 'default'} config did not return JSON") from exc


def _host_ports(service: dict[str, Any]) -> list[dict[str, Any]]:
    return [port for port in service.get("ports", []) if isinstance(port, dict)]


def _assert_loopback(name: str, service: dict[str, Any]) -> None:
    ports = _host_ports(service)
    if not ports:
        raise RuntimeError(f"service {name} has no host port mapping")
    if any(port.get("host_ip") != "127.0.0.1" for port in ports):
        raise RuntimeError(f"service {name} exposes a non-loopback host port")


def validate_rendered_profiles(default: dict[str, Any], demo: dict[str, Any]) -> dict[str, Any]:
    services = default.get("services") or {}
    demo_services = demo.get("services") or {}
    required = {"postgres", "api", "worker", "web"}
    if not required.issubset(services):
        raise RuntimeError(f"default Compose profile is missing services: {', '.join(sorted(required - set(services)))}")
    if not required.union({"fixture"}).issubset(demo_services):
        raise RuntimeError("demo Compose profile does not include postgres, api, worker, web, and fixture")

    for name in ("postgres", "api", "web"):
        _assert_loopback(name, services[name])
    if "fixture" in services:
        raise RuntimeError("fixture must remain profile-gated")
    fixture = demo_services["fixture"]
    if fixture.get("network_mode") != "service:api" or fixture.get("ports"):
        raise RuntimeError("fixture must share the API namespace and have no independent host port mapping")
    if "demo" not in fixture.get("profiles", []):
        raise RuntimeError("fixture must require the demo profile")
    if demo_services["api"].get("network_mode"):
        raise RuntimeError("API must retain its own network namespace")

    worker_dependencies = demo_services["worker"].get("depends_on") or {}
    api_dependency = worker_dependencies.get("api") or {}
    if api_dependency.get("condition") != "service_healthy":
        raise RuntimeError("worker must wait for the healthy API")
    api_command = json.dumps(demo_services["api"].get("command", []))
    if "alembic" not in api_command or "upgrade head" not in api_command:
        raise RuntimeError("API startup must migrate the database to Alembic head")

    return {
        "default_profile": "passed",
        "demo_profile": "passed",
        "services": sorted(required),
        "fixture_profile_gated": True,
        "fixture_shared_api_namespace": True,
        "loopback_only_host_ports": True,
        "worker_waits_for_healthy_api": True,
        "api_migrates_to_head": True,
        "containers_started": False,
        "images_built": False,
    }


def check(runner: Runner = subprocess.run) -> dict[str, Any]:
    default = _render(None, runner)
    demo = _render("demo", runner)
    return validate_rendered_profiles(default, demo)


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
