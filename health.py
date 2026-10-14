import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STALE_AFTER_SECONDS = 120


class Health:
    def __init__(self, stale_after: float = STALE_AFTER_SECONDS):
        self._lock = threading.RLock()
        self._stale_after = stale_after
        self._last_beat = time.monotonic()
        self._ready = False
        self._watcher_alive = lambda: True

    def track(self, observer):
        with self._lock:
            self._watcher_alive = observer.is_alive

    def beat(self):
        with self._lock:
            self._last_beat = time.monotonic()

    def mark_ready(self):
        with self._lock:
            self._ready = True

    def live(self) -> tuple[bool, str]:
        with self._lock:
            if not self._watcher_alive():
                return False, "watcher thread is not alive"
            age = time.monotonic() - self._last_beat
            if age > self._stale_after:
                return False, f"main loop last checked in {age:.0f}s ago"
            return True, "ok"

    def ready(self) -> tuple[bool, str]:
        with self._lock:
            if not self._ready:
                return False, "watcher not ready"
            return self.live()


def start_health_server(health: Health, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                ok, detail = health.live()
            elif self.path == "/readyz":
                ok, detail = health.ready()
            else:
                self.send_error(404)
                return
            body = f"{detail}\n".encode()
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="health").start()
    logging.info("Health endpoints listening on :%d (/healthz, /readyz)", port)
    return server
