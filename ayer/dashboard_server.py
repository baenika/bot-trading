"""Panel web del consejo.

  python dashboard_server.py              → abre http://localhost:8050
  python dashboard_server.py --lan        → visible también desde el móvil en tu misma wifi

Muestra los datos EN VIVO (dashboard.json, lo escribe live.py) si existen;
si no, los del último backtest (results/dashboard.json).
Para forzar uno: http://localhost:8050/?fuente=backtest  o  ?fuente=live
Mesa Memecoins: http://localhost:8050/#memes  (o el botón 🪙 Memes del panel)
Solo sirve el panel y sus datos: nunca config.yaml ni tus tokens.
"""
import argparse
import os
import socket
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(ROOT, "dashboard", "index.html")
LIVE = os.path.join(ROOT, "dashboard.json")
BACKTEST = os.path.join(ROOT, "results", "dashboard.json")
MEMES_LIVE = os.path.join(ROOT, "dashboard_memes.json")
MEMES_BACKTEST = os.path.join(ROOT, "results", "memes_dashboard.json")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(PAGE, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/data":
            q = parse_qs(u.query)
            want, mesa = q.get("fuente", [""])[0], q.get("mesa", ["cripto"])[0]
            if mesa == "memes":
                order = {"live": [MEMES_LIVE], "backtest": [MEMES_BACKTEST]}.get(want, [MEMES_LIVE, MEMES_BACKTEST])
            else:
                order = {"live": [LIVE], "backtest": [BACKTEST]}.get(want, [LIVE, BACKTEST])
            for p in order:
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        return self._send(200, f.read(), "application/json")
            return self._send(404, b'{"error":"sin datos"}', "application/json")
        self._send(404, b"no encontrado", "text/plain")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--lan", action="store_true", help="accesible desde otros dispositivos de tu red")
    a = ap.parse_args()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    srv = ThreadingHTTPServer((host, a.port), Handler)
    url = f"http://localhost:{a.port}"
    print(f"Panel en {url}")
    if a.lan:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            print(f"Desde el móvil (misma wifi): http://{s.getsockname()[0]}:{a.port}")
            s.close()
        except Exception:
            pass
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("Ctrl+C para cerrar.")
    srv.serve_forever()


if __name__ == "__main__":
    main()
