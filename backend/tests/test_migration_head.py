from pathlib import Path

from scripts.migration_head import repository_alembic_head


def test_repository_alembic_head_matches_current_migration_graph():
    root = Path(__file__).resolve().parents[2]

    assert repository_alembic_head(root) == "0019_workflow_review_events"
