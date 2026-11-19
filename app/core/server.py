"""Threaded WSGI server on the standard library (replaces waitress).

Each request runs in a worker thread from a fixed-size pool, so a slow request does not
block others and the number of threads stays bounded. Meant to sit behind nginx.
"""
from __future__ import annotations

import logging
import queue
import threading
from wsgiref.simple_server import ServerHandler, WSGIRequestHandler, WSGIServer

log = logging.getLogger("khatashop.server")


class _Handler(WSGIRequestHandler):
    server_version = "KhataShop"
    sys_version = ""

    def log_message(self, fmt, *args):  # quiet: the app logs what matters
        log.debug("%s - %s", self.address_string(), fmt % args)

    def handle(self):
        """Like WSGIRequestHandler.handle, with a size limit on the request line and headers."""
        self.raw_requestline = self.rfile.readline(65537)
        if len(self.raw_requestline) > 65536:
            self.send_error(414)
            return
        if not self.parse_request():
            return
        handler = _ServerHandler(self.rfile, self.wfile, self.get_stderr(), self.get_environ(), multithread=True)
        handler.request_handler = self
        handler.run(self.server.get_app())


class _ServerHandler(ServerHandler):
    server_software = "KhataShop"

    def log_exception(self, exc_info):
        log.error("Error while sending the response", exc_info=exc_info)


class PooledWSGIServer(WSGIServer):
    """Hands each connection to one of `threads` worker threads."""
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, address, handler, threads=8):
        super().__init__(address, handler)
        self._jobs: queue.Queue = queue.Queue(maxsize=threads * 16)
        for n in range(max(1, threads)):
            t = threading.Thread(target=self._worker, name=f"ks-worker-{n}", daemon=True)
            t.start()

    def _worker(self):
        while True:
            request, client_address = self._jobs.get()
            try:
                self.finish_request(request, client_address)
            except Exception:  # noqa: BLE001
                self.handle_error(request, client_address)
            finally:
                self.shutdown_request(request)

    def process_request(self, request, client_address):
        try:
            self._jobs.put_nowait((request, client_address))
        except queue.Full:
            log.warning("Server busy: refusing a connection from %s", client_address[0])
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)

    def handle_error(self, request, client_address):
        log.exception("Connection error from %s", client_address[0] if client_address else "?")


def make_server(app, host="0.0.0.0", port=5000, threads=8) -> PooledWSGIServer:
    server = PooledWSGIServer((host, port), _Handler, threads=threads)
    server.set_app(app)
    return server


def serve(app, host="0.0.0.0", port=5000, threads=8, **_ignored):
    server = make_server(app, host, port, threads)
    log.info("Serving on http://%s:%s with %s threads", host, port, threads)
    try:
        server.serve_forever()
    finally:
        server.server_close()
