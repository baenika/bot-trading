"""Informe semanal del Consejo por Telegram: cómo va, qué ha fallado y qué toca revisar. Todo en papel.

    py informe_semanal.py               # imprime el informe y lo guarda en salida/informe_semanal_<fecha>.md
    py informe_semanal.py --telegram    # además lo manda al bot

Lee lo que ya escriben los demás programas (no baja nada ni opera):
salida/papel_cripto.json, estado_papel_cripto.json, papel_memes.json, orb_informe_*.md,
autoajuste_historial_cripto.json y avisos.json.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from consejo import avisos

RAIZ = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(RAIZ, "salida")


def _json(nombre: str):
    try:
        with open(os.path.join(SALIDA, nombre), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _t(x: str) -> datetime:
    d = datetime.fromisoformat(str(x).replace(" ", "T"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def bloque_cripto(desde: datetime, revisar: list[str]) -> list[str]:
    e, panel = _json("papel_cripto.json"), _json("estado_papel_cripto.json") or {}
    if not e:
        revisar.append("El papel de cripto no tiene estado: ¿está arrancado papel.py?")
        return ["🏛️ Cripto: sin datos todavía."]
    ops = e.get("operaciones", [])
    sem = [o for o in ops if _t(o["closed"]) >= desde]
    pnl = sum(o["pnl"] for o in sem)
    gan = sum(o["pnl"] for o in sem if o["pnl"] > 0)
    per = -sum(o["pnl"] for o in sem if o["pnl"] < 0)
    ini = panel.get("start_equity") or 10000.0
    lin = [f"🏛️ Cripto (papel desde {str(e.get('inicio', ''))[:10]})",
           f"• Capital ficticio: {e.get('capital', 0):,.2f} $ ({(e.get('capital', ini) / ini - 1) * 100:+.1f} % desde el inicio)",
           f"• Esta semana: {len(sem)} operaciones cerradas, {pnl:+,.2f} $"
           + (f", acierto {100 * sum(o['pnl'] > 0 for o in sem) / len(sem):.0f} %, PF {gan / per:.2f}" if sem and per else ""),
           f"• Abiertas ahora: {len(e.get('abiertas') or {})}"]
    bh = (panel.get("skeptic_saved") or {}).get("buy_hold_pct")
    if bh is not None:
        lin.append(f"• Comprar y mantener en el mismo tiempo: {bh:+.1f} %")
    exam = panel.get("exam") or []
    if exam:
        ok = sum(x["ok"] for x in exam)
        lin.append(f"• Examen para pensar en dinero real: {ok}/{len(exam)} casillas · "
                   + ", ".join(f"{'✅' if x['ok'] else '❌'} {x['criterio']}" for x in exam))
    cal = panel.get("calibration") or {}
    if cal.get("error_pts") is not None:
        lin.append(f"• Máquina del tiempo: error {cal['error_pts']:.1f} puntos con {cal.get('n', 0)} casos "
                   f"({'vota' if cal.get('usable') else 'solo informa'})")
    if e.get("parado"):
        revisar.append("El bot de cripto está PARADO por drawdown: hay que revisarlo juntos.")
    if not ops:
        lin.append("• Aún no ha cerrado ninguna operación: es normal las primeras semanas (es exigente).")
    if panel.get("veto_counts"):
        top = sorted(panel["veto_counts"].items(), key=lambda x: -x[1])[:3]
        lin.append("• Lo que más frena: " + "; ".join(f"{k} ({v})" for k, v in top))
    return lin


def bloque_memes(desde: datetime) -> list[str]:
    e = _json("papel_memes.json")
    if not e:
        return ["🪙 Memes: sin datos todavía."]
    b = e.get("broker", {})
    sem = [x for x in b.get("trades", []) if _t(x.get("closed", x.get("opened", "1970-01-01"))) >= desde]
    pos = b.get("positions") or {}
    eq = b.get("cash", 0) + sum(p.get("usd", 0) for p in pos.values())
    rugs = sum("rug" in str(x.get("reason", "")).lower() for x in sem)
    return ["🪙 Memes (Solana, papel)",
            f"• Capital ficticio: unos {eq:,.2f} $ · {len(pos)} abiertas",
            f"• Esta semana: {len(sem)} operaciones, {sum(x.get('pnl', 0) for x in sem):+,.2f} $"
            + (f", {rugs} por rug" if rugs else "")]


def bloque_orb() -> list[str]:
    rutas = sorted(glob.glob(os.path.join(SALIDA, "orb_informe_*.md")))
    if not rutas:
        return ["🕤 Estrategia de las 9:30: todavía sin informe."]
    with open(rutas[-1], encoding="utf-8") as f:
        txt = f.read()
    que = txt.split("## Qué haría", 1)[-1].strip().split("\n")[0] if "## Qué haría" in txt else ""
    return ["🕤 Estrategia de las 9:30 (SPY/QQQ)", f"• {que[:400] or 'ver ' + os.path.basename(rutas[-1])}"]


def bloque_autoajuste(desde: datetime) -> list[str]:
    h = _json("autoajuste_historial_cripto.json") or []
    sem = [x for x in h if _t(x["fecha"]) >= desde]
    if not sem:
        return ["⚙️ Autoajuste: no se ha ejecutado esta semana."]
    x = sem[-1]
    c = x.get("cambio")
    if c:
        return ["⚙️ Autoajuste", f"• CAMBIO: {c['parametro']} {c['antes']} → {c['despues']} "
                                 f"(en el tramo que no había visto: PF {c['comprobacion']['pf']:.2f})"]
    return ["⚙️ Autoajuste", f"• Sin cambios: {x.get('motivo', '')}"]


def informe(ahora: datetime | None = None) -> str:
    ahora = ahora or datetime.now(timezone.utc)
    desde = ahora - timedelta(days=7)
    revisar: list[str] = []
    lin = [f"📊 Informe semanal del Consejo · {desde:%d/%m} – {ahora:%d/%m/%Y} (todo en papel)", ""]
    lin += bloque_cripto(desde, revisar) + [""] + bloque_memes(desde) + [""] + bloque_orb() + [""]
    lin += bloque_autoajuste(desde) + [""]
    av = avisos.registro(desde)
    errores = [x for x in av if x["tipo"] in ("error", "datos", "parada")]
    lin.append(f"🔔 Avisos de la semana: {len(av)} ({len(errores)} de errores o datos)")
    for x in errores[-5:]:
        lin.append(f"• {x['t'][:16].replace('T', ' ')} · {x['origen']}: {x['texto'][:120]}")
    if len(errores) >= 3:
        revisar.append(f"Ha habido {len(errores)} avisos de errores o datos esta semana: conviene mirarlos.")
    lin += ["", "👉 Qué toca revisar"] + ([f"• {x}" for x in revisar] or ["• Nada urgente. Seguimos acumulando semanas de papel."])
    return "\n".join(lin)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--telegram", nargs="?", const=os.path.join(RAIZ, "telegram.json"))
    a = ap.parse_args(argv)
    texto = informe()
    os.makedirs(SALIDA, exist_ok=True)
    with open(os.path.join(SALIDA, f"informe_semanal_{datetime.now():%Y-%m-%d}.md"), "w", encoding="utf-8") as f:
        f.write(texto)
    print(texto)
    if a.telegram:
        from consejo.telegram import Grupo
        Grupo(a.telegram).enviar("skeptic", texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
