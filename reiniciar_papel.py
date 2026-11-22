"""Pone a cero una cartera de PAPEL (dinero ficticio) sin perder su historia.

    py reiniciar_papel.py cripto        # también: mercados, lab, memes, copia
    py reiniciar_papel.py todo

No borra nada: mueve los archivos a salida/archivo/<fecha>/ para poder compararlos después.
Después hay que reiniciar el proceso de esa mesa para que empiece con el capital inicial.
"""

from __future__ import annotations

import glob
import os
import shutil
import sys
from datetime import datetime

RAIZ = os.path.dirname(os.path.abspath(__file__))
ARCHIVOS = {
    "cripto": ["papel_cripto.json", "estado_papel_cripto.json"],
    "mercados": ["papel_mercados.json", "estado_papel_mercados.json"],
    "lab": ["papel_cripto_lab.json", "estado_papel_cripto_lab.json"],
    "memes": ["papel_memes.json", "estado_papel_memes.json"],
    "copia": ["copia_*.json"],
}


def reiniciar(mesa: str, salida: str | None = None) -> list[str]:
    salida = salida or os.path.join(RAIZ, "salida")
    destino = os.path.join(salida, "archivo", datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    movidos = []
    for patron in ARCHIVOS[mesa]:
        for ruta in glob.glob(os.path.join(salida, patron)):
            os.makedirs(destino, exist_ok=True)
            shutil.move(ruta, os.path.join(destino, os.path.basename(ruta)))
            movidos.append(os.path.basename(ruta))
    return movidos


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in list(ARCHIVOS) + ["todo"]:
        print(__doc__)
        return 1
    for mesa in (list(ARCHIVOS) if args[0] == "todo" else [args[0]]):
        movidos = reiniciar(mesa)
        print(f"{mesa}: " + (f"archivados {', '.join(movidos)}" if movidos else "no había nada que reiniciar"))
    print("Reinicia el proceso de esa mesa para que empiece con el capital inicial (dinero ficticio).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
