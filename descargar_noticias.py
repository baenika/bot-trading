"""Descarga noticias históricas públicas de GDELT a datos/noticias/ (solo lectura).

    python descargar_noticias.py cripto       # BTC y ETH
    python descargar_noticias.py mercados     # S&P 500, Nasdaq, oro, petróleo, IBEX...
    python descargar_noticias.py todo
    python descargar_noticias.py cripto --desde 2024-01-01 --dias 7
    python descargar_noticias.py cripto --fuente hn     # Hacker News, si GDELT da 429

GDELT (gdeltproject.org) es un archivo abierto de noticias de todo el mundo, sin
cuenta ni clave. Por cada símbolo guarda:
- los titulares (fecha de publicación, título, medio), en ventanas de --dias días;
- el tono medio y el número de artículos de cada día.

El bot de News solo usa titulares publicados ANTES de cada decisión.
GDELT corta si se le pide más de una consulta cada ~10 segundos, así que tarda:
unos 30 minutos por símbolo desde 2024. Si se corta, vuelve a lanzarlo y sigue donde lo dejó.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from consejo import mesas

API = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = "Mozilla/5.0 (compatible; bot-trading/0.1)"
RAIZ = os.path.dirname(os.path.abspath(__file__))
PAUSA = 10.0          # GDELT corta con 429 si se le pide más rápido


def _fmt(d: datetime) -> str:
    return d.strftime("%Y%m%d%H%M%S")


def _consulta(simbolo: str) -> str:
    idioma = "spanish" if simbolo == "^IBEX" else "english"
    return f"{mesas.NOTICIAS[simbolo]} sourcelang:{idioma}"


def _pedir(params: dict, reintentos: int = 5) -> dict | None:
    url = API + "?" + urllib.parse.urlencode(params)
    texto = ""
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
            if "limit requests" not in texto.lower():
                return None             # consulta mal hecha: reintentar no sirve
        except urllib.error.HTTPError as e:
            if e.code != 429:
                print(f"   error HTTP {e.code}, reintento {k + 1}/{reintentos}")
            else:
                espera = 30 * 2 ** k   # 30, 60, 120, 240, 480 s
                print(f"   GDELT pide calma (429): espero {espera} s, reintento {k + 1}/{reintentos}")
                time.sleep(espera)
                continue
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
    t, n_nuevos, vacias = ini, 0, []
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
            else:
                vacias.append(f"{t:%Y-%m-%d}")
            print(f"   {simbolo} {t:%Y-%m-%d}: {len(filas)} titulares en esta ventana "
                  f"({len(d['titulares'])} en total)")
            time.sleep(PAUSA)
        t = fin
    guardar()
    print(f"{simbolo}: {len(d['titulares'])} titulares ({n_nuevos} nuevos), {len(d['dias'])} días con tono → {ruta}")
    if vacias:
        print(f"⚠️  {simbolo}: {len(vacias)} ventanas sin titulares ({', '.join(vacias[:8])}"
              f"{'…' if len(vacias) > 8 else ''}). News no opinará en esas fechas. "
              "Vuelve a lanzar el comando para reintentarlas.")


# --- segunda fuente: Hacker News (API pública de Algolia, sin clave) -----------------------
# Tiene histórico completo con fecha y hora exactas y límites generosos. Cubre bien
# cripto, tecnología y macro; casi nada de oro, petróleo o la bolsa española.

HN_API = "https://hn.algolia.com/api/v1/search_by_date"
HN_CONSULTAS = {
    "BTCUSDT": ["bitcoin", "btc"], "ETHUSDT": ["ethereum"],
    "SPY": ["S&P 500", "stock market"], "QQQ": ["nasdaq", "tech stocks"], "DIA": ["dow jones"],
    "IWM": ["russell 2000"], "GLD": ["gold price"], "GC=F": ["gold price", "gold futures"], "SLV": ["silver price"], "USO": ["oil price"],
    "TLT": ["treasury yields", "federal reserve"],
    "EURUSD=X": ["ecb", "eurozone", "euro exchange rate", "lagarde"],
}


def _en_titulo(titulo: str, terminos: list[str] | None) -> bool:
    """El término tiene que estar en el titular como palabra o frase entera (Algolia busca de más)."""
    if not terminos:
        return True
    t = " " + re.sub(r"[^a-z0-9&$ ]", " ", titulo.lower()) + " "
    return any(f" {x.lower()} " in t for x in terminos)


def hn_a_filas(js: dict, terminos: list[str] | None = None, min_puntos: int = 0) -> list[dict]:
    filas = []
    for h in (js or {}).get("hits", []) or []:
        titulo, t = (h.get("title") or "").strip(), h.get("created_at_i")
        if (h.get("points") or 0) < min_puntos or not _en_titulo(titulo, terminos):
            continue
        if titulo and t:
            filas.append({"t": datetime.fromtimestamp(int(t), tz=timezone.utc).isoformat(), "titulo": titulo,
                          "dominio": urllib.parse.urlparse(h.get("url") or "").netloc or "news.ycombinator.com",
                          "url": h.get("url") or "", "fuente": "hn"})
    return filas


def bajar_hn(simbolo: str, destino: str, desde: date, dias: int = 30) -> None:
    ruta = os.path.join(destino, mesas.archivo_noticias(simbolo))
    d = {"simbolo": simbolo, "titulares": [], "dias": {}, "cubre": []}
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            d.update(json.load(f))
    d["fuente"] = ", ".join(sorted(set(filter(None, [d.get("fuente"), "Hacker News"]))))
    # se rehace lo de Hacker News desde cero (por si había titulares de una búsqueda anterior)
    d["titulares"] = [x for x in d["titulares"] if x.get("fuente") != "hn"]
    vistos = {(x["t"], x["titulo"]) for x in d["titulares"]}
    hechos = [list(c) for c in d["cubre"]]
    ahora = datetime.now(timezone.utc)
    t = datetime(desde.year, desde.month, desde.day, tzinfo=timezone.utc)
    while t < ahora:
        fin = min(t + timedelta(days=dias), ahora)
        n = 0
        for q in HN_CONSULTAS[simbolo]:
            for pagina in range(10):
                url = HN_API + "?" + urllib.parse.urlencode({
                    "query": f'"{q}"', "tags": "story", "hitsPerPage": 1000, "page": pagina,
                    "typoTolerance": "false",
                    "numericFilters": f"created_at_i>={int(t.timestamp())},created_at_i<{int(fin.timestamp())}"})
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        js = json.loads(r.read())
                except Exception as e:  # noqa: BLE001
                    print(f"   HN {simbolo} {t:%Y-%m}: {e}")
                    break
                for x in hn_a_filas(js, [q], min_puntos=3):
                    if (x["t"], x["titulo"]) not in vistos:
                        vistos.add((x["t"], x["titulo"]))
                        d["titulares"].append(x)
                        n += 1
                time.sleep(1)
                if pagina + 1 >= js.get("nbPages", 0):
                    break
        if n:
            hechos.append([t.isoformat(), fin.isoformat()])
        print(f"   {simbolo} {t:%Y-%m-%d}: {n} titulares nuevos de Hacker News")
        t = fin
    d["titulares"].sort(key=lambda x: x["t"])
    d["cubre"] = _juntar(hechos)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    print(f"{simbolo}: {len(d['titulares'])} titulares en total → {ruta}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mesa", choices=["cripto", "mercados", "todo"])
    ap.add_argument("--desde", default="2024-01-01", help="primer día (AAAA-MM-DD)")
    ap.add_argument("--dias", type=int, default=7, help="días por consulta de titulares (máx. 250 por consulta)")
    ap.add_argument("--simbolo", action="append", default=[], help="solo estos símbolos")
    ap.add_argument("--pausa", type=float, default=10.0, help="segundos entre consultas a GDELT")
    ap.add_argument("--fuente", choices=["gdelt", "hn"], default="gdelt",
                    help="gdelt (por defecto) o hn (Hacker News: histórico completo, sin límites estrictos)")
    a = ap.parse_args(argv)
    global PAUSA
    PAUSA = a.pausa
    destino = os.path.join(RAIZ, "datos", "noticias")
    os.makedirs(destino, exist_ok=True)
    simbolos = a.simbolo or ((list(mesas.CRIPTO) if a.mesa in ("cripto", "todo") else [])
                             + (list(mesas.MERCADOS) if a.mesa in ("mercados", "todo") else []))
    for s in simbolos:
        if a.fuente == "hn":
            if s not in HN_CONSULTAS:
                print(f"{s}: Hacker News no cubre este activo, lo salto")
                continue
            print(f"{s}: bajando titulares de Hacker News desde {a.desde}…")
            bajar_hn(s, destino, date.fromisoformat(a.desde), 30)
            continue
        if s not in mesas.NOTICIAS:
            print(f"{s}: no tiene búsqueda de noticias en consejo/mesas.py, lo salto")
            continue
        print(f"{s}: bajando noticias desde {a.desde}…")
        bajar(s, destino, date.fromisoformat(a.desde), a.dias)
    return 0


if __name__ == "__main__":
    sys.exit(main())
