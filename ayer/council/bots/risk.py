"""🛡️ RISK MANAGER — cuánto se arriesga y cuándo se apaga todo.

  • Tamaño: arriesga un % fijo del capital por trade (por defecto 1%). Si el SL salta,
    se pierde exactamente eso, ni un euro más.
  • Sin apalancamiento: la posición nunca vale más que el capital disponible.
  • Límite diario: si hoy se pierde X%, no se abren más trades hasta mañana.
  • Interruptor de emergencia: si la caída desde el máximo supera Y%, se para todo.
  • Máximo de posiciones abiertas a la vez.
"""
from ..models import RiskDecision


class RiskManager:
    name, emoji = "Risk", "🛡️"

    def __init__(self, cfg: dict):
        self.risk_per_trade = cfg.get("risk_per_trade_pct", 1.0) / 100
        self.daily_loss_limit = cfg.get("daily_loss_limit_pct", 3.0) / 100
        self.max_drawdown = cfg.get("max_drawdown_pct", 15.0) / 100
        self.max_positions = cfg.get("max_open_positions", 3)
        self.max_leverage = cfg.get("max_leverage", 1.0)

    def evaluate(self, setup, equity, peak_equity, day_start_equity, open_positions,
                 symbol, entry_price=None) -> RiskDecision:
        reasons = []
        dd = 1 - equity / peak_equity if peak_equity > 0 else 0
        day = equity / day_start_equity - 1 if day_start_equity > 0 else 0
        if dd >= self.max_drawdown:
            return RiskDecision(False, reasons=[f"INTERRUPTOR: caída del {dd*100:.1f}% desde el máximo. Todo parado."])
        if day <= -self.daily_loss_limit:
            return RiskDecision(False, reasons=[f"límite diario alcanzado ({day*100:.1f}%). Mañana más."])
        if len(open_positions) >= self.max_positions:
            return RiskDecision(False, reasons=[f"ya hay {len(open_positions)} posiciones abiertas"])
        if any(p["symbol"] == symbol for p in open_positions):
            return RiskDecision(False, reasons=[f"ya hay una posición abierta en {symbol}"])

        entry = entry_price or setup.entry
        dist = abs(entry - setup.sl)
        if dist <= 0:
            return RiskDecision(False, reasons=["stop inválido"])
        risk_amount = equity * self.risk_per_trade
        qty = risk_amount / dist
        max_notional = equity * self.max_leverage
        if qty * entry > max_notional:
            qty = max_notional / entry
            risk_amount = qty * dist
            reasons.append("tamaño recortado para no usar apalancamiento")
        reasons.append(f"arriesgo {risk_amount:.2f} ({risk_amount/equity*100:.2f}% del capital)")
        return RiskDecision(True, qty=qty, notional=qty * entry, risk_amount=risk_amount, reasons=reasons)

    def message(self, d: RiskDecision, equity: float) -> str:
        if not d.approved:
            return "🚫 <b>Bloqueado</b>\n" + "\n".join(f"• {r}" for r in d.reasons)
        return (f"✅ <b>Aprobado</b> — capital {equity:,.2f}\n"
                f"Tamaño: {d.qty:.6f} (≈ {d.notional:,.2f})\n" + "\n".join(f"• {r}" for r in d.reasons))
