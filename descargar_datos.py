"""Descarga velas históricas públicas a la carpeta datos/ (solo lectura).

    python descargar_datos.py cripto      # BTC y ETH de data.binance.vision, desde 2024
    python descargar_datos.py mercados    # SPY, QQQ, oro, petróleo, EUR/USD, IBEX... de Yahoo
    python descargar_datos.py todo

No usa ninguna cuenta ni clave: son los mismos datos que ve cualquiera en la web.
Yahoo solo da velas de 1 hora de los últimos ~2 años; para más historia usa --intervalo 1d.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timezone

from consejo import mesas

UA = "Mozilla/5.0 (compatible; bot-trading/0.1)"
RAIZ = os.path.dirname(os.path.abspath(__file__))


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _meses(desde: date, hasta: date):
    y, m = desde.year, desde.month
    while (y, m) <= (hasta.year, hasta.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def cripto(destino: str, desde: str = "2024-01", intervalo: str = "1h") -> None:
    y0, m0 = map(int, desde.split("-"))
    hoy = date.today()
    for sim in mesas.CRIPTO:
        filas = []
        for y, m in _meses(date(y0, m0, 1), hoy):
            url = (f"https://data.binance.vision/data/spot/monthly/klines/{sim}/{intervalo}/"
                   f"{sim}-{intervalo}-{y}-{m:02d}.zip")
            try:
                z = zipfile.ZipFile(io.BytesIO(_get(url)))
            except urllib.error.HTTPError as e:
                if e.code == 404:   # el mes en curso todavía no está publicado
                    continue
                raise
            for nombre in z.namelist():
                filas += list(csv.reader(io.TextIOWrapper(z.open(nombre), encoding="utf-8")))
        filas = [f for f in filas if f and f[0].strip().isdigit()]
        filas.sort(key=lambda f: int(f[0]))
        ruta = os.path.join(destino, mesas.archivo(sim, intervalo))
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(filas)
        print(f"{sim}: {len(filas)} velas → {ruta}")


def yahoo_a_filas(js: dict) -> list[list]:
    """Convierte la respuesta de la API de gráficos de Yahoo en filas de CSV."""
    res = (js.get("chart") or {}).get("result") or []
    if not res:
        return []
    r = res[0]
    q = r["indicators"]["quote"][0]
    filas = []
    for t, o, h, l, c, v in zip(r.get("timestamp") or [], q["open"], q["high"], q["low"], q["close"],
                                q.get("volume") or [0] * len(q["open"])):
        if None in (o, h, l, c):
            continue
        filas.append([datetime.fromtimestamp(t, tz=timezone.utc).isoformat(), o, h, l, c, v or 0])
    return filas


def mercados(destino: str, intervalo: str = "1h") -> None:
    rango = "730d" if intervalo in ("1h", "60m") else "10y"
    for sim, nombre in mesas.MERCADOS.items():
        url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sim)}"
               f"?range={rango}&interval={intervalo}")
        try:
            filas = yahoo_a_filas(json.loads(_get(url)))
        except Exception as e:  # noqa: BLE001 - un símbolo caído no para el resto
            print(f"{sim} ({nombre}): no se pudo descargar ({e})")
            continue
        ruta = os.path.join(destino, mesas.archivo(sim, intervalo))
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "open", "high", "low", "close", "volume"])
            w.writerows(filas)
        print(f"{sim} ({nombre}): {len(filas)} velas → {ruta}")
        time.sleep(1)   # sin prisas, para no saturar


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mesa", choices=["cripto", "mercados", "todo"])
    ap.add_argument("--intervalo", default="1h")
    ap.add_argument("--desde", default="2024-01", help="solo cripto: primer mes (AAAA-MM)")
    a = ap.parse_args(argv)
    destino = os.path.join(RAIZ, "datos")
    os.makedirs(destino, exist_ok=True)
    if a.mesa in ("cripto", "todo"):
        cripto(destino, a.desde, a.intervalo)
    if a.mesa in ("mercados", "todo"):
        mercados(destino, a.intervalo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
