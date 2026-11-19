"""Bróker de PAPEL para memecoins. Pesimista a propósito:

  • Impacto en el precio según la liquidez del pool (comprar 1.000 $ en un pool de 40k mueve el precio).
  • Comisión por lado (swap + prioridad de Solana).
  • Si el precio salta por debajo del stop, se sale al precio real, no al del stop.
  • Si la liquidez cae más de un 70% o el par desaparece, se trata como rug: se sale con lo que quede.
"""
import json
from datetime import datetime, timezone


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemeBroker:
    def __init__(self, equity, p):
        self.cash = equity
        self.start = equity
        self.peak = equity
        self.day_start, self.day = equity, None
        self.p = p
        self.positions, self.trades = {}, []
        self.last_px = {}

    def _impact(self, usd, liq):
        if not liq or liq <= 0:
            return 0.9
        return min(0.9, usd / (liq / 2))

    def equity(self):
        return self.cash + sum(pos["qty"] * self.last_px.get(m, pos["ref"]) * (1 - self._impact(
            pos["qty"] * self.last_px.get(m, pos["ref"]), pos.get("liq_now", pos["liq0"]))) for m, pos in self.positions.items())

    def new_day(self, ts):
        d = str(ts)[:10]
        if d != self.day:
            self.day, self.day_start = d, self.equity()

    def can_open(self):
        p, eq = self.p, self.equity()
        if eq <= self.peak * (1 - p["max_drawdown_pct"] / 100):
            return False, f"interruptor: caída > {p['max_drawdown_pct']}%"
        if eq <= self.day_start * (1 - p["daily_loss_pct"] / 100):
            return False, f"límite diario ({p['daily_loss_pct']}%)"
        if len(self.positions) >= p["max_open"]:
            return False, f"ya hay {p['max_open']} posiciones"
        return True, ""

    def open(self, c, usd, ts=None, meta=None):
        if c["mint"] in self.positions or usd <= 0 or usd > self.cash:
            return None
        imp = self._impact(usd, c["liq"])
        fill = c["price"] * (1 + imp) * (1 + self.p["fee_pct"] / 100)
        qty = usd / fill
        self.cash -= usd
        pos = {"mint": c["mint"], "symbol": c["symbol"], "ref": c["price"], "fill": fill, "qty": qty,
               "usd": usd, "peak": c["price"], "liq0": c["liq"], "liq_now": c["liq"], "opened": str(ts or now_iso()),
               "last_seen": str(ts or now_iso()), "meta": meta or {}}
        self.positions[c["mint"]] = pos
        self.last_px[c["mint"]] = c["price"]
        return pos

    def _close(self, mint, price, liq, ts, reason):
        pos = self.positions.pop(mint)
        value = pos["qty"] * price
        imp = self._impact(value, liq)
        proceeds = value * (1 - imp) * (1 - self.p["fee_pct"] / 100)
        self.cash += proceeds
        pnl = proceeds - pos["usd"]
        t = {"symbol": "$" + str(pos["symbol"]), "mint": mint, "dir": "long", "kind": "papel",
             "opened": pos["opened"], "closed": str(ts or now_iso()), "entry": pos["ref"], "exit": price,
             "usd": pos["usd"], "pnl": pnl, "pnl_pct": pnl / pos["usd"] * 100,
             "R": pnl / (pos["usd"] * self.p["stop_pct"] / 100), "reason": reason,
             "score": pos["meta"].get("score")}
        self.trades.append(t)
        self.peak = max(self.peak, self.equity())
        return t

    def update(self, mint, price, liq, ts=None, minutes_open=None):
        """Llamar con cada nuevo precio. Devuelve el trade si se ha cerrado."""
        pos = self.positions.get(mint)
        if not pos:
            return None
        p = self.p
        if price is None:
            return None
        self.last_px[mint] = price
        pos["liq_now"] = liq or pos["liq_now"]
        pos["last_seen"] = str(ts or now_iso())
        pos["peak"] = max(pos["peak"], price)
        if liq is not None and pos["liq0"] and liq < pos["liq0"] * 0.3:
            return self._close(mint, price, liq, ts, "rug: la liquidez se ha ido")
        if price <= pos["ref"] * (1 - p["stop_pct"] / 100):
            return self._close(mint, price, liq, ts, f"stop −{p['stop_pct']}%")
        if pos["peak"] >= pos["ref"] * (1 + p["trail_start_pct"] / 100) and price <= pos["peak"] * (1 - p["trail_pct"] / 100):
            return self._close(mint, price, liq, ts, "trailing stop")
        if minutes_open is not None and minutes_open >= p["max_hold_min"]:
            return self._close(mint, price, liq, ts, "tiempo máximo")
        return None

    def vanished(self, mint, ts=None):
        """El par ya no aparece: se asume rug y se recupera un 10%."""
        pos = self.positions.get(mint)
        if pos:
            return self._close(mint, pos["ref"] * 0.1, pos["liq0"], ts, "rug: el par ha desaparecido")

    # ---- persistencia ----
    def to_json(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({k: getattr(self, k) for k in ("cash", "start", "peak", "day_start", "day", "positions",
                                                     "trades", "last_px")}, f, default=str)

    def load_json(self, path):
        with open(path, encoding="utf-8") as f:
            for k, v in json.load(f).items():
                setattr(self, k, v)
