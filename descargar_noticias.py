"""Descarga noticias históricas públicas de GDELT a datos/noticias/ (solo lectura).

    python descargar_noticias.py cripto       # BTC y ETH
    python descargar_noticias.py mercados     # S&P 500, Nasdaq, oro, petróleo, IBEX...
    python descargar_noticias.py todo
    python descargar_noticias.py cripto --desde 2024-01-01 --dias 7

GDELT (gdeltproject.org) es un archivo abierto de noticias de todo el mundo, sin
cuenta ni clave. Por cada símbolo guarda:
- los titulares (fecha de publicación, título, medio), en ventanas de --dias días;
- el tono medio y el número de artículos de cada día.

El bot de News solo usa titulares publicados ANTES de cada decisión.
GDELT pide una consulta cada 5 segundos, así que tarda: unos 15-20 minutos por
símbolo desde 2024. Si se corta, vuelve a lanzarlo y sigue donde lo dejó.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from consejo import mesas

API = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = "Mozilla/5.0 (compatible; bot-trading/0.1)"
RAIZ = os.path.dirname(os.path.abspath(__file__))
PAUSA = 5.5


def _fmt(d: datetime) -> str:
    return d.strftime("%Y%m%d%H%M%S")


def _consulta(simbolo: str) -> str:
    idioma = "spanish" if simbolo == "^IBEX" else "english"
    return f"{mesas.NOTICIAS[simbolo]} sourcelang:{idioma}"


def _pedir(params: dict, reintentos: int = 3) -> dict | None:
    url = API + "?" + urllib.parse.urlencode(params)
    for k in range(reintentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                texto = r.read().decode("utf-8", "replace")
            if not texto.strip():
                return {}
            return json.loads(texto)
        except json.JSONDecodeError:
            print(f"   GDELT respondió: {texto[:120].strip()!r}")
        except Exception as e:  # noqa: BLE001 - red caída, se reintenta
            print(f"   error de red ({e}), reintento {k + 1}/{reintentos}")
        time.sleep(PAUSA * (k + 2))
    return None


def titulares_a_filas(js: dict) -> list[dict]:
    """Convierte la respuesta 'artlist' de GDELT en {t, titulo, dominio, url}."""
    filas = []
    for a in (js or {}).get("articles", []) or []:
        sd, titulo = a.get("seendate"), (a.get("title") or "").strip()
        if not sd or not titulo:
            continue
        t = datetime.strptime(sd[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        filas.append({"t": t.isoformat(), "titulo": titulo, "dominio": a.get("domain", ""),
                      "url": a.get("url", "")})
    return filas


def serie_diaria(js: dict) -> dict[str, float]:
    """Convierte un 'timeline' de GDELT en {AAAA-MM-DD: valor} (media si viene por horas)."""
    tl = (js or {}).get("timeline") or []
    if not tl:
        return {}
    suma: dict[str, list[float]] = {}
    for p in tl[0].get("data", []):
        d = p.get("date", "")
        if len(d) >= 8:
            suma.setdefault(f"{d[:4]}-{d[4:6]}-{d[6:8]}", []).append(float(p.get("value") or 0))
    return {k: sum(v) / len(v) for k, v in suma.items()}


def _juntar(tramos: list[list[str]]) -> list[list[str]]:
    out: list[list[str]] = []
    for a, b in sorted(tramos):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def bajar(simbolo: str, destino: str, desde: date, dias: int) -> None:
    ruta = os.path.join(destino, mesas.archivo_noticias(simbolo))
    d = {"simbolo": simbolo, "consulta": _consulta(simbolo), "fuente": "GDELT DOC 2.0",
         "titulares": [], "dias": {}, "cubre": []}
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            d.update(json.load(f))
    vistos = {(x["t"], x["titulo"]) for x in d["titulares"]}
    hechos = {tuple(c) for c in d["cubre"]}

    def guardar():
        d["titulares"].sort(key=lambda x: x["t"])
        d["cubre"] = _juntar([list(c) for c in hechos])
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)

    ahora = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    ini = datetime(desde.year, desde.month, desde.day, tzinfo=timezone.utc)
    paso = timedelta(days=dias)

    # 1) tono y volumen diarios, por tramos de 6 meses
    t = ini
    while t < ahora:
        fin = min(t + timedelta(days=180), ahora)
        base = {"query": d["consulta"], "format": "json", "startdatetime": _fmt(t), "enddatetime": _fmt(fin)}
        tono = _pedir({**base, "mode": "timelinetone"})
        time.sleep(PAUSA)
        vol = _pedir({**base, "mode": "timelinevolraw"})
        time.sleep(PAUSA)
        for k, v in serie_diaria(tono).items():
            d["dias"].setdefault(k, {})["tono"] = round(v, 3)
        for k, v in serie_diaria(vol).items():
            d["dias"].setdefault(k, {})["volumen"] = round(v, 1)
        t = fin
    guardar()

    # 2) titulares, ventana a ventana (se salta las ya bajadas)
    t, n_nuevos = ini, 0
    while t < ahora:
        fin = min(t + paso, ahora)
        clave = (t.isoformat(), fin.isoformat())
        if not any(a <= clave[0] and clave[1] <= b for a, b in hechos):
            js = _pedir({"query": d["consulta"], "mode": "artlist", "format": "json", "maxrecords": 250,
                         "sort": "hybridrel", "startdatetime": _fmt(t), "enddatetime": _fmt(fin)})
            filas = titulares_a_filas(js) if js is not None else []
            # una ventana vacía no cuenta como cubierta: así News no confunde
            # "GDELT no tiene datos de esa fecha" con "no hubo noticias"
            if filas:
                for x in filas:
                    if (x["t"], x["titulo"]) not in vistos:
                        vistos.add((x["t"], x["titulo"]))
                        d["titulares"].append(x)
                        n_nuevos += 1
                hechos.add(clave)
                guardar()
            print(f"   {simbolo} {t:%Y-%m-%d}: {len(d['titulares'])} titulares en total")
            time.sleep(PAUSA)
        t = fin
    guardar()
    print(f"{simbolo}: {len(d['titulares'])} titulares ({n_nuevos} nuevos), {len(d['dias'])} días con tono → {ruta}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mesa", choices=["cripto", "mercados", "todo"])
    ap.add_argument("--desde", default="2024-01-01", help="primer día (AAAA-MM-DD)")
    ap.add_argument("--dias", type=int, default=7, help="días por consulta de titulares (máx. 250 por consulta)")
    ap.add_argument("--simbolo", action="append", default=[], help="solo estos símbolos")
    a = ap.parse_args(argv)
    destino = os.path.join(RAIZ, "datos", "noticias")
    os.makedirs(destino, exist_ok=True)
    simbolos = a.simbolo or ((list(mesas.CRIPTO) if a.mesa in ("cripto", "todo") else [])
                             + (list(mesas.MERCADOS) if a.mesa in ("mercados", "todo") else []))
    for s in simbolos:
        if s not in mesas.NOTICIAS:
            print(f"{s}: no tiene búsqueda de noticias en consejo/mesas.py, lo salto")
            continue
        print(f"{s}: bajando noticias desde {a.desde}…")
        bajar(s, destino, date.fromisoformat(a.desde), a.dias)
    return 0


if __name__ == "__main__":
    sys.exit(main())
