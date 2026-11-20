"""Indicadores técnicos sencillos, sin dependencias externas."""

from __future__ import annotations

import math


def ema(xs: list[float], n: int) -> list[float]:
    if not xs:
        return []
    k = 2 / (n + 1)
    out = [xs[0]]
    for x in xs[1:]:
        out.append(out[-1] + k * (x - out[-1]))
    return out


def atr(velas, n: int = 14) -> list[float]:
    trs, prev = [], None
    for v in velas:
        tr = v.h - v.l if prev is None else max(v.h - v.l, abs(v.h - prev), abs(v.l - prev))
        trs.append(tr)
        prev = v.c
    out, s = [], 0.0
    for i, tr in enumerate(trs):  # media de Wilder
        if i < n:
            s += tr
            out.append(s / (i + 1))
        else:
            out.append((out[-1] * (n - 1) + tr) / n)
    return out


def media_desv(xs: list[float]) -> tuple[float, float]:
    if not xs:
        return 0.0, 0.0
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / max(len(xs) - 1, 1)
    return m, math.sqrt(var)


def rsi(cierres: list[float], n: int = 14) -> list[float]:
    """RSI de Wilder."""
    out = [50.0] * len(cierres)
    if len(cierres) <= n:
        return out
    sube = baja = 0.0
    for k in range(1, n + 1):
        d = cierres[k] - cierres[k - 1]
        sube += max(d, 0)
        baja += max(-d, 0)
    sube, baja = sube / n, baja / n
    for k in range(n, len(cierres)):
        if k > n:
            d = cierres[k] - cierres[k - 1]
            sube = (sube * (n - 1) + max(d, 0)) / n
            baja = (baja * (n - 1) + max(-d, 0)) / n
        out[k] = 100.0 if baja == 0 else 100 - 100 / (1 + sube / baja)
    return out


class Contexto:
    """Precalcula los indicadores de una serie para que los agentes los lean rápido."""

    def __init__(self, simbolo: str, velas):
        self.simbolo = simbolo
        self.velas = velas
        cierres = [v.c for v in velas]
        self.ema20 = ema(cierres, 20)
        self.ema50 = ema(cierres, 50)
        self.ema200 = ema(cierres, 200)
        self.atr = atr(velas, 14)
        self.rsi = rsi(cierres, 14)
        self.logvol = [math.log(max(v.v, 1e-9)) for v in velas]
        self.rend = [0.0] + [math.log(velas[i].c / velas[i - 1].c) for i in range(1, len(velas))]

    def max_previo(self, i: int, n: int) -> float:
        return max(v.h for v in self.velas[max(0, i - n):i]) if i > 0 else self.velas[0].h

    def min_previo(self, i: int, n: int) -> float:
        return min(v.l for v in self.velas[max(0, i - n):i]) if i > 0 else self.velas[0].l

    def z_volumen(self, i: int, n: int = 100) -> float:
        m, d = media_desv(self.logvol[max(0, i - n):i])
        return (self.logvol[i] - m) / d if d > 0 else 0.0

    def compresion(self, i: int, corto: int = 10, largo: int = 100) -> bool:
        """¿Venía la volatilidad muy apretada antes de esta vela?"""
        if i < largo:
            return False
        a_corto = sum(self.atr[i - corto:i]) / corto
        a_largo = sum(self.atr[i - largo:i]) / largo
        return a_corto < 0.7 * a_largo
