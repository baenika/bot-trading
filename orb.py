"""Estrategia de la vela de las 9:30 de Nueva York (reel 13) en SPY y QQQ, con velas reales de 5 min.

    py orb.py actualizar     # baja las velas de 5 min de Yahoo y las AÑADE a datos/ (la historia crece cada día)
    py orb.py backtest       # resultado con todas las velas guardadas
    py orb.py informe        # informe de la última semana y comparación de variantes → salida/orb_informe_<fecha>.md
    py orb.py diario         # actualizar + informe (para lanzarlo cada día al cerrar Wall Street)

Yahoo solo da 60 días de velas de 5 min, así que el primer backtest es corto. Como `actualizar`
guarda lo nuevo cada día, la muestra crece sola. Todo es simulado: no hay broker ni órdenes.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

from consejo import datos, mesas
from consejo.estrategias import orb
from consejo.telegram import Grupo

RAIZ = os.path.dirname(os.path.abspath(__file__))
SIMBOLOS = ["SPY", "QQQ"]

# variantes que se comparan cada semana para saber qué cambiar (siempre con la misma regla de base)
VARIANTES = {
    "como en el vídeo (order block, 2R)": {},
    "entrada en el máximo del rango": {"entrada": "maximo_rango"},
    "sin esperar retroceso (al cierre de la ruptura)": {"entrada": "cierre_ruptura"},
    "objetivo 1,5R": {"rr": 1.5},
    "objetivo 3R": {"rr": 3.0},
    "solo la primera hora": {"limite_entrada_min": 60},
}


def _ruta(s: str) -> str:
    return os.path.join(RAIZ, "datos", mesas.archivo(s, "5m"))


def actualizar() -> None:
    import descargar_datos
    os.makedirs(os.path.join(RAIZ, "datos"), exist_ok=True)
    for s in SIMBOLOS:
        url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(s)}"
               f"?range=60d&interval=5m")
        filas = descargar_datos.yahoo_a_filas(json.loads(descargar_datos._get(url)))
        viejas = {}
        if os.path.exists(_ruta(s)):
            for v in datos.cargar_csv(_ruta(s)):
                viejas[v.t.isoformat()] = [v.t.isoformat(), v.o, v.h, v.l, v.c, v.v]
        ahora = datetime.now(timezone.utc)
        for f in filas:   # solo velas cerradas
            if datetime.fromisoformat(f[0]) + timedelta(minutes=5) <= ahora:
                viejas[datetime.fromisoformat(f[0]).isoformat()] = f
        with open(_ruta(s), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "open", "high", "low", "close", "volume"])
            w.writerows(viejas[k] for k in sorted(viejas))
        print(f"{s}: {len(viejas)} velas de 5 min guardadas → {_ruta(s)}")


def cargar() -> dict:
    series = {s: datos.cargar_csv(_ruta(s)) for s in SIMBOLOS if os.path.exists(_ruta(s))}
    if not series:
        raise SystemExit("No hay velas de 5 min. Primero: py orb.py actualizar")
    return series


def _linea(nombre: str, st: dict) -> str:
    return (f"| {nombre} | {st['n']} | {st['acierto']:.0f} % | {st['R_total']:+.1f} | {st['pf']:.2f} | "
            f"{st['rentabilidad']:+.1f} % | {st['drawdown']:.1f} % |")


def informe(series: dict, hoy: datetime | None = None) -> str:
    hoy = hoy or datetime.now(timezone.utc)
    hace7 = (hoy - timedelta(days=7)).date().isoformat()
    base = orb.backtest(series)
    semana = [o for o in base["operaciones"] if o.dia >= hace7]
    st_sem = orb.estadisticas(semana)
    dias = sorted({v.t.date() for vs in series.values() for v in vs})
    lineas = [f"# Estrategia de las 9:30 · informe del {hoy:%d/%m/%Y}", "",
              f"Velas reales de 5 min de SPY y QQQ (Yahoo), del {dias[0]:%d/%m/%Y} al {dias[-1]:%d/%m/%Y}. "
              "Todo simulado.", "",
              "## Esta semana", ""]
    if semana:
        lineas += ["| Día | Símbolo | Entrada | Stop | Objetivo | Salida | Motivo | R |",
                   "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        lineas += [f"| {o.dia} | {o.simbolo} | {o.entrada:.2f} | {o.stop:.2f} | {o.objetivo:.2f} | {o.salida:.2f} | "
                   f"{o.motivo} | {o.R:+.2f} |" for o in semana]
        lineas += ["", f"Semana: {st_sem['n']} operaciones, acierto {st_sem['acierto']:.0f} %, "
                       f"{st_sem['R_total']:+.1f} R."]
    else:
        lineas.append("Ninguna operación esta semana.")
    lineas += ["", "## Desde el principio, y variantes", "",
               "| Variante | Operaciones | Acierto | R total | Profit factor | Rentabilidad (1 % por operación) | Drawdown |",
               "| --- | --- | --- | --- | --- | --- | --- |"]
    resultados = {}
    for nombre, cambio in VARIANTES.items():
        st = orb.backtest(series, cambio)["stats"]
        resultados[nombre] = st
        lineas.append(_linea(nombre, st))
    lineas += ["", "Por qué no hubo operación los demás días: " +
               ", ".join(f"{k} ({v})" for k, v in sorted(base["motivos"].items(), key=lambda x: -x[1])
                         if k != "operación"), "", "## Qué haría", ""]
    st0 = resultados["como en el vídeo (order block, 2R)"]
    mejor = max(resultados.items(), key=lambda x: x[1]["R_total"])
    if st0["n"] < 30:
        lineas.append(f"Con {st0['n']} operaciones todavía no se puede sacar ninguna conclusión: hacen falta "
                      "al menos 30. Lo dejo tal como se explicó en el vídeo y sigo acumulando días.")
    elif mejor[0] != "como en el vídeo (order block, 2R)" and mejor[1]["R_total"] > st0["R_total"] + 5:
        lineas.append(f"La variante «{mejor[0]}» va {mejor[1]['R_total'] - st0['R_total']:+.1f} R por delante. "
                      "Antes de cambiar, hay que comprobar que gana también en la primera y la segunda mitad "
                      "de los datos por separado.")
    else:
        lineas.append("La versión del vídeo aguanta frente a las variantes. No cambiaría nada todavía.")
    return "\n".join(lineas)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("accion", choices=["actualizar", "backtest", "informe", "diario"])
    ap.add_argument("--telegram", default=os.path.join(RAIZ, "telegram.json"))
    a = ap.parse_args(argv)
    if a.accion in ("actualizar", "diario"):
        actualizar()
    if a.accion == "backtest":
        r = orb.backtest(cargar())
        for o in r["operaciones"]:
            print(f"{o.dia} {o.simbolo} {o.dir} entrada {o.entrada:.2f} stop {o.stop:.2f} → {o.motivo} {o.R:+.2f}R")
        st = r["stats"]
        print(f"{st['n']} operaciones · acierto {st['acierto']:.0f}% · {st['R_total']:+.1f} R · PF {st['pf']:.2f} · "
              f"{st['rentabilidad']:+.1f}% · drawdown {st['drawdown']:.1f}%")
        print("Días sin operación: " + ", ".join(f"{k} ({v})" for k, v in r["motivos"].items() if k != "operación"))
    if a.accion in ("informe", "diario"):
        texto = informe(cargar())
        ruta = os.path.join(RAIZ, "salida", f"orb_informe_{datetime.now():%Y-%m-%d}.md")
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)
        print(texto)
        print(f"\nGuardado en {ruta}")
        if a.accion == "diario":
            g = Grupo(a.telegram)
            if g.activo("chart"):
                g.enviar("chart", texto[:3900])
    return 0


if __name__ == "__main__":
    sys.exit(main())
