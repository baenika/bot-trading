"""Análisis bajo pedido para Telegram: «/analizar», «/analizar oro», «/analizar btc nasdaq».

Mira la última vela CERRADA de cada activo y cuenta, en pocas líneas:
precio y cambio en 24 h, tendencia (medias de 20, 50 y 200 velas), RSI, volatilidad (ATR),
si el Scout ve algo raro ahora mismo y, si lo ve, el plan de Charts con la probabilidad de la
máquina del tiempo. También dice si el papel tiene una posición abierta en ese activo.

Solo lee precios públicos. No abre operaciones (ni siquiera en papel): eso lo hace la pasada normal.
"""

from __future__ import annotations

import unicodedata

from . import maquina_tiempo, mesas
from .agentes import charts, scout
from .indicadores import Contexto

# palabras que puede escribir David -> símbolo
ALIAS = {
    "btc": "BTCUSDT", "bitcoin": "BTCUSDT", "btcusdt": "BTCUSDT", "btcusd": "BTCUSDT",
    "eth": "ETHUSDT", "ethereum": "ETHUSDT", "ether": "ETHUSDT", "ethusdt": "ETHUSDT",
    "spy": "SPY", "sp500": "SPY", "s&p": "SPY", "s&p500": "SPY", "sp": "SPY",
    "qqq": "QQQ", "nasdaq": "QQQ", "nas100": "QQQ", "nq": "QQQ",
    "dia": "DIA", "dow": "DIA", "dowjones": "DIA",
    "iwm": "IWM", "russell": "IWM",
    "oro": "GC=F", "gold": "GC=F", "xau": "GC=F", "xauusd": "GC=F", "gld": "GLD",
    "plata": "SLV", "silver": "SLV", "slv": "SLV",
    "petroleo": "USO", "oil": "USO", "uso": "USO", "crudo": "USO",
    "bonos": "TLT", "tlt": "TLT",
    "eurusd": "EURUSD=X", "euro": "EURUSD=X", "eur": "EURUSD=X",
    "ibex": "^IBEX", "ibex35": "^IBEX", "dax": "^GDAXI",
}
POR_DEFECTO = ["BTCUSDT", "ETHUSDT", "SPY", "QQQ", "GC=F"]
MAX_ACTIVOS = 6


def _limpio(p: str) -> str:
    p = unicodedata.normalize("NFKD", p.lower()).encode("ascii", "ignore").decode()
    return p.strip(" ,.;/")


def resolver(texto: str) -> tuple[list[str], list[str]]:
    """Palabras -> (símbolos conocidos, palabras que no entiende). Sin palabras: la lista por defecto."""
    simbolos, raras = [], []
    for p in texto.replace(",", " ").split():
        k = _limpio(p)
        if not k:
            continue
        s = ALIAS.get(k) or next((x for x in list(mesas.CRIPTO) + list(mesas.MERCADOS) if x.lower() == k), None)
        if s is None:
            raras.append(p)
        elif s not in simbolos:
            simbolos.append(s)
    if not simbolos and not raras:
        simbolos = list(POR_DEFECTO)
    return simbolos[:MAX_ACTIVOS], raras


def nombre(s: str) -> str:
    return mesas.CRIPTO.get(s) or mesas.MERCADOS.get(s) or s


def _num(x: float) -> str:
    return f"{x:,.2f}" if x >= 100 else f"{x:.4g}"


def niveles(velas) -> str:
    """Máximo y mínimo de ayer, de la semana pasada y de 52 semanas (vídeo de Claude + TradingView)."""
    from datetime import timedelta
    v = velas[-1]
    hoy = v.t.date()
    ayer = [x for x in velas if x.t.date() == hoy - timedelta(days=1)]
    if not ayer:  # lunes o festivo: el último día con velas antes de hoy
        previas = [x for x in velas if x.t.date() < hoy]
        ayer = [x for x in previas if previas and x.t.date() == previas[-1].t.date()]
    lunes = hoy - timedelta(days=hoy.weekday())
    semana = [x for x in velas if lunes - timedelta(days=7) <= x.t.date() < lunes]
    anio = [x for x in velas if x.t.date() > hoy - timedelta(days=364)]
    dias = (hoy - velas[0].t.date()).days
    partes = []
    if ayer:
        partes.append(f"ayer {_num(min(x.l for x in ayer))} – {_num(max(x.h for x in ayer))}")
    if semana:
        partes.append(f"semana pasada {_num(min(x.l for x in semana))} – {_num(max(x.h for x in semana))}")
    alto = max(x.h for x in anio)
    partes.append(f"máximo de 52 semanas {_num(alto)}" if dias >= 360 else f"máximo de {dias} días {_num(alto)}")
    return "📏 Niveles: " + " · ".join(partes)


