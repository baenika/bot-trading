"""Las mesas del Consejo y qué vigila cada una.

- cripto: BTC y ETH, con velas de Binance (data.binance.vision).
- mercados: lo que más se tradea hoy (índices, ETFs, oro, petróleo, divisas),
  con velas de Yahoo Finance. Es solo lectura de precios públicos.
"""

CRIPTO = {
    "BTCUSDT": "Bitcoin",
    "ETHUSDT": "Ethereum",
}

# símbolo de Yahoo -> nombre para el panel
MERCADOS = {
    "SPY": "S&P 500 (ETF SPY)",
    "QQQ": "Nasdaq 100 (ETF QQQ)",
    "DIA": "Dow Jones (ETF DIA)",
    "IWM": "Russell 2000 (ETF IWM)",
    "GLD": "Oro (ETF GLD)",
    "SLV": "Plata (ETF SLV)",
    "USO": "Petróleo (ETF USO)",
    "TLT": "Bonos EE. UU. 20 años (ETF TLT)",
    "EURUSD=X": "EUR/USD",
    "^IBEX": "IBEX 35",
    "^GDAXI": "DAX",
}

# Ajustes que cambian por mesa (el resto sale de backtest.CONFIG_DEFECTO).
AJUSTES = {
    "cripto": {"capital_inicial": 10000.0, "comision_pct": 0.10},
    # ETFs e índices: comisiones más bajas y mercado con horario (las velas
    # de noche y fin de semana no existen, el bot las salta sin más).
    "mercados": {"capital_inicial": 10000.0, "comision_pct": 0.05, "deslizamiento_pct": 0.03},
}


def archivo(simbolo: str, intervalo: str = "1h") -> str:
    """Nombre de archivo seguro para un símbolo (sin ^ ni =)."""
    limpio = simbolo.replace("^", "IDX_").replace("=X", "_FX").replace("=", "_")
    return f"{limpio}-{intervalo}.csv"


def simbolo_de_archivo(nombre: str) -> str:
    base = nombre.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].split("-")[0]
    return base.replace("IDX_", "^").replace("_FX", "=X")
