"""Sirve el panel del Consejo en http://localhost:8000 (solo en tu ordenador).

El panel pide `data?mesa=cripto`, `data?mesa=mercados` o `data?mesa=memes` y este servidor le
devuelve `salida/estado_<mesa>.json`. Si no existe, el panel enseña sus
datos de demostración.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import urllib.parse

RAIZ = os.path.dirname(os.path.abspath(__file__))


class Manejador(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=os.path.join(RAIZ, "panel"), **kw)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path.rstrip("/").endswith("/data") or url.path == "/data":
            q = urllib.parse.parse_qs(url.query)
            mesa = q.get("mesa", ["cripto"])[0]
            if mesa not in ("cripto", "mercados", "memes"):
                return self.send_error(400, "mesa desconocida")
            ruta = os.path.join(RAIZ, "salida", f"estado_{mesa}.json")
            if not os.path.exists(ruta):
                return self.send_error(404, "Sin datos: corre antes python run_backtest.py")
            with open(ruta, "rb") as f:
                cuerpo = f.read()
            json.loads(cuerpo)  # que sea JSON válido
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)
            return
        return super().do_GET()


def main():
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", puerto), Manejador)
    print(f"Panel en http://localhost:{puerto}  (Ctrl+C para parar)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
