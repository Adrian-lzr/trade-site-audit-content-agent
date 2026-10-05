"""Resolve the repository's single Alembic migration head.

Runtime smoke checks must compare a database with the migrations shipped by
this checkout.  Keeping the value in a script constant caused new migrations
to make an otherwise current database look stale, so callers derive it from
the repository's migration graph instead.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def repository_alembic_head(root: Path | None = None) -> str:
    """Return the sole Alembic head for ``root``.

    A branched migration graph is an operator error for these smoke checks and
    is rejected explicitly instead of selecting an arbitrary head.
    """

    project_root = (root or Path(__file__).resolve().parents[1]).resolve()
    config = Config(str(project_root / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "backend" / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected one Alembic head, found {heads!r}")
    return heads[0]
