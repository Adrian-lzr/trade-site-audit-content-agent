from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from sqlalchemy import func, select, update

from backend.database import SessionLocal
from backend.models import AuditFinding, Job, PageSnapshot, Site, Workspace, utcnow


@contextmanager
def local_fixture_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<!doctype html><html><head></head><body><h1>Crash recovery fixture</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def wait_until(predicate, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def stop_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.poll() is None:
        process.kill()
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        return process.communicate(timeout=5)


def test_worker_process_restart_recovers_interrupted_crawl(tmp_path):
    with local_fixture_server() as origin:
        with SessionLocal() as db:
            workspace = Workspace(name="Worker process recovery", external_id="worker-process-recovery")
            db.add(workspace)
            db.flush()
            site = Site(
                workspace_id=workspace.id,
                name="Recovery fixture",
                base_url=origin,
                allowed_paths='["/"]',
                is_synthetic=True,
            )
            db.add(site)
            db.flush()
            job = Job(site_id=site.id, status="queued")
            db.add(job)
            db.commit()
            job_id = job.id

        marker = tmp_path / "crawl-started"
        crash_script = """
import sys
import time
from pathlib import Path
import backend.audit as audit

class HangingCrawler:
    def fetch(self, *args, **kwargs):
        Path(sys.argv[1]).write_text("started", encoding="utf-8")
        time.sleep(600)

audit.FixtureCrawler = HangingCrawler
from backend.worker import JobWorker
JobWorker().run_forever(poll_interval=0.01)
"""
        repo_root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env["ALLOW_LOOPBACK"] = "true"
        crashed_worker = subprocess.Popen(
            [sys.executable, "-c", crash_script, str(marker)],
            cwd=repo_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert wait_until(lambda: marker.exists() or crashed_worker.poll() is not None), "worker did not reach the crawl"
            assert marker.exists(), "worker exited before the crawl started"
            crashed_worker.kill()
            crashed_worker.communicate(timeout=5)
        finally:
            if crashed_worker.poll() is None:
                stop_process(crashed_worker)

        with SessionLocal() as db:
            interrupted = db.get(Job, job_id)
            assert interrupted is not None
            assert interrupted.status == "running"
            assert interrupted.lease_token
            assert interrupted.lease_expires_at
            db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(lease_expires_at=utcnow() - timedelta(seconds=1))
            )
            db.commit()

        restarted_worker = subprocess.Popen(
            [sys.executable, "-m", "backend.worker"],
            cwd=repo_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            def recovered():
                with SessionLocal() as db:
                    current = db.get(Job, job_id)
                    snapshot_count = db.scalar(
                        select(func.count()).select_from(PageSnapshot).where(PageSnapshot.job_id == job_id)
                    )
                    return current is not None and current.status == "succeeded" and snapshot_count == 1

            assert wait_until(recovered), "restarted worker did not complete the expired job"
        finally:
            worker_stdout, worker_stderr = stop_process(restarted_worker)

        with SessionLocal() as db:
            completed = db.get(Job, job_id)
            assert completed is not None
            assert completed.status == "succeeded", worker_stderr or worker_stdout
            snapshots = db.scalars(select(PageSnapshot).where(PageSnapshot.job_id == job_id)).all()
            assert len(snapshots) == 1
            assert {finding.code for finding in snapshots[0].findings} == {"TITLE_MISSING", "RULE_SITEMAP_VALIDITY"}
            assert db.scalar(select(func.count()).select_from(AuditFinding).where(AuditFinding.snapshot_id == snapshots[0].id)) == 2
