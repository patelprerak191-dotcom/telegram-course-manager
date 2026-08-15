import os
import signal
import subprocess
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

PROCESS_COMMANDS = [
    ("admin_bot", [sys.executable, "-m", "app.admin_bot.main"]),
    ("customer_bot", [sys.executable, "-m", "app.customer_bot.main"]),
    ("expiry_worker", [sys.executable, "-m", "app.expiry_worker"]),
]

processes = {}


def start_process(name, command):
    print(f"[RUNNER] Starting {name}: {' '.join(command)}", flush=True)
    process = subprocess.Popen(command)
    processes[name] = process


def stop_all():
    print("[RUNNER] Stopping child processes...", flush=True)
    for name, process in list(processes.items()):
        if process.poll() is None:
            print(f"[RUNNER] Terminating {name} (pid={process.pid})", flush=True)
            try:
                process.terminate()
            except Exception as exc:
                print(f"[RUNNER] terminate error for {name}: {exc!r}", flush=True)

    deadline = time.time() + 10
    for name, process in list(processes.items()):
        if process.poll() is not None:
            continue
        remaining = max(0, deadline - time.time())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            print(f"[RUNNER] Killing {name} (pid={process.pid})", flush=True)
            try:
                process.kill()
            except Exception as exc:
                print(f"[RUNNER] kill error for {name}: {exc!r}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for name, command in PROCESS_COMMANDS:
        start_process(name, command)

    print("[RUNNER] All child processes started.", flush=True)
    yield
    stop_all()


app = FastAPI(title="Telegram Course Manager", version="1.0.0", lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "service": "telegram-course-manager",
        "status": "online",
        "health": "/health",
    }


@app.get("/health")
async def health():
    child_status = {}
    all_running = True

    for name, process in processes.items():
        running = process.poll() is None
        child_status[name] = {
            "running": running,
            "pid": process.pid,
        }
        if not running:
            all_running = False

    status_code = 200 if all_running and len(processes) == len(PROCESS_COMMANDS) else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if status_code == 200 else "degraded",
            "services": child_status,
        },
    )


def handle_signal(signum, frame):
    stop_all()
    raise SystemExit(0)


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "10000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
