import os
import signal
import subprocess
import sys
import time
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse


# ============================================================
# CHILD PROCESSES
# ============================================================

PROCESS_COMMANDS = [
    ("admin_bot", [sys.executable, "-m", "app.admin_bot.main"]),
    ("customer_bot", [sys.executable, "-m", "app.customer_bot.main"]),
    ("expiry_worker", [sys.executable, "-m", "app.expiry_worker"]),
]


processes = {}
reported_exits = set()
monitor_thread = None
shutdown_requested = False


# ============================================================
# CHILD PROCESS OUTPUT
# ============================================================

def forward_output(name, stream, label):
    """
    Forward child process stdout/stderr to the main
    Render/Uvicorn console.

    UTF-8 is explicitly used so emoji and Unicode
    characters do not crash the child processes.
    """

    try:
        for line in iter(stream.readline, ""):
            if line:
                print(
                    f"[{name}][{label}] {line.rstrip()}",
                    flush=True,
                )

    except Exception as exc:
        print(
            f"[RUNNER] Output forwarding error "
            f"for {name}/{label}: {exc!r}",
            flush=True,
        )

    finally:
        try:
            stream.close()
        except Exception:
            pass


# ============================================================
# START CHILD PROCESS
# ============================================================

def start_process(name, command):
    print(
        f"[RUNNER] Starting {name}: {' '.join(command)}",
        flush=True,
    )

    try:
        # Force UTF-8 for child Python processes.
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )

        processes[name] = process

        print(
            f"[RUNNER] {name} started "
            f"with pid={process.pid}",
            flush=True,
        )

        # Forward normal output.
        threading.Thread(
            target=forward_output,
            args=(name, process.stdout, "STDOUT"),
            daemon=True,
        ).start()

        # Forward errors.
        threading.Thread(
            target=forward_output,
            args=(name, process.stderr, "STDERR"),
            daemon=True,
        ).start()

    except Exception as exc:
        print(
            f"[RUNNER] FAILED to start {name}: {exc!r}",
            flush=True,
        )


# ============================================================
# MONITOR CHILD PROCESSES
# ============================================================

def monitor_processes():
    """
    Monitor child processes.

    If a child process exits, report it once instead
    of printing the same error every few seconds.
    """

    global shutdown_requested

    while not shutdown_requested:

        time.sleep(5)

        for name, process in list(processes.items()):

            return_code = process.poll()

            if return_code is not None:

                if name not in reported_exits:

                    reported_exits.add(name)

                    print(
                        f"[RUNNER][PROCESS EXITED] "
                        f"{name} stopped with "
                        f"exit code {return_code}",
                        flush=True,
                    )


# ============================================================
# STOP ALL CHILD PROCESSES
# ============================================================

def stop_all():

    global shutdown_requested

    if shutdown_requested:
        return

    shutdown_requested = True

    print(
        "[RUNNER] Stopping child processes...",
        flush=True,
    )

    # First try graceful termination.
    for name, process in list(processes.items()):

        if process.poll() is None:

            print(
                f"[RUNNER] Terminating {name} "
                f"(pid={process.pid})",
                flush=True,
            )

            try:
                process.terminate()

            except Exception as exc:

                print(
                    f"[RUNNER] terminate error "
                    f"for {name}: {exc!r}",
                    flush=True,
                )

    # Give processes up to 10 seconds.
    deadline = time.time() + 10

    for name, process in list(processes.items()):

        if process.poll() is not None:
            continue

        remaining = max(
            0,
            deadline - time.time(),
        )

        try:

            process.wait(
                timeout=remaining
            )

        except subprocess.TimeoutExpired:

            print(
                f"[RUNNER] Killing {name} "
                f"(pid={process.pid})",
                flush=True,
            )

            try:
                process.kill()

            except Exception as exc:

                print(
                    f"[RUNNER] kill error "
                    f"for {name}: {exc!r}",
                    flush=True,
                )


# ============================================================
# FASTAPI LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    global monitor_thread

    print(
        "[RUNNER] ========================================",
        flush=True,
    )

    print(
        "[RUNNER] Starting Telegram Course Manager",
        flush=True,
    )

    print(
        "[RUNNER] ========================================",
        flush=True,
    )

    # Start all child services.
    for name, command in PROCESS_COMMANDS:

        start_process(
            name,
            command,
        )

    # Start process monitor.
    monitor_thread = threading.Thread(
        target=monitor_processes,
        daemon=True,
    )

    monitor_thread.start()

    print(
        "[RUNNER] All child processes started.",
        flush=True,
    )

    # Application remains alive.
    yield

    # Shutdown.
    stop_all()


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="Telegram Course Manager",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================
# ROOT ENDPOINT
# ============================================================

@app.get("/")
async def root():

    return {
        "service": "telegram-course-manager",
        "status": "online",
        "health": "/health",
    }


@app.head("/")
async def root_head():

    return


# ============================================================
# HEALTH ENDPOINT
# ============================================================

@app.get("/health")
async def health():

    child_status = {}

    all_running = True

    for name, process in processes.items():

        return_code = process.poll()

        running = return_code is None

        child_status[name] = {
            "running": running,
            "pid": process.pid,
            "exit_code": return_code,
        }

        if not running:

            all_running = False

    # Healthy only when all expected processes
    # are present and running.
    healthy = (
        all_running
        and len(processes) == len(PROCESS_COMMANDS)
    )

    status_code = (
        200
        if healthy
        else 503
    )

    return JSONResponse(

        status_code=status_code,

        content={

            "status": (
                "healthy"
                if healthy
                else "degraded"
            ),

            "services": child_status,

        },
    )


@app.head("/health")
async def health_head():

    return


# ============================================================
# SIGNAL HANDLING
# ============================================================

def handle_signal(signum, frame):

    print(
        f"[RUNNER] Received signal {signum}",
        flush=True,
    )

    stop_all()

    raise SystemExit(0)


signal.signal(
    signal.SIGTERM,
    handle_signal,
)

signal.signal(
    signal.SIGINT,
    handle_signal,
)


# ============================================================
# START UVICORN
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    print(
        f"[RUNNER] Starting Uvicorn "
        f"on 0.0.0.0:{port}",
        flush=True,
    )

    uvicorn.run(

        app,

        host="0.0.0.0",

        port=port,
    )