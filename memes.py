"""Mesa MEMECOINS (Solana): grabar el mercado real y hacer backtest sobre lo grabado.

    py memes.py grabar               # opera en PAPEL y graba todo en datos/memes/ (se queda funcionando)
    py memes.py grabar --una-vez     # una sola pasada
    py memes.py backtest             # backtest sobre lo grabado
    py memes.py backtest --grid      # compara 24 estrategias sobre lo grabado
    py memes.py ballena añadir <dirección> [alias]   # seguir una wallet (solo lectura)
    py memes.py ballena quitar <dirección>
    py memes.py ballena lista

Viene del Consejo v5 (rama consejo-v5-ayer). Datos públicos de DexScreener y RugCheck,
sin cuentas ni claves. No hay wallet, ni claves, ni órdenes: todo es dinero ficticio.

En memecoins no hay velas históricas fiables con las monedas que hicieron rug, así que
la única forma honesta de probar es GRABAR el mercado unas semanas (rugs incluidos)
y hacer el backtest sobre esa grabación. Cuantos más días, más fiable.
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
import os
import sys
import time
from datetime import datetime, timezone

from consejo import seguridad
from consejo.memes.engine import MemeEngine
from consejo.telegram import Grupo

RAIZ = os.path.dirname(os.path.abspath(__file__))
CAPITAL = 1000.0


def _rutas():
    return {"grabacion": os.path.join(RAIZ, "datos", "memes"), "estado": os.path.join(RAIZ, "salida", "papel_memes.json"),
            "panel_papel": os.path.join(RAIZ, "salida", "estado_papel_memes.json"),
            "panel_backtest": os.path.join(RAIZ, "salida", "estado_memes.json")}


def _guardar(datos: dict, ruta: str) -> None:
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, default=str)


# --- grabar (papel en vivo) ---------------------------------------------------------

def grabar(params: dict, cada: int, una_vez: bool, grupo: Grupo) -> None:
    from consejo.memes.sources import DexScreener, RugCheck
    r = _rutas()
    eng = MemeEngine(params, CAPITAL)
    extra = {"watch": {}, "safety": {}, "debated": [], "nombres": {}}
    if os.path.exists(r["estado"]):
        with open(r["estado"], encoding="utf-8") as f:
            st = json.load(f)
        for k in ("cash", "start", "peak", "day_start", "day", "positions", "trades", "last_px"):
            if k in st.get("broker", {}):
                setattr(eng.broker, k, st["broker"][k])
        eng.kills.update(st.get("kills", {}))
        eng.decisions.update(st.get("decisions", {}))
        eng.curve = st.get("curve", [])
        eng.last_decision = st.get("last_decision")
        extra.update({k: st.get(k, extra[k]) for k in extra})

    ds, rc = DexScreener(), RugCheck()
    rc.cache.update(extra["safety"])
    os.makedirs(r["grabacion"], exist_ok=True)

    def seguridad_de(m):
        s = rc.safety(m)
        extra["safety"][m] = s
        return s

    from consejo.avisos import Avisador
    from consejo.memes.ballenas import Ballenas, frenado_si_429
    avisos = Avisador(grupo, "memes")
    avisos.version()
    ballenas = Ballenas(os.path.join(r["grabacion"], "ballenas.json"), pausa_s=2.0,
                        fuente_poseedores=frenado_si_429(rc.poseedores))
    hilo = None
    while True:
        try:
            _pasada_memes(eng, ds, rc, extra, r, seguridad_de, grupo)
            # las ballenas van en un hilo aparte: si el RPC va lento o frena, la grabación sigue cada minuto
            if una_vez:
                _pasada_ballenas(eng, extra, r, ballenas, grupo)
            elif hilo is None or not hilo.is_alive():
                import threading
                hilo = threading.Thread(target=_ballenas_seguro, args=(eng, extra, r, ballenas, grupo, avisos),
                                        daemon=True)
                hilo.start()
        except Exception as e:  # noqa: BLE001 - un fallo no tumba la grabación: avisa y sigue
            print(f"Error en la pasada de memes: {e!r}")
            avisos.error(e, "la grabación de memes")
        if una_vez:
            return
        time.sleep(cada)


def _pasada_memes(eng, ds, rc, extra, r, seguridad_de, grupo) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    nuevos = ds.latest_mints(60)
    limite = time.time() - 72 * 3600          # vigila 72 h todo lo que pasó el Scout, para grabar su final
    extra["watch"] = {m: t for m, t in extra["watch"].items() if t > limite}
    mints = list(dict.fromkeys(nuevos + list(eng.broker.positions) + list(extra["watch"])))[:600]
    pares = ds.pairs_for(mints)
    for c in pares:
        if eng.desk.scout(c):
            extra["watch"].setdefault(c["mint"], time.time())
        if c["mint"] in extra["watch"] or c["mint"] in eng.broker.positions:
            extra["nombres"][c["mint"]] = [c.get("symbol") or c["mint"][:6], c.get("pair")]
    extra["nombres"] = {m: v for m, v in extra["nombres"].items() if m in extra["watch"] or m in eng.broker.positions}

    eventos, debates = eng.step(ts, pares, seguridad_de)

    vistos = {c["mint"] for c in pares}
    with open(os.path.join(r["grabacion"], ts[:10] + ".jsonl"), "a", encoding="utf-8") as f:
        for c in pares:
            f.write(json.dumps({"ts": ts, "c": c, "s": rc.cache.get(c["mint"])}) + "\n")
        for m in set(mints) - vistos:
            f.write(json.dumps({"ts": ts, "gone": m}) + "\n")

    for rv, tam in debates:   # al grupo solo las compras y las que se quedaron cerca, una vez por moneda
        cerca = rv.score >= eng.p["min_score"] - 10 and rv.c["mint"] not in extra["debated"]
        if rv.decision != "COMPRAR" and not cerca:
            continue
        extra["debated"] = (extra["debated"] + [rv.c["mint"]])[-2000:]
        grupo.conversacion(eng.desk.messages(rv, tam))
    for tipo, x in eventos:
        if tipo == "close":
            grupo.enviar("risk", f"🪙 {'🟢' if x['pnl'] > 0 else '🔴'} Cerrado {x['symbol']} por {x['reason']}: "
                                 f"{x['pnl']:+.2f} $ ({x['pnl_pct']:+.0f}%) · capital ficticio "
                                 f"{eng.broker.equity():,.2f} $")

    _guardar(eng.snapshot("live", "Solana · memecoins"), r["panel_papel"])
    _guardar({"broker": {k: getattr(eng.broker, k) for k in ("cash", "start", "peak", "day_start", "day",
                                                               "positions", "trades", "last_px")},
              "kills": eng.kills, "decisions": eng.decisions, "curve": eng.curve[-5000:],
              "last_decision": eng.last_decision, **extra}, r["estado"])
    print(f"{ts[11:16]} · {len(pares)} pares · {len(eng.broker.positions)} abiertas · "
          f"capital ficticio {eng.broker.equity():,.2f} $", flush=True)


def _ballenas_seguro(eng, extra, r, ballenas, grupo, avisos) -> None:
    try:
        _pasada_ballenas(eng, extra, r, ballenas, grupo)
    except Exception as e:  # noqa: BLE001
        print(f"Error en las ballenas: {e!r}", flush=True)
        avisos.error(e, "las ballenas de memes")


def _pasada_ballenas(eng, extra, r, ballenas, grupo) -> list[dict]:
    """Ballenas de las monedas abiertas y de las que pasaron el Scout (solo lectura, solo informa)."""
    from consejo.memes.ballenas import texto
    nombres = dict(extra.get("nombres", {}))
    simbolos = {m: p.get("symbol") or m[:6] for m, p in list(eng.broker.positions.items())}
    vigiladas = dict(extra["watch"])
    for m in sorted(vigiladas, key=lambda m: -vigiladas[m]):      # las más nuevas primero
        simbolos.setdefault(m, (nombres.get(m) or [m[:6]])[0])
    eventos = ballenas.pasada(simbolos, excluir={v[1] for v in nombres.values() if v and v[1]})
    if not eventos:
        return []
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(os.path.join(r["grabacion"], f"ballenas_{ts[:10]}.jsonl"), "a", encoding="utf-8") as f:
        for x in eventos:
            f.write(json.dumps({"ts": ts, **x}) + "\n")
    abiertas = set(eng.broker.positions)
    importantes = [x for x in eventos if x["tipo"] in ("venta", "compra", "concentracion")
                   or x.get("mint") in abiertas or x.get("mint") in vigiladas]
    if importantes:
        grupo.enviar("scout", "\n".join(texto(x, simbolos) + (" (la tenemos en papel)" if x.get("mint") in abiertas
                                                              else "") for x in importantes[:8]))
    return eventos


# --- backtest sobre lo grabado ----------------------------------------------------------

def cargar_grabacion(carpeta: str) -> list[list[dict]]:
    tandas: dict[str, list[dict]] = {}
    for ruta in sorted(glob.glob(os.path.join(carpeta, "*.jsonl"))):
        with open(ruta, encoding="utf-8") as f:
            for linea in f:
                if linea.strip():
                    x = json.loads(linea)
                    tandas.setdefault(x["ts"], []).append(x)
    return [tandas[k] for k in sorted(tandas)]


def correr(tandas: list[list[dict]], params: dict, capital: float = CAPITAL) -> MemeEngine:
    eng = MemeEngine(params, capital)
    seguridad_conocida: dict[str, dict] = {}
    for tanda in tandas:
        ts, pares = tanda[0]["ts"], []
        for x in tanda:
            if "c" in x:
                pares.append(x["c"])
                if x.get("s") is not None:     # solo lo que se sabía en ese momento o antes
                    seguridad_conocida[x["c"]["mint"]] = x["s"]
        eng.step(ts, pares, lambda m: seguridad_conocida.get(m, {}))
    fin = tandas[-1][0]["ts"]
    for m in list(eng.broker.positions):
        p = eng.broker.positions[m]
        eng.broker._close(m, eng.broker.last_px.get(m, p["ref"]), p["liq_now"], fin, "fin del backtest")
    eng.curve.append([fin, eng.broker.equity()])
    return eng


def backtest(params: dict, grid: bool, carpeta: str) -> int:
    tandas = cargar_grabacion(carpeta)
    if len(tandas) < 10:
        print(f"Muy pocos datos en {carpeta}. Deja `py memes.py grabar` funcionando unos días.")
        return 1
    ini, fin = tandas[0][0]["ts"][:16], tandas[-1][0]["ts"][:16]
    eng = correr(tandas, params)
    s = eng.stats()
    snap = eng.snapshot("backtest", "Solana · memecoins", period=f"{ini} → {fin}")
    _guardar(snap, _rutas()["panel_backtest"])
    f = lambda x, d=1: "-" if x is None else round(x, d)  # noqa: E731
    print(f"Mesa Memecoins · grabación {ini} → {fin} · {len(tandas)} ciclos · capital ficticio {CAPITAL:.0f} $")
    print(f"Rentabilidad {f(s.get('return_pct'))}% · {s['trades']} operaciones · acierto {f(s.get('win_rate'))}% · "
          f"PF {f(s.get('profit_factor'), 2)} · drawdown {f(s.get('max_dd_pct'))}% · rugs sufridos {s.get('rugs', 0)}")
    print("Descartes por regla:")
    for k, v in eng.kills.most_common(12):
        print(f"  • {k}: {v}")
    if grid:
        filas = []
        for ms, stop, ts_, tr in itertools.product([60, 70, 80], [6, 10], [12, 25], [8, 15]):
            st = correr(tandas, {**params, "min_score": ms, "stop_pct": stop, "trail_start_pct": ts_,
                                 "trail_pct": tr}).stats()
            filas.append((st.get("return_pct", 0), ms, stop, ts_, tr, st["trades"], st.get("win_rate") or 0,
                          st.get("profit_factor"), st.get("max_dd_pct", 0)))
        filas.sort(reverse=True)
        print("\nMejores 8 estrategias (puntuación mín. · stop · trailing desde · trailing):")
        for ret, ms, stop, ts_, tr, n, wr, pf, dd in filas[:8]:
            print(f"  {ms} · −{stop}% · +{ts_}% · {tr}% → {ret:+.1f}% · {n} ops · acierto {wr:.0f}% · "
                  f"PF {'-' if pf is None else round(pf, 2)} · DD {dd:.1f}%")
        print("Ojo: la mejor del pasado no es la mejor del futuro. Fíate de las que salen bien en VARIAS semanas.")
    print(f"Panel: py dashboard_server.py y pestaña Memes.")
    return 0


def ballena(resto: list[str], ruta: str) -> int:
    import re
    from consejo.memes.ballenas import Ballenas, frenado_si_429
    b = Ballenas(ruta)
    orden = (resto or ["lista"])[0].lower()
    if orden in ("añadir", "anadir", "quitar"):
        if len(resto) < 2 or not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", resto[1]):
            print("Falta una dirección pública de Solana válida (solo la dirección, nunca una clave).")
            return 2
        if orden == "quitar":
            b.d["wallets"].pop(resto[1], None)
        else:
            b.seguir(resto[1], " ".join(resto[2:]))
        b.guardar()
    for w, x in b.d["wallets"].items():
        print(f"{x.get('alias')}: {w} ({'a mano' if x.get('manual') else 'automática'})")
    if not b.d["wallets"]:
        print("No sigo ninguna wallet todavía.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("accion", choices=["grabar", "backtest", "ballena"])
    ap.add_argument("resto", nargs="*", help="ballena: añadir <dirección> [alias] | quitar <dirección> | lista")
    ap.add_argument("--una-vez", action="store_true")
    ap.add_argument("--cada", type=int, default=60, help="segundos entre pasadas al grabar")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--carpeta", default=None, help="carpeta de la grabación (por defecto datos/memes)")
    ap.add_argument("--config", help="JSON con parámetros de la mesa (min_score, stop_pct...)")
    ap.add_argument("--telegram", default=os.path.join(RAIZ, "telegram.json"))
    a = ap.parse_args(argv)
    seguridad.comprobar_modo("paper")
    params = {}
    if a.config:
        with open(a.config, encoding="utf-8") as f:
            params = json.load(f)
        seguridad.comprobar_config(params)
    if a.accion == "ballena":
        return ballena(a.resto, os.path.join(_rutas()["grabacion"], "ballenas.json"))
    if a.accion == "grabar":
        grabar(params, a.cada, a.una_vez, Grupo(a.telegram))
        return 0
    return backtest(params, a.grid, a.carpeta or _rutas()["grabacion"])


if __name__ == "__main__":
    sys.exit(main())
