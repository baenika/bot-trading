"""Candado de seguridad: este proyecto solo funciona en modo simulado.

David lo pidió explícitamente (18-nov-2026): nada con dinero real. Este módulo
no tiene ningún modo para desactivarlo. Si algún día se quiere operar en real,
tendrá que ser una decisión consciente que cambie este archivo.
"""

MODOS_PERMITIDOS = ("paper", "backtest")

# Nombres de variables de entorno que suelen llevar claves de brokers o exchanges.
_PISTAS_DE_CLAVES = ("API_KEY", "SECRET", "PRIVATE_KEY", "SEED", "MNEMONIC", "PASSPHRASE")


class ModoNoPermitido(RuntimeError):
    pass


def comprobar_modo(modo: str) -> str:
    """Devuelve el modo si es simulado; lanza ModoNoPermitido si no."""
    m = (modo or "").strip().lower()
    if m not in MODOS_PERMITIDOS:
        raise ModoNoPermitido(
            f"Modo '{modo}' bloqueado: este bot solo funciona en simulación "
            f"({', '.join(MODOS_PERMITIDOS)}). Nada de dinero real."
        )
    return m


def comprobar_config(cfg: dict) -> dict:
    """Valida una configuración entera antes de arrancar."""
    comprobar_modo(cfg.get("modo", "paper"))
    for k in cfg:
        if any(p in k.upper() for p in _PISTAS_DE_CLAVES):
            raise ModoNoPermitido(
                f"La configuración trae '{k}', que parece una clave. "
                "Este bot no usa claves de ningún broker ni exchange."
            )
    return cfg
