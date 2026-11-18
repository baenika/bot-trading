"""Bróker de PAPEL (dinero ficticio). Lo usan el backtest y el modo en vivo simulado.

Reglas conservadoras para no engañarse:
  • La entrada a mercado se hace en la APERTURA de la vela siguiente, con deslizamiento.
  • Si en una misma vela se tocan el SL y el TP, se asume que saltó el SL.
  • Comisiones en la entrada y en la salida.
"""
import json


class PaperBroker:
    def __init__(self, equity: float, fee_pct: float = 0.1, slippage_pct: float = 0.05,
                 max_bars_in_trade: int = 48, wait_bars: int = 6):
        self.equity = equity
        self.peak = equity
        self.day_start = equity
        self.day = None
        self.fee = fee_pct / 100
        self.slip = slippage_pct / 100
        self.max_bars = max_bars_in_trade
        self.wait_bars = wait_bars
        self.positions, self.pending, self.queued, self.trades = [], [], [], []

    # ---------- estado ----------
    def new_day_check(self, ts):
        d = ts.date() if hasattr(ts, "date") else ts
        if d != self.day:
            self.day, self.day_start = d, self.equity

    def loss_streak(self) -> int:
        n = 0
        for t in reversed(self.trades):
            if t["pnl"] < 0:
                n += 1
            else:
                break
        return n

    def open_list(self):
        return self.positions + self.pending + self.queued

    # ---------- órdenes ----------
    def queue_market(self, symbol, setup, qty, risk_amount, meta=None):
        """Entrar a mercado en la apertura de la próxima vela."""
        self.queued.append({"symbol": symbol, "setup": setup, "qty": qty, "risk": risk_amount,
                            "meta": meta or {}})

    def place_limit(self, symbol, setup, limit, qty, risk_amount, meta=None):
        self.pending.append({"symbol": symbol, "setup": setup, "limit": limit, "qty": qty,
                             "risk": risk_amount, "bars": 0, "meta": meta or {}})

    def _open(self, symbol, setup, price, qty, risk, ts, meta, kind):
        fee = qty * price * self.fee
        self.equity -= fee
        pos = {"symbol": symbol, "dir": setup.direction, "entry": price, "sl": setup.sl, "tp": setup.tp,
               "qty": qty, "risk": risk, "fees": fee, "opened": str(ts), "bars": 0, "kind": kind,
               "meta": meta}
        self.positions.append(pos)
        return {"type": "open", "pos": pos}

    def _close(self, pos, price, ts, reason):
        sign = 1 if pos["dir"] == "long" else -1
        fee = pos["qty"] * price * self.fee
        gross = sign * (price - pos["entry"]) * pos["qty"]
        self.equity += gross - fee
        pnl = gross - fee - pos["fees"]
        trade = {"symbol": pos["symbol"], "dir": pos["dir"], "kind": pos["kind"], "opened": pos["opened"],
                 "closed": str(ts), "entry": pos["entry"], "exit": price, "sl": pos["sl"], "tp": pos["tp"],
                 "qty": pos["qty"], "pnl": pnl, "R": pnl / pos["risk"] if pos["risk"] else 0,
                 "reason": reason, "bars": pos["bars"], **{f"m_{k}": v for k, v in pos["meta"].items()}}
        self.trades.append(trade)
        self.positions.remove(pos)
        self.peak = max(self.peak, self.equity)
        return {"type": "close", "trade": trade}

    # ---------- procesar una vela ----------
    def on_bar(self, symbol, ts, o, h, l, c):
        ev = []
        for q in [q for q in self.queued if q["symbol"] == symbol]:
            self.queued.remove(q)
            s = q["setup"]
            price = o * (1 + self.slip) if s.direction == "long" else o * (1 - self.slip)
            ev.append(self._open(symbol, s, price, q["qty"], q["risk"], ts, q["meta"], "mercado"))

        for p in [p for p in self.pending if p["symbol"] == symbol]:
            s, lim = p["setup"], p["limit"]
            p["bars"] += 1
            long = s.direction == "long"
            touched = l <= lim if long else h >= lim
            ran_away = (h >= s.tp) if long else (l <= s.tp)
            if touched:
                self.pending.remove(p)
                price = min(o, lim) if long else max(o, lim)
                ev.append(self._open(symbol, s, price, p["qty"], p["risk"], ts, p["meta"], "retroceso"))
            elif ran_away or p["bars"] >= self.wait_bars:
                self.pending.remove(p)
                ev.append({"type": "cancel", "symbol": symbol,
                           "why": "se fue sin nosotros" if ran_away else "no volvió al precio"})

        for pos in [p for p in self.positions if p["symbol"] == symbol]:
            pos["bars"] += 1
            long = pos["dir"] == "long"
            hit_sl = l <= pos["sl"] if long else h >= pos["sl"]
            hit_tp = h >= pos["tp"] if long else l <= pos["tp"]
            if hit_sl:  # conservador: si toca los dos, gana el SL
                px = min(o, pos["sl"]) if long else max(o, pos["sl"])
                px = px * (1 - self.slip) if long else px * (1 + self.slip)
                ev.append(self._close(pos, px, ts, "stop loss"))
            elif hit_tp:
                ev.append(self._close(pos, pos["tp"], ts, "take profit"))
            elif pos["bars"] >= self.max_bars:
                ev.append(self._close(pos, c, ts, "tiempo máximo"))
        return ev

    def mark_to_market(self, prices: dict) -> float:
        eq = self.equity
        for p in self.positions:
            px = prices.get(p["symbol"], p["entry"])
            eq += (1 if p["dir"] == "long" else -1) * (px - p["entry"]) * p["qty"]
        return eq

    # ---------- persistencia (modo en vivo) ----------
    def to_json(self, path):
        from dataclasses import asdict
        def ser(x):
            x = dict(x)
            if "setup" in x:
                x["setup"] = asdict(x["setup"])
            return x
        with open(path, "w") as f:
            json.dump({"equity": self.equity, "peak": self.peak, "day_start": self.day_start,
                       "day": str(self.day), "positions": self.positions,
                       "pending": [ser(p) for p in self.pending], "queued": [ser(q) for q in self.queued],
                       "trades": self.trades}, f, indent=1, default=str)

    def load_json(self, path):
        from .models import Setup
        with open(path) as f:
            d = json.load(f)
        self.equity, self.peak, self.day_start = d["equity"], d["peak"], d["day_start"]
        self.positions, self.trades = d["positions"], d["trades"]
        self.pending = [{**p, "setup": Setup(**p["setup"])} for p in d["pending"]]
        self.queued = [{**q, "setup": Setup(**q["setup"])} for q in d["queued"]]
