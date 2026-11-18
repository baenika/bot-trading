"""Risk manager: decide el tamaño y para el bot si se pasan los límites."""

from __future__ import annotations


class GestorRiesgo:
    def __init__(self, cfg: dict):
        self.riesgo_pct = cfg.get("risk_per_trade_pct", 1.0)
        self.perdida_diaria_pct = cfg.get("daily_loss_limit_pct", 3.0)
        self.drawdown_max_pct = cfg.get("max_drawdown_pct", 15.0)
        self.max_posiciones = cfg.get("max_open_positions", 3)
        self.parado = False

    def limites(self) -> dict:
        return {"risk_per_trade_pct": self.riesgo_pct, "daily_loss_limit_pct": self.perdida_diaria_pct,
                "max_drawdown_pct": self.drawdown_max_pct, "max_open_positions": self.max_posiciones}

    def revisar_cuenta(self, capital: float, pico: float) -> None:
        if pico > 0 and (capital / pico - 1) * 100 <= -self.drawdown_max_pct:
            self.parado = True

    def aprobar(self, capital: float, inicio_dia: float, abiertas: int, plan, simbolo_abierto: bool) -> dict:
        if self.parado:
            return {"ok": False, "texto": f"⛔ Bot parado: drawdown de más del {self.drawdown_max_pct:.0f}%"}
        if inicio_dia > 0 and (capital / inicio_dia - 1) * 100 <= -self.perdida_diaria_pct:
            return {"ok": False, "texto": f"⛔ Límite de pérdida diaria ({self.perdida_diaria_pct:.0f}%) alcanzado: "
                                          "hoy no se opera más"}
        if abiertas >= self.max_posiciones:
            return {"ok": False, "texto": f"⛔ Ya hay {abiertas} posiciones abiertas (máximo {self.max_posiciones})"}
        if simbolo_abierto:
            return {"ok": False, "texto": "⛔ Ya hay una posición abierta en este símbolo"}
        arriesgo = capital * self.riesgo_pct / 100
        tam = arriesgo / (plan.riesgo or 1e-9)
        tam = min(tam, capital / plan.entrada)   # sin apalancamiento
        return {"ok": True, "tam": tam, "arriesgo": tam * plan.riesgo,
                "texto": f"✅ Aprobado — capital {capital:,.2f}\nTamaño: {tam:.6f} (≈ {tam * plan.entrada:,.2f})\n"
                         f"• arriesgo {tam * plan.riesgo:,.2f} ({tam * plan.riesgo / capital * 100:.2f}% del capital)"}
