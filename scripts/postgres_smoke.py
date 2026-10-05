"""Run a destructive-free PostgreSQL application and checkpoint smoke test.

The target database must be an isolated validation database. The script only
creates one temporary workspace site and one LangGraph checkpoint thread; it
does not crawl a site or call publication/CMS endpoints.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TypedDict
from uuid import uuid4


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="PostgreSQL SQLAlchemy DSN; defaults to DATABASE_URL",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    database_url = args.database_url.strip()
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://", "postgres://")):
        raise SystemExit("--database-url must be an isolated PostgreSQL DSN")
    os.environ["DATABASE_URL"] = database_url
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    from scripts.migration_head import repository_alembic_head

    from fastapi.testclient import TestClient
    from langgraph.graph import END, START, StateGraph
    from sqlalchemy import text

    from backend.app import app
    from backend.content_worker import checkpoint_saver
    from backend.database import engine

    expected_migration = repository_alembic_head(project_root)
    with engine.connect() as connection:
        migration = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    if migration != expected_migration:
        raise RuntimeError(
            f"unexpected migration head: {migration!r}; expected repository head {expected_migration!r}"
        )

    site_name = f"postgres-smoke-{uuid4().hex[:8]}"
    with TestClient(app) as client:
        health = client.get("/health")
        if health.status_code != 200 or health.json().get("status") != "ok":
            raise RuntimeError(f"health check failed: {health.status_code} {health.text}")
        api_health = client.get("/api/health")
        if api_health.status_code != 200:
            raise RuntimeError(f"API health check failed: {api_health.status_code} {api_health.text}")

        registration = client.post(
            "/api/sites",
            json={
                "workspace_id": "demo-workspace",
                "name": site_name,
                "origin": "https://example.com",
                "allowed_paths": ["/"],
                "is_synthetic": False,
            },
        )
        if registration.status_code != 201:
            raise RuntimeError(f"site registration failed: {registration.status_code} {registration.text}")
        site = registration.json()
        listing = client.get("/api/sites", params={"workspace_id": "demo-workspace"})
        if listing.status_code != 200:
            raise RuntimeError(f"site listing failed: {listing.status_code} {listing.text}")
        if not any(item.get("id") == site.get("id") for item in listing.json()):
            raise RuntimeError("registered site was not returned in its workspace listing")

    class State(TypedDict):
        count: int

    def increment(state: State) -> dict[str, int]:
        return {"count": state["count"] + 1}

    builder = StateGraph(State)
    builder.add_node("increment", increment)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    thread_id = f"postgres-smoke-thread:{uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id}}
    with checkpoint_saver(database_url) as saver:
        graph = builder.compile(checkpointer=saver)
        result = graph.invoke({"count": 0}, config)
        if result.get("count") != 1:
            raise RuntimeError(f"checkpoint graph returned unexpected state: {result!r}")
    with checkpoint_saver(database_url) as saver:
        graph = builder.compile(checkpointer=saver)
        state = graph.get_state(config)
        if state.values.get("count") != 1:
            raise RuntimeError(f"checkpoint was not readable after reopen: {state.values!r}")

    print(
        json.dumps(
            {
                "migration": migration,
                "health": health.json().get("status"),
                "api_health": api_health.json().get("status"),
                "site_registered": site_name,
                "checkpoint_reopen_count": state.values["count"],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
