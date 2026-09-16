"""Serve a throwaway MCRIT instance for the end-to-end tests.

By default storage is in memory and the queue is MCRIT's "fake" one, so neither MongoDB nor a
worker process is involved. ``--storage mongodb`` runs MCRIT in its production shape instead,
against a MongoDB that is already running; that shape needs a worker as well, which is this same
script with ``--role worker``. MCRIT reads its storage and queue settings from dataclass defaults
only, with no config file or environment override, which is why this launcher exists.

    python tests/e2e/mcrit_server.py --port 8123
    python tests/e2e/mcrit_server.py --storage mongodb --mongo-db mcrit_e2e --port 8123
    python tests/e2e/mcrit_server.py --storage mongodb --mongo-db mcrit_e2e --role worker
"""

from __future__ import annotations

import argparse
import os
import secrets
import signal
import sys
import types
from http import HTTPStatus

READY_PREFIX = "MCRIT_E2E_READY "
FAIL_PATH_ENV = "MCRIT_E2E_FAIL_PATH"
FAIL_COUNT_ENV = "MCRIT_E2E_FAIL_COUNT"
FAIL_STATUS_ENV = "MCRIT_E2E_FAIL_STATUS"


def _use_short_hex_ids() -> None:
    # /jobs/{id} and /results/{id} only route 24-character hex ids, while the fake queue mints
    # uuid4 strings, which those routes answer with HTTP 400. Still the case on mcrit 1.9.0, so
    # this patch can go once LocalQueue and JobResource agree on an id format upstream.
    from mcrit.queue import LocalQueue

    setattr(LocalQueue, "uuid", types.SimpleNamespace(uuid4=lambda: secrets.token_hex(12)))


def _with_failure_hook(app):
    """Fail the first ``MCRIT_E2E_FAIL_COUNT`` requests below ``MCRIT_E2E_FAIL_PATH``."""
    prefix = os.environ.get(FAIL_PATH_ENV, "")
    try:
        remaining = int(os.environ.get(FAIL_COUNT_ENV, "0") or 0)
        status = int(os.environ.get(FAIL_STATUS_ENV, "500") or 500)
    except ValueError:
        remaining, status = 0, 500
    if not prefix or remaining <= 0:
        return app
    reason = HTTPStatus(status).phrase
    state = {"left": remaining}

    def wrapper(environ, start_response):
        if state["left"] > 0 and environ.get("PATH_INFO", "").startswith(prefix):
            state["left"] -= 1
            body = b'{"status": "failed", "data": {"message": "injected failure"}}'
            start_response(
                f"{status} {reason}",
                [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
            )
            return [body]
        return app(environ, start_response)

    return wrapper


def configure(storage: str, host: str, port: int, database: str) -> None:
    """Point MCRIT at the requested backend. Every process reads these class-level configs."""
    from mcrit.config.McritConfig import McritConfig
    from mcrit.queue.QueueFactory import QueueFactory
    from mcrit.storage.StorageFactory import StorageFactory

    if storage == "memory":
        McritConfig.STORAGE_CONFIG.STORAGE_METHOD = StorageFactory.STORAGE_METHOD_MEMORY
        McritConfig.QUEUE_CONFIG.QUEUE_METHOD = QueueFactory.QUEUE_METHOD_FAKE
        _use_short_hex_ids()
        return
    McritConfig.STORAGE_CONFIG.STORAGE_METHOD = StorageFactory.STORAGE_METHOD_MONGODB
    McritConfig.QUEUE_CONFIG.QUEUE_METHOD = QueueFactory.QUEUE_METHOD_MONGODB
    McritConfig.STORAGE_CONFIG.STORAGE_SERVER = host
    McritConfig.STORAGE_CONFIG.STORAGE_PORT = str(port)
    McritConfig.STORAGE_CONFIG.STORAGE_MONGODB_DBNAME = database
    McritConfig.QUEUE_CONFIG.QUEUE_SERVER = host
    McritConfig.QUEUE_CONFIG.QUEUE_PORT = str(port)
    McritConfig.QUEUE_CONFIG.QUEUE_MONGODB_DBNAME = database


def build_app():
    from mcrit.server.application_routes import get_app

    return _with_failure_hook(get_app())


def _install_shutdown_handlers() -> None:
    # waitress turns SystemExit inside its loop into a clean shutdown. Windows has no SIGTERM
    # delivery for another process, so CTRL_BREAK (SIGBREAK) is the signal that arrives there.
    def stop(signum, frame):
        sys.exit(0)

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            signal.signal(number, stop)


def serve(port: int, threads: int) -> int:
    from waitress import create_server

    server = create_server(build_app(), host="127.0.0.1", port=port, threads=threads)
    _install_shutdown_handlers()
    print(f"{READY_PREFIX}http://127.0.0.1:{server.effective_port}/", flush=True)
    server.run()
    return 0


def work() -> int:
    from mcrit.Worker import Worker

    _install_shutdown_handlers()
    print(f"{READY_PREFIX}worker", flush=True)
    with Worker() as worker:
        worker.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCRIT_E2E_PORT", "0") or 0),
        help="port on 127.0.0.1 to listen on (0 picks a free one)",
    )
    parser.add_argument("--role", choices=("server", "worker"), default="server")
    parser.add_argument(
        "--storage",
        choices=("memory", "mongodb"),
        default=os.environ.get("MCRIT_E2E_STORAGE", "memory"),
    )
    parser.add_argument("--mongo-host", default=os.environ.get("MCRIT_E2E_MONGO_HOST", "127.0.0.1"))
    parser.add_argument(
        "--mongo-port", type=int, default=int(os.environ.get("MCRIT_E2E_MONGO_PORT", "27017"))
    )
    parser.add_argument("--mongo-db", default=os.environ.get("MCRIT_E2E_MONGO_DB", "mcrit_e2e"))
    args = parser.parse_args(argv)
    configure(args.storage, args.mongo_host, args.mongo_port, args.mongo_db)
    if args.role == "worker":
        return work()
    # The fake queue runs each job inside the request that submits it, on MCRIT's in-memory
    # storage, which no lock protects: one thread keeps concurrent requests off each other.
    return serve(args.port, threads=1 if args.storage == "memory" else 4)


if __name__ == "__main__":
    raise SystemExit(main())
