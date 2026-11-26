"""Worker nạp PDF: nhận job đang chờ, bóc tách, embed, rồi ghi trạng thái."""

import time

from app.ingest import ingest_pdf_file
from app.jobs import claim_job, recover_stuck_jobs, schedule_retry, update_job

_QUOTA_MARKERS = ("hạn mức", "quota", "RESOURCE_EXHAUSTED", "per day")
_RETRY_MARKERS = ("429", "503", "timed out", "Timeout", "temporarily")


def _permanent(exc: BaseException) -> bool:
    text = str(exc)
    return any(marker in text for marker in _QUOTA_MARKERS)


def _retryable(exc: BaseException) -> bool:
    if _permanent(exc):
        return False
    text = str(exc)
    return any(marker in text for marker in _RETRY_MARKERS)


def _backoff(attempt: int) -> float:
    return min(45.0, 5.0 * (3 ** max(attempt - 1, 0)))


def process_job(job: dict) -> None:
    job_id = job["id"]

    def on_stage(stage: str, progress: int) -> None:
        status = stage if stage in {"parsing", "indexing"} else "indexing"
        update_job(job_id, status=status, stage=stage, progress=progress)

    try:
        result = ingest_pdf_file(
            job["file_path"],
            original_filename=job["filename"],
            namespace=job["namespace"],
            on_stage=on_stage,
        )
    except Exception as exc:
        if _retryable(exc) and job["attempt"] < job["max_attempts"]:
            delay = _backoff(job["attempt"])
            schedule_retry(job_id, delay, f"Lần {job['attempt']} lỗi tạm thời, chạy lại sau {delay:.0f}s.")
            print(f"[worker] {job_id} retry in {delay:.0f}s", flush=True)
            return
        update_job(job_id, status="failed", stage="failed", progress=100, error=str(exc)[:500])
        print(f"[worker] {job_id} failed: {exc}", flush=True)
        return

    status = "duplicate" if result["status"] == "duplicate" else "ready"
    update_job(
        job_id,
        status=status,
        stage=status,
        progress=100,
        document_id=result["document_id"],
        chunk_count=result["chunk_count"],
        error=None,
    )
    print(f"[worker] {job_id} {status} chunks={result['chunk_count']}", flush=True)


def run() -> None:
    from app.indexing import beat, claim_event, process_event, recover_stuck_events

    from app.files import claim_workspace_file, process_workspace_file, recover_stuck_files

    recovered = recover_stuck_jobs()
    recovered_events = recover_stuck_events()
    recovered_files = recover_stuck_files()
    print(
        f"[worker] listening, recovered={recovered}, events={recovered_events}, files={recovered_files}",
        flush=True,
    )
    while True:
        beat("idle")
        uploaded = claim_workspace_file()
        if uploaded:
            try:
                process_workspace_file(uploaded)
            except Exception as exc:
                print(f"[worker] file {uploaded['id']} failed: {exc}", flush=True)
                time.sleep(1)
            continue
        job = claim_job()
        if job:
            try:
                process_job(job)
            except Exception as exc:
                print(f"[worker] crash on {job['id']}: {exc}", flush=True)
                time.sleep(1)
            continue
        event = claim_event()
        if not event:
            time.sleep(1)
            continue
        try:
            process_event(event)
        except Exception as exc:
            print(f"[worker] index crash on {event['id']}: {exc}", flush=True)
            time.sleep(1)


if __name__ == "__main__":
    run()
