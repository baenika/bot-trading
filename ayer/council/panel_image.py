"""Dibuja el panel en una imagen PNG (tema oscuro) para mandarla por Telegram."""
import io
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

BG, CARD, LINE, INK, MUTED = "#0a0a0c", "#141417", "#232328", "#f3f3f5", "#7c7c86"
ACC, UP, DOWN, WAIT = "#f05a28", "#34d27f", "#ff5d5d", "#f2b14c"


def _card(fig, x, y, w, h, color=CARD):
    fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.018",
                                      transform=fig.transFigure, facecolor=color, edgecolor=LINE, lw=0.8, zorder=-10))


def _fmt(v, d=2, suf=""):
    if v is None:
        return "—"
    s = f"{v:+,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s + suf


def _money(v):
    return "$" + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def render(snap: dict) -> bytes:
    s = snap.get("stats", {})
    eq, start = snap["equity"], snap["start_equity"]
    ret = (eq / start - 1) * 100
    fig = plt.figure(figsize=(8, 10), dpi=140, facecolor=BG)
    T = lambda x, y, t, **k: fig.text(x, y, t, color=k.pop("c", INK), family="DejaVu Sans", **k)

    # cabecera
    src = "EN VIVO" if snap.get("source") == "live" else "BACKTEST"
    T(0.05, 0.955, "Consejo de Trading", size=17, weight="bold")
    T(0.05, 0.93, f"{', '.join(snap.get('symbols', []))} · {snap.get('timeframe', '')} · {src} · PAPER",
      size=9, c=MUTED)
    if snap.get("stopped"):
        T(0.95, 0.955, "⛔ PARADO", size=11, weight="bold", c=DOWN, ha="right")

    # capital
    _card(fig, 0.05, 0.775, 0.9, 0.135)
    T(0.08, 0.88, "Capital total", size=9, c=MUTED)
    T(0.08, 0.815, _money(eq), size=28, weight="bold")
    T(0.92, 0.835, _fmt(ret, 2, "%"), size=18, weight="bold", c=UP if ret >= 0 else DOWN, ha="right")
    sub = (f"Hoy {_fmt(snap.get('today_pnl', 0))} $" if snap.get("source") == "live"
           else f"Inicial {_money(start)}")
    T(0.92, 0.80, sub, size=9, c=MUTED, ha="right")

    # KPIs
    kp = [("Rentabilidad", _fmt(s.get("return_pct", ret), 1, "%"), True),
          ("Acierto", f"{s['win_rate']:.0f}%" if s.get("win_rate") is not None else "—", False),
          ("Profit factor", f"{s['profit_factor']:.2f}" if s.get("profit_factor") else "—", False),
          ("Drawdown máx.", _fmt(s.get("max_dd_pct"), 1, "%"), False)]
    for i, (k, v, hot) in enumerate(kp):
        x = 0.05 + i * 0.2275
        _card(fig, x, 0.665, 0.2125, 0.095, ACC if hot else CARD)
        T(x + 0.02, 0.732, k, size=8, c="#ffe3d6" if hot else MUTED)
        T(x + 0.02, 0.682, v, size=15, weight="bold")

    # curva
    _card(fig, 0.05, 0.375, 0.9, 0.275)
    T(0.08, 0.625, "Curva de capital", size=11, weight="bold")
    ax = fig.add_axes([0.1, 0.405, 0.82, 0.2], facecolor=CARD, zorder=5)
    curve = snap.get("curve") or []
    if curve:
        ys = [c[1] for c in curve]
        ax.fill_between(range(len(ys)), ys, min(ys + [start]), color=ACC, alpha=0.12, lw=0)
        ax.plot(ys, color=ACC, lw=2)
        if snap.get("compare_curve"):
            ax.plot([c[1] for c in snap["compare_curve"]], color="#9a9aa4", lw=1, ls="--")
        ax.axhline(start, color="#55555e", lw=0.8, ls=":")
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=7, length=0)
    ax.set_xticks([])
    ax.grid(axis="y", color=LINE, lw=0.6)

    # veredicto
    _card(fig, 0.05, 0.155, 0.9, 0.205)
    T(0.08, 0.333, "Último veredicto del Skeptic", size=11, weight="bold")
    v = snap.get("last_decision")
    if v:
        col = {"COMPRAR": UP, "VENDER": UP, "ESPERAR": WAIT, "PASAR": DOWN}.get(v["decision"], INK)
        T(0.08, 0.29, v["decision"], size=18, weight="bold", c=col)
        T(0.33, 0.297, f"{v['symbol']} · {'largo' if v['dir'] == 'long' else 'corto'} · "
                       f"puntuación {_fmt(v['score'], 1)}", size=9)
        T(0.33, 0.28, str(v["time"]).replace("T", " "), size=8, c=MUTED)
        lines = [("⛔ " if True else "") + x for x in v.get("vetoes", [])[:2]] + \
                ["+ " + x for x in v.get("pros", [])[:2]] + ["– " + x for x in v.get("cons", [])[:1]]
        for i, ln in enumerate(lines[:4]):
            T(0.08, 0.25 - i * 0.022, ln.replace("⛔ ", "✕ ")[:78], size=8.5, c="#c2c2ca")
    else:
        T(0.08, 0.29, "Sin alertas todavía.", size=10, c=MUTED)

    # riesgo + embudo
    _card(fig, 0.05, 0.04, 0.44, 0.1)
    L = snap.get("limits", {})
    pos = len(snap.get("positions", []))
    T(0.08, 0.115, "Riesgo", size=10, weight="bold")
    T(0.08, 0.085, f"Posiciones {pos}/{L.get('max_open_positions', 3)}", size=8.5, c="#c2c2ca")
    T(0.08, 0.063, f"Riesgo por trade {L.get('risk_per_trade_pct', 1)}%", size=8.5, c="#c2c2ca")
    _card(fig, 0.51, 0.04, 0.44, 0.1)
    D = snap.get("decisions", {})
    T(0.54, 0.115, "Embudo del Skeptic", size=10, weight="bold")
    T(0.54, 0.085, f"Alertas {sum(D.values())} · Pasar {D.get('PASAR', 0)}", size=8.5, c="#c2c2ca")
    T(0.54, 0.063, f"Esperar {D.get('ESPERAR', 0)} · Comprar {D.get('COMPRAR', 0) + D.get('VENDER', 0)}",
      size=8.5, c="#c2c2ca")
    T(0.95, 0.015, datetime.now().strftime("%d/%m %H:%M"), size=7, c=MUTED, ha="right")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


def caption(snap: dict) -> str:
    s = snap.get("stats", {})
    ret = (snap["equity"] / snap["start_equity"] - 1) * 100
    v = snap.get("last_decision")
    txt = (f"<b>Panel del consejo</b> ({'en vivo' if snap.get('source') == 'live' else 'backtest'})\n"
           f"Capital: <b>{_money(snap['equity'])}</b> ({_fmt(ret, 2, '%')})\n"
           f"Operaciones: {s.get('trades', 0)} · acierto "
           f"{(str(round(s['win_rate'])) + '%') if s.get('win_rate') is not None else '—'}\n"
           f"Posiciones abiertas: {len(snap.get('positions', []))}")
    if v:
        txt += f"\nÚltimo veredicto: <b>{v['decision']}</b> en {v['symbol']}"
    if snap.get("stopped"):
        txt += "\n⛔ <b>Bot parado</b> (usa /reanudar)"
    return txt
