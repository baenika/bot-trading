"""Cartera de COPIA de ballenas: dinero ficticio, separada de la del Consejo.

Cuando una ballena compra fuerte, esta cartera «copia» la compra en papel con los mismos parámetros
que usa el backtest (riesgo por operación, stop por ATR, objetivo a R:R, tiempo máximo, comisión y
deslizamiento) y apunta cómo acaba. Sirve para medir si seguir a las ballenas gana o pierde.

NUNCA dinero real: no hay broker, ni claves, ni órdenes. Solo números en un JSON.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone


class CarteraCopia:
    def __init__(self, ruta: str, cfg: dict, capital: float | None = None):
        self.ruta, self.cfg = ruta, cfg
        cap = float(capital if capital is not None else cfg.get("capital_inicial", 10000.0))
        self.d = {"inicio": datetime.now(timezone.utc).isoformat(timespec="seconds"), "capital": cap,
                  "capital_inicial": cap, "abiertas": {}, "operaciones": [], "señales": 0}
        if os.path.exists(ruta):
            try:
                with open(ruta, encoding="utf-8") as f:
                    self.d.update(json.load(f))
            except (OSError, ValueError):
                pass

    # --- costes reales del backtest ------------------------------------------------------------
    def _com(self) -> float:
        return self.cfg.get("comision_pct", 0.1) / 100

    def _desl(self) -> float:
        return self.cfg.get("deslizamiento_pct", 0.05) / 100

    def guardar(self) -> None:
        os.makedirs(os.path.dirname(self.ruta) or ".", exist_ok=True)
        tmp = self.ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, self.ruta)

    def abrir(self, simbolo: str, precio: float, atr: float, t, motivo: str, max_barras: int | None = None) -> dict | None:
        """Compra en papel al precio actual + deslizamiento. Stop = stop_atr × ATR (con tope stop_max_atr),
        objetivo = rr_objetivo × riesgo, tamaño = risk_per_trade_pct del capital (máx. 33 %)."""
        self.d["señales"] += 1
        if simbolo in self.d["abiertas"] or precio <= 0 or atr <= 0:
            return None
        if len(self.d["abiertas"]) >= self.cfg.get("max_open_positions", 3):
            return None
        cfg = self.cfg
        entrada = precio * (1 + self._desl())
        dist = min(cfg.get("stop_atr", 1.5), cfg.get("stop_max_atr", 3.0)) * atr
        stop = entrada - dist
        objetivo = entrada + cfg.get("rr_objetivo", 2.0) * dist
        riesgo_usd = self.d["capital"] * cfg.get("risk_per_trade_pct", 1.0) / 100
        tam = min(riesgo_usd / dist, self.d["capital"] * 0.33 / entrada)
        if tam <= 0:
            return None
        self.d["capital"] -= entrada * tam * self._com()
        p = {"symbol": simbolo, "entry": entrada, "stop": stop, "tp": objetivo, "qty": tam,
             "usd": round(entrada * tam, 2), "opened": str(t), "motivo": motivo, "barras": 0,
             "max_barras": max_barras or cfg.get("max_barras", 48)}
        self.d["abiertas"][simbolo] = p
        return p

    def _cerrar(self, s: str, precio: float, motivo: str, t) -> dict:
        p = self.d["abiertas"].pop(s)
        bruto = (precio - p["entry"]) * p["qty"]
        pnl = bruto - precio * p["qty"] * self._com()
        self.d["capital"] += bruto - precio * p["qty"] * self._com()
        riesgo = (p["entry"] - p["stop"]) * p["qty"]
        op = {"symbol": s, "opened": p["opened"], "closed": str(t), "entry": p["entry"], "exit": precio,
              "usd": p["usd"], "pnl": round(pnl - p["entry"] * p["qty"] * self._com(), 2),
              "R": round((pnl - p["entry"] * p["qty"] * self._com()) / riesgo, 2) if riesgo else 0.0,
              "reason": motivo, "motivo": p["motivo"]}
        self.d["operaciones"].append(op)
        return op

    def actualizar(self, simbolo: str, alto: float, bajo: float, cierre: float, t) -> dict | None:
        """Con cada vela CERRADA nueva: stop primero (lo prudente), luego objetivo, luego tiempo."""
        p = self.d["abiertas"].get(simbolo)
        if not p:
            return None
        p["barras"] += 1
        if bajo <= p["stop"]:
            return self._cerrar(simbolo, p["stop"] * (1 - self._desl()), "stop loss", t)
        if alto > p["tp"]:                     # límite: el precio tiene que pasar el objetivo
            return self._cerrar(simbolo, p["tp"], "take profit", t)
        if p["barras"] >= p["max_barras"]:
            return self._cerrar(simbolo, cierre * (1 - self._desl()), "tiempo máximo", t)
        return None

    def resumen(self, titulo: str) -> str:
        ops = self.d["operaciones"]
        gan = [o for o in ops if o["pnl"] > 0]
        bruto_g = sum(o["pnl"] for o in gan)
        bruto_p = -sum(o["pnl"] for o in ops if o["pnl"] <= 0)
        pf = f"{bruto_g / bruto_p:.2f}" if bruto_p > 0 else ("∞" if bruto_g else "—")
        ret = (self.d["capital"] / self.d["capital_inicial"] - 1) * 100
        lin = [f"🐋 {titulo} (papel desde {str(self.d['inicio'])[:10]}): {self.d['capital']:,.2f} $ ficticios "
               f"({ret:+.2f} %) · {len(ops)} cerradas · acierto {len(gan) / len(ops) * 100:.0f} % · PF {pf}"
               if ops else f"🐋 {titulo}: {self.d['capital']:,.2f} $ ficticios · aún sin operaciones cerradas "
                           f"({self.d['señales']} señales de ballenas)"]
        for s, p in self.d["abiertas"].items():
            lin.append(f"  • {s}: entrada {p['entry']:.6g} · stop {p['stop']:.6g} · objetivo {p['tp']:.6g} ({p['motivo']})")
        return "\n".join(lin)
