"""WORKER_MODE=external: the web process only enqueues; a separate `backend/worker.py` process does the work.
Run: python tests/worker_process.py"""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
env = {**os.environ, "LEARNHUB_DB": os.path.join(tempfile.mkdtemp(), "w.db"), "WORKER_MODE": "external"}
for k in ("ANTHROPIC_API_KEY", "DATABASE_URL", "SENTRY_DSN"):
    env.pop(k, None)
os.environ.update(env)
sys.path.insert(0, os.path.join(ROOT, "backend"))

import fitz  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

doc = fitz.open()
doc.new_page().insert_textbox(fitz.Rect(40, 40, 555, 800), "Breadth-first search explores a graph level by level using a queue. " * 12, fontsize=10)
c = TestClient(app)
tok = c.post("/api/auth/register", json={"email": "w@x.com", "password": "password123"}).json()["token"]
h = {"Authorization": "Bearer " + tok}
sid = c.post("/api/subjects", headers=h, json={"name": "T"}).json()["id"]
c.post(f"/api/subjects/{sid}/documents", headers=h, files={"file": ("a.pdf", doc.tobytes(), "application/pdf")})


def status():
    return c.get(f"/api/subjects/{sid}", headers=h).json()["documents"][0]["status"]


time.sleep(3)
assert status() == "processing" and c.get("/api/health").json()["queue"] == {"queued": 1}, "web process must not do the work itself"

worker = subprocess.Popen([sys.executable, os.path.join(ROOT, "backend", "worker.py")], env=env, cwd=ROOT)
try:
    for _ in range(300):
        if status() != "processing":
            break
        time.sleep(0.5)
    assert status() == "ready", status()
    assert c.get("/api/health").json()["queue"] == {"done": 1}
    print("external worker OK")
finally:
    worker.terminate()
    worker.wait(timeout=30)
