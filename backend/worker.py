from __future__ import annotations

from threading import Event

from sqlalchemy import select, update

from .audit import JobAlreadyClaimed, execute_job
from .database import SessionLocal
from .models import Job, JobStatus, utcnow


_QUEUE_KINDS = ("audit", "content", "review", "publication", "visibility")


class JobWorker:
    def __init__(self) -> None:
        # Keep a cursor for the lifetime of the worker process.  A fixed
        # ``audit first`` fallback starves content and publication whenever a
        # crawl backlog is continuously replenished.
        self._queue_cursor = 0

    def recover_interrupted(self) -> int:
        now = utcnow()
        with SessionLocal() as db:
            result = db.execute(
                update(Job)
                .execution_options(synchronize_session=False)
                .where(
                    Job.status == JobStatus.running.value,
                    Job.lease_expires_at.is_not(None),
                    Job.lease_expires_at <= now,
                )
                .values(
                    status=JobStatus.queued.value,
                    started_at=None,
                    finished_at=None,
                    error="requeued after worker lease expired",
                    lease_token=None,
                    lease_expires_at=None,
                )
            )
            db.commit()
            return result.rowcount or 0

    def _run_kind(self, kind: str) -> bool:
        """Try one queue family and return whether work was claimed."""

        if kind == "audit":
            with SessionLocal() as db:
                job_id = db.scalar(
                    select(Job.id)
                    .where(Job.status == JobStatus.queued.value)
                    .order_by(Job.created_at, Job.id)
                )
            if job_id is None:
                return False
            with SessionLocal() as work_db:
                try:
                    execute_job(work_db, job_id)
                except JobAlreadyClaimed:
                    return False
                except Exception:
                    # execute_job records a guarded failure; a bad task must
                    # not stop the scheduler from serving other queue kinds.
                    pass
            return True

        if kind == "content":
            # Content tasks share this worker process but have their own lease
            # and checkpoint lifecycle. A missing optional workflow dependency
            # must not prevent unrelated queues from progressing.
            try:
                from .content_worker import ContentGenerationWorker
            except ModuleNotFoundError as exc:
                if exc.name != "langgraph":
                    raise
                return False
            return ContentGenerationWorker().run_once()

        if kind == "review":
            try:
                from .review_worker import ReviewResumeWorker
            except ModuleNotFoundError as exc:
                if exc.name != "langgraph":
                    raise
                return False

            return ReviewResumeWorker().run_once()
        if kind == "publication":
            from .publication_worker import PublicationWorker

            return PublicationWorker().run_once()
        if kind == "visibility":
            from .visibility_worker import VisibilityWorker

            return VisibilityWorker().run_once()
        raise ValueError(f"unknown queue kind: {kind}")

    def run_once(self) -> bool:
        self.recover_interrupted()
        for offset in range(len(_QUEUE_KINDS)):
            index = (self._queue_cursor + offset) % len(_QUEUE_KINDS)
            if self._run_kind(_QUEUE_KINDS[index]):
                self._queue_cursor = (index + 1) % len(_QUEUE_KINDS)
                return True
        return False

    def run_forever(self, poll_interval: float = 1.0, stop_event: Event | None = None) -> None:
        stop_event = stop_event or Event()
        self.recover_interrupted()
        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(poll_interval)


if __name__ == "__main__":
    JobWorker().run_forever()