def ficha(s: str, velas, cfg: dict, estado: dict | None = None, caminos: int = 3000) -> str:
    """El análisis de un activo con sus velas cerradas (de más antigua a más reciente)."""
    if len(velas) < 210:
        return f"{nombre(s)}: no hay velas suficientes para analizar ({len(velas)})."
    ctx = Contexto(s, velas)
    i = len(velas) - 1
    v = velas[i]
    hace = velas[max(0, i - 24)]
    cambio = (v.c / hace.c - 1) * 100
    e20, e50, e200 = ctx.ema20[i], ctx.ema50[i], ctx.ema200[i]
    if v.c > e50 > e200:
        tend = "alcista 📈 (precio sobre las medias de 50 y 200)"
    elif v.c < e50 < e200:
        tend = "bajista 📉 (precio bajo las medias de 50 y 200)"
    else:
        tend = "lateral ↔️ (las medias no se ponen de acuerdo)"
    r = ctx.rsi[i]
    rsi_txt = "sobrecompra" if r >= 70 else "sobreventa" if r <= 30 else "neutral"
    atr_pct = ctx.atr[i] / v.c * 100 if v.c else 0
    alto, bajo = ctx.max_previo(i + 1, 20), ctx.min_previo(i + 1, 20)
    lin = [f"📊 {nombre(s)} ({s})",
           f"Precio {_num(v.c)} · vela de las {v.t:%d/%m %H:%M} UTC · {cambio:+.2f}% en 24 velas",
           f"Tendencia: {tend}",
           f"RSI {r:.0f} ({rsi_txt}) · ATR {atr_pct:.2f}% · rango de 20 velas {_num(bajo)} – {_num(alto)}",
           niveles(velas)]

    alerta = scout.escanear(ctx, i, cfg)
    if alerta is None:
        lin.append("🔭 Scout: nada raro en la última vela. No hay opción de compra ahora mismo.")
    else:
        plan = charts.planificar(ctx, alerta, cfg)
        sim = maquina_tiempo.simular(ctx.rend[max(1, i - 1000):i + 1], plan.entrada, plan.stop, plan.objetivo,
                                     plan.dir, horizonte=cfg.get("max_barras", 48), caminos=caminos, semilla=i)
        lin.append(alerta.texto(cfg.get("timeframe", "1h")))
        if alerta.dir == "long":
            lin.append(f"🟢 POSIBLE COMPRA (papel): entrada {_num(plan.entrada)} · stop {_num(plan.stop)} · "
                       f"objetivo {_num(plan.objetivo)} · R:R {plan.rr:.1f} · mejor si retrocede a "
                       f"{_num(plan.precio_espera)}")
        else:
            lin.append("🔴 Movimiento a la baja: no es una opción de compra (los cortos están desactivados).")
        if sim.get("paths") and alerta.dir == "long":
            lin.append(f"⏳ Máquina del tiempo ({sim['paths']} futuros): objetivo primero {sim['p_tp']:.0f}% · "
                       f"stop primero {sim['p_sl']:.0f}% (solo orientativo)")
        lin.append("La decisión final (Sentimental, Skeptic y Risk) la toma la pasada del Consejo.")

    estado = estado or {}
    p = (estado.get("abiertas") or {}).get(s)
    if p:
        lin.append(f"💼 En papel: posición abierta desde {_num(p['entry'])} · stop {_num(p['stop'])} · "
                   f"objetivo {_num(p['tp'])}")
    u = estado.get("ultima") or {}
    if u.get("symbol") == s:
        lin.append(f"🏛️ Última decisión del Consejo aquí: {u.get('decision')} ({str(u.get('time', ''))[:16]} UTC)")
    return "\n".join(lin)


def analizar(texto: str, cfg: dict, bajar, estado: dict | None = None) -> str:
    """`bajar(símbolo)` devuelve las velas cerradas. Un activo que falla no tumba a los demás."""
    simbolos, raras = resolver(texto)
    partes = []
    if raras:
        partes.append(f"No conozco: {', '.join(raras)}. Prueba btc, eth, oro, plata, nasdaq, sp500, dow, "
                      "ibex, dax, petroleo, eurusd o bonos.")
    for s in simbolos:
        try:
            partes.append(ficha(s, bajar(s), cfg, estado))
        except Exception as e:  # noqa: BLE001 - sin datos de uno, se sigue con el resto
            partes.append(f"{nombre(s)}: no llegan los precios ahora ({type(e).__name__}).")
    partes.append("Todo es análisis y papel: dinero ficticio, sin órdenes reales.")
    return "\n\n".join(partes)
