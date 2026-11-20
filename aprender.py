"""El Consejo aprende de sus operaciones y se afina solo, sin sobreajustarse. Todo en papel.

    py aprender.py                    # mesa cripto: informe del diario y autoajuste semanal
    py aprender.py --solo-informe     # solo lo que enseñan las operaciones guardadas
    py aprender.py --telegram         # manda el resumen al bot

Cómo se afina sin hacerse trampas (la regla de los dos tramos):
1. Las velas REALES se parten en dos: los 2/3 más antiguos para BUSCAR y el último 1/3 para COMPROBAR.
2. En el tramo de búsqueda se prueba mover UN parámetro cada vez a sus valores vecinos.
3. El mejor cambio solo se acepta si TAMBIÉN gana en el tramo de comprobación (que no ha visto),
   tiene al menos 30 operaciones ahí, profit factor > 1,3 y drawdown < 20 %.
4. Como mucho un cambio por semana, y solo dentro de unos márgenes fijos. Se guarda en
   salida/config_auto.json (papel.py lo lee) y en salida/autoajuste_historial.json, con el porqué.
Si nada pasa la prueba, no cambia nada: es lo normal.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

from consejo import backtest, datos, mesas, seguridad
from consejo.agentes import news

RAIZ = os.path.dirname(os.path.abspath(__file__))

# solo estos parámetros se tocan solos, y solo dentro de estos valores
RANGOS = {
    "stop_atr": [1.2, 1.5, 2.0, 2.5],
    "rr_objetivo": [2.0, 2.5, 3.0],
    "puntos_para_entrar": [2, 3],
    "z_volumen_min": [2.0, 2.5, 3.0],
    "fomo_atr": [2.5, 3.0, 3.5],
}
MIN_OPS_COMPROBAR = 30


def ruta_auto(mesa: str) -> str:
    return os.path.join(RAIZ, "salida", f"config_auto_{mesa}.json")


def cargar_auto(mesa: str) -> dict:
    """Ajustes aceptados por el autoajuste (solo claves permitidas y dentro de su rango)."""
    try:
        with open(ruta_auto(mesa), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.get("parametros", {}).items() if k in RANGOS and v in RANGOS[k]}


def stats_ops(ops: list[dict], capital: float) -> dict:
    gan = sum(o["pnl"] for o in ops if o["pnl"] > 0)
    per = -sum(o["pnl"] for o in ops if o["pnl"] < 0)
    acum = pico = dd = 0.0
    for o in sorted(ops, key=lambda o: o["closed"]):
        acum += o["pnl"]
        pico = max(pico, acum)
        dd = min(dd, (acum - pico) / (capital + pico) * 100)
    return {"n": len(ops), "pf": gan / per if per else (99.0 if gan else 0.0), "ret": sum(o["pnl"] for o in ops) / capital * 100,
            "dd": dd, "R": sum(o["R"] for o in ops)}


def evaluar(series: dict, cfg: dict, noticias: dict, desde: datetime | None, hasta: datetime | None) -> dict:
    """Corre el Consejo y mide solo las operaciones abiertas en [desde, hasta)."""
    corte = {}
    for s, vs in series.items():
        a = 0 if desde is None else max(0, next((i for i, v in enumerate(vs) if v.t >= desde), len(vs)) - 300)
        b = len(vs) if hasta is None else next((i for i, v in enumerate(vs) if v.t >= hasta), len(vs))
        if b - a > 260:
            corte[s] = vs[a:b]
    m = backtest.Motor(corte, cfg, noticias=noticias).correr()
    ops = [o for o in m.operaciones
           if (desde is None or o["opened"] >= str(desde)) and (hasta is None or o["opened"] < str(hasta))]
    return stats_ops(ops, cfg["capital_inicial"])


def mejor_que(a: dict, b: dict) -> bool:
    return a["n"] >= MIN_OPS_COMPROBAR and a["pf"] > b["pf"] and a["ret"] > b["ret"]


def autoajuste(series: dict, cfg: dict, noticias: dict, log=print) -> dict:
    ts = sorted(v.t for vs in series.values() for v in (vs[0], vs[-1]))
    corte = (ts[0] + (ts[-1] - ts[0]) * 2 / 3).replace(minute=0, second=0, microsecond=0)
    rapido = {**cfg, "caminos_backtest": min(cfg.get("caminos_backtest", 1500), 300), "kronos_ruta": ""}
    log(f"Tramo de búsqueda: hasta {corte:%Y-%m-%d}; tramo de comprobación: desde {corte:%Y-%m-%d}")
    base_a = evaluar(series, rapido, noticias, None, corte)
    log(f"Actual en búsqueda: {base_a['n']} ops, PF {base_a['pf']:.2f}, {base_a['ret']:+.1f} %")
    candidatos = []
    for k, valores in RANGOS.items():
        for v in valores:
            if v == cfg.get(k):
                continue
            prueba = {**rapido, k: v}
            if prueba["rr_objetivo"] < prueba["rr_minimo"]:
                continue
            st = evaluar(series, prueba, noticias, None, corte)
            log(f"  {k} = {v}: {st['n']} ops, PF {st['pf']:.2f}, {st['ret']:+.1f} %")
            if st["n"] >= MIN_OPS_COMPROBAR and mejor_que(st, base_a):
                candidatos.append((st["pf"], k, v, st))
    candidatos.sort(key=lambda x: -x[0])
    res = {"fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), "corte": corte.isoformat(),
           "actual_busqueda": base_a, "cambio": None, "probados": len(candidatos)}
    if not candidatos:
        res["motivo"] = "ningún cambio mejora en el tramo de búsqueda"
        return res
    base_b = evaluar(series, rapido, noticias, corte, None)
    res["actual_comprobacion"] = base_b
    for _, k, v, st_a in candidatos[:3]:
        st_b = evaluar(series, {**rapido, k: v}, noticias, corte, None)
        log(f"  comprobación {k} = {v}: {st_b['n']} ops, PF {st_b['pf']:.2f}, {st_b['ret']:+.1f} % "
            f"(actual PF {base_b['pf']:.2f}, {base_b['ret']:+.1f} %)")
        if mejor_que(st_b, base_b) and st_b["pf"] > 1.3 and st_b["dd"] > -20:
            res["cambio"] = {"parametro": k, "antes": cfg.get(k), "despues": v, "busqueda": st_a, "comprobacion": st_b}
            return res
    res["motivo"] = "los cambios que mejoraban en búsqueda no aguantan en el tramo de comprobación (sobreajuste)"
    return res


def lecciones(ops: list[dict]) -> list[str]:
    """Qué enseñan las operaciones guardadas, agrupadas por lo que vio el Consejo al entrar."""
    if not ops:
        return ["Todavía no hay operaciones cerradas con ficha (en papel se van guardando solas)."]
    grupos: dict[str, list[float]] = defaultdict(list)
    for o in ops:
        f = o.get("ficha") or {}
        if "score" in f:
            grupos[f"puntos {round(f['score'])}"].append(o["R"])
        grupos["con MOVING" if f.get("moving") else "sin MOVING"].append(o["R"])
        grupos["con FOMO (entró en retroceso)" if f.get("fomo") else "sin FOMO"].append(o["R"])
        if f.get("hora_utc") is not None:
            grupos[f"franja {f['hora_utc'] // 6 * 6:02d}-{f['hora_utc'] // 6 * 6 + 6:02d} h UTC"].append(o["R"])
        for e in f.get("etiquetas", []):
            grupos[f"noticia «{e}»"].append(o["R"])
    lin = [f"{len(ops)} operaciones con ficha. Por grupos (n · acierto · R medio):"]
    for g, rs in sorted(grupos.items(), key=lambda x: -len(x[1])):
        lin.append(f"• {g}: {len(rs)} · {100 * sum(r > 0 for r in rs) / len(rs):.0f} % · {sum(rs) / len(rs):+.2f} R"
                   + ("  (pocas, no concluyente)" if len(rs) < 20 else ""))
    return lin


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mesa", choices=["cripto", "mercados"], default="cripto")
    ap.add_argument("--solo-informe", action="store_true")
    ap.add_argument("--telegram", nargs="?", const=os.path.join(RAIZ, "telegram.json"))
    a = ap.parse_args(argv)
    seguridad.comprobar_modo("paper")

    ops = []
    for ruta in (os.path.join(RAIZ, "salida", f"papel_{a.mesa}.json"),):
        if os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as f:
                ops += [o for o in json.load(f).get("operaciones", []) if o.get("ficha")]
    lin = ["🧠 Lo que enseña el diario (papel)", *lecciones(ops), ""]

    if not a.solo_informe:
        rutas = [os.path.join(RAIZ, "datos", mesas.archivo(s, "1h"))
                 for s in (mesas.CRIPTO if a.mesa == "cripto" else mesas.MERCADOS)]
        series = {mesas.simbolo_de_archivo(r): datos.cargar_csv(r) for r in rutas if os.path.exists(r)}
        series = {s: v for s, v in series.items() if len(v) > 1000}
        if not series:
            print("No hay velas reales en datos/: py descargar_datos.py todo")
            return 1
        noticias = {}
        for s in series:
            arch = news.Archivo.cargar(os.path.join(RAIZ, "datos", "noticias", mesas.archivo_noticias(s)))
            if arch:
                noticias[s] = arch
        auto = cargar_auto(a.mesa)
        cfg = {**backtest.CONFIG_DEFECTO, **mesas.AJUSTES[a.mesa], **auto}
        seguridad.comprobar_config(cfg)
        res = autoajuste(series, cfg, noticias)
        hist_ruta = os.path.join(RAIZ, "salida", f"autoajuste_historial_{a.mesa}.json")
        hist = []
        if os.path.exists(hist_ruta):
            with open(hist_ruta, encoding="utf-8") as f:
                hist = json.load(f)
        hist.append(res)
        os.makedirs(os.path.dirname(hist_ruta), exist_ok=True)
        with open(hist_ruta, "w", encoding="utf-8") as f:
            json.dump(hist[-100:], f, ensure_ascii=False, indent=1)
        c = res["cambio"]
        if c:
            nuevo = {**auto, c["parametro"]: c["despues"]}
            with open(ruta_auto(a.mesa), "w", encoding="utf-8") as f:
                json.dump({"parametros": nuevo, "actualizado": res["fecha"]}, f, ensure_ascii=False, indent=1)
            b, p = c["comprobacion"], res["actual_comprobacion"]
            lin += ["⚙️ Autoajuste: CAMBIO aceptado",
                    f"{c['parametro']}: {c['antes']} → {c['despues']}",
                    f"En el tramo que no había visto: PF {p['pf']:.2f} → {b['pf']:.2f}, "
                    f"{p['ret']:+.1f} % → {b['ret']:+.1f} %, {b['n']} operaciones, drawdown {b['dd']:.1f} %.",
                    "El bot de papel lo usará al reiniciarse. Si no te convence, borra salida/config_auto_"
                    f"{a.mesa}.json y vuelve a lo de antes."]
        else:
            lin += ["⚙️ Autoajuste: sin cambios", f"Motivo: {res.get('motivo')}.",
                    "Es lo normal: solo se cambia algo cuando gana en los dos tramos."]
    texto = "\n".join(lin)
    print(texto)
    if a.telegram:
        from consejo.telegram import Grupo
        Grupo(a.telegram).enviar("skeptic", texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
