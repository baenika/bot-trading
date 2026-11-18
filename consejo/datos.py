"""Velas: generador sintético y cargador de CSV con datos reales."""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Vela:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float


def sinteticas(simbolo: str, n: int = 6000, inicio: float = 30000.0, semilla: int | None = None,
               desde: datetime | None = None, paso_h: int = 1) -> list[Vela]:
    """Velas horarias inventadas, con fases de tendencia y lateral y picos de volumen.

    Solo sirven para probar que el código funciona. Un resultado sobre datos
    sintéticos no dice nada de si una estrategia gana dinero de verdad.
    """
    rnd = random.Random(semilla if semilla is not None else hash(simbolo) & 0xFFFF)
    t = desde or datetime(2024, 1, 1, tzinfo=timezone.utc)
    precio = inicio
    deriva, vol, restante = 0.0, 0.006, 0
    velas = []
    for _ in range(n):
        if restante <= 0:  # nueva fase de mercado
            deriva = rnd.choice([-1, 0, 0, 1]) * rnd.uniform(0.0002, 0.0012)
            vol = rnd.uniform(0.003, 0.012)
            restante = rnd.randint(48, 400)
        restante -= 1
        choque = rnd.random() < 0.01
        r = deriva + vol * rnd.gauss(0, 1) * (3.5 if choque else 1.0)
        o = precio
        c = max(o * math.exp(r), 0.01)
        mecha = abs(rnd.gauss(0, vol * 0.6))
        h = max(o, c) * (1 + mecha)
        l = min(o, c) * (1 - abs(rnd.gauss(0, vol * 0.6)))
        v = rnd.lognormvariate(10, 0.35) * (rnd.uniform(4, 12) if choque else 1.0) * (1 + 40 * abs(r))
        velas.append(Vela(t, o, h, l, c, v))
        precio = c
        t += timedelta(hours=paso_h)
    return velas


def _fecha(x: str) -> datetime:
    x = x.strip()
    if x.replace(".", "", 1).isdigit():
        n = float(x)
        if n > 1e14:      # microsegundos (los CSV nuevos de Binance)
            n /= 1e6
        elif n > 1e11:    # milisegundos
            n /= 1e3
        return datetime.fromtimestamp(n, tz=timezone.utc)
    d = datetime.fromisoformat(x.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def cargar_csv(ruta: str) -> list[Vela]:
    """Lee un CSV de velas.

    Acepta el formato de los ficheros de data.binance.vision (sin cabecera:
    open_time, open, high, low, close, volume, ...) o cualquier CSV con
    cabecera que tenga columnas de fecha, open, high, low, close y volume.
    """
    with open(ruta, newline="", encoding="utf-8") as f:
        filas = list(csv.reader(f))
    if not filas:
        return []
    cab = [c.strip().lower() for c in filas[0]]
    if any(c in cab for c in ("open", "close", "apertura", "cierre")):
        def col(*nombres):
            for n in nombres:
                if n in cab:
                    return cab.index(n)
            raise ValueError(f"{ruta}: falta una columna {nombres}")
        it, io, ih, il, ic, iv = (col("timestamp", "date", "datetime", "time", "open_time", "fecha"),
                                  col("open", "apertura"), col("high", "maximo", "máximo"),
                                  col("low", "minimo", "mínimo"), col("close", "cierre"),
                                  col("volume", "volumen"))
        filas = filas[1:]
    else:
        it, io, ih, il, ic, iv = 0, 1, 2, 3, 4, 5
    velas = [Vela(_fecha(r[it]), float(r[io]), float(r[ih]), float(r[il]), float(r[ic]), float(r[iv]))
             for r in filas if len(r) > max(it, io, ih, il, ic, iv) and r[io].strip()]
    velas.sort(key=lambda x: x.t)
    return velas
