from __future__ import annotations

from threading import Event

from sqlalchemy import select, update

from .audit import JobAlreadyClaimed, execute_job
from .database import SessionLocal
from .models import Job, JobStatus, utcnow


class JobWorker:
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

    def run_once(self) -> bool:
        self.recover_interrupted()
        with SessionLocal() as db:
            job_id = db.scalar(select(Job.id).where(Job.status == JobStatus.queued.value).order_by(Job.created_at))
            if job_id is None:
                # Content tasks share this worker process but have their own lease
                # and checkpoint lifecycle. Import lazily to avoid startup cycles.
                # A missing optional workflow dependency must not prevent unrelated
                # publication or visibility work from progressing. The dependency
                # is still reported by the content worker when content work exists.
                try:
                    from .content_worker import ContentGenerationWorker
                except ModuleNotFoundError as exc:
                    if exc.name != "langgraph":
                        raise
                else:
                    if ContentGenerationWorker().run_once():
                        return True
                from .publication_worker import PublicationWorker

                if PublicationWorker().run_once():
                    return True
                from .visibility_worker import VisibilityWorker

                return VisibilityWorker().run_once()
        with SessionLocal() as work_db:
            try:
                execute_job(work_db, job_id)
            except JobAlreadyClaimed:
                pass
            except Exception:
                pass
        return True

    def run_forever(self, poll_interval: float = 1.0, stop_event: Event | None = None) -> None:
        stop_event = stop_event or Event()
        self.recover_interrupted()
        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(poll_interval)


if __name__ == "__main__":
    JobWorker().run_forever()
