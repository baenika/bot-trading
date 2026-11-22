"""Fábrica de estrategias: genera miles de combinaciones sencillas y se queda con las robustas.

    py fabrica.py descargar                 # velas de 1 h reales: oro, Nasdaq 100 y S&P 500 (Dukascopy)
    py fabrica.py oro --n 12000             # la fábrica entera en el oro (también: nasdaq, sp500, btc, eth)
    py fabrica.py todo --n 3000 --telegram  # todos los activos y resumen al móvil

Salida: salida/fabrica_<activo>.json y salida/fabrica_<activo>.html (el informe; también en el panel,
en http://localhost:8000/fabrica). Es todo backtest con velas reales: ni broker, ni claves, ni órdenes.
Las reglas de construcción y las pruebas están explicadas en consejo/fabrica.py.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import lzma
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from consejo import datos, fabrica, mesas

RAIZ = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(RAIZ, "datos")
SALIDA = os.path.join(RAIZ, "salida")

# alias -> (símbolo de la fábrica, símbolo en Dukascopy o None si viene de Binance, rango de precios esperado)
ACTIVOS = {
    "oro": ("XAUUSD", "XAUUSD", (250, 10000)),
    "nasdaq": ("NAS100", "USATECHIDXUSD", (1000, 60000)),
    "sp500": ("SPX500", "USA500IDXUSD", (500, 15000)),
    "btc": ("BTCUSDT", None, None),
    "eth": ("ETHUSDT", None, None),
}
DESDE = date(2018, 1, 1)
UA = "Mozilla/5.0 (bot-trading; backtest)"


PAUSA = 3.0          # segundos entre peticiones: Dukascopy corta con 503 si se le pide muy seguido
_ultima = [0.0]


def _get(url: str) -> bytes | None:
    """Baja una URL con calma. Con 429/503 espera mucho más (1, 2, 4, 8 y 16 min) antes de rendirse."""
    for k in range(6):
        espera = _ultima[0] + PAUSA - time.time()
        if espera > 0:
            time.sleep(espera)
        _ultima[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            pausa = 60 * 2 ** k if e.code in (429, 503) else 5 * 2 ** k
            print(f"  Dukascopy responde {e.code}; espero {pausa} s y reintento…")
            time.sleep(pausa)
        except Exception:  # noqa: BLE001
            time.sleep(5 * 2 ** k)
    raise RuntimeError(f"no se pudo bajar {url}")


def _get_cache(url: str, ruta: str) -> bytes | None:
    """Cada fichero mensual se guarda en datos/dukascopy/: si se corta, al relanzar sigue donde iba."""
    if os.path.exists(ruta):
        with open(ruta, "rb") as f:
            return f.read()
    raw = _get(url)
    if raw is not None:
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "wb") as f:
            f.write(raw)
    return raw


def _decodificar(raw: bytes, base: datetime, rango) -> list[list]:
    """Velas de Dukascopy (24 bytes: segundos, apertura, cierre, mínimo, máximo, volumen). La escala de
    precio cambia según el activo, así que se elige el divisor que deja el precio en el rango esperado."""
    if not raw:
        return []
    d = lzma.decompress(raw)
    filas = [struct.unpack(">5if", d[i:i + 24]) for i in range(0, len(d) - 23, 24)]
    if not filas:
        return []
    div = next((x for x in (1000, 100, 10, 1, 100000, 10000) if rango[0] <= filas[0][2] / x <= rango[1]), None)
    if div is None:
        raise ValueError(f"escala de Dukascopy desconocida (primer cierre {filas[0][2]})")
    return [[(base + timedelta(seconds=s)).isoformat(), o / div, h / div, lo / div, c / div, v]
            for s, o, c, lo, h, v in filas]


def descargar_dukascopy(sim: str, duk: str, rango, desde: date = DESDE) -> str:
    """Velas de 1 h (precio BID) mes a mes. Se guardan en datos/<SIM>-1h.csv y solo se baja lo nuevo."""
    ruta = os.path.join(DATOS, mesas.archivo(sim, "1h"))
    filas = {}
    if os.path.exists(ruta):
        for v in datos.cargar_csv(ruta):
            filas[v.t.isoformat()] = [v.t.isoformat(), v.o, v.h, v.l, v.c, v.v]
    ultimo = max(filas) if filas else None
    hoy = datetime.now(timezone.utc).date()
    y, m = desde.year, desde.month
    if ultimo:
        u = datetime.fromisoformat(ultimo)
        y, m = u.year, u.month
    while (y, m) <= (hoy.year, hoy.month):
        base = datetime(y, m, 1, tzinfo=timezone.utc)
        url = f"https://datafeed.dukascopy.com/datafeed/{duk}/{y}/{m - 1:02d}/BID_candles_hour_1.bi5"
        mes_en_curso = (y, m) == (hoy.year, hoy.month)
        raw = _get(url) if mes_en_curso else _get_cache(url, os.path.join(DATOS, "dukascopy", duk, f"{y}-{m:02d}_h1.bi5"))
        if raw is None and mes_en_curso:
            # el mes en curso no tiene fichero mensual todavía: se arma con los días ya cerrados
            raw_dias = []
            d = base.date()
            while d < hoy:
                r = _get(f"https://datafeed.dukascopy.com/datafeed/{duk}/{d.year}/{d.month - 1:02d}/"
                         f"{d.day:02d}/BID_candles_min_1.bi5")
                raw_dias += _a_horas(_decodificar(r, datetime(d.year, d.month, d.day, tzinfo=timezone.utc), rango))
                d += timedelta(days=1)
            nuevas = raw_dias
        else:
            nuevas = _decodificar(raw, base, rango)
        for f in nuevas:
            if f[5] > 0 or f[2] != f[3]:  # fuera de horas Dukascopy repite el precio sin volumen: se salta
                filas[f[0]] = f
        print(f"  {sim} {y}-{m:02d}: {len(nuevas)} velas")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    _guardar(ruta, filas)
    print(f"{sim}: {len(filas)} velas de 1 h → {ruta}")
    return ruta


def _guardar(ruta: str, filas: dict) -> None:
    os.makedirs(DATOS, exist_ok=True)
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        w.writerows(filas[k] for k in sorted(filas))


def _a_horas(minutos: list[list]) -> list[list]:
    horas = {}
    for t, o, h, lo, c, v in minutos:
        k = t[:13] + ":00:00+00:00"
        if k not in horas:
            horas[k] = [k, o, h, lo, c, v]
        else:
            x = horas[k]
            x[2], x[3], x[4], x[5] = max(x[2], h), min(x[3], lo), c, x[5] + v
    return list(horas.values())


# si Dukascopy no deja bajar, se usa la vela de 1 h de Yahoo que ya baja descargar_datos.py (~2 años)
RESPALDO_YAHOO = {"XAUUSD": "GC=F", "NAS100": "QQQ", "SPX500": "SPY"}


def cargar(alias: str):
    sim, duk, _ = ACTIVOS[alias]
    ruta = os.path.join(DATOS, mesas.archivo(sim, "1h"))
    respaldo = os.path.join(DATOS, mesas.archivo(RESPALDO_YAHOO.get(sim, sim), "1h"))
    if not os.path.exists(ruta) and sim in RESPALDO_YAHOO and os.path.exists(respaldo):
        print(f"Sin velas de Dukascopy para {sim}: uso las de Yahoo ({RESPALDO_YAHOO[sim]}, solo ~2 años).")
        return fabrica.Datos(RESPALDO_YAHOO[sim], datos.cargar_csv(respaldo))
    if not os.path.exists(ruta):
        if duk is None:
            raise SystemExit(f"Faltan las velas de {sim}: corre antes  py descargar_datos.py cripto")
        raise SystemExit(f"Faltan las velas de {sim}: corre antes  py fabrica.py descargar")
    return fabrica.Datos(sim, datos.cargar_csv(ruta))


# --- informe HTML ---------------------------------------------------------------------------------
def _n(x, d=1):
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _svg_puntos(nube, resaltados=(), ancho=520, alto=300, ex="caída máxima %", ey="rentabilidad anual %"):
    if not nube:
        return "<p class='nota'>Sin puntos.</p>"
    xs = [p[1] for p in nube] + [p[1] for p in resaltados]
    ys = [p[0] for p in nube] + [p[0] for p in resaltados]
    x0, x1 = 0, max(max(xs), 1)
    y0, y1 = min(min(ys), 0), max(max(ys), 1)
    m = 36

    def X(x):
        return m + (x - x0) / (x1 - x0 or 1) * (ancho - m - 8)

    def Y(y):
        return alto - m + 8 - (y - y0) / (y1 - y0 or 1) * (alto - m - 8)
    pts = "".join(f"<circle cx='{X(p[1]):.1f}' cy='{Y(p[0]):.1f}' r='2' class='p'/>" for p in nube)
    res = "".join(f"<circle cx='{X(p[1]):.1f}' cy='{Y(p[0]):.1f}' r='5' class='r'/>" for p in resaltados)
    return (f"<svg viewBox='0 0 {ancho} {alto}' class='g'><line x1='{m}' x2='{ancho}' y1='{Y(0):.1f}' "
            f"y2='{Y(0):.1f}' class='cero'/>{pts}{res}<text x='{ancho / 2}' y='{alto - 4}' class='eje'>{ex}</text>"
            f"<text x='10' y='{alto / 2}' class='eje' transform='rotate(-90 10 {alto / 2})'>{ey}</text>"
            f"<text x='{m}' y='{Y(y1) + 10:.0f}' class='eje'>{_n(y1, 0)}</text>"
            f"<text x='{m}' y='{Y(y0) - 2:.0f}' class='eje'>{_n(y0, 0)}</text>"
            f"<text x='{ancho - 30}' y='{alto - m + 22}' class='eje'>{_n(x1, 0)}</text></svg>")


def _svg_curva(curva, ancho=520, alto=160, corte=None):
    if len(curva) < 2:
        return ""
    ys = [0.0] + [c[1] for c in curva]
    y0, y1 = min(ys), max(ys)
    n = len(curva)
    pts = " ".join(f"{6 + i / (n - 1) * (ancho - 12):.1f},{alto - 6 - (c[1] - y0) / (y1 - y0 or 1) * (alto - 12):.1f}"
                   for i, c in enumerate(curva))
    k = next((i for i, c in enumerate(curva) if corte and c[0] >= corte), None)
    linea = (f"<line x1='{6 + k / (n - 1) * (ancho - 12):.1f}' x2='{6 + k / (n - 1) * (ancho - 12):.1f}' y1='0' "
             f"y2='{alto}' class='corte'/>" if k else "")
    return f"<svg viewBox='0 0 {ancho} {alto}' class='g'>{linea}<polyline points='{pts}' class='curva'/></svg>"


def informe_html(r: dict) -> str:
    e = r["embudo"]
    pasos = [("Generadas", e["generadas"]), ("Distintas", e["distintas"]), ("Ganan dentro de muestra", e["ganadoras"]),
             (f"≥{r['criterios']['min_ops']} ops, acierto ≥{_n(r['criterios']['min_acierto'], 0)} %, "
              f"beneficio/caída ≥{_n(r['criterios']['min_ret_dd'], 0)}", e["filtros_basicos"]),
             (f"Monte Carlo (≥{_n(r['criterios']['mc_pct'], 0)} % de vueltas ganan)", e["monte_carlo"]),
             ("Sin repetidas", e["sin_parecidas"]),
             (f"Vecinos ±20 % (≥{_n(r['criterios']['vecinos_pct'], 0)} % ganan)", e["vecinos"]),
             ("Ganan fuera de muestra", e["fuera_de_muestra"])]
    mx = max(e["generadas"], 1)
    embudo = "".join(f"<div class='paso'><span>{html.escape(t)}</span><div class='barra'><i style='width:"
                     f"{max(0.5, 100 * v / mx):.1f}%'></i></div><b>{_n(v, 0)}</b></div>" for t, v in pasos)
    fam = "".join(f"<tr><td>{k.replace('_', ' ')}</td><td>{v['probadas']}</td><td>{v['ganan']}</td>"
                  f"<td>{_n(100 * v['ganan'] / max(v['probadas'], 1))} %</td></tr>"
                  for k, v in sorted(r["por_familia"].items(), key=lambda x: -x[1]["ganan"] / max(x[1]["probadas"], 1)))
    tort = "".join(f"<li><b>{t['nombre']}</b>: {t['is']['trades']} ops, {_n(t['is']['ret_pct'])} %, caída "
                   f"{_n(t['is']['dd_pct'])} %, beneficio/caída {_n(t['is']['ret_dd'], 2)}</li>" for t in r["tortugas"])
    fins = []
    for k, f in enumerate(r["finalistas"], 1):
        v = f.get("vecinos", {})
        fins.append(f"""
