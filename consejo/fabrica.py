"""Fábrica de estrategias (vídeos del 22-nov: «12.000 estrategias», StrategyQuant y optimización estable).

Idea: no inventar una estrategia, sino generar miles de combinaciones SENCILLAS y quedarse solo con
las que aguantan pruebas duras. Todo es backtest con velas reales: nada de órdenes ni brokers.

Reglas de construcción (las del vídeo de StrategyQuant):
- Entrada: 1 o 2 condiciones como mucho (un disparador y, si toca, un filtro). Estrategias simples
  aguantan más en el tiempo.
- Stop OBLIGATORIO por volatilidad: ATR de periodo largo (varias sesiones) × 1 a 3.
- Objetivo opcional por ATR × 1 a 6, salida por tiempo opcional y salida por canal opuesto opcional
  (como las tortugas). Como mucho 3 tipos de salida contando el stop.
- Riesgo fijo: 1 % del saldo INICIAL por operación (1.000 $ en una cuenta de 100.000 $).
- Entrada a la apertura de la vela siguiente, con spread, comisión y swap del activo. Si en una vela
  se tocan stop y objetivo, cuenta el stop. El objetivo tiene que pasarse, no basta con tocarlo.

Embudo de pruebas (vídeos 1 y 3):
1. Dentro de muestra (la parte antigua): mínimo de operaciones, acierto ≥ 30 % y beneficio/caída ≥ 3.
2. Monte Carlo: se barajan las operaciones con reposición; el 95 % de las vueltas tiene que ganar.
3. Vecinos ±20 %: se mueven todos los parámetros hasta un 20 % arriba o abajo y la mayoría de las
   vecinas tiene que seguir ganando (zona estable, no un unicornio).
4. Fuera de muestra: la parte reciente, que la fábrica no vio al elegir, tiene que ganar.
5. Examen de cuenta fondeada (consejo/fondeo.py) con todas las operaciones.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, timedelta, timezone
from itertools import accumulate

from .indicadores import atr as _atr, ema as _ema, rsi as _rsi

# --- costes por activo ----------------------------------------------------------------------------
# spread en unidades de precio (se paga entero al entrar), comisión en % del nocional ida y vuelta,
# swap en % del nocional por noche (largos / cortos), apalancamiento máximo y horas UTC sin entradas
# (el cambio de día del broker, cuando los spreads se disparan). Valores prudentes de CFD en 2026.
PERFILES = {
    "XAUUSD": {"nombre": "Oro (XAU/USD)", "spread": 0.30, "comision_pct": 0.005, "swap_largo_pct": 0.014,
               "swap_corto_pct": 0.004, "apalancamiento": 20, "sin_entradas_utc": (20, 23), "intradia": False},
    "NAS100": {"nombre": "Nasdaq 100", "spread": 1.5, "comision_pct": 0.0, "swap_largo_pct": 0.018,
               "swap_corto_pct": 0.006, "apalancamiento": 20, "sin_entradas_utc": (20, 23), "intradia": True},
    "SPX500": {"nombre": "S&P 500", "spread": 0.6, "comision_pct": 0.0, "swap_largo_pct": 0.018,
               "swap_corto_pct": 0.006, "apalancamiento": 20, "sin_entradas_utc": (20, 23), "intradia": True},
    "BTCUSDT": {"nombre": "Bitcoin", "spread": 0.0, "comision_pct": 0.20, "deslizamiento_pct": 0.05,
                "swap_largo_pct": 0.0, "swap_corto_pct": 0.0, "apalancamiento": 2, "sin_entradas_utc": None,
                "intradia": False},
    "ETHUSDT": {"nombre": "Ethereum", "spread": 0.0, "comision_pct": 0.20, "deslizamiento_pct": 0.05,
                "swap_largo_pct": 0.0, "swap_corto_pct": 0.0, "apalancamiento": 2, "sin_entradas_utc": None,
                "intradia": False},
}
GENERICO = {"nombre": "", "spread": 0.0, "comision_pct": 0.10, "deslizamiento_pct": 0.05, "swap_largo_pct": 0.0,
            "swap_corto_pct": 0.0, "apalancamiento": 5, "sin_entradas_utc": None, "intradia": False}


def perfil(simbolo: str) -> dict:
    return dict(PERFILES.get(simbolo, GENERICO))


# --- hora de Nueva York sin zoneinfo (en Windows falta la base de husos) -------------------------
def _domingo(anio: int, mes: int, n: int) -> date:
    d = date(anio, mes, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def hora_ny(t: datetime) -> datetime:
    """UTC → hora de Nueva York (horario de verano de EE. UU.: 2º domingo de marzo a 1er domingo de nov)."""
    ini, fin = _domingo(t.year, 3, 2), _domingo(t.year, 11, 1)
    verano = ini <= t.date() < fin
    return (t - timedelta(hours=4 if verano else 5)).replace(tzinfo=None)


# --- datos precalculados --------------------------------------------------------------------------
class Datos:
    """Velas en listas sueltas + indicadores en caché (cada combinación se calcula una sola vez)."""

    def __init__(self, simbolo: str, velas):
        self.simbolo = simbolo
        self.velas = velas
        self.t = [v.t for v in velas]
        self.o = [v.o for v in velas]
        self.h = [v.h for v in velas]
        self.l = [v.l for v in velas]
        self.c = [v.c for v in velas]
        self.v = [v.v for v in velas]
        self.n = len(velas)
        self.ny = [hora_ny(t) for t in self.t]
        self._cache: dict = {}
        self._senales: dict = {}
        self._dias()

    def _dias(self):
        """Máximo, mínimo y cierre del día anterior (día de Nueva York) para pivotes y niveles."""
        dia, h, l, c = None, None, None, None
        ayer = None
        self.ayer = []
        for i in range(self.n):
            d = self.ny[i].date()
            if d != dia:
                if dia is not None:
                    ayer = (h, l, c)
                dia, h, l = d, self.h[i], self.l[i]
            h, l, c = max(h, self.h[i]), min(l, self.l[i]), self.c[i]
            self.ayer.append(ayer)

    def ind(self, nombre: str, *p):
        k = (nombre,) + p
        if k not in self._cache:
            self._cache[k] = getattr(self, "_" + nombre)(*p)
        return self._cache[k]

    def _ema(self, n):
        return _ema(self.c, n)

    def _sma(self, n):
        out, s = [], 0.0
        for i, x in enumerate(self.c):
            s += x
            if i >= n:
                s -= self.c[i - n]
            out.append(s / min(i + 1, n))
        return out

    def _sd(self, n):
        m = self.ind("sma", n)
        out = []
        for i in range(self.n):
            a = max(0, i - n + 1)
            xs = self.c[a:i + 1]
            out.append(math.sqrt(sum((x - m[i]) ** 2 for x in xs) / max(len(xs) - 1, 1)))
        return out

    def _atr(self, n):
        return _atr(self.velas, n)

    def _rsi(self, n):
        return _rsi(self.c, n)

    def _maxh(self, n):  # máximo de las n velas ANTERIORES (sin la actual)
        out = []
        for i in range(self.n):
            a = max(0, i - n)
            out.append(max(self.h[a:i]) if i > a else self.h[i])
        return out

    def _minl(self, n):
        out = []
        for i in range(self.n):
            a = max(0, i - n)
            out.append(min(self.l[a:i]) if i > a else self.l[i])
        return out

    def _volz(self, n):
        out = []
        for i in range(self.n):
            xs = self.v[max(0, i - n):i]
            if len(xs) < 10:
                out.append(0.0)
                continue
            m = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) or 1.0
            out.append((self.v[i] - m) / sd)
        return out


# --- bloques de entrada ---------------------------------------------------------------------------
# Cada bloque: familia, rangos de parámetros y una función que dice si se cumple al CIERRE de la vela i
# (dir +1 compra, -1 venta). Los cortos usan la regla en espejo.

def _cruce(a, b, i):
    return a[i - 1] <= b[i - 1] and a[i] > b[i]


def b_cruce_ema(D, i, s, rapida, lenta):
    r, l = D.ind("ema", rapida), D.ind("ema", lenta)
    return _cruce(r, l, i) if s > 0 else _cruce(l, r, i)


def b_sobre_ema(D, i, s, n):
    e = D.ind("ema", n)
    return D.c[i] > e[i] if s > 0 else D.c[i] < e[i]


def b_pendiente_ema(D, i, s, n, k):
    e = D.ind("ema", n)
    return i >= k and ((e[i] > e[i - k]) if s > 0 else (e[i] < e[i - k]))


def b_rsi_cruza(D, i, s, n, nivel):
    r = D.ind("rsi", n)
    if s > 0:
        return r[i - 1] <= nivel < r[i]
    return r[i - 1] >= 100 - nivel > r[i]


def b_rsi_extremo(D, i, s, n, nivel):
    r = D.ind("rsi", n)
    return r[i] < nivel if s > 0 else r[i] > 100 - nivel


def b_roc(D, i, s, n, umbral):
    if i < n:
        return False
    x = (D.c[i] / D.c[i - n] - 1) * 100
    return x > umbral if s > 0 else x < -umbral


def b_macd(D, i, s, rapida, lenta, senal):
    k = ("macd", rapida, lenta, senal)
    if k not in D._cache:
        a, b = D.ind("ema", rapida), D.ind("ema", lenta)
        m = [x - y for x, y in zip(a, b)]
        D._cache[k] = (m, _ema(m, senal))
    m, sg = D._cache[k]
    return _cruce(m, sg, i) if s > 0 else _cruce(sg, m, i)


def b_donchian(D, i, s, n):
    return D.c[i] > D.ind("maxh", n)[i] if s > 0 else D.c[i] < D.ind("minl", n)[i]


def b_max_ayer(D, i, s):
    a = D.ayer[i]
    if not a:
        return False
    return (D.c[i] > a[0] >= D.c[i - 1]) if s > 0 else (D.c[i] < a[1] <= D.c[i - 1])


def b_bollinger_ruptura(D, i, s, n, k):
    m, sd = D.ind("sma", n), D.ind("sd", n)
    return D.c[i] > m[i] + k * sd[i] if s > 0 else D.c[i] < m[i] - k * sd[i]


def b_bollinger_rebote(D, i, s, n, k):
    m, sd = D.ind("sma", n), D.ind("sd", n)
    if s > 0:
        lo = m[i - 1] - k * sd[i - 1]
        return D.c[i - 1] < lo and D.c[i] > m[i] - k * sd[i]
    hi = m[i - 1] + k * sd[i - 1]
    return D.c[i - 1] > hi and D.c[i] < m[i] + k * sd[i]


def b_keltner(D, i, s, n, k):
    e, a = D.ind("ema", n), D.ind("atr", n)
    return D.c[i] > e[i] + k * a[i] if s > 0 else D.c[i] < e[i] - k * a[i]


def b_compresion(D, i, s, corto, largo):
    return D.ind("atr", corto)[i] < 0.75 * D.ind("atr", largo)[i]


def b_volumen(D, i, s, n, z):
    sube = D.c[i] > D.o[i]
    return D.ind("volz", n)[i] > z and (sube if s > 0 else not sube)


def b_envolvente(D, i, s):
    o, c = D.o, D.c
    if s > 0:
        return c[i - 1] < o[i - 1] and c[i] > o[i] and c[i] >= o[i - 1] and o[i] <= c[i - 1]
    return c[i - 1] > o[i - 1] and c[i] < o[i] and c[i] <= o[i - 1] and o[i] >= c[i - 1]


def b_interior_rota(D, i, s):
    if i < 2:
        return False
    dentro = D.h[i - 1] <= D.h[i - 2] and D.l[i - 1] >= D.l[i - 2]
    return dentro and (D.c[i] > D.h[i - 1] if s > 0 else D.c[i] < D.l[i - 1])


def b_velas_seguidas(D, i, s, n):
    if i < n + 1:
        return False
    c = D.c
    if s > 0:
        return all(c[j] < c[j - 1] for j in range(i - n, i)) and c[i] > c[i - 1]
    return all(c[j] > c[j - 1] for j in range(i - n, i)) and c[i] < c[i - 1]


def b_rango_apertura(D, i, s, barras):
    """Ruptura del rango de las primeras `barras` velas de la sesión de Nueva York (9:30)."""
    ny = D.ny[i]
    if not (9 <= ny.hour < 15):
        return False
    j, ini = i, None
    while j > 0 and D.ny[j].date() == ny.date():
        if D.ny[j].hour == 9:
            ini = j
        j -= 1
    if ini is None or i < ini + barras:
        return False
    hi, lo = max(D.h[ini:ini + barras]), min(D.l[ini:ini + barras])
    return (D.c[i] > hi >= D.c[i - 1]) if s > 0 else (D.c[i] < lo <= D.c[i - 1])


def b_pivote(D, i, s):
    """Pivotes clásicos del día anterior: compra si toca S1 y cierra por encima (venta en espejo con R1)."""
    a = D.ayer[i]
    if not a:
        return False
    p = (a[0] + a[1] + a[2]) / 3
    if s > 0:
        s1 = 2 * p - a[0]
        return D.l[i] <= s1 < D.c[i]
    r1 = 2 * p - a[1]
    return D.h[i] >= r1 > D.c[i]


def b_hora(D, i, s, hora):
    return D.ny[i].hour == hora


def b_dia_semana(D, i, s, dia):
    return D.ny[i].weekday() == dia


def b_zscore(D, i, s, n, z):
    m, sd = D.ind("sma", n), D.ind("sd", n)
    if not sd[i]:
        return False
    x = (D.c[i] - m[i]) / sd[i]
    return x < -z if s > 0 else x > z


def b_lejos_media(D, i, s, n, k):
    m, a = D.ind("sma", n), D.ind("atr", 50)
    return D.c[i] < m[i] - k * a[i] if s > 0 else D.c[i] > m[i] + k * a[i]


BLOQUES = {
    # nombre: (familia, función, {parámetro: valores}, explicación con {parámetros})
    "cruce_ema": ("tendencia", b_cruce_ema, {"rapida": [8, 10, 12, 15, 20, 25, 30, 40], "lenta": [50, 60, 80, 100, 150, 200]},
                  "la media exponencial de {rapida} cruza la de {lenta}"),
    "sobre_ema": ("tendencia", b_sobre_ema, {"n": [20, 30, 50, 75, 100, 150, 200, 300]},
                  "el precio está del lado bueno de la media de {n}"),
    "pendiente_ema": ("tendencia", b_pendiente_ema, {"n": [20, 50, 100, 200], "k": [3, 5, 10, 20]},
                      "la media de {n} sube (o baja) respecto a hace {k} velas"),
    "rsi_cruza": ("momentum", b_rsi_cruza, {"n": [7, 10, 14, 20, 28], "nivel": [50, 55, 60, 65]},
                  "el RSI de {n} cruza {nivel} (100−{nivel} en ventas)"),
    "roc": ("momentum", b_roc, {"n": [5, 10, 20, 40], "umbral": [0.3, 0.5, 1.0, 2.0, 3.0]},
            "el precio ha subido más de un {umbral} % en {n} velas"),
    "macd": ("momentum", b_macd, {"rapida": [8, 12, 16], "lenta": [21, 26, 34], "senal": [5, 9, 12]},
             "el MACD ({rapida},{lenta}) cruza su señal de {senal}"),
    "rsi_extremo": ("sobrecompra_sobreventa", b_rsi_extremo, {"n": [2, 3, 5, 7, 14], "nivel": [10, 15, 20, 25, 30]},
                    "el RSI de {n} está por debajo de {nivel} (sobreventa; en ventas, sobrecompra)"),
    "zscore": ("estadistica", b_zscore, {"n": [20, 50, 100], "z": [1.5, 2.0, 2.5, 3.0]},
               "el precio está a más de {z} desviaciones de su media de {n}"),
    "lejos_media": ("estadistica", b_lejos_media, {"n": [20, 50, 100, 200], "k": [1.0, 1.5, 2.0, 3.0]},
                    "el precio está a más de {k} ATR de su media de {n}"),
    "donchian": ("ruptura", b_donchian, {"n": [10, 20, 30, 40, 55, 80, 100]},
                 "el cierre rompe el máximo (mínimo en ventas) de las {n} velas anteriores"),
    "max_ayer": ("ruptura", b_max_ayer, {}, "el cierre rompe el máximo (mínimo) de ayer"),
    "bollinger_ruptura": ("volatilidad_canales", b_bollinger_ruptura, {"n": [20, 30, 50], "k": [1.5, 2.0, 2.5]},
                          "el cierre sale por fuera de la banda de Bollinger ({n}, {k})"),
    "bollinger_rebote": ("volatilidad_canales", b_bollinger_rebote, {"n": [20, 30, 50], "k": [1.5, 2.0, 2.5]},
                         "el cierre vuelve a entrar en la banda de Bollinger ({n}, {k})"),
    "keltner": ("volatilidad_canales", b_keltner, {"n": [20, 30, 50], "k": [1.0, 1.5, 2.0, 2.5]},
                "el cierre sale del canal de Keltner ({n}, {k} ATR)"),
    "compresion": ("volatilidad_canales", b_compresion, {"corto": [5, 10, 14], "largo": [50, 100]},
                   "la volatilidad de {corto} velas es menor que el 75 % de la de {largo} (mercado comprimido)"),
    "volumen": ("volumen", b_volumen, {"n": [50, 100], "z": [1.5, 2.0, 3.0]},
                "el volumen está {z} desviaciones por encima de lo normal y la vela va a favor"),
    "envolvente": ("velas", b_envolvente, {}, "vela envolvente a favor"),
    "interior_rota": ("velas", b_interior_rota, {}, "una vela interior se rompe a favor"),
    "velas_seguidas": ("velas", b_velas_seguidas, {"n": [2, 3, 4, 5]},
                       "tras {n} cierres en contra, una vela gira a favor"),
    "rango_apertura": ("estructura_sesion", b_rango_apertura, {"barras": [1, 2]},
                       "el precio rompe el rango de las {barras} primeras velas de Nueva York"),
    "pivote": ("estructura_sesion", b_pivote, {}, "toca el pivote S1 de ayer y cierra por encima (R1 en ventas)"),
    "hora": ("calendario", b_hora, {"hora": [3, 4, 8, 9, 10, 11, 12, 13, 14, 15]},
             "la vela cierra a las {hora}:59 de Nueva York"),
    "dia_semana": ("calendario", b_dia_semana, {"dia": [0, 1, 2, 3, 4]}, "es {dia_txt}"),
}
FILTROS = {"sobre_ema", "pendiente_ema", "compresion", "hora", "dia_semana", "rsi_extremo", "zscore"}
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

SALIDAS = {"atr_n": [50, 75, 100, 120], "sl": [1.0, 1.5, 2.0, 2.5, 3.0], "tp": [None, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0],
           "barras": [None, 5, 10, 24, 48, 120], "canal": [None, 10, 20]}


# --- estrategias ----------------------------------------------------------------------------------
def firma(e: dict) -> str:
    return hashlib.md5(repr(sorted((k, repr(v)) for k, v in e.items())).encode()).hexdigest()[:10]


def aleatoria(rnd: random.Random, intradia: bool, direcciones=("long", "short", "ambas")) -> dict:
    nombres = list(BLOQUES)
    disparadores = [b for b in nombres if b not in ("hora", "dia_semana", "compresion")]
    if not intradia:
        disparadores = [b for b in disparadores if b != "rango_apertura"]
    a = rnd.choice(disparadores)
    cond = [(a, {k: rnd.choice(v) for k, v in BLOQUES[a][2].items()})]
    if rnd.random() < 0.65:
        b = rnd.choice([x for x in FILTROS if x != a])
        cond.append((b, {k: rnd.choice(v) for k, v in BLOQUES[b][2].items()}))
    if a == "cruce_ema" and cond[0][1]["rapida"] >= cond[0][1]["lenta"]:
        cond[0][1]["lenta"] = 200
    salida = {k: rnd.choice(v) for k, v in SALIDAS.items()}
    # como mucho 3 tipos de salida contando el stop (regla del vídeo)
    extras = [k for k in ("tp", "barras", "canal") if salida[k] is not None]
    while len(extras) > 2:
        salida[extras.pop(rnd.randrange(len(extras)))] = None
    return {"cond": cond, "dir": rnd.choice(direcciones), **salida, "intradia": intradia}


def tortugas() -> list[dict]:
    """La estrategia de las tortugas de Richard Dennis (vídeo de TradingView): ruptura de 20 y 55 velas,
    stop a 2 ATR de 20, salida al romper el canal contrario de 10 y 20 velas."""
    return [{"cond": [("donchian", {"n": 20})], "dir": "ambas", "atr_n": 20, "sl": 2.0, "tp": None, "barras": None,
             "canal": 10, "intradia": False, "nombre": "Tortugas S1"},
            {"cond": [("donchian", {"n": 55})], "dir": "ambas", "atr_n": 20, "sl": 2.0, "tp": None, "barras": None,
             "canal": 20, "intradia": False, "nombre": "Tortugas S2"}]


def describir(e: dict) -> str:
    partes = []
    for nombre, p in e["cond"]:
        q = dict(p)
        if "dia" in q:
            q["dia_txt"] = DIAS[q["dia"]]
        partes.append(BLOQUES[nombre][3].format(**q))
    d = {"long": "Solo compras", "short": "Solo ventas", "ambas": "Compras y ventas"}[e["dir"]]
    s = [f"{d}. Entra a la apertura de la vela siguiente cuando " + " y ".join(partes) + ".",
         f"Stop a {e['sl']:g} ATR de {e['atr_n']} velas."]
    if e.get("tp"):
        s.append(f"Objetivo a {e['tp']:g} ATR.")
    if e.get("barras"):
        s.append(f"Sale a las {e['barras']} velas si no ha tocado nada.")
    if e.get("canal"):
        s.append(f"Sale si rompe el canal contrario de {e['canal']} velas.")
    if e.get("intradia"):
        s.append("Todo dentro del día: cierra antes del final de la sesión de Nueva York.")
    return " ".join(s).replace(".5 ATR", ",5 ATR").replace(".0 ATR", " ATR")


def _bloque(D: Datos, nombre: str, p: dict, s: int) -> set[int]:
    """Velas donde se cumple un bloque (en caché: cada bloque con sus parámetros se calcula una vez)."""
    clave = (nombre, tuple(sorted(p.items())), s)
    if clave not in D._senales:
        f = BLOQUES[nombre][1]
        D._senales[clave] = {i for i in range(210, D.n - 1) if f(D, i, s, **p)}
    return D._senales[clave]


def senales(D: Datos, e: dict, s: int) -> list[int]:
    """Índices de las velas en cuyo cierre se cumplen todas las condiciones de entrada."""
    conjuntos = sorted((_bloque(D, n, p, s) for n, p in e["cond"]), key=len)
    return sorted(conjuntos[0].intersection(*conjuntos[1:]))


# --- simulador ------------------------------------------------------------------------------------
def simular(D: Datos, e: dict, desde: int = 0, hasta: int | None = None, capital: float = 100_000.0,
            riesgo_pct: float = 1.0, P: dict | None = None) -> list[dict]:
    """Opera la estrategia entre las velas [desde, hasta). Una posición a la vez.

    Devuelve las operaciones con beneficio neto en $ y en R (múltiplos del riesgo)."""
    P = P or perfil(D.simbolo)
    hasta = D.n if hasta is None else hasta
    a = D.ind("atr", e["atr_n"])
    riesgo = capital * riesgo_pct / 100
    lados = {"long": [1], "short": [-1], "ambas": [1, -1]}[e["dir"]]
    entradas = sorted((i, s) for s in lados for i in senales(D, e, s) if desde <= i < hasta - 1)
    sin = P.get("sin_entradas_utc")
    canal_h = D.ind("minl", e["canal"]) if e.get("canal") else None
    canal_hh = D.ind("maxh", e["canal"]) if e.get("canal") else None
    desl = P.get("deslizamiento_pct", 0.0) / 100
    ops, libre = [], -1
    for i, s in entradas:
        if i < libre:
            continue
        j = i + 1
        t = D.t[j]
        if sin and sin[0] <= t.hour < sin[1]:
            continue
        if e.get("intradia") and not (9 <= D.ny[j].hour < 15):
            continue
        dist = e["sl"] * a[i]
        if dist <= 0:
            continue
        precio = D.o[j] + s * (P["spread"] + D.o[j] * desl)  # se compra al ask, se vende al bid
        stop = precio - s * dist
        tp = precio + s * e["tp"] * a[i] if e.get("tp") else None
        tam = riesgo / dist
        tam = min(tam, capital * P["apalancamiento"] / precio)
        salida, motivo, k = None, "fin de datos", j
        for k in range(j, hasta):
            lo, hi = D.l[k], D.h[k]
            if s > 0 and lo <= stop:
                salida, motivo = min(D.o[k], stop) if k > j else stop, "stop"
            elif s < 0 and hi >= stop:
                salida, motivo = max(D.o[k], stop) if k > j else stop, "stop"
            elif tp is not None and ((s > 0 and hi > tp) or (s < 0 and lo < tp)):
                salida, motivo = tp, "objetivo"
            elif e.get("barras") and k - j + 1 >= e["barras"]:
                salida, motivo = D.c[k], "tiempo"
            elif canal_h and k > j and ((s > 0 and D.c[k] < canal_h[k]) or (s < 0 and D.c[k] > canal_hh[k])):
                salida, motivo = D.c[k], "canal"
            elif e.get("intradia") and D.ny[k].hour >= 15:
                salida, motivo = D.c[k], "fin de sesión"
            if salida is not None:
                break
        if salida is None:
            salida = D.c[k]
        noches = max(0, (D.t[k].date() - t.date()).days)
        nocional = tam * precio
        coste = nocional * (P["comision_pct"] / 100 + noches *
                            (P["swap_largo_pct"] if s > 0 else P["swap_corto_pct"]) / 100) + tam * salida * desl
        neto = s * (salida - precio) * tam - coste
        ops.append({"i": j, "k": k, "opened": t, "closed": D.t[k], "dir": "long" if s > 0 else "short",
                    "entry": precio, "exit": salida, "pnl": neto, "R": neto / riesgo, "reason": motivo})
        libre = k + 1
    return ops


def stats(ops: list[dict], capital: float = 100_000.0, dias: float | None = None) -> dict:
    n = len(ops)
    if not n:
        return {"trades": 0, "neto": 0.0, "ret_pct": 0.0, "dd": 0.0, "dd_pct": 0.0, "ret_dd": 0.0, "pf": 0.0,
                "acierto": 0.0, "anual_pct": 0.0, "barras_media": 0.0}
    pnl = [o["pnl"] for o in ops]
    eq = list(accumulate(pnl))
    picos = list(accumulate([0.0] + eq, max))[1:]
    dd = max([0.0] + [p - x for p, x in zip(picos, eq)])
    neto = eq[-1]
    gan, per = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x < 0)
    if dias is None:
        dias = max((ops[-1]["closed"] - ops[0]["opened"]).days, 1)
    return {"trades": n, "neto": neto, "ret_pct": 100 * neto / capital, "dd": dd, "dd_pct": 100 * dd / capital,
            "ret_dd": neto / dd if dd else (99.0 if neto > 0 else 0.0), "pf": gan / per if per else (99.0 if gan else 0.0),
            "acierto": 100 * sum(x > 0 for x in pnl) / n, "anual_pct": 100 * neto / capital * 365 / max(dias, 1),
            "barras_media": sum(o["k"] - o["i"] + 1 for o in ops) / n}


# --- robustez -------------------------------------------------------------------------------------
def monte_carlo(ops: list[dict], vueltas: int = 2000, semilla: int = 7, capital: float = 100_000.0) -> dict:
    """Baraja las operaciones con reposición. Devuelve % de vueltas que ganan y la caída del percentil 95."""
    pnl = [o["pnl"] for o in ops]
    if len(pnl) < 2:
        return {"pct_ganan": 0.0, "dd95_pct": 0.0}
    rnd = random.Random(semilla)
    n, ganan, dds = len(pnl), 0, []
    for _ in range(vueltas):
        m = rnd.choices(pnl, k=n)
        eq = list(accumulate(m))
        if eq[-1] > 0:
            ganan += 1
        picos = accumulate([0.0] + eq, max)
        next(picos)
        dds.append(max(map(lambda p, x: p - x, picos, eq)))
    dds.sort()
    return {"pct_ganan": 100 * ganan / vueltas, "dd95_pct": 100 * dds[int(0.95 * (len(dds) - 1))] / capital}


def vecina(e: dict, rnd: random.Random, margen: float = 0.20) -> dict:
    """Mueve cada parámetro numérico hasta ±20 % (los enteros se redondean y nunca bajan de 1)."""
    def mover(x):
        if isinstance(x, bool) or x is None:
            return x
        y = x * (1 + rnd.uniform(-margen, margen))
        return max(1, round(y)) if isinstance(x, int) else round(y, 3)
    cond = [(n, {k: mover(v) for k, v in p.items() if k != "dia" and k != "hora"} | {k: v for k, v in p.items()
                                                                                    if k in ("dia", "hora")})
            for n, p in e["cond"]]
    out = dict(e, cond=cond)
    for k in ("atr_n", "sl", "tp", "barras", "canal"):
        out[k] = mover(e.get(k))
    return out


def vecinos(D: Datos, e: dict, desde: int, hasta: int, n: int = 150, semilla: int = 11) -> dict:
    rnd = random.Random(semilla)
    puntos = []
    for _ in range(n):
        st = stats(simular(D, vecina(e, rnd), desde, hasta))
        puntos.append((st["anual_pct"], st["dd_pct"], st["neto"]))
    ganan = sum(1 for p in puntos if p[2] > 0)
    anuales = sorted(p[0] for p in puntos)
    return {"pct_ganan": 100 * ganan / n, "mediana_anual": anuales[len(anuales) // 2], "puntos": puntos}


# --- la fábrica entera ----------------------------------------------------------------------------
CRITERIOS = {"min_ops": 100, "min_acierto": 30.0, "min_ret_dd": 3.0, "mc_pct": 95.0, "vecinos_pct": 70.0,
             "max_mc": 300, "max_vecinos": 30, "vueltas": 2000, "n_vecinos": 150}


def _parecida(a: dict, b: dict) -> bool:
    return [n for n, _ in a["cond"]] == [n for n, _ in b["cond"]] and a["dir"] == b["dir"]


def correr(D: Datos, n: int = 3000, corte: datetime | None = None, semilla: int = 1, criterios: dict | None = None,
           progreso=print) -> dict:
    """Genera `n` estrategias, las pasa por el embudo y devuelve el informe (dict listo para JSON/HTML)."""
    C = dict(CRITERIOS, **(criterios or {}))
    P = perfil(D.simbolo)
    if corte is None:  # el último 25 % de las velas queda fuera de muestra
        corte = D.t[int(D.n * 0.75)]
    k_corte = next((i for i, t in enumerate(D.t) if t >= corte), D.n)
    rnd = random.Random(semilla)
    dias_is = max((D.t[k_corte - 1] - D.t[0]).days, 1)
    candidatas = tortugas() + [aleatoria(rnd, P["intradia"]) for _ in range(n)]
    vistas, filas, distintas = set(), [], 0
    embudo = {"generadas": len(candidatas)}
    for num, e in enumerate(candidatas):
        if num and num % 500 == 0:
            progreso(f"  {num}/{len(candidatas)} estrategias probadas…")
        ops = simular(D, e, 0, k_corte, P=P)
        huella = hashlib.md5(repr([(o["i"], o["dir"], round(o["pnl"], 2)) for o in ops]).encode()).hexdigest()
        if huella in vistas:
            continue
        vistas.add(huella)
        distintas += 1
        st = stats(ops, dias=dias_is)
        filas.append({"id": num, "e": e, "is": st, "familia": BLOQUES[e["cond"][0][0]][0]})
    embudo["distintas"] = distintas
    sin_ops = sum(1 for f in filas if f["is"]["trades"] == 0)
    ganadoras = [f for f in filas if f["is"]["neto"] > 0]
    embudo["sin_operaciones"] = sin_ops
    embudo["ganadoras"] = len(ganadoras)
    pasan = [f for f in ganadoras if f["is"]["trades"] >= C["min_ops"] and f["is"]["acierto"] >= C["min_acierto"]
             and f["is"]["ret_dd"] >= C["min_ret_dd"]]
    embudo["filtros_basicos"] = len(pasan)
    pasan.sort(key=lambda f: -f["is"]["ret_dd"])
    mc_ok = []
    for f in pasan[:C["max_mc"]]:
        f["mc"] = monte_carlo(simular(D, f["e"], 0, k_corte, P=P), C["vueltas"], semilla=f["id"])
        if f["mc"]["pct_ganan"] >= C["mc_pct"]:
            mc_ok.append(f)
    embudo["monte_carlo"] = len(mc_ok)
    unicas = []
    for f in mc_ok:  # quitar las muy parecidas (mismas reglas y dirección): se queda la mejor
        if not any(_parecida(f["e"], u["e"]) for u in unicas):
            unicas.append(f)
    embudo["sin_parecidas"] = len(unicas)
    estables = []
    for f in unicas[:C["max_vecinos"]]:
        f["vecinos"] = vecinos(D, f["e"], 0, k_corte, C["n_vecinos"], semilla=f["id"])
        if f["vecinos"]["pct_ganan"] >= C["vecinos_pct"]:
            estables.append(f)
    embudo["vecinos"] = len(estables)
    from . import fondeo
    finalistas = []
    for f in estables:
        oos = simular(D, f["e"], k_corte, D.n, P=P)
        f["oos"] = stats(oos, dias=max((D.t[-1] - D.t[k_corte]).days, 1) if k_corte < D.n else 1)
        todas = simular(D, f["e"], 0, D.n, P=P)
        f["todo"] = stats(todas)
        f["fondeo"] = fondeo.examen(todas, 100_000.0)
        f["curva"] = [[o["closed"].isoformat()[:10], round(x, 2)] for o, x in
                      zip(todas, accumulate(o["pnl"] for o in todas))]
        if f["oos"]["neto"] > 0:
            finalistas.append(f)
    embudo["fuera_de_muestra"] = len(finalistas)
    finalistas.sort(key=lambda f: -(f["is"]["ret_dd"] * min(f["oos"]["ret_dd"], f["is"]["ret_dd"])))
    por_familia = {}
    for f in filas:
        d = por_familia.setdefault(f["familia"], {"probadas": 0, "ganan": 0})
        d["probadas"] += 1
        d["ganan"] += f["is"]["neto"] > 0
    hold = (D.c[k_corte - 1] / D.c[0] - 1) * 100 if k_corte > 0 else 0.0
    tortuga = [f for f in filas if f["e"].get("nombre")]
    return {"simbolo": D.simbolo, "nombre": P["nombre"] or D.simbolo, "velas": D.n,
            "desde": D.t[0].isoformat()[:10], "corte": D.t[min(k_corte, D.n - 1)].isoformat()[:10],
            "hasta": D.t[-1].isoformat()[:10], "criterios": C, "perfil": P, "embudo": embudo,
            "por_familia": por_familia, "comprar_y_mantener_is_pct": hold,
            "tortugas": [{"nombre": f["e"]["nombre"], "is": f["is"]} for f in tortuga],
            "finalistas": [_limpia(f) for f in finalistas[:10]],
            "nube": [[round(f["is"]["anual_pct"], 2), round(f["is"]["dd_pct"], 2)] for f in filas
                     if f["is"]["trades"] >= 20][:4000]}


def _limpia(f: dict) -> dict:
    out = {k: v for k, v in f.items() if k != "e"}
    out["reglas"] = describir(f["e"])
    out["estrategia"] = f["e"]
    out["firma"] = firma({"c": repr(f["e"])})
    if "vecinos" in out:
        out["vecinos"] = dict(out["vecinos"], puntos=[[round(a, 2), round(b, 2)] for a, b, _ in
                                                       out["vecinos"]["puntos"]])
    return out
