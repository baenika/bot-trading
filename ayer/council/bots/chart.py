"""📈 CHART — elige entrada, stop loss (SL) y objetivo (TP) en la gráfica.

  • Entrada: precio de cierre (a mercado en la siguiente vela).
  • SL: detrás del último mínimo/máximo de 5 velas, con margen de 0,2 ATR,
    y nunca más cerca de 1 ATR ni más lejos de 3 ATR.
  • TP: a R:R fijo (por defecto 2:1), y marca si hay una resistencia/soporte antes.
  • Entrada en retroceso: precio mejor (EMA20 o 0,75 ATR) por si el Skeptic dice ESPERAR.
"""
from ..models import Setup


class ChartBot:
    name, emoji = "Chart", "📈"

    def __init__(self, cfg: dict):
        self.rr_target = cfg.get("rr_target", 2.0)
        self.min_sl_atr = cfg.get("min_sl_atr", 1.0)
        self.max_sl_atr = cfg.get("max_sl_atr", 3.0)
        self.swing_bars = cfg.get("swing_bars", 5)
        self.level_lookback = cfg.get("level_lookback", 150)

    def _pivots(self, s, high: bool, w: int = 5):
        """Máximos/mínimos de giro (el punto más alto/bajo de su ventana de ±w velas)."""
        seg = s.iloc[-self.level_lookback:-w]
        roll = s.iloc[-self.level_lookback - w:].rolling(2 * w + 1, center=True)
        ext = (roll.max() if high else roll.min()).reindex(seg.index)
        return seg[seg == ext]

    def plan(self, df, direction: str) -> Setup:
        row = df.iloc[-1]
        entry, a = float(row["close"]), float(row["atr"])
        recent = df.iloc[-self.swing_bars:]
        notes = []
        if direction == "long":
            raw_sl = float(recent["low"].min()) - 0.2 * a
            dist = min(max(entry - raw_sl, self.min_sl_atr * a), self.max_sl_atr * a)
            sl = entry - dist
            tp = entry + self.rr_target * dist
            piv = self._pivots(df["high"], high=True)
            above = piv[piv > entry * 1.002]
            level = float(above.min()) if len(above) else None
            pull = max(float(row["ema20"]), entry - 0.75 * a)
            pull = min(pull, entry - 0.25 * a)
        else:
            raw_sl = float(recent["high"].max()) + 0.2 * a
            dist = min(max(raw_sl - entry, self.min_sl_atr * a), self.max_sl_atr * a)
            sl = entry + dist
            tp = entry - self.rr_target * dist
            piv = self._pivots(df["low"], high=False)
            below = piv[piv < entry * 0.998]
            level = float(below.max()) if len(below) else None
            pull = min(float(row["ema20"]), entry + 0.75 * a)
            pull = max(pull, entry + 0.25 * a)

        rr_to_level = None
        if level is not None:
            rr_to_level = abs(level - entry) / dist
            if rr_to_level < self.rr_target:
                kind = "resistencia" if direction == "long" else "soporte"
                notes.append(f"hay {kind} en {level:.2f} antes del objetivo (R:R {rr_to_level:.1f})")
        else:
            notes.append("sin niveles cercanos en el camino: espacio libre")

        return Setup(direction=direction, entry=entry, sl=sl, tp=tp, rr=self.rr_target,
                     risk_pct=dist / entry * 100, resistance=level, rr_to_level=rr_to_level,
                     pullback_entry=pull, notes=notes)

    def message(self, s: Setup) -> str:
        side = "LONG" if s.direction == "long" else "SHORT"
        lines = [f"Plan <b>{side}</b>",
                 f"Entrada: {s.entry:.2f}",
                 f"Stop loss: {s.sl:.2f} (−{s.risk_pct:.2f}%)",
                 f"Take profit: {s.tp:.2f} (R:R {s.rr:.1f})",
                 f"Entrada mejor si retrocede: {s.pullback_entry:.2f}"]
        lines += [f"• {n}" for n in s.notes]
        return "\n".join(lines)