<section class='card'><h3>#{k} · {html.escape(f['familia'].replace('_', ' '))}</h3>
<p>{html.escape(f['reglas'])}</p>
<div class='kpis'>
<div><small>Dentro de muestra</small><b>{_n(f['is']['ret_pct'])} %</b><span>caída {_n(f['is']['dd_pct'])} % · b/c {_n(f['is']['ret_dd'], 1)} · {f['is']['trades']} ops · acierto {_n(f['is']['acierto'], 0)} %</span></div>
<div><small>Fuera de muestra</small><b>{_n(f['oos']['ret_pct'])} %</b><span>caída {_n(f['oos']['dd_pct'])} % · {f['oos']['trades']} ops · PF {_n(f['oos']['pf'], 2)}</span></div>
<div><small>Monte Carlo</small><b>{_n(f['mc']['pct_ganan'], 0)} %</b><span>de vueltas ganan · caída p95 {_n(f['mc']['dd95_pct'])} %</span></div>
<div><small>Vecinos ±20 %</small><b>{_n(v.get('pct_ganan', 0), 0)} %</b><span>ganan · mediana {_n(v.get('mediana_anual', 0))} % anual</span></div>
</div>
<p class='fondeo'>🏦 {html.escape(f['fondeo']['texto'])}</p>
<div class='dos'><div><small>Curva de todas las operaciones (la raya marca el inicio de fuera de muestra)</small>{_svg_curva(f['curva'], corte=r['corte'])}</div>
<div><small>Vecinas (azul) frente a la original (naranja)</small>{_svg_puntos(v.get('puntos', []), [[f['is']['anual_pct'], f['is']['dd_pct']]], 520, 220)}</div></div>
</section>""")
    if not fins:
        fins.append("<section class='card'><p>Ninguna estrategia ha pasado todas las pruebas. Es un resultado "
                    "válido: mejor no operar que operar algo que no aguanta.</p></section>")
    nube_fin = [[f["is"]["anual_pct"], f["is"]["dd_pct"]] for f in r["finalistas"]]
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Fábrica de estrategias</title><style>
:root{{--bg:#f6f7f9;--fg:#15181d;--mut:#5d6573;--card:#fff;--lin:#e2e5ea;--azul:#2f6fdf;--nar:#e8792b;--ok:#1d9a5b}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#0f1216;--fg:#e8ebf0;--mut:#9aa3b2;--card:#171b21;--lin:#2a3039;--azul:#6b9cff;--nar:#ff9a52;--ok:#4cc38a}}}}
:root[data-theme=dark]{{--bg:#0f1216;--fg:#e8ebf0;--mut:#9aa3b2;--card:#171b21;--lin:#2a3039;--azul:#6b9cff;--nar:#ff9a52;--ok:#4cc38a}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:20px 16px 60px}}h1{{font-size:24px;margin:0 0 4px}}h2{{font-size:18px;margin:28px 0 10px}}h3{{margin:0 0 6px;font-size:16px}}
.nota,small{{color:var(--mut)}}.card{{background:var(--card);border:1px solid var(--lin);border-radius:12px;padding:16px;margin:12px 0}}
.paso{{display:grid;grid-template-columns:minmax(120px,320px) 1fr 70px;gap:10px;align-items:center;margin:4px 0}}
.barra{{background:var(--lin);border-radius:6px;height:12px}}.barra i{{display:block;height:12px;border-radius:6px;background:var(--azul)}}
.paso b{{text-align:right}}table{{border-collapse:collapse;width:100%}}td,th{{padding:4px 8px;border-bottom:1px solid var(--lin);text-align:left}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:10px 0}}.kpis div{{display:flex;flex-direction:column}}
.kpis b{{font-size:20px}}.kpis span{{color:var(--mut);font-size:13px}}.dos{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
.g{{width:100%;height:auto}}.g .p{{fill:var(--azul);opacity:.35}}.g .r{{fill:var(--nar)}}.g .cero{{stroke:var(--mut);stroke-dasharray:3 3}}
.g .eje{{fill:var(--mut);font-size:11px}}.g .curva{{fill:none;stroke:var(--ok);stroke-width:1.6}}.g .corte{{stroke:var(--nar);stroke-dasharray:4 3}}
.fondeo{{background:var(--bg);border-radius:8px;padding:8px 10px}}
@media (max-width:560px){{.paso{{grid-template-columns:1fr 60px}}.paso .barra{{grid-column:1/3;grid-row:2}}}}
</style></head><body><main>
<h1>Fábrica de estrategias · {html.escape(r['nombre'])}</h1>
<p class='nota'>Velas de 1 h reales del {r['desde']} al {r['hasta']} ({_n(r['velas'], 0)} velas). Se eligen con los datos hasta el {r['corte']} y se comprueban después (fuera de muestra).
Cuenta simulada de 100.000 $, riesgo fijo del 1 % por operación, costes del activo: spread {r['perfil']['spread']}, comisión {r['perfil']['comision_pct']} % ida y vuelta, swap {r['perfil']['swap_largo_pct']} % por noche en largos.
Comprar y mantener en el mismo tramo: {_n(r['comprar_y_mantener_is_pct'])} % (con el 100 % del dinero; las estrategias arriesgan solo el 1 %). Solo backtest: nada de dinero real.</p>
<h2>Embudo</h2><section class='card'>{embudo}</section>
<h2>Las {len(r['finalistas'])} finalistas</h2>{''.join(fins)}
<h2>Todas las estrategias (cada punto es una)</h2><section class='card'>{_svg_puntos(r['nube'], nube_fin)}<p class='nota'>Naranja: finalistas. Arriba a la izquierda es mejor (más rentabilidad, menos caída).</p></section>
<h2>Por familia</h2><section class='card'><table><tr><th>Familia</th><th>Probadas</th><th>Ganan</th><th>%</th></tr>{fam}</table></section>
<h2>Las tortugas de Richard Dennis</h2><section class='card'><ul>{tort}</ul><p class='nota'>Entran en la fábrica como dos candidatas más y pasan por las mismas pruebas.</p></section>
<p class='nota'>Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} con fabrica.py. Pruebas: fuera de muestra, Monte Carlo con reposición, vecinos ±20 % y examen de cuenta fondeada.</p>
</main></body></html>"""


