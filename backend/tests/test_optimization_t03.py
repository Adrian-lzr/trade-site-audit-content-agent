from __future__ import annotations

from datetime import timedelta

from backend.app import app
from backend.database import SessionLocal
from backend.models import Fact, utcnow
from backend.services.facts import resolve_current_facts
from fastapi.testclient import TestClient


def _workspace(client: TestClient, name: str) -> dict:
    response = client.post("/api/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _fact_payload(workspace_id: int, **extra: object) -> dict:
    payload = {
        "workspace_id": workspace_id,
        "subject": "Valve-A",
        "predicate": "minimum_order_quantity",
        "value": "20",
        "unit": "pieces",
        "source_id": "catalog",
        "source_locator": "catalog.csv#minimum_order_quantity",
        "visibility": "public",
    }
    payload.update(extra)
    return payload


def test_resolver_replaces_open_ended_v1_at_v2_start_and_keeps_future_version_inactive():
    with TestClient(app) as client:
        workspace = _workspace(client, "T03 lifecycle")
        now = utcnow()
        first = client.post(
            f"/api/workspaces/{workspace['id']}/facts",
            json=_fact_payload(workspace["id"], valid_from=(now - timedelta(days=2)).isoformat()),
        ).json()
        second = client.post(
            f"/api/workspaces/{workspace['id']}/facts",
            json=_fact_payload(
                workspace["id"],
                series_id=first["series_id"],
                value="30",
                valid_from=(now + timedelta(days=1)).isoformat(),
            ),
        ).json()
        for item in (first, second):
            response = client.post(
                f"/api/workspaces/{workspace['id']}/facts/{item['id']}/confirm",
                json={"reviewer": "t03"},
            )
            assert response.status_code == 200, response.text
        with SessionLocal() as db:
            current, conflicts = resolve_current_facts(db, workspace["id"], as_of=now)
            assert not conflicts and [row.id for row in current] == [first["id"]]
            current, conflicts = resolve_current_facts(db, workspace["id"], as_of=now + timedelta(days=2))
            assert not conflicts and [row.id for row in current] == [second["id"]]


def test_resolver_reports_overlap_without_guessing_and_cross_workspace_isolated():
    with TestClient(app) as client:
        one = _workspace(client, "T03 overlap one")
        two = _workspace(client, "T03 overlap two")
        first = client.post(
            f"/api/workspaces/{one['id']}/facts",
            json=_fact_payload(one["id"], valid_until=(utcnow() + timedelta(days=3)).isoformat()),
        ).json()
        second = client.post(
            f"/api/workspaces/{one['id']}/facts",
            json=_fact_payload(one["id"], series_id=first["series_id"], value="40", valid_from=utcnow().isoformat()),
        ).json()
        # Confirming the second row is rejected because both explicit windows
        # are effective at the same point and the service cannot infer intent.
        client.post(f"/api/workspaces/{one['id']}/facts/{first['id']}/confirm", json={"reviewer": "t03"})
        rejected = client.post(f"/api/workspaces/{one['id']}/facts/{second['id']}/confirm", json={"reviewer": "t03"})
        assert rejected.status_code == 409
        foreign = client.post(f"/api/workspaces/{two['id']}/facts", json=_fact_payload(two["id"], series_id=first["series_id"])).json()
        with SessionLocal() as db:
            current, conflicts = resolve_current_facts(db, two["id"], series_ids=[first["series_id"]])
            assert not conflicts and current == []
        assert foreign["version"] == 1

