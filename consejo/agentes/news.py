"""News: busca por qué se mueve el precio.

En backtest no hay noticias históricas fiables, así que no opina. Más adelante
se puede conectar a fuentes públicas (RSS, calendario macro) en modo lectura.
"""


def opinar(alerta, en_backtest: bool = True) -> dict:
    if en_backtest:
        return {"veto": None, "pro": None, "texto": "Backtest: sin noticias históricas, no opino."}
    return {"veto": None, "pro": None, "texto": "Sin fuente de noticias configurada todavía."}
