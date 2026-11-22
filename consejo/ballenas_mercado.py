"""Ballenas de BTC/ETH y de los mercados (solo LECTURA de datos públicos, sin claves).

1. BTC y ETH: operaciones gigantes en Binance (aggTrades públicos de data-api.binance.vision).
   Una orden grande suele trocearse, así que se suman las operaciones del mismo lado dentro del
   mismo segundo: si pasan del umbral, es una «impresión de ballena» (compra si el agresor compra).
   Cada hora cerrada se apunta cuánto compraron y vendieron las ballenas; si compran mucho más de lo
   que venden, es una SEÑAL DE COMPRA de ballenas.

2. Mercados (oro, plata, crudo, S&P 500, Nasdaq, Russell, euro, bonos y también BTC/ETH de CME):
   el informe COT semanal de la CFTC (publicreporting.cftc.gov) dice qué hacen los grandes fondos
   especulativos. Si suben mucho su posición neta comprada, es una SEÑAL DE COMPRA de ballenas.

Las señales van a una cartera de COPIA en papel (consejo/copia.py) para medir si seguirlas gana.
NUNCA dinero real.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

UA = "Mozilla/5.0 (compatible; bot-trading/0.1)"
BINANCE = "https://data-api.binance.vision/api/v3/aggTrades"
SOCRATA = "https://publicreporting.cftc.gov/resource/{ds}.json"

# Umbral de una «impresión de ballena» (suma del mismo lado en el mismo segundo), en dólares.
# Medido en la torre el 22-nov (domingo tranquilo): p99,9 de una operación ≈ 95k $ en BTC y 32k $ en ETH.
UMBRAL_USD = {"BTCUSDT": 250_000, "ETHUSDT": 100_000}
# Señal de compra en una hora: neto comprado por ballenas ≥ este importe y compras ≥ 2× ventas.
NETO_SENAL_USD = {"BTCUSDT": 2_000_000, "ETHUSDT": 800_000}
RATIO_SENAL = 2.0

# COT: código de contrato de la CFTC -> (dataset, grupo de «ballenas», símbolo que se copia en papel)
#   72hh-3qpy = Disaggregated (managed money = fondos especulativos)
#   gpe5-46if = Traders in Financial Futures (leveraged funds = fondos especulativos)
COT = {
    "088691": ("72hh-3qpy", "m_money", "GC=F", "Oro"),
    "084691": ("72hh-3qpy", "m_money", "SLV", "Plata"),
    "067651": ("72hh-3qpy", "m_money", "USO", "Crudo WTI"),
    "13874A": ("gpe5-46if", "lev_money", "SPY", "S&P 500"),
    "209742": ("gpe5-46if", "lev_money", "QQQ", "Nasdaq 100"),
    "239742": ("gpe5-46if", "lev_money", "IWM", "Russell 2000"),
    "124603": ("gpe5-46if", "lev_money", "DIA", "Dow Jones"),
    "099741": ("gpe5-46if", "lev_money", "EURUSD=X", "Euro"),
    "020601": ("gpe5-46if", "lev_money", "TLT", "Bonos EE. UU."),
    "133741": ("gpe5-46if", "lev_money", "BTCUSDT", "Bitcoin (CME)"),
    "146021": ("gpe5-46if", "lev_money", "ETHUSDT", "Ether (CME)"),
}
# Señal COT: el neto de los fondos sube al menos este % del interés abierto en una semana.
COT_SENAL_PCT_OI = 3.0


def _get(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# --- 1. Flujo de ballenas en Binance ---------------------------------------------------------------

def impresiones(trades: list[dict], simbolo: str, umbral: float | None = None) -> list[dict]:
    """aggTrades -> impresiones de ballena: mismo segundo y mismo lado, suma ≥ umbral."""
    umbral = umbral or UMBRAL_USD.get(simbolo, 250_000)
    grupos: dict = defaultdict(float)
    precio: dict = {}
    for x in trades:
        lado = "venta" if x["m"] else "compra"        # m = el comprador es maker -> el agresor vende
        k = (int(x["T"]) // 1000, lado)
        usd = float(x["p"]) * float(x["q"])
        grupos[k] += usd
        precio[k] = float(x["p"])
    return [{"t": k[0], "lado": k[1], "usd": round(v, 2), "precio": precio[k]}
            for k, v in sorted(grupos.items()) if v >= umbral]


class FlujoBinance:
    """Lee TODAS las operaciones nuevas (paginando con fromId) y acumula las ballenas por hora."""

    def __init__(self, simbolos=("BTCUSDT", "ETHUSDT"), get=_get):
        self.simbolos, self.get = list(simbolos), get
        self.desde: dict[str, int | None] = {s: None for s in simbolos}
        self.horas: dict[str, dict] = {s: {} for s in simbolos}   # hora (epoch s) -> {"compra","venta","n"}
        self.grandes: list[dict] = []                              # impresiones nuevas para avisar

    def leer(self, max_paginas: int = 20) -> None:
        for s in self.simbolos:
            for _ in range(max_paginas):
                q = {"symbol": s, "limit": 1000}
                if self.desde[s] is not None:
                    q["fromId"] = self.desde[s]
                lote = self.get(BINANCE + "?" + urllib.parse.urlencode(q))
                if not lote:
                    break
                if self.desde[s] is None:            # primera vez: se empieza desde ahora, sin pasado
                    self.desde[s] = lote[-1]["a"] + 1
                    break
                self.desde[s] = lote[-1]["a"] + 1
                for imp in impresiones(lote, s):
                    h = imp["t"] // 3600 * 3600
                    acc = self.horas[s].setdefault(h, {"compra": 0.0, "venta": 0.0, "n": 0})
                    acc[imp["lado"]] += imp["usd"]
                    acc["n"] += 1
                    self.grandes.append({"symbol": s, **imp})
                if len(lote) < 1000:
                    break

    def horas_cerradas(self, ahora: float | None = None) -> list[tuple[str, int, dict]]:
        """Devuelve (y olvida) las horas ya cerradas con su resumen."""
        ahora = ahora or time.time()
        hora_actual = int(ahora) // 3600 * 3600
        out = []
        for s, hs in self.horas.items():
            for h in sorted(x for x in hs if x < hora_actual):
                out.append((s, h, hs.pop(h)))
        return out


def senal_flujo(simbolo: str, resumen: dict) -> str | None:
    """Texto de la señal si las ballenas compraron fuerte esa hora; None si no."""
    neto = resumen["compra"] - resumen["venta"]
    if neto >= NETO_SENAL_USD.get(simbolo, 2_000_000) and resumen["compra"] >= RATIO_SENAL * max(resumen["venta"], 1):
        return (f"ballenas compran {resumen['compra'] / 1e6:.1f} M$ y venden {resumen['venta'] / 1e6:.1f} M$ "
                f"en una hora ({resumen['n']} órdenes gigantes)")
    return None


# --- 2. COT de la CFTC --------------------------------------------------------------------------

def cot_ultimos(codigo: str, get=_get, n: int = 2) -> list[dict]:
    ds = COT[codigo][0]
    q = {"$where": f"cftc_contract_market_code='{codigo}'", "$order": "report_date_as_yyyy_mm_dd DESC",
         "$limit": n}
    return get(SOCRATA.format(ds=ds) + "?" + urllib.parse.urlencode(q))


def _num(fila: dict, campo: str) -> float:
    try:
        return float(fila.get(campo) or 0)
    except (TypeError, ValueError):
        return 0.0


def cot_resumen(codigo: str, filas: list[dict]) -> dict | None:
    """Neto de los fondos (largos − cortos), su cambio en la semana y en % del interés abierto."""
    if len(filas) < 2:
        return None
    grupo = COT[codigo][1]
    largo = f"{grupo}_positions_long_all" if grupo == "m_money" else f"{grupo}_positions_long"
    corto = f"{grupo}_positions_short_all" if grupo == "m_money" else f"{grupo}_positions_short"
    neto = _num(filas[0], largo) - _num(filas[0], corto)
    antes = _num(filas[1], largo) - _num(filas[1], corto)
    oi = _num(filas[0], "open_interest_all") or 1.0
    return {"codigo": codigo, "nombre": COT[codigo][3], "simbolo": COT[codigo][2],
            "fecha": str(filas[0].get("report_date_as_yyyy_mm_dd", ""))[:10], "neto": neto, "cambio": neto - antes,
            "cambio_pct_oi": round((neto - antes) / oi * 100, 2), "neto_pct_oi": round(neto / oi * 100, 1)}


def senal_cot(r: dict) -> str | None:
    if r and r["cambio_pct_oi"] >= COT_SENAL_PCT_OI:
        return (f"los grandes fondos suben su neto comprado en {r['nombre']} en {r['cambio']:+,.0f} contratos "
                f"({r['cambio_pct_oi']:+.1f} % del interés abierto) en la semana del {r['fecha']}")
    return None


def texto_cot(r: dict) -> str:
    flecha = "🟢" if r["cambio_pct_oi"] >= COT_SENAL_PCT_OI else "🔴" if r["cambio_pct_oi"] <= -COT_SENAL_PCT_OI else "⚪"
    return (f"{flecha} {r['nombre']}: neto {r['neto']:+,.0f} contratos ({r['neto_pct_oi']:+.0f} % del interés "
            f"abierto), cambio semanal {r['cambio']:+,.0f} ({r['cambio_pct_oi']:+.1f} %)")


def hora_txt(h: int) -> str:
    return datetime.fromtimestamp(h, tz=timezone.utc).strftime("%d/%m %H:00 UTC")


def guardar_linea(ruta: str, x: dict) -> None:
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(x, ensure_ascii=False) + "\n")
