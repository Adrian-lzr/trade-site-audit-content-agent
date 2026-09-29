from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import app
from backend.database import SessionLocal
from backend.models import Page, PageSnapshot, utcnow


SYNTHETIC_DEMO_VALVE_QUESTIONS = (
    "How should a buyer compare valve materials for a corrosive process fluid?",
    "What process details are needed to select a valve for a specified pressure range?",
    "How can a buyer determine the required valve size for a process line?",
    "Which operating conditions should be specified when requesting a valve recommendation?",
    "What information should a buyer provide when requesting a custom valve quotation?",
    "Which dimensional drawings should be reviewed before ordering a replacement valve?",
    "How can a buyer check whether a valve connection matches an existing pipeline?",
    "What test records can a buyer request for a specific valve model and production lot?",
    "Which certificates should a buyer verify for a valve shipment to the target market?",
    "How should a buyer compare the pressure and temperature limits stated for valve models?",
    "What maintenance details should a buyer review before choosing an industrial valve?",
    "Which spare parts should a buyer identify for planned valve maintenance?",
    "What packaging details should be confirmed for valves shipped overseas?",
    "Which lead-time details should a buyer confirm before placing a valve order?",
    "What information is needed to evaluate a valve sample for a new application?",
    "How can a buyer identify the correct actuator requirements for an automated valve?",
    "What installation clearances should a buyer verify for a selected valve assembly?",
    "Which documentation should accompany a valve for installation and maintenance?",
    "What questions should a buyer ask when comparing valve options for water treatment?",
    "How can a buyer check whether a proposed valve configuration matches the stated duty?",
)


