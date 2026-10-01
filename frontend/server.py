"""Servidor del frontend de pruebas (solo librería estándar).

Uso local:   python3 server.py                     -> http://localhost:5500
Variables:   API_URL (por defecto http://localhost:8000), PORT (por defecto 5500; Cloud Run define PORT).
"""

import json
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"


class Handler(SimpleHTTPRequestHandler):
    """Sirve /static y genera /config.js con la URL del backend."""

    def do_GET(self):
        if self.path.split("?")[0] == "/config.js":
            body = f"window.APP_CONFIG = {json.dumps({'apiUrl': os.getenv('API_URL', 'http://localhost:8000')})};"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body.encode())
            return
        super().do_GET()


def main() -> None:
    port = int(os.getenv("PORT", "5500"))
    host = "0.0.0.0" if os.getenv("K_SERVICE") else "127.0.0.1"  # K_SERVICE existe en Cloud Run
    server = ThreadingHTTPServer((host, port), partial(Handler, directory=str(STATIC_DIR)))
    print(f"Frontend en http://localhost:{port}  ->  API {os.getenv('API_URL', 'http://localhost:8000')}")
    server.serve_forever()


if __name__ == "__main__":
    main()
