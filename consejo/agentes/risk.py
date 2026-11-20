"""Risk manager: decide el tamaño y para el bot si se pasan los límites."""

from __future__ import annotations


class GestorRiesgo:
    def __init__(self, cfg: dict):
        self.riesgo_pct = cfg.get("risk_per_trade_pct", 1.0)
        self.perdida_diaria_pct = cfg.get("daily_loss_limit_pct", 3.0)
        self.drawdown_max_pct = cfg.get("max_drawdown_pct", 15.0)
        self.max_posiciones = cfg.get("max_open_positions", 3)
        self.max_entradas_dia = cfg.get("max_entradas_dia", 3)
        self.max_posicion_pct = cfg.get("max_posicion_pct", 33.0)
        self.efectivo_min_pct = cfg.get("efectivo_min_pct", 10.0)
        self.parado = False

    def limites(self) -> dict:
        return {"risk_per_trade_pct": self.riesgo_pct, "daily_loss_limit_pct": self.perdida_diaria_pct,
                "max_drawdown_pct": self.drawdown_max_pct, "max_open_positions": self.max_posiciones,
                "max_entradas_dia": self.max_entradas_dia, "max_posicion_pct": self.max_posicion_pct,
                "efectivo_min_pct": self.efectivo_min_pct}

    def revisar_cuenta(self, capital: float, pico: float) -> None:
        if pico > 0 and (capital / pico - 1) * 100 <= -self.drawdown_max_pct:
            self.parado = True

    def aprobar(self, capital: float, inicio_dia: float, abiertas: int, plan, simbolo_abierto: bool,
                riesgo_hoy: float = 0.0, entradas_hoy: int = 0, exposicion: float = 0.0,
                ya_disparada: bool = False) -> dict:
        """riesgo_hoy: lo que se perdería si saltaran los stops de TODAS las entradas de hoy (ganen o pierdan).
        exposicion: dinero metido ahora en posiciones abiertas."""
        if self.parado:
            return {"ok": False, "texto": f"⛔ Bot parado: drawdown de más del {self.drawdown_max_pct:.0f}%"}
        if inicio_dia > 0 and (capital / inicio_dia - 1) * 100 <= -self.perdida_diaria_pct:
            return {"ok": False, "texto": f"⛔ Límite de pérdida diaria ({self.perdida_diaria_pct:.0f}%) alcanzado: "
                                          "hoy no se opera más"}
        if abiertas >= self.max_posiciones:
            return {"ok": False, "texto": f"⛔ Ya hay {abiertas} posiciones abiertas (máximo {self.max_posiciones})"}
        if simbolo_abierto:
            return {"ok": False, "texto": "⛔ Ya hay una posición abierta en este símbolo"}
        if ya_disparada:
            return {"ok": False, "texto": "⛔ Esta señal ya se disparó hoy en este símbolo (un solo disparo al día)"}
        if entradas_hoy >= self.max_entradas_dia:
            return {"ok": False, "texto": f"⛔ Ya van {entradas_hoy} entradas hoy (máximo {self.max_entradas_dia})"}
        arriesgo = capital * self.riesgo_pct / 100
        base = inicio_dia if inicio_dia > 0 else capital
        hueco = base * self.perdida_diaria_pct / 100 - riesgo_hoy
        if hueco < arriesgo * 0.25:
            return {"ok": False, "texto": f"⛔ Si saltaran todos los stops de hoy se perdería {riesgo_hoy:,.2f}: "
                                          f"no cabe otra entrada sin pasar del {self.perdida_diaria_pct:.0f}% diario"}
        arriesgo = min(arriesgo, hueco)
        tam = arriesgo / (plan.riesgo or 1e-9)
        tope = min(capital * self.max_posicion_pct / 100,
                   capital * (1 - self.efectivo_min_pct / 100) - exposicion) / plan.entrada
        if tope <= 0:
            return {"ok": False, "texto": f"⛔ Sin efectivo libre: hay que dejar siempre un {self.efectivo_min_pct:.0f}%"}
        tam = min(tam, tope)
        return {"ok": True, "tam": tam, "arriesgo": arriesgo, "tope_usd": tope * plan.entrada,
                "texto": f"✅ Aprobado — capital {capital:,.2f}\nTamaño: {tam:.6f} (≈ {tam * plan.entrada:,.2f}, "
                         f"{tam * plan.entrada / capital * 100:.0f}% de la cuenta)\n"
                         f"• arriesgo {tam * plan.riesgo:,.2f} ({tam * plan.riesgo / capital * 100:.2f}% del capital)\n"
                         f"• entradas hoy: {entradas_hoy + 1} de {self.max_entradas_dia} · riesgo del día con esta: "
                         f"{(riesgo_hoy + tam * plan.riesgo) / base * 100:.1f}% de {self.perdida_diaria_pct:.0f}%"}
