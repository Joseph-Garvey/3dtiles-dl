"""Local HTTP server backing the map picker GUI."""

import http.server
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent.parent
HTML = Path(__file__).parent / "map_picker.html"
PYTHON = sys.executable

# job_id -> {"status": "running"|"done"|"error", "q": Queue}
_jobs: dict = {}
_jobs_lock = threading.Lock()


_OUTPUT_EXTENSIONS = {".obj", ".fbx", ".dae", ".mtl"}


def _clean_previous(tiles_dir: str) -> list[str]:
    """Remove tiles dir, textures dir, and output files from a previous capture.
    Returns log lines describing what was cleaned."""
    logs: list[str] = []
    root = ROOT

    # Remove tiles directory
    tiles_path = root / tiles_dir
    if tiles_path.exists():
        shutil.rmtree(tiles_path)
        logs.append(f"Cleaned tiles directory: {tiles_dir}/")

    # Remove textures directory
    textures_path = root / "textures"
    if textures_path.exists():
        shutil.rmtree(textures_path)
        logs.append("Cleaned textures directory: textures/")

    # Remove output files (obj, fbx, dae, mtl, atlas texture)
    for f in root.iterdir():
        if f.is_file() and f.suffix.lower() in _OUTPUT_EXTENSIONS:
            f.unlink()
            logs.append(f"Removed output file: {f.name}")
    atlas = root / "atlas_texture.jpg"
    if atlas.exists():
        atlas.unlink()
        logs.append("Removed atlas_texture.jpg")

    return logs


def _run_job(job_id: str, cmd: list[str]):
    job = _jobs[job_id]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=str(ROOT),
        )
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if line:
                job["q"].put(("log", line))
        proc.wait()
        status = "done" if proc.returncode == 0 else "error"
        job["q"].put(("status", status))
        job["status"] = status
    except Exception as exc:
        job["q"].put(("log", f"ERROR: {exc}"))
        job["q"].put(("status", "error"))
        job["status"] = "error"


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # silence access log

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_file(HTML, "text/html; charset=utf-8")
        elif self.path.startswith("/api/stream/"):
            self._sse(self.path.split("/")[-1])
        else:
            self.send_error(404)

    def _serve_file(self, path: Path, ct: str):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, job_id: str):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if not job:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                try:
                    kind, data = job["q"].get(timeout=30)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                msg = json.dumps({"type": kind, "data": data})
                self.wfile.write(f"data: {msg}\n\n".encode())
                self.wfile.flush()
                if kind == "status":
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/api/download":
            self._start_download(body)
        elif self.path == "/api/convert":
            self._start_convert(body)
        else:
            self.send_error(404)

    def _start_download(self, body: dict):
        coords = body.get("coords", [])
        out_dir = body.get("out_dir", "tiles")
        if len(coords) != 8:
            self._json(400, {"error": "need 8 coordinate values"})
            return

        # Clean previous capture artifacts before starting a new download
        cleaned = _clean_previous(out_dir)

        cmd = [PYTHON, "-m", "scripts.download_tiles", "-c", *[str(c) for c in coords], "-o", out_dir]
        job_id = self._launch(cmd, preamble=cleaned)
        self._json(200, {"job_id": job_id})

    def _start_convert(self, body: dict):
        fmt = body.get("format", "obj").lower()
        in_dir = body.get("in_dir", "tiles")
        out_file = body.get("out_file", f"output.{fmt}")
        merge = body.get("merge", True)
        blender = body.get("blender") or None

        module = {
            "obj": "scripts.convert_to_obj",
            "dae": "scripts.convert_to_dae",
            "fbx": "scripts.convert_to_fbx",
        }.get(fmt)
        if not module:
            self._json(400, {"error": f"unknown format: {fmt}"})
            return

        cmd = [PYTHON, "-m", module, "-i", in_dir, "-o", out_file]
        if not merge:
            cmd.append("--no-merge")
        if blender:
            cmd += ["--blender", blender]

        # FBX extras
        if fmt == "fbx":
            if body.get("atlas"):
                cmd.append("--atlas")
            if body.get("jpeg"):
                cmd.append("--jpeg")
            if not body.get("embed_textures", True):
                cmd.append("--no-embed-textures")

        job_id = self._launch(cmd)
        self._json(200, {"job_id": job_id})

    def _launch(self, cmd: list[str], preamble: list[str] | None = None) -> str:
        job_id = str(uuid.uuid4())
        job = {"status": "running", "q": queue.Queue()}
        if preamble:
            for line in preamble:
                job["q"].put(("log", line))
        with _jobs_lock:
            _jobs[job_id] = job
        threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
        return job_id

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = 7473
    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"GUI running at {url}  (Ctrl+C to stop)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
