"""Estrategia de la vela de las 9:30 de Nueva York (reel 13), para SPY y QQQ.

Reglas, tal como se explicaron en el vídeo:
1. Marcar el máximo y el mínimo de la primera vela de 15 min (9:30-9:45 de Nueva York).
2. Esperar a que una vela de 5 min CIERRE por encima del máximo (largo).
3. Entrar en el "order block" más cercano: la última vela bajista de 5 min antes de la ruptura.
   La orden espera en su máximo (el precio vuelve a tocarlo) y el stop va debajo de su mínimo.
4. Objetivo: 2 veces lo arriesgado (el vídeo no lo concreta; se puede cambiar).

Lo que el vídeo no dice y aquí se ha decidido (todo se puede cambiar en la config):
- Si no hay vela bajista entre la apertura y la ruptura, se usa el máximo del rango como entrada
  y el mínimo del rango como stop.
- Solo se entra hasta 2 h después de la apertura; lo que siga abierto se cierra al final de la sesión.
- Una operación por símbolo y día. Si una vela toca stop y objetivo, cuenta el stop.
- Cortos desactivados, como en el resto del Consejo.

Solo usa velas ya cerradas. Todo es simulado: no hay broker ni órdenes reales.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

CONFIG_ORB = {
    "rr": 2.0,
    "minutos_rango": 15,
    "limite_entrada_min": 120,
    "entrada": "order_block",       # "order_block", "maximo_rango" o "cierre_ruptura" (sin esperar retroceso)
    "permitir_cortos": False,
    "comision_pct": 0.01,          # ETF muy líquido: casi sin comisión, pero con horquilla
    "deslizamiento_pct": 0.01,
    "stop_min_pct": 0.0,           # si el stop queda más cerca que esto (% del precio), se aleja hasta aquí
    "riesgo_pct": 1.0,
    "capital_inicial": 10000.0,
}


def _domingo(anio: int, mes: int, n: int) -> date:
    d = date(anio, mes, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def apertura_ny_utc(dia: date) -> datetime:
    """9:30 de Nueva York en UTC (horario de verano de EE. UU.: 2.º domingo de marzo a 1.er domingo de noviembre)."""
    verano = _domingo(dia.year, 3, 2) <= dia < _domingo(dia.year, 11, 1)
    return datetime(dia.year, dia.month, dia.day, 13 if verano else 14, 30, tzinfo=timezone.utc)


@dataclass
class OperacionORB:
    simbolo: str
    dia: str
    dir: str
    entrada: float
    stop: float
    objetivo: float
    t_entrada: str
    t_salida: str
    salida: float
    motivo: str
    R: float


def operar_dia(simbolo: str, velas: list, dia: date, cfg: dict) -> tuple[OperacionORB | None, str]:
    """Aplica la estrategia a las velas de 5 min de un día. Devuelve (operación o None, qué pasó)."""
    ab = apertura_ny_utc(dia)
    fin_rango = ab + timedelta(minutes=cfg["minutos_rango"])
    cierre = ab + timedelta(hours=6, minutes=30)
    limite = ab + timedelta(minutes=cfg["limite_entrada_min"])
    sesion = [v for v in velas if ab <= v.t < cierre]
    rango = [v for v in sesion if v.t < fin_rango]
    if len(rango) < cfg["minutos_rango"] // 5:
        return None, "sin datos de la apertura"
    alto, bajo = max(v.h for v in rango), min(v.l for v in rango)
    resto = [v for v in sesion if v.t >= fin_rango]

    # 2) ruptura: primera vela de 5 min que cierra por encima del máximo (o debajo del mínimo)
    k_rup, dir_ = None, None
    for k, v in enumerate(resto):
        if v.t >= limite:
            break
        if v.c > alto:
            k_rup, dir_ = k, "long"
            break
        if v.c < bajo and cfg["permitir_cortos"]:
            k_rup, dir_ = k, "short"
            break
    if k_rup is None:
        return None, "no hubo ruptura antes del límite"

    # 3) order block: última vela contraria antes de la ruptura (desde la apertura)
    previas = rango + resto[:k_rup]
    contrarias = [v for v in previas if (v.c < v.o if dir_ == "long" else v.c > v.o)]
    rup = resto[k_rup]
    if cfg["entrada"] == "cierre_ruptura":
        entrada = rup.c
        stop = bajo if dir_ == "long" else alto
    elif cfg["entrada"] == "order_block" and contrarias:
        ob = contrarias[-1]
        entrada, stop = (ob.h, ob.l) if dir_ == "long" else (ob.l, ob.h)
    else:
        entrada, stop = (alto, bajo) if dir_ == "long" else (bajo, alto)
    signo = 1 if dir_ == "long" else -1
    minimo = entrada * cfg["stop_min_pct"] / 100
    if abs(entrada - stop) < minimo:
        stop = entrada - signo * minimo
    riesgo = abs(entrada - stop)
    if riesgo <= 0:
        return None, "order block sin rango"
    objetivo = entrada + signo * cfg["rr"] * riesgo
    desl = cfg["deslizamiento_pct"] / 100

    # 4) esperar a que el precio vuelva a la entrada (salvo "cierre_ruptura")
    pendientes = resto[k_rup + 1:]
    k_ent, precio = None, None
    if cfg["entrada"] == "cierre_ruptura":
        if pendientes:
            k_ent, precio = 0, pendientes[0].o * (1 + signo * desl)
    else:
        for k, v in enumerate(pendientes):
            if v.t >= limite:
                break
            if (dir_ == "long" and v.h >= objetivo and v.l > entrada) or \
               (dir_ == "short" and v.l <= objetivo and v.h < entrada):
                return None, "se fue al objetivo sin volver a la entrada"
            toca = v.l <= entrada if dir_ == "long" else v.h >= entrada
            if toca:
                k_ent = k
                precio = min(v.o, entrada) if dir_ == "long" else max(v.o, entrada)
                break
    if k_ent is None:
        return None, "no volvió a la entrada a tiempo"

    # 5) gestión: stop primero, objetivo, o cierre de la sesión
    for k in range(k_ent, len(pendientes)):
        v = pendientes[k]
        mismo = k == k_ent
        if (dir_ == "long" and v.l <= stop) or (dir_ == "short" and v.h >= stop):
            sal = (min(v.o, stop) if dir_ == "long" else max(v.o, stop)) if not mismo else stop
            sal *= (1 - signo * desl)
            return _op(simbolo, dia, dir_, precio, stop, objetivo, v0=pendientes[k_ent], v1=v, sal=sal,
                       motivo="stop", cfg=cfg), "operación"
        # en la vela de entrada no se sabe si tocó antes la entrada o el objetivo: no se cuenta el objetivo
        if not mismo and ((dir_ == "long" and v.h >= objetivo) or (dir_ == "short" and v.l <= objetivo)):
            return _op(simbolo, dia, dir_, precio, stop, objetivo, pendientes[k_ent], v, objetivo,
                       "objetivo", cfg), "operación"
    ult = pendientes[-1]
    return _op(simbolo, dia, dir_, precio, stop, objetivo, pendientes[k_ent], ult,
               ult.c * (1 - signo * desl), "cierre de la sesión", cfg), "operación"


def _op(simbolo, dia, dir_, entrada, stop, objetivo, v0, v1, sal, motivo, cfg) -> OperacionORB:
    signo = 1 if dir_ == "long" else -1
    riesgo = abs(entrada - stop) or 1e-9
    coste = 2 * cfg["comision_pct"] / 100 * entrada
    r = (signo * (sal - entrada) - coste) / riesgo
    return OperacionORB(simbolo, dia.isoformat(), dir_, entrada, stop, objetivo, v0.t.isoformat(),
                        v1.t.isoformat(), sal, motivo, r)


def backtest(series: dict, cfg: dict | None = None) -> dict:
    cfg = {**CONFIG_ORB, **(cfg or {})}
    ops, motivos = [], {}
    for s, vs in series.items():
        for dia in sorted({v.t.date() for v in vs}):
            if dia.weekday() >= 5:
                continue
            op, que = operar_dia(s, vs, dia, cfg)
            motivos[que] = motivos.get(que, 0) + 1
            if op:
                ops.append(op)
    ops.sort(key=lambda o: o.t_entrada)
    return {"operaciones": ops, "motivos": motivos, "stats": estadisticas(ops, cfg)}


def estadisticas(ops: list[OperacionORB], cfg: dict | None = None) -> dict:
    cfg = {**CONFIG_ORB, **(cfg or {})}
    capital = pico = cfg["capital_inicial"]
    dd = 0.0
    for o in ops:
        capital += capital * cfg["riesgo_pct"] / 100 * o.R
        pico = max(pico, capital)
        dd = min(dd, (capital / pico - 1) * 100)
    gan = sum(o.R for o in ops if o.R > 0)
    per = -sum(o.R for o in ops if o.R < 0)
    return {"n": len(ops), "acierto": 100 * sum(o.R > 0 for o in ops) / len(ops) if ops else 0.0,
            "R_total": sum(o.R for o in ops), "R_medio": sum(o.R for o in ops) / len(ops) if ops else 0.0,
            "pf": gan / per if per else (999.0 if gan else 0.0),
            "rentabilidad": (capital / cfg["capital_inicial"] - 1) * 100, "drawdown": dd}
