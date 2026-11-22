"""Papel de la fábrica: las mejores estrategias de `fabrica.py` operando con velas reales y dinero FICTICIO.

    py papel_fabrica.py                    # oro, las 2 mejores finalistas, una pasada cada hora
    py papel_fabrica.py --top 3 --telegram # 3 estrategias y avisos al móvil
    py papel_fabrica.py --una-vez          # una sola pasada (para probar)

La primera vez congela las estrategias elegidas en salida/papel_fabrica_<activo>_estrategias.json:
si luego se vuelve a correr la fábrica, el papel sigue con las mismas hasta que se reinicie
(`py reiniciar_papel.py fabrica` o borrando ese archivo). Así se ve si aguantan en vivo.

Cada estrategia tiene su cuenta ficticia de 100.000 $ y arriesga el 1 % por operación, con los mismos
costes y reglas del backtest (consejo/fabrica.py). Solo cuentan las operaciones que EMPIEZAN después
de arrancar el papel. Ni broker, ni claves, ni órdenes reales.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from consejo import datos, fabrica, fondeo

RAIZ = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(RAIZ, "salida")
CAPITAL = 100_000.0
YAHOO = {"oro": "GC=F", "nasdaq": "QQQ", "sp500": "SPY", "btc": "BTC-USD", "eth": "ETH-USD"}
PERFIL = {"oro": "GC=F", "nasdaq": "QQQ", "sp500": "SPY", "btc": "BTCUSDT", "eth": "ETHUSDT"}


def velas_yahoo(alias: str, rango: str = "120d") -> list:
    """Velas de 1 h CERRADAS de Yahoo (la vela en curso se descarta)."""
    import descargar_datos
    s = YAHOO[alias]
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(s)}?range={rango}&interval=1h"
    filas = descargar_datos.yahoo_a_filas(json.loads(descargar_datos._get(url)))
    ahora = datetime.now(timezone.utc)
    out = []
    for f in filas:
        t = datetime.fromisoformat(f[0])
        if t + timedelta(hours=1) <= ahora:
            out.append(datos.Vela(t, float(f[1]), float(f[2]), float(f[3]), float(f[4]), float(f[5] or 0)))
    return out


def estrategias(alias: str, top: int) -> list[dict]:
    ruta = os.path.join(SALIDA, f"papel_fabrica_{alias}_estrategias.json")
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    origen = os.path.join(SALIDA, f"fabrica_{alias}.json")
    if not os.path.exists(origen):
        raise SystemExit(f"No hay resultados de la fábrica para {alias}: corre antes  py fabrica.py {alias}")
    with open(origen, encoding="utf-8") as f:
        r = json.load(f)
    if not r["finalistas"]:
        raise SystemExit(f"La fábrica no dejó finalistas en {alias}: no hay nada que poner en papel.")
    elegidas = [{"n": k, "reglas": x["reglas"], "estrategia": x["estrategia"], "familia": x["familia"],
                 "backtest": {"dentro_pct": x["is"]["ret_pct"], "fuera_pct": x["oos"]["ret_pct"],
                              "caida_pct": x["todo"]["dd_pct"], "reto": x["fondeo"].get("pct_aprobadas")},
                 "inicio": datetime.now(timezone.utc).isoformat()}
                for k, x in enumerate(r["finalistas"][:top], 1)]
    os.makedirs(SALIDA, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(elegidas, f, ensure_ascii=False, indent=1)
    return elegidas


def _num(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pasada(alias: str, top: int, avisar=print, velas=None) -> dict:
    ruta = os.path.join(SALIDA, f"papel_fabrica_{alias}.json")
    previo = {}
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            previo = json.load(f)
    velas = velas if velas is not None else velas_yahoo(alias)
    D = fabrica.Datos(PERFIL[alias], velas)
    P = fabrica.perfil(PERFIL[alias])
    cuentas = []
    for est in estrategias(alias, top):
        n = est["n"]
        inicio = datetime.fromisoformat(est["inicio"])
        ant = next((c for c in previo.get("cuentas", []) if c["n"] == n), {})
        cerradas = ant.get("cerradas", [])
        vistas = {c["opened"] for c in cerradas}
        ops = [o for o in fabrica.simular(D, est["estrategia"], P=P) if o["opened"] >= inicio]
        abierta = None
        for o in ops:
            if o["reason"] == "fin de datos":
                abierta = o
                continue
            if o["opened"].isoformat() in vistas:
                continue
            c = {"opened": o["opened"].isoformat(), "closed": o["closed"].isoformat(), "dir": o["dir"],
                 "entry": round(o["entry"], 2), "exit": round(o["exit"], 2), "pnl": round(o["pnl"], 2),
                 "R": round(o["R"], 2), "reason": o["reason"]}
            cerradas.append(c)
            avisar(f"🏭 Papel fábrica · {alias} #{n}: CIERRE {c['dir']} {_num(c['entry'])} → {_num(c['exit'])} "
                   f"({c['reason']}) · {'+' if c['pnl'] >= 0 else ''}{_num(c['pnl'])} $ ({c['R']:+.2f} R). Dinero ficticio.")
        ab = None
        if abierta:
            ab = {"opened": abierta["opened"].isoformat(), "dir": abierta["dir"], "entry": round(abierta["entry"], 2),
                  "precio": round(D.c[-1], 2), "pnl": round(abierta["pnl"], 2)}
            if (ant.get("abierta") or {}).get("opened") != ab["opened"]:
                a = D.ind("atr", est["estrategia"]["atr_n"])[abierta["i"] - 1]
                stop = abierta["entry"] - (1 if abierta["dir"] == "long" else -1) * est["estrategia"]["sl"] * a
                ab["stop"] = round(stop, 2)
                avisar(f"🏭 Papel fábrica · {alias} #{n}: ENTRADA {'COMPRA' if ab['dir'] == 'long' else 'VENTA'} en "
                       f"{_num(ab['entry'])}, stop {_num(stop)}. Regla: {est['reglas']} Dinero ficticio.")
            else:
                ab["stop"] = (ant.get("abierta") or {}).get("stop")
        neto = sum(c["pnl"] for c in cerradas)
        curva, acum = [[est["inicio"][:16], CAPITAL]], CAPITAL
        for c in sorted(cerradas, key=lambda x: x["closed"]):
            acum += c["pnl"]
            curva.append([c["closed"][:16], round(acum, 2)])
        curva.append([D.t[-1].isoformat()[:16], round(acum + (ab["pnl"] if ab else 0.0), 2)])
        cuentas.append({"n": n, "reglas": est["reglas"], "familia": est["familia"], "inicio": est["inicio"],
                        "backtest": est["backtest"], "cerradas": cerradas, "abierta": ab,
                        "capital": round(CAPITAL + neto, 2), "con_abierta": round(CAPITAL + neto + (ab["pnl"] if ab else 0), 2),
                        "curva": curva,
                        "reto": fondeo.examen(cerradas, CAPITAL)["texto"] if cerradas else "Sin operaciones cerradas aún."})
    estado = {"activo": alias, "simbolo": PERFIL[alias], "actualizado": datetime.now(timezone.utc).isoformat(),
              "ultima_vela": D.t[-1].isoformat(), "precio": D.c[-1], "cuentas": cuentas}
    os.makedirs(SALIDA, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1)
    return estado


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("activo", nargs="?", default="oro", choices=list(YAHOO))
    ap.add_argument("--top", type=int, default=2)
    ap.add_argument("--una-vez", action="store_true")
    ap.add_argument("--telegram", nargs="?", const=os.path.join(RAIZ, "telegram.json"))
    a = ap.parse_args(argv)
    avisar = print
    if a.telegram:
        from consejo.telegram import Grupo
        g = Grupo(a.telegram)

        def avisar(txt):
            print(txt)
            g.enviar("risk", txt)
    while True:
        try:
            e = pasada(a.activo, a.top, avisar)
            for c in e["cuentas"]:
                print(f"{e['ultima_vela'][:16]} #{c['n']}: {_num(c['con_abierta'])} $ · {len(c['cerradas'])} cerradas · "
                      f"{'abierta ' + c['abierta']['dir'] if c['abierta'] else 'sin posición'}")
        except SystemExit:
            raise
        except Exception as ex:  # noqa: BLE001 - un fallo de red no para el papel
            print(f"Fallo en la pasada ({type(ex).__name__}: {ex}); reintento en la próxima hora.")
        if a.una_vez:
            return 0
        ahora = datetime.now(timezone.utc)
        siguiente = (ahora + timedelta(hours=1)).replace(minute=3, second=0, microsecond=0)
        time.sleep(max(60.0, (siguiente - ahora).total_seconds()))


if __name__ == "__main__":
    sys.exit(main())
