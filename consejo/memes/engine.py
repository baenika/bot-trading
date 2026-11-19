"""Motor de la Mesa Memecoins. El MISMO código se usa en vivo (papel) y en el backtest sobre
grabaciones, para que el backtest pruebe exactamente lo que se ejecutaría."""
from collections import Counter
from datetime import datetime, timezone

from .broker import MemeBroker
from .desk import MemeDesk


def _plain(t):
    return t[:400]


def _ts(x):
    return datetime.fromisoformat(str(x).replace("Z", "+00:00"))


class MemeEngine:
    def __init__(self, params: dict, equity: float):
        self.desk = MemeDesk(params)
        self.p = self.desk.p
        self.broker = MemeBroker(equity, self.p)
        self.kills = Counter()          # monedas distintas descartadas por cada regla
        self._killed = set()
        self.decisions = Counter()
        self.last_review = {}
        self.miss = Counter()
        self.curve = []
        self.last_decision = None
        self.stopped = False
        self.last_msgs = None

    def step(self, ts, pairs, get_safety, seen_mints=None, review_every_min=15):
        """ts: momento del ciclo · pairs: lista de pares normalizados · get_safety(mint) -> dict
        Devuelve (eventos, debates): eventos = aperturas/cierres; debates = revisiones a contar al grupo."""
        b, events, debates = self.broker, [], []
        now = _ts(ts)
        b.new_day(ts)
        by_mint = {c["mint"]: c for c in pairs}

        # 1) posiciones abiertas: stop, trailing, rug, tiempo
        for mint in list(b.positions):
            c = by_mint.get(mint)
            if c is None or c.get("price") is None:
                self.miss[mint] += 1
                if self.miss[mint] >= 5:
                    t = b.vanished(mint, ts)
                    if t:
                        events.append(("close", t))
                continue
            self.miss[mint] = 0
            mins = (now - _ts(b.positions[mint]["opened"])).total_seconds() / 60
            t = b.update(mint, c["price"], c.get("liq"), ts, mins)
            if t:
                events.append(("close", t))

        # 2) candidatos nuevos
        for c in pairs:
            m = c["mint"]
            if m in b.positions or not self.desk.scout(c):
                continue
            last = self.last_review.get(m)
            if last and (now - last).total_seconds() < review_every_min * 60:
                continue
            self.last_review[m] = now
            safety = get_safety(m) if self.desk.cheap_ok(c) else None
            rv = self.desk.review(c, safety)
            self.decisions[rv.decision] += 1
            labels = list(rv.kills)
            if rv.fomo and not rv.kills:
                labels.append("FOMO (Sentimental)")
            if not rv.kills and not rv.fomo and rv.decision == "PASAR":
                labels.append(f"puntuación < {self.p['min_score']}")
            for k in labels:
                if (k, m) not in self._killed:
                    self._killed.add((k, m))
                    self.kills[k] += 1
            if rv.kills:
                continue
            size = None
            if rv.decision == "COMPRAR":
                ok, why = b.can_open()
                if self.stopped:
                    ok, why = False, "bot parado con /stop"
                if ok:
                    size = min(self.desk.size(b.equity(), c), b.cash)
                    pos = b.open(c, size, ts, {"score": rv.score})
                    if pos:
                        events.append(("open", pos))
                else:
                    rv.reasons.append(f"🛡️ Risk: {why}")
            self.last_decision = {"time": str(ts)[:16], "symbol": "$" + str(c.get("symbol")),
                                  "decision": rv.decision, "score": rv.score, "dir": "long",
                                  "pros": [r[2:] for r in rv.reasons if r.startswith("➕")],
                                  "cons": rv.fomo, "vetoes": [r[2:] for r in rv.reasons if r.startswith("⛔")],
                                  "entry": c.get("price"), "sl": c["price"] * (1 - self.p["stop_pct"] / 100),
                                  "tp": c["price"] * (1 + self.p["trail_start_pct"] / 100),
                                  "wait_price": None, "temperature": 85 if rv.fomo else 40}
            debates.append((rv, size))
            self.last_msgs = {"time": str(ts)[:16], "msgs": self.desk.messages(rv, size)}

        self.curve.append([str(ts), b.equity()])
        return events, debates

    def stats(self):
        t = self.broker.trades
        s = {"trades": len(t)}
        if self.curve:
            peak, mdd = self.curve[0][1], 0.0
            for _, e in self.curve:
                peak = max(peak, e)
                mdd = min(mdd, e / peak - 1)
            s["max_dd_pct"] = mdd * 100
            s["return_pct"] = (self.curve[-1][1] / self.broker.start - 1) * 100
        if t:
            w = [x for x in t if x["pnl"] > 0]
            l = [x for x in t if x["pnl"] <= 0]
            s["win_rate"] = len(w) / len(t) * 100
            s["avg_R"] = sum(x["R"] for x in t) / len(t)
            gl = abs(sum(x["pnl"] for x in l))
            s["profit_factor"] = sum(x["pnl"] for x in w) / gl if gl else None
            s["rugs"] = sum(1 for x in t if x["reason"].startswith("rug"))
        return s

    def snapshot(self, source, symbols_label, bot_log=None, period=None):
        """Mismo formato que el panel de la Mesa Cripto, más los descartes por regla."""
        b = self.broker
        thin = self.curve[:: max(1, len(self.curve) // 600)] + self.curve[-1:]
        daily = {}
        for t in b.trades:
            daily[t["closed"][:10]] = daily.get(t["closed"][:10], 0) + t["pnl"]
        today = (self.curve[-1][0][:10] if self.curve else "")
        return {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": source,
            "desk": "memes", "mode": "paper", "stopped": self.stopped, "symbols": [symbols_label],
            "timeframe": "on-chain", "period": period, "start_equity": b.start, "equity": b.equity(),
            "peak": b.peak, "day_start": b.day_start, "today_pnl": daily.get(today, 0.0),
            "curve": [[str(t)[:16], round(e, 2)] for t, e in thin], "compare_curve": None,
            "daily_pnl": [[d, round(v, 2)] for d, v in sorted(daily.items())[-14:]],
            "positions": [{"symbol": "$" + str(p["symbol"]), "dir": "long", "entry": p["ref"],
                           "sl": p["ref"] * (1 - self.p["stop_pct"] / 100),
                           "tp": p["ref"] * (1 + self.p["trail_start_pct"] / 100), "qty": p["qty"],
                           "price": b.last_px.get(m), "pnl": p["qty"] * b.last_px.get(m, p["ref"]) - p["usd"],
                           "opened": p["opened"]} for m, p in b.positions.items()],
            "trades": [{k: t[k] for k in ("symbol", "dir", "kind", "opened", "closed", "entry", "exit", "pnl",
                                          "R", "reason")} for t in b.trades[-30:]][::-1],
            "stats": self.stats(),
            "limits": {"risk_per_trade_pct": self.p["risk_cap_pct"], "daily_loss_limit_pct": self.p["daily_loss_pct"],
                       "max_drawdown_pct": self.p["max_drawdown_pct"], "max_open_positions": self.p["max_open"]},
            "bots": bot_log or ({k: {"time": self.last_msgs["time"], "text": _plain(t)} for k, t in self.last_msgs["msgs"]}
                                if self.last_msgs else {}), "last_decision": self.last_decision, "decisions": dict(self.decisions),
            "kills": dict(self.kills.most_common(12)), "skeptic_saved": None,
        }
