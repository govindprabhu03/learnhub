"""Standalone job worker. Run with: python backend/worker.py
Use together with WORKER_MODE=external on the web process (see docker-compose.yml); run as many copies as you like."""
import logging
import signal

import config
import db
import ingest  # noqa: F401  (importing registers the job handlers)
import jobs
import migrate
import observability


def main():
    observability.init("worker")
    migrate.upgrade()
    db.init()
    jobs.recover()
    stop, threads = jobs.start_workers(config.WORKER_THREADS)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())  # finish the current job, then exit
    logging.getLogger("worker").info("worker started with %d threads", config.WORKER_THREADS)
    stop.wait()
    for t in threads:
        t.join(timeout=60)


if __name__ == "__main__":
    main()
