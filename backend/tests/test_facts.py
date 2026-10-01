from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from backend.app import app
from backend.database import SessionLocal
from backend.models import Fact, utcnow


def _workspace(client: TestClient, name: str) -> dict:
    response = client.post("/api/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _payload(workspace_id: int, **overrides) -> dict:
    payload = {
        "workspace_id": workspace_id,
        "subject": "Bench press",
        "predicate": "max_load",
        "value": "100",
        "unit": "kg",
        "source_id": "catalog-42",
        "source_locator": "https://example.test/products/42#specs",
    }
    payload.update(overrides)
    return payload


def test_fact_import_confirmation_public_query_and_expiration():
    with TestClient(app) as client:
        workspace = _workspace(client, "facts")
        imported = client.post(f"/api/workspaces/{workspace['id']}/facts", json=_payload(workspace["id"], visibility="public"))
        assert imported.status_code == 201, imported.text
        fact = imported.json()
        assert fact["status"] == "proposed"
        assert fact["version"] == 1
        assert fact["visibility"] == "public"
        assert fact["source_locator"].endswith("#specs")
        assert client.get(f"/api/workspaces/{workspace['id']}/facts/public").json() == []

        missing_reviewer = client.post(f"/api/workspaces/{workspace['id']}/facts/{fact['id']}/confirm", json={})
        assert missing_reviewer.status_code == 422
        confirmed = client.post(
            f"/api/workspaces/{workspace['id']}/facts/{fact['id']}/confirm",
            json={"reviewer": "reviewer-1", "expected_version": 1},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "confirmed"
        public = client.get(f"/api/workspaces/{workspace['id']}/facts/public")
        assert public.status_code == 200
        assert public.json()[0]["value"] == "100"
        assert public.json()[0]["visibility"] == "public"
        assert public.json()[0]["source_locator"].endswith("#specs")

        with SessionLocal() as db:
            row = db.get(Fact, fact["id"])
            row.valid_until = utcnow() - timedelta(seconds=1)
            db.commit()
        assert client.get(f"/api/workspaces/{workspace['id']}/facts/public").json() == []
        listed = client.get(f"/api/workspaces/{workspace['id']}/facts").json()
        assert listed[0]["status"] == "expired"


def test_csv_fact_import_creates_proposed_rows_and_rolls_back_invalid_batch():
    with TestClient(app) as client:
        workspace = _workspace(client, "CSV facts")
        url = f"/api/workspaces/{workspace['id']}/facts/import-csv"
        header = "subject,predicate,value,unit,source_id,source_locator,visibility,valid_from,valid_until\r\n"
        valid_csv = (
            "\ufeff" + header
            + '"Bench, compact",max_load,250,kg,catalog-42,"https://example.test/catalog#bench",public,,\r\n'
            + "Bench,frame_material,steel,,catalog-42,product-sheet.xlsx#materials,,,,\r\n"
        )
        imported = client.post(url, content=valid_csv.encode("utf-8"), headers={"Content-Type": "text/csv"})
        assert imported.status_code == 201, imported.text
        facts = imported.json()
        assert len(facts) == 2
        assert facts[0]["subject"] == "Bench, compact"
        assert facts[0]["status"] == facts[1]["status"] == "proposed"
        assert facts[0]["visibility"] == "public"
        assert facts[1]["visibility"] == "internal_only"
        assert client.get(f"/api/workspaces/{workspace['id']}/facts/public").json() == []

        invalid_csv = header + "Row one,weight,25,kg,catalog-42,spec.xlsx#weight,internal_only,,\r\n" + "Row two,capacity,100,kg,,spec.xlsx#capacity,internal_only,,\r\n"
        rejected = client.post(url, content=invalid_csv, headers={"Content-Type": "text/csv"})
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["row"] == 3
        listed = client.get(f"/api/workspaces/{workspace['id']}/facts").json()
        assert len(listed) == 2


def test_fact_visibility_defaults_to_internal_and_public_query_requires_current_confirmation():
    with TestClient(app) as client:
        workspace = _workspace(client, "fact visibility")
        internal = client.post(f"/api/workspaces/{workspace['id']}/facts", json=_payload(workspace["id"])).json()
        assert internal["visibility"] == "internal_only"
        assert client.post(
            f"/api/workspaces/{workspace['id']}/facts/{internal['id']}/confirm",
            json={"reviewer": "reviewer-1"},
        ).status_code == 200

        current = client.post(
            f"/api/workspaces/{workspace['id']}/facts",
            json=_payload(workspace["id"], series_id="public-current", visibility="public"),
        ).json()
        future = client.post(
            f"/api/workspaces/{workspace['id']}/facts",
            json=_payload(
                workspace["id"],
                series_id="public-future",
                visibility="public",
                valid_from=(utcnow() + timedelta(days=1)).isoformat(),
            ),
        ).json()
        expired = client.post(
            f"/api/workspaces/{workspace['id']}/facts",
            json=_payload(
                workspace["id"],
                series_id="public-expired",
                visibility="public",
                valid_from=(utcnow() - timedelta(days=2)).isoformat(),
                valid_until=(utcnow() - timedelta(days=1)).isoformat(),
            ),
        ).json()
        proposed_public = client.post(
            f"/api/workspaces/{workspace['id']}/facts",
            json=_payload(workspace["id"], series_id="public-proposed", visibility="public"),
        ).json()

        assert [item["id"] for item in client.get(f"/api/workspaces/{workspace['id']}/facts/public").json()] == []
        for fact in (current, future, expired):
            confirmed = client.post(
                f"/api/workspaces/{workspace['id']}/facts/{fact['id']}/confirm",
                json={"reviewer": "reviewer-1"},
            )
            assert confirmed.status_code == 200, confirmed.text

        public = client.get(f"/api/workspaces/{workspace['id']}/facts/public")
        assert [item["id"] for item in public.json()] == [current["id"]]
        assert public.json()[0]["visibility"] == "public"
        invalid = client.post(
            f"/api/workspaces/{workspace['id']}/facts",
            json=_payload(workspace["id"], series_id="invalid-visibility", visibility="private"),
        )
        assert invalid.status_code == 422


def test_fact_versions_are_append_only_and_review_is_version_checked():
    with TestClient(app) as client:
        workspace = _workspace(client, "versioned facts")
        first = client.post("/api/facts", json=_payload(workspace["id"])).json()
        second_response = client.post(
            f"/api/workspaces/{workspace['id']}/facts",
            json=_payload(workspace["id"], parent_id=first["id"], value="110"),
        )
        assert second_response.status_code == 201, second_response.text
        second = second_response.json()
        assert second["id"] != first["id"]
        assert second["version"] == 2
        assert second["parent_id"] == first["id"]
        conflict = client.post(
            f"/api/workspaces/{workspace['id']}/facts/{second['id']}/confirm",
            json={"reviewer": "reviewer-1", "expected_version": 1},
        )
        assert conflict.status_code == 409
        assert client.get(f"/api/workspaces/{workspace['id']}/facts/{first['id']}").json()["status"] == "proposed"


def test_facts_are_isolated_by_workspace():
    with TestClient(app) as client:
        owner = _workspace(client, "owner")
        other = _workspace(client, "other")
        fact = client.post("/api/facts", json=_payload(owner["id"], series_id="shared-client-key")).json()
        other_fact = client.post("/api/facts", json=_payload(other["id"], series_id="shared-client-key")).json()
        assert other_fact["version"] == 1
        assert [item["id"] for item in client.get(f"/api/workspaces/{other['id']}/facts").json()] == [other_fact["id"]]
        assert client.get(f"/api/workspaces/{other['id']}/facts/{fact['id']}").status_code == 404
        assert client.post(
            f"/api/workspaces/{other['id']}/facts/{fact['id']}/confirm",
            json={"reviewer": "reviewer-1"},
        ).status_code == 404
        assert [item["id"] for item in client.get(f"/api/facts?workspace_id={other['id']}").json()] == [other_fact["id"]]
