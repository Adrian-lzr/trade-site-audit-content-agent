from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from backend.app import app
from backend.database import SessionLocal
from backend.models import (
    ChangeRequest,
    ChangeRevision,
    ContentGenerationItem,
    ContentGenerationTask,
    Fact,
    Page,
    PageSnapshot,
    ProcurementQuestion,
    ProcurementQuestionPageMapping,
    Site,
    utcnow,
)


def _clear_content_tasks() -> None:
    with SessionLocal() as db:
        db.query(ContentGenerationItem).delete()
        db.query(ContentGenerationTask).delete()
        db.commit()


def _workspace(client: TestClient, name: str) -> dict:
    response = client.post("/api/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _site(client: TestClient, workspace_id: int, name: str) -> dict:
    response = client.post(
        "/api/sites",
        json={"workspace_id": workspace_id, "name": name, "origin": f"https://{name.lower()}.example.test"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _source_rows(site_id: int, workspace_id: int, paths: list[str], *, with_snapshot: bool = True) -> tuple[list[dict], int]:
    with SessionLocal() as db:
        pages = [Page(site_id=site_id, canonical_url=f"https://task-test.example.test{path}") for path in paths]
        db.add_all(pages)
        db.flush()
        snapshots: list[PageSnapshot] = []
        for index, page in enumerate(pages):
            if with_snapshot:
                content = f"<html><title>Fixture page {index}</title></html>"
                snapshots.append(
                    PageSnapshot(
                        page_id=page.id,
                        url=page.canonical_url,
                        status_code=200,
                        title=f"Fixture page {index}",
                        content_hash=hashlib.sha256(content.encode()).hexdigest(),
                        content=content,
                        fetched_at=utcnow() - timedelta(minutes=index),
                    )
                )
        fact = Fact(
            workspace_id=workspace_id,
            subject="Industrial valve",
            predicate="documentation availability",
            value="A product drawing can be requested for review.",
            source_id="synthetic-fixture",
            source_locator="fixture://valve/documentation",
            visibility="public",
            status="confirmed",
            valid_from=utcnow() - timedelta(minutes=1),
        )
        db.add_all([*snapshots, fact])
        db.commit()
        return [
            {
                "id": page.id,
                "canonical_url": page.canonical_url,
                "snapshot_id": snapshots[index].id if with_snapshot else None,
                "snapshot_hash": snapshots[index].content_hash if with_snapshot else None,
            }
            for index, page in enumerate(pages)
        ], fact.id


def _question_set(client: TestClient, workspace_id: int, site_id: int, page_ids: list[int]) -> dict:
    questions = [
        {
            "question": f"How should a buyer select an industrial valve for procurement scenario {index + 1}?",
            "product": "industrial valve",
            "use_case": "synthetic supplier-evaluation scenario",
            "buyer_role": "procurement engineer",
            "purchase_stage": "supplier evaluation",
            "target_market": "United States",
            "language": "en",
            "page_ids": [page_ids[index % len(page_ids)]],
        }
        for index in range(20)
    ]
    response = client.post(
        f"/api/workspaces/{workspace_id}/sites/{site_id}/query-sets",
        json={"name": f"Content task set {uuid4().hex}", "questions": questions},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _freeze(client: TestClient, workspace_id: int, site_id: int, query_set: dict) -> dict:
    response = client.post(
        f"/api/workspaces/{workspace_id}/sites/{site_id}/query-sets/{query_set['id']}/versions/1/freeze",
        json={"expected_version": 1},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _item(question_id: int, page: dict, fact_id: int, *, summary: str = "Prepare a cautious supplier-evaluation draft.") -> dict:
    return {
        "question_id": question_id,
        "page_id": page["id"],
        "expected_snapshot_id": page["snapshot_id"],
        "expected_snapshot_hash": page["snapshot_hash"],
        "request_summary": summary,
        "required_fact_ids": [fact_id],
    }


def test_content_task_creation_binds_snapshot_facts_and_change_revision():
    _clear_content_tasks()
    with TestClient(app) as client:
        workspace = _workspace(client, "content task success")
        site = _site(client, workspace["id"], "content-task")
        pages, fact_id = _source_rows(site["id"], workspace["id"], ["/products/valves", "/support/documents"])
        query_set = _question_set(client, workspace["id"], site["id"], [page["id"] for page in pages])
        frozen = _freeze(client, workspace["id"], site["id"], query_set)
        questions = frozen["questions"]

        response = client.post(
            f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{query_set['id']}/versions/1/content-tasks",
            json={"items": [_item(questions[0]["id"], pages[0], fact_id), _item(questions[1]["id"], pages[1], fact_id)]},
        )

        assert response.status_code == 202, response.text
        task = response.json()
        assert task["status"] == "queued"
        assert task["generation_source"] == "fixture"
        assert task["workspace_id"] == workspace["id"]
        assert task["site_id"] == int(site["id"])
        assert task["question_set_version_id"] == frozen["id"]
        assert len(task["items"]) == 2
        assert len({item["id"] for item in task["items"]}) == 2
        assert len({item["thread_id"] for item in task["items"]}) == 2

        first = task["items"][0]
        assert first["question"] == questions[0]["question"]
        assert first["canonical_url"] == pages[0]["canonical_url"]
        assert first["status"] == "queued"
        assert first["snapshot_id"] == pages[0]["snapshot_id"]
        assert first["snapshot_hash"] == pages[0]["snapshot_hash"]
        assert first["required_fact_ids"] == [fact_id]
        assert first["change_request"]["state"] == "draft"
        assert first["change_request"]["revision"]["base_snapshot_id"] == pages[0]["snapshot_id"]
        assert first["change_request"]["revision"]["base_content_hash"] == pages[0]["snapshot_hash"]
        assert first["change_request"]["revision"]["field_diff"] == {}
        assert first["change_request"]["revision"]["fact_versions"][0]["fact_id"] == fact_id
        assert first["change_request"]["revision"]["content_hash"]

        detail = client.get(f"/api/workspaces/{workspace['id']}/sites/{site['id']}/content-tasks/{task['id']}")
        assert detail.status_code == 200
        assert detail.json() == task
        listed = client.get(f"/api/workspaces/{workspace['id']}/sites/{site['id']}/content-tasks")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == task["id"]
        assert "items" not in listed.json()[0]


def test_content_task_rejects_duplicate_pairs_noncurrent_version_and_bad_mapping():
    _clear_content_tasks()
    with TestClient(app) as client:
        workspace = _workspace(client, "content task validation")
        site = _site(client, workspace["id"], "content-validation")
        pages, fact_id = _source_rows(site["id"], workspace["id"], ["/products/valves", "/support/documents"])
        query_set = _question_set(client, workspace["id"], site["id"], [page["id"] for page in pages])
        frozen = _freeze(client, workspace["id"], site["id"], query_set)
        questions = frozen["questions"]
        path = f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{query_set['id']}/versions/1/content-tasks"

        duplicate = _item(questions[0]["id"], pages[0], fact_id)
        response = client.post(path, json={"items": [duplicate, duplicate]})
        assert response.status_code == 422

        wrong_page = client.post(path, json={"items": [_item(questions[0]["id"], pages[1], fact_id)]})
        assert wrong_page.status_code == 422

        next_version = client.post(
            f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{query_set['id']}/versions",
            json={"expected_version": 1},
        )
        assert next_version.status_code == 201, next_version.text
        stale_frozen_version = client.post(path, json={"items": [_item(questions[0]["id"], pages[0], fact_id)]})
        assert stale_frozen_version.status_code == 409

        draft_version_path = f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{query_set['id']}/versions/2/content-tasks"
        draft_version = client.post(
            draft_version_path,
            json={"items": [_item(next_version.json()["questions"][0]["id"], pages[0], fact_id)]},
        )
        assert draft_version.status_code == 409


def test_content_task_rejects_stale_or_missing_snapshots_and_nonpublic_facts_atomically():
    _clear_content_tasks()
    with TestClient(app) as client:
        workspace = _workspace(client, "content task stale sources")
        site = _site(client, workspace["id"], "content-stale")
        pages, fact_id = _source_rows(site["id"], workspace["id"], ["/products/valves", "/support/documents", "/request-quote"])
        query_set = _question_set(client, workspace["id"], site["id"], [page["id"] for page in pages])
        frozen = _freeze(client, workspace["id"], site["id"], query_set)
        questions = frozen["questions"]

        with SessionLocal() as db:
            page = db.get(Page, pages[0]["id"])
            new_content = "<html><title>new source revision</title></html>"
            db.add(
                PageSnapshot(
                    page_id=page.id,
                    url=page.canonical_url,
                    status_code=200,
                    content_hash=hashlib.sha256(new_content.encode()).hexdigest(),
                    content=new_content,
                    fetched_at=utcnow() + timedelta(seconds=1),
                )
            )
            internal = Fact(
                workspace_id=workspace["id"],
                subject="Internal valve note",
                predicate="capacity",
                value="Confidential internal value",
                source_id="internal-fixture",
                source_locator="fixture://internal",
                visibility="internal_only",
                status="confirmed",
                valid_from=utcnow() - timedelta(minutes=1),
            )
            db.add(internal)
            db.commit()
            internal_fact_id = internal.id

        base = f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{query_set['id']}/versions/1/content-tasks"
        stale = client.post(base, json={"items": [_item(questions[0]["id"], pages[0], fact_id)]})
        assert stale.status_code == 409

        missing_page, missing_fact = _source_rows(site["id"], workspace["id"], ["/page-without-snapshot"], with_snapshot=False)
        with SessionLocal() as db:
            question = db.get(ProcurementQuestion, questions[2]["id"])
            db.add(ProcurementQuestionPageMapping(question_id=question.id, page_id=missing_page[0]["id"], position=2))
            db.commit()
        no_snapshot = client.post(
            base,
            json={
                "items": [
                    {
                        "question_id": questions[2]["id"],
                        "page_id": missing_page[0]["id"],
                        "expected_snapshot_id": 1,
                        "expected_snapshot_hash": "a" * 64,
                        "request_summary": "Prepare draft",
                        "required_fact_ids": [fact_id],
                    }
                ]
            },
        )
        assert no_snapshot.status_code == 409

        invalid_fact = client.post(
            base,
            json={"items": [_item(questions[1]["id"], pages[1], internal_fact_id)]},
        )
        assert invalid_fact.status_code == 409

        with SessionLocal() as db:
            assert db.query(ContentGenerationTask).count() == 0
            assert db.query(ChangeRequest).count() == 0


def test_content_task_scope_and_revision_generation_id_uniqueness():
    _clear_content_tasks()
    with TestClient(app) as client:
        workspace = _workspace(client, "content task scope")
        site = _site(client, workspace["id"], "content-scope")
        other_workspace = _workspace(client, "other content task scope")
        other_site = _site(client, other_workspace["id"], "other-content-scope")
        pages, fact_id = _source_rows(site["id"], workspace["id"], ["/products/valves"])
        other_pages, _ = _source_rows(int(other_site["id"]), other_workspace["id"], ["/products/valves"])
        query_set = _question_set(client, workspace["id"], site["id"], [pages[0]["id"]])
        frozen = _freeze(client, workspace["id"], site["id"], query_set)
        question = frozen["questions"][0]
        with SessionLocal() as db:
            db.add(
                ProcurementQuestionPageMapping(
                    question_id=question["id"], page_id=other_pages[0]["id"], position=2
                )
            )
            db.commit()
        path = f"/api/workspaces/{workspace['id']}/sites/{site['id']}/query-sets/{query_set['id']}/versions/1/content-tasks"
        created = client.post(path, json={"items": [_item(question["id"], pages[0], fact_id)]})
        assert created.status_code == 202, created.text
        task_id = created.json()["id"]

        cross_site_page = client.post(
            path,
            json={"items": [_item(question["id"], other_pages[0], fact_id)]},
        )
        assert cross_site_page.status_code == 422
        assert client.get(f"/api/workspaces/{other_workspace['id']}/sites/{site['id']}/content-tasks/{task_id}").status_code == 404
        assert client.get(f"/api/workspaces/{workspace['id']}/sites/{other_site['id']}/content-tasks/{task_id}").status_code == 404
        assert client.post(
            f"/api/workspaces/{other_workspace['id']}/sites/{site['id']}/query-sets/{query_set['id']}/versions/1/content-tasks",
            json={"items": [_item(question["id"], pages[0], fact_id)]},
        ).status_code == 404

        with SessionLocal() as db:
            change = db.get(ChangeRequest, created.json()["items"][0]["change_request_id"])
            revision = db.get(ChangeRevision, change.current_revision_id)
            revision.generation_id = "content-task-test-generation"
            db.commit()
            db.add(
                ChangeRevision(
                    change_request_id=change.id,
                    revision=revision.revision + 1,
                    state="draft",
                    base_snapshot_id=revision.base_snapshot_id,
                    base_content_hash=revision.base_content_hash,
                    field_diff_json=json.dumps({}),
                    fact_versions_json=json.dumps([]),
                    content_hash="f" * 64,
                    generation_id="content-task-test-generation",
                )
            )
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()
