"""🔭 SCOUT — vigila las velas y detecta movimientos grandes antes que nadie.

Busca tres cosas en la última vela cerrada:
  1. Volumen anormal (z-score frente a las 50 velas anteriores).
  2. Vela grande (rango medido en ATRs).
  3. Ruptura del máximo/mínimo de las 20 velas anteriores, mejor si viene de una compresión.
"""
from ..models import ScoutReport


class Scout:
    name, emoji = "Scout", "🔭"

    def __init__(self, cfg: dict):
        self.min_vol_z = cfg.get("min_vol_z", 2.0)
        self.min_range_atr = cfg.get("min_range_atr", 1.6)
        self.min_strength = cfg.get("min_strength", 45)

    def analyze(self, df) -> ScoutReport:
        row = df.iloc[-1]
        reasons, strength = [], 0.0
        up = row["close"] > row["open"]

        if row["vol_z"] >= self.min_vol_z:
            strength += min(35, 10 * row["vol_z"])
            reasons.append(f"volumen {row['vol_z']:.1f}σ por encima de lo normal")
        if row["range_atr"] >= self.min_range_atr:
            strength += min(30, 12 * row["range_atr"])
            reasons.append(f"vela de {row['range_atr']:.1f} ATR ({row['body_pct']:+.2f}%)")

        breakout = ""
        if row["close"] > row["hh20"]:
            breakout = "long"
            strength += 20
            reasons.append(f"rompe el máximo de 20 velas ({row['hh20']:.2f})")
        elif row["close"] < row["ll20"]:
            breakout = "short"
            strength += 20
            reasons.append(f"rompe el mínimo de 20 velas ({row['ll20']:.2f})")
        if breakout and row["bbw_pct"] < 0.25:
            strength += 15
            reasons.append("sale de una compresión de volatilidad")

        direction = breakout or ("long" if up else "short")
        # la dirección de la vela tiene que coincidir con la ruptura
        if breakout and (breakout == "long") != up:
            strength -= 25
            reasons.append("ojo: la vela cierra en contra de la ruptura")

        strength = max(0.0, min(100.0, strength))
        has_trigger = row["vol_z"] >= self.min_vol_z or bool(breakout)
        alert = has_trigger and strength >= self.min_strength
        return ScoutReport(
            alert=alert, direction=direction, strength=round(strength, 1), reasons=reasons,
            metrics={"close": float(row["close"]), "vol_z": float(row["vol_z"]),
                     "range_atr": float(row["range_atr"]), "body_pct": float(row["body_pct"])},
        )

    def message(self, symbol: str, tf: str, r: ScoutReport) -> str:
        arrow = "🟢 al alza" if r.direction == "long" else "🔴 a la baja"
        lines = [f"<b>{symbol} {tf}</b> — movimiento grande {arrow} (fuerza {r.strength:.0f}/100)"]
        lines += [f"• {x}" for x in r.reasons]
        lines.append(f"Precio: {r.metrics['close']:.2f}. ¿Noticias, por qué se mueve?")
        return "\n".join(lines)
