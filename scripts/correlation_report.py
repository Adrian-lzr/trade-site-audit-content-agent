"""Emit a read-only T16 request-to-publication correlation report.

The command only reads the configured database.  It reports identifiers and
state transitions, never prompts, model responses, page bodies, or secrets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal
from backend.observability import build_correlation_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=int)
    parser.add_argument("--request-id")
    parser.add_argument("--task-id", type=int)
    parser.add_argument("--change-request-id", type=int)
    parser.add_argument("--visibility-run-id", type=int)
    parser.add_argument("--artifact", action="append", default=[], type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    command = ["python", "scripts/correlation_report.py", *sys.argv[1:]]
    with SessionLocal() as db:
        report = build_correlation_report(
            db,
            workspace_id=args.workspace_id,
            request_id=args.request_id,
            task_id=args.task_id,
            change_request_id=args.change_request_id,
            visibility_run_id=args.visibility_run_id,
            command=command,
            artifact_paths=args.artifact,
        )
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
