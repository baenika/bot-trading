"""Sirve el panel del Consejo en http://localhost:8000 (solo en tu ordenador).

    py dashboard_server.py            # solo este ordenador
    py dashboard_server.py --red      # también el móvil en el mismo wifi (http://IP-de-la-torre:8000)

El panel solo LEE resultados en papel: no tiene botones que operen ni muestra claves.

El panel pide `data?mesa=cripto`, `data?mesa=mercados` o `data?mesa=memes` y este servidor le
devuelve `salida/estado_<mesa>.json`. Con `&fuente=papel` (abre http://localhost:8000/?fuente=papel)
devuelve el modo papel en vivo, `salida/estado_papel_<mesa>.json`. Si no existe, el panel enseña sus
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
            papel = q.get("fuente", [""])[0] == "papel"
            ruta = os.path.join(RAIZ, "salida", f"estado_{'papel_' if papel else ''}{mesa}.json")
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


def ip_local() -> str:
    """IP de este ordenador en la red de casa (no envía nada: solo pregunta al sistema la ruta)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    red = "--red" in args
    nums = [x for x in args if x.isdigit()]
    puerto = int(nums[0]) if nums else 8000
    srv = http.server.ThreadingHTTPServer(("0.0.0.0" if red else "127.0.0.1", puerto), Manejador)
    print(f"Panel en http://localhost:{puerto}" + (f" y en el móvil (mismo wifi) http://{ip_local()}:{puerto}"
                                                   if red else "") + "  (Ctrl+C para parar)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
