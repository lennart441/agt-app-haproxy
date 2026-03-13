"""Dummy backend for HAProxy integration tests.

Runs on all ports that HAProxy backends expect (3101-3114).  Fast for normal
requests, slow for /slow (keeps the connection open ~3 s).
"""

import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

PORTS = {
    3101: "client",
    3102: "website",
    3111: "api_sync",
    3112: "api_report",
    3113: "api_primaer",
    3114: "api_get",
}

HEALTH_PATHS = {
    "/index.html",
    "/v3/sync-api/ready",
    "/v3/report/ready",
    "/v3/pri-api/ready",
    "/v3/agt-get-api/ready",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in HEALTH_PATHS:
            self._send(200, "ready")
        elif "/slow" in self.path:
            self._slow_response()
        else:
            name = PORTS.get(self.server.server_address[1], "unknown")
            self._send(200, f'{{"backend":"{name}"}}')

    do_POST = do_GET

    def _send(self, code, body):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _slow_response(self):
        """Stream a response over ~3 seconds to keep the connection active."""
        payload = b"." * 30
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        for chunk in [payload[i:i + 1] for i in range(len(payload))]:
            self.wfile.write(chunk)
            self.wfile.flush()
            time.sleep(0.1)

    def log_message(self, *_args):
        pass


def main():
    servers = []
    for port in PORTS:
        srv = HTTPServer(("0.0.0.0", port), Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append(srv)

    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        for s in servers:
            s.shutdown()


if __name__ == "__main__":
    main()
