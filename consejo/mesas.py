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
    "GC=F": "Oro (futuros, se mueve como XAU/USD)",
    "SLV": "Plata (ETF SLV)",
    "USO": "Petróleo (ETF USO)",
    "TLT": "Bonos EE. UU. 20 años (ETF TLT)",
    "EURUSD=X": "EUR/USD",
    "^IBEX": "IBEX 35",
    "^GDAXI": "DAX",
}

# Búsqueda de noticias en GDELT para cada símbolo (frases entre comillas, OR entre paréntesis).
NOTICIAS = {
    "BTCUSDT": '(bitcoin OR "BTC price")',
    "ETHUSDT": '(ethereum OR "ether price")',
    "SPY": '("S&P 500" OR "Wall Street stocks")',
    "QQQ": '(nasdaq OR "tech stocks")',
    "DIA": '"Dow Jones"',
    "IWM": '("Russell 2000" OR "small caps")',
    "GLD": '("gold price" OR "gold prices")',
    "GC=F": '("gold price" OR "gold prices" OR "gold futures")',
    "SLV": '("silver price" OR "silver prices")',
    "USO": '("oil price" OR "oil prices" OR "crude oil")',
    "TLT": '("treasury yields" OR "bond yields")',
    "EURUSD=X": '("euro dollar" OR "EUR/USD" OR "euro falls" OR "euro rises")',
    "^IBEX": '("Ibex 35" OR "bolsa española")',
    "^GDAXI": '("DAX index" OR "German stocks")',
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
    limpio = simbolo.replace("^", "IDX_").replace("=X", "_FX").replace("=F", "_FUT").replace("=", "_")
    return f"{limpio}-{intervalo}.csv"


def simbolo_de_archivo(nombre: str) -> str:
    base = nombre.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].split("-")[0]
    return base.replace("IDX_", "^").replace("_FX", "=X").replace("_FUT", "=F")


def archivo_noticias(simbolo: str) -> str:
    """Nombre del archivo de noticias de un símbolo dentro de datos/noticias/."""
    return archivo(simbolo, "noticias").replace(".csv", ".json")