def _workspace(client: TestClient, name: str) -> dict:
    response = client.post("/api/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _site(client: TestClient, workspace_id: int, name: str) -> dict:
    response = client.post(
        "/api/sites",
        json={
            "workspace_id": workspace_id,
            "name": name,
            "origin": f"https://{name.replace(' ', '-').lower()}.example.test",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _pages(site_id: int, paths: list[str]) -> list[Page]:
    with SessionLocal() as db:
        pages = [
            Page(site_id=site_id, canonical_url=f"https://valve-demo.example.test{path}")
            for path in paths
        ]
        db.add_all(pages)
        db.commit()
        for page in pages:
            db.refresh(page)
        return pages


def _question_payload(page_ids: list[int], *, count: int = 20) -> list[dict]:
    return [
        {
            "question": question,
            "product": "industrial valve",
            "use_case": "synthetic valve-supplier demo; buyer evaluating process requirements",
            "buyer_role": "procurement engineer",
            "purchase_stage": "supplier evaluation",
            "target_market": "United States",
            "language": "en",
            "page_ids": [page_ids[index % len(page_ids)]],
        }
        for index, question in enumerate(SYNTHETIC_DEMO_VALVE_QUESTIONS[:count])
    ]


def _create_set(client: TestClient, workspace_id: int, site_id: int, questions: list[dict]) -> dict:
    response = client.post(
        f"/api/workspaces/{workspace_id}/sites/{site_id}/query-sets",
        json={"name": f"Synthetic demo valve procurement {uuid4().hex}", "questions": questions},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_frozen_twenty_question_version_is_mapped_immutable_and_copyable():
    with TestClient(app) as client:
        workspace = _workspace(client, "synthetic valve questions")
        site = _site(client, workspace["id"], "Valve Demo")
        pages = _pages(site["id"], ["/products/industrial-valves", "/products/ball-valves", "/support/documents", "/request-a-quote"])
        page_ids = [page.id for page in pages]

        created = _create_set(client, workspace["id"], site["id"], _question_payload(page_ids))
        assert created["current_version"] == 1
        first = created["versions"][0]
        assert len(first["questions"]) == 20
        assert first["state"] == "draft"
        assert all(question["page_mappings"] for question in first["questions"])
        assert all(mapping["canonical_url"].startswith("https://valve-demo.example.test/") for question in first["questions"] for mapping in question["page_mappings"])
        collection_path = f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets"
        listed = client.get(collection_path)
        assert listed.status_code == 200
        assert listed.json()[0]["current_state"] == "draft"
        detail = client.get(f"{collection_path}/{created['id']}")
        assert detail.status_code == 200
        version_detail = client.get(f"{collection_path}/{created['id']}/versions/1")
        assert version_detail.status_code == 200
        assert version_detail.json()["questions"][0]["page_mappings"][0]["page_id"] in page_ids

        frozen = client.post(
            f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{created['id']}/versions/1/freeze",
            json={"expected_version": 1},
        )
        assert frozen.status_code == 200, frozen.text
        assert frozen.json()["state"] == "frozen"
        assert frozen.json()["edit_version"] == 2
        assert frozen.json()["frozen_at"] is not None
        assert len(frozen.json()["questions"]) == 20

        immutable = client.put(
            f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{created['id']}/versions/1",
            json={"expected_version": 2, "questions": _question_payload(page_ids)},
        )
        assert immutable.status_code == 409

        second_response = client.post(
            f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{created['id']}/versions",
            json={"expected_version": 1},
        )
        assert second_response.status_code == 201, second_response.text
        second = second_response.json()
        assert second["version"] == 2
        assert second["state"] == "draft"
        assert len(second["questions"]) == 20
        assert [item["question"] for item in second["questions"]] == [item["question"] for item in frozen.json()["questions"]]

        updated_questions = _question_payload(page_ids)
        updated_questions[0]["question"] = "Which process-fluid details should a buyer supply when screening valve materials?"
        update_response = client.put(
            f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{created['id']}/versions/2",
            json={"expected_version": 1, "questions": updated_questions},
        )
        assert update_response.status_code == 200, update_response.text
        assert update_response.json()["edit_version"] == 2
        assert update_response.json()["questions"][0]["question"] == updated_questions[0]["question"]
        stale = client.put(
            f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{created['id']}/versions/2",
            json={"expected_version": 1, "questions": updated_questions},
        )
        assert stale.status_code == 409


def test_freeze_requires_twenty_questions_and_a_mapping_for_every_question():
    with TestClient(app) as client:
        workspace = _workspace(client, "question set validation")
        site = _site(client, workspace["id"], "Question Site")
        pages = _pages(site["id"], ["/products/valves"])
        page_id = pages[0].id
        created = _create_set(client, workspace["id"], site["id"], _question_payload([page_id], count=19))
        path = f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{created['id']}/versions/1"
        incomplete = client.post(f"{path}/freeze", json={"expected_version": 1})
        assert incomplete.status_code == 409
        assert "exactly 20" in incomplete.json()["detail"]

        twenty_unmapped = _question_payload([page_id])
        twenty_unmapped[0]["page_ids"] = []
        replaced = client.put(path, json={"expected_version": 1, "questions": twenty_unmapped})
        assert replaced.status_code == 200, replaced.text
        missing_mapping = client.post(f"{path}/freeze", json={"expected_version": 2})
        assert missing_mapping.status_code == 409
        assert "map to at least one page" in missing_mapping.json()["detail"]


def test_question_sets_and_page_mappings_are_site_and_workspace_scoped():
    with TestClient(app) as client:
        owner = _workspace(client, "question owner")
        other = _workspace(client, "question other")
        owner_site = _site(client, owner["id"], "Owner Valve Site")
        other_site = _site(client, other["id"], "Other Valve Site")
        owner_page = _pages(owner_site["id"], ["/products/valves"])[0]
        other_page = _pages(other_site["id"], ["/products/valves"])[0]

        mismatch = client.get(f"/api/workspaces/{owner['id']}/sites/{other_site['id']}/query-sets")
        assert mismatch.status_code == 404
        response = client.post(
            f"/api/workspaces/{owner['id']}/sites/{owner_site['id']}/query-sets",
            json={
                "name": "Invalid cross-site map",
                "questions": [{**_question_payload([owner_page.id], count=1)[0], "page_ids": [other_page.id]}],
            },
        )
        assert response.status_code == 422
        assert client.get(f"/api/workspaces/{other['id']}/sites/{owner_site['id']}/query-sets").status_code == 404


def test_site_page_listing_exposes_stable_page_id_for_mappings():
    with TestClient(app) as client:
        workspace = _workspace(client, "page mapping ids")
        site = _site(client, workspace["id"], "Page Mapping Site")
        page = _pages(site["id"], ["/products/industrial-valves"])[0]
        with SessionLocal() as db:
            db.add(
                PageSnapshot(
                    page_id=page.id,
                    url=page.canonical_url,
                    status_code=200,
                    content_hash="a" * 64,
                    fetched_at=utcnow() - timedelta(seconds=1),
                )
            )
            db.commit()
        response = client.get(f"/api/sites/{site['id']}/pages")
        assert response.status_code == 200
        assert response.json()[0]["page_id"] == page.id
