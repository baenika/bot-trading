"""Máquina del tiempo: repite el plan miles de veces con rendimientos pasados.

Idea tomada de la Time Machine de Jakeschincariol/day-trader-skill (MIT): en
vez de fiarse de una sola historia, se generan muchos futuros posibles
remuestreando bloques de rendimientos reales del propio activo, y se cuenta
cuántas veces el precio toca el objetivo antes que el stop.

Es una simulación, no una predicción.
"""

from __future__ import annotations

import math
import random


def simular(rend: list[float], entrada: float, stop: float, objetivo: float, dir_: str,
            horizonte: int = 48, caminos: int = 10000, bloque: int = 6,
            semilla: int | None = None) -> dict:
    """Devuelve el % de caminos que tocan objetivo, stop o ninguno."""
    hist = [r for r in rend if r == r]  # sin NaN
    if len(hist) < bloque * 4:
        return {"p_tp": 0.0, "p_sl": 0.0, "p_none": 100.0, "breakeven": 0.0,
                "paths": 0, "horizon": horizonte, "median_bars_tp": None}
    rnd = random.Random(semilla)
    l_tp = math.log(objetivo / entrada)
    l_sl = math.log(stop / entrada)
    tp = sl = 0
    barras_tp = []
    n = len(hist)
    for _ in range(caminos):
        acc, paso, hecho = 0.0, 0, False
        while paso < horizonte and not hecho:
            ini = rnd.randrange(0, n - bloque)
            for r in hist[ini:ini + bloque]:
                acc += r
                paso += 1
                if (dir_ == "long" and acc >= l_tp) or (dir_ == "short" and acc <= l_tp):
                    tp += 1
                    barras_tp.append(paso)
                    hecho = True
                    break
                if (dir_ == "long" and acc <= l_sl) or (dir_ == "short" and acc >= l_sl):
                    sl += 1
                    hecho = True
                    break
                if paso >= horizonte:
                    break
    rr = abs(objetivo - entrada) / (abs(entrada - stop) or 1e-9)
    barras_tp.sort()
    return {
        "p_tp": round(100 * tp / caminos, 1),
        "p_sl": round(100 * sl / caminos, 1),
        "p_none": round(100 * (caminos - tp - sl) / caminos, 1),
        "breakeven": round(100 / (1 + rr), 1),   # % de acierto que hace falta para no perder
        "paths": caminos,
        "horizon": horizonte,
        "median_bars_tp": barras_tp[len(barras_tp) // 2] if barras_tp else None,
    }
