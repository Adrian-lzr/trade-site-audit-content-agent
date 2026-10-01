from pathlib import Path

from scripts.container_build_check import validate_compose, validate_sources


ROOT = Path(__file__).resolve().parents[2]


def test_container_sources_are_locked_unprivileged_and_checked():
    report = validate_sources(ROOT)
    assert report == {
        "backend_healthcheck": True,
        "backend_locked_dependencies": True,
        "backend_non_root": True,
        "build_context_hygiene": True,
        "web_healthcheck": True,
        "web_locked_dependencies": True,
        "web_non_root": True,
    }


def test_compose_builds_from_repo_root_and_gates_services():
    loopback = [{"host_ip": "127.0.0.1", "target": 8080, "published": "5173"}]
    config = {
        "services": {
            "postgres": {},
            "api": {
                "build": {"context": str(ROOT), "dockerfile": "backend/Dockerfile"},
                "command": ["sh", "-c", "alembic upgrade head"],
                "healthcheck": {"test": ["CMD", "true"]},
            },
            "worker": {
                "build": {"context": str(ROOT), "dockerfile": "backend/Dockerfile"},
                "depends_on": {"api": {"condition": "service_healthy"}},
            },
            "web": {
                "build": {"context": str(ROOT), "dockerfile": "apps/web/Dockerfile"},
                "depends_on": {"api": {"condition": "service_healthy"}},
                "healthcheck": {"test": ["CMD", "true"]},
                "ports": loopback,
            },
        }
    }
    report = validate_compose(config, ROOT)
    assert report["images_built"] is False
    assert report["web_publishes_8080_loopback"] is True


def test_compose_validator_rejects_privileged_web_port():
    config = {
        "services": {
            "postgres": {},
            "api": {"build": {"context": str(ROOT), "dockerfile": "backend/Dockerfile"}, "command": ["upgrade head"], "healthcheck": {}},
            "worker": {"build": {"context": str(ROOT), "dockerfile": "backend/Dockerfile"}, "depends_on": {"api": {"condition": "service_healthy"}}},
            "web": {
                "build": {"context": str(ROOT), "dockerfile": "apps/web/Dockerfile"},
                "depends_on": {"api": {"condition": "service_healthy"}},
                "healthcheck": {},
                "ports": [{"host_ip": "127.0.0.1", "target": 80, "published": "5173"}],
            },
        }
    }
    try:
        validate_compose(config, ROOT)
    except RuntimeError as exc:
        assert "8080" in str(exc)
    else:
        raise AssertionError("expected the validator to reject target port 80")
