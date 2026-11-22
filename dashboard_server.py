"""Sirve el panel del Consejo en http://localhost:8000 (solo en tu ordenador).

    py dashboard_server.py            # solo este ordenador
    py dashboard_server.py --red      # también el móvil en el mismo wifi (http://IP-de-la-torre:8000)

El panel solo LEE resultados en papel: no tiene botones que operen ni muestra claves.

El panel pide `data?mesa=cripto`, `data?mesa=mercados` o `data?mesa=memes` y este servidor le
devuelve `salida/estado_<mesa>.json`. Con `&fuente=papel` (abre http://localhost:8000/?fuente=papel)
devuelve el modo papel en vivo, `salida/estado_papel_<mesa>.json`. Sin `fuente` también da el papel en
vivo si existe; `&fuente=backtest` fuerza el backtest. El chip «En vivo / Backtest» del panel cambia entre los dos. Si no existe, el panel enseña sus
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
            if mesa not in ("cripto", "mercados", "memes", "lab"):
                return self.send_error(400, "mesa desconocida")
            if mesa == "lab":                 # 🧪 laboratorio: solo existe en papel
                mesa, q["fuente"] = "cripto_lab", ["papel"]
            fuente = q.get("fuente", [""])[0]
            ruta_papel = os.path.join(RAIZ, "salida", f"estado_papel_{mesa}.json")
            # por defecto, el papel EN VIVO si existe (precios reales); ?fuente=backtest para el histórico
            papel = fuente == "papel" or (fuente != "backtest" and os.path.exists(ruta_papel))
            ruta = ruta_papel if papel else os.path.join(RAIZ, "salida", f"estado_{mesa}.json")
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
        if url.path.rstrip("/") == "/fabrica" or url.path.startswith("/fabrica/"):
            return self._fabrica(url.path)
        return super().do_GET()

    def _fabrica(self, ruta: str):
        """Informes de la fábrica de estrategias (salida/fabrica_<activo>.html)."""
        carpeta = os.path.join(RAIZ, "salida")
        nombre = ruta.rstrip("/").rsplit("/", 1)[-1]
        if nombre != "fabrica":
            archivo = os.path.join(carpeta, f"fabrica_{nombre}.html")
            if not nombre.isalnum() or not os.path.exists(archivo):
                return self.send_error(404, "Sin informe: corre antes py fabrica.py <activo>")
            with open(archivo, "rb") as f:
                cuerpo = f.read()
        else:
            hay = sorted(x[8:-5] for x in os.listdir(carpeta) if x.startswith("fabrica_") and x.endswith(".html")) \
                if os.path.isdir(carpeta) else []
            enlaces = "".join(f"<li><a href='/fabrica/{h}'>{h}</a></li>" for h in hay) or \
                "<li>Todavía no hay informes: corre <code>py fabrica.py oro</code></li>"
            cuerpo = ("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
                      "<title>Fábrica de estrategias</title><body style='font:16px system-ui;margin:24px;"
                      "background:#f6f7f9;color:#15181d'><h1>🏭 Fábrica de estrategias</h1><ul>"
                      f"{enlaces}</ul><p><a href='/'>← Volver al panel</a></p></body>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)


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