def resumen_texto(r: dict) -> str:
    e = r["embudo"]
    lin = [f"🏭 Fábrica · {r['nombre']}: {e['generadas']} estrategias → {e['fuera_de_muestra']} finalistas "
           f"(Monte Carlo {e['monte_carlo']}, vecinos {e['vecinos']})."]
    for k, f in enumerate(r["finalistas"][:3], 1):
        lin.append(f"#{k} {f['reglas']}\n   Dentro {_n(f['is']['ret_pct'])} % (caída {_n(f['is']['dd_pct'])} %) · "
                   f"fuera {_n(f['oos']['ret_pct'])} % · {f['fondeo']['texto']}")
    if not r["finalistas"]:
        lin.append("Ninguna aguanta todas las pruebas: mejor no operar este activo así.")
    lin.append("Solo backtest y papel, sin dinero real.")
    return "\n".join(lin)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("que", help="descargar, todo o un activo: " + ", ".join(ACTIVOS))
    ap.add_argument("--n", type=int, default=3000, help="estrategias a generar (el vídeo usa 12000)")
    ap.add_argument("--corte", help="fecha AAAA-MM-DD desde la que empieza fuera de muestra (defecto: último 25 %%)")
    ap.add_argument("--semilla", type=int, default=1)
    ap.add_argument("--vueltas", type=int, default=2000, help="vueltas de Monte Carlo por estrategia")
    ap.add_argument("--telegram", nargs="?", const=os.path.join(RAIZ, "telegram.json"))
    a = ap.parse_args(argv)
    if a.que == "descargar":
        for alias, (sim, duk, rango) in ACTIVOS.items():
            if duk:
                descargar_dukascopy(sim, duk, rango)
        return 0
    alias_l = list(ACTIVOS) if a.que == "todo" else [a.que]
    os.makedirs(SALIDA, exist_ok=True)
    textos = []
    for alias in alias_l:
        if alias not in ACTIVOS:
            raise SystemExit(f"No conozco «{alias}». Usa: {', '.join(ACTIVOS)} o todo")
        try:
            D = cargar(alias)
        except SystemExit as e:
            print(e)
            continue
        corte = datetime.fromisoformat(a.corte).replace(tzinfo=timezone.utc) if a.corte else None
        print(f"{D.simbolo}: {D.n} velas, generando {a.n} estrategias…")
        r = fabrica.correr(D, a.n, corte, a.semilla, {"vueltas": a.vueltas})
        base = os.path.join(SALIDA, f"fabrica_{alias}")
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, default=str)
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(informe_html(r))
        textos.append(resumen_texto(r))
        print(textos[-1])
        print(f"Informe: {base}.html")
    if a.telegram and textos:
        from consejo.telegram import Grupo
        Grupo(a.telegram).enviar("skeptic", "\n\n".join(textos) + "\n\nInforme completo en el panel: /fabrica")
    return 0


if __name__ == "__main__":
    sys.exit(main())
