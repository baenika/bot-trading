"""Genera dashboard.json: todo lo que muestra el panel web (capital, curva, bots, Skeptic, riesgo)."""
import json
import os
from collections import Counter
from datetime import datetime, timezone

BOTS = ["scout", "news", "chart", "sentimental", "skeptic", "risk"]


def _strip(t: str) -> str:
    for tag in ("<b>", "</b>", "<i>", "</i>"):
        t = t.replace(tag, "")
    return t


def _stats(trades, curve, start_equity):
    s = {"trades": len(trades)}
    if curve:
        eqs = [e for _, e in curve]
        peak, mdd = eqs[0], 0.0
        for e in eqs:
            peak = max(peak, e)
            mdd = min(mdd, e / peak - 1)
        s["max_dd_pct"] = mdd * 100
        s["return_pct"] = (eqs[-1] / start_equity - 1) * 100
    if trades:
        wins = [t for t in trades if t["pnl"] > 0]
        loss = [t for t in trades if t["pnl"] <= 0]
        s["win_rate"] = len(wins) / len(trades) * 100
        s["avg_R"] = sum(t["R"] for t in trades) / len(trades)
        gl = abs(sum(t["pnl"] for t in loss))
        s["profit_factor"] = (sum(t["pnl"] for t in wins) / gl) if gl else None
    return s


def _daily(trades, days=14):
    agg = {}
    for t in trades:
        d = str(t["closed"])[:10]
        agg[d] = agg.get(d, 0.0) + t["pnl"]
    return [[d, round(v, 2)] for d, v in sorted(agg.items())[-days:]]


def build(broker, cfg, *, source, curve, bot_log=None, last_decision=None, decisions=None,
          compare_curve=None, skeptic_saved=None, prices=None, stopped=False):
    prices = prices or {}
    acc, risk = cfg["account"], cfg.get("bots", {}).get("risk", {})
    start = acc["starting_equity"]
    equity = broker.mark_to_market(prices) if prices else broker.equity
    positions = []
    for p in broker.positions:
        px = prices.get(p["symbol"], p["entry"])
        pnl = (1 if p["dir"] == "long" else -1) * (px - p["entry"]) * p["qty"]
        positions.append({"symbol": p["symbol"], "dir": p["dir"], "entry": p["entry"], "sl": p["sl"],
                          "tp": p["tp"], "qty": p["qty"], "price": px, "pnl": pnl, "opened": p["opened"]})
    for p in broker.pending:
        positions.append({"symbol": p["symbol"], "dir": p["setup"].direction, "entry": p["limit"],
                          "sl": p["setup"].sl, "tp": p["setup"].tp, "qty": p["qty"], "price": None,
                          "pnl": None, "opened": "esperando retroceso"})

    def thin(c, n=600):
        step = max(1, len(c) // n)
        out = c[::step]
        if c and out[-1] != c[-1]:
            out.append(c[-1])
        return [[str(t)[:16], round(float(e), 2)] for t, e in out]

    today = datetime.now(timezone.utc).date().isoformat()
    today_pnl = sum(t["pnl"] for t in broker.trades if str(t["closed"])[:10] == today)
    snap = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source, "mode": cfg.get("mode", "paper"), "stopped": stopped,
        "symbols": cfg.get("symbols", []), "timeframe": cfg.get("timeframe", "1h"),
        "start_equity": start, "equity": equity, "peak": broker.peak, "day_start": broker.day_start,
        "today_pnl": today_pnl,
        "curve": thin(curve), "compare_curve": thin(compare_curve) if compare_curve else None,
        "daily_pnl": _daily(broker.trades),
        "positions": positions,
        "trades": [{k: t[k] for k in ("symbol", "dir", "kind", "opened", "closed", "entry", "exit",
                                      "pnl", "R", "reason")} for t in broker.trades[-30:]][::-1],
        "stats": _stats(broker.trades, curve, start),
        "limits": {"risk_per_trade_pct": risk.get("risk_per_trade_pct", 1.0),
                   "daily_loss_limit_pct": risk.get("daily_loss_limit_pct", 3.0),
                   "max_drawdown_pct": risk.get("max_drawdown_pct", 15.0),
                   "max_open_positions": risk.get("max_open_positions", 3)},
        "bots": bot_log or {},
        "last_decision": last_decision,
        "decisions": dict(Counter(decisions or [])),
        "skeptic_saved": skeptic_saved,
    }
    return snap


def write(snap, path="dashboard.json"):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snap, f, default=str)
    os.replace(tmp, path)


def decision_payload(symbol, ts, res):
    v, s = res["verdict"], res["setup"]
    return {"time": str(ts)[:16], "symbol": symbol, "decision": v.decision, "score": v.score,
            "pros": v.pros, "cons": v.cons, "vetoes": v.vetoes, "dir": s.direction,
            "entry": s.entry, "sl": s.sl, "tp": s.tp, "wait_price": v.wait_price,
            "temperature": res["senti"].temperature, "sim": res["senti"].sim}


def log_messages(bot_log, ts, msgs):
    for bot, text in msgs:
        bot_log[bot] = {"time": str(ts)[:16], "text": _strip(text)[:400]}
