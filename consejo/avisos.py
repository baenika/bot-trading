"""Avisos al móvil (por el bot de Telegram) de lo que David tiene que saber sin buscarlo:
errores, paradas, datos que no llegan, versiones nuevas y cambios del autoajuste.

Cada aviso repetido se manda como mucho una vez cada `cada_h` horas, para no llenar el chat.
Queda apuntado en salida/avisos.json (el informe semanal los cuenta).
"""

from __future__ import annotations

import json
import os
import subprocess
import traceback
from datetime import datetime, timedelta, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA = os.path.join(RAIZ, "salida", "avisos.json")
ICONOS = {"error": "⚠️", "parada": "⛔", "datos": "📡", "novedad": "🆕", "cambio": "⚙️"}


class Avisador:
    def __init__(self, grupo, origen: str, ruta: str | None = None, cada_h: float = 6.0):
        self.grupo, self.origen, self.ruta, self.cada = grupo, origen, ruta or RUTA, timedelta(hours=cada_h)

    def _leer(self) -> dict:
        try:
            with open(self.ruta, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"ultimo": {}, "registro": [], "version": {}}

    def _guardar(self, d: dict) -> None:
        os.makedirs(os.path.dirname(self.ruta), exist_ok=True)
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)

    def avisar(self, tipo: str, clave: str, texto: str, ahora: datetime | None = None) -> bool:
        """Manda el aviso si no se mandó el mismo en las últimas horas. Devuelve si se mandó."""
        ahora = ahora or datetime.now(timezone.utc)
        d = self._leer()
        k = f"{self.origen}:{tipo}:{clave}"
        prev = d["ultimo"].get(k)
        if prev and ahora - datetime.fromisoformat(prev) < self.cada:
            return False
        d["ultimo"][k] = ahora.isoformat(timespec="seconds")
        d["registro"] = (d.get("registro", []) + [{"t": d["ultimo"][k], "origen": self.origen, "tipo": tipo,
                                                    "texto": texto[:300]}])[-500:]
        self._guardar(d)
        self.grupo.enviar("risk", f"{ICONOS.get(tipo, '🔔')} [{self.origen}] {texto}")
        return True

    def error(self, e: BaseException, donde: str) -> None:
        linea = traceback.format_exception_only(type(e), e)[-1].strip()[:200]
        self.avisar("error", f"{donde}:{type(e).__name__}",
                    f"Error en {donde}: {linea}\nEl bot sigue funcionando y lo reintenta. "
                    "Si se repite, revísalo (todo sigue en papel).")

    def version(self) -> None:
        """Si el código cambió desde la última vez que arrancó, avisa de qué hay nuevo."""
        try:
            sha = subprocess.run(["git", "-C", RAIZ, "rev-parse", "--short", "HEAD"], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
        except Exception:  # noqa: BLE001
            return
        if not sha:
            return
        d = self._leer()
        antes = d.get("version", {}).get(self.origen)
        if antes == sha:
            return
        try:
            log = subprocess.run(["git", "-C", RAIZ, "log", "--format=• %s", f"{antes}..{sha}" if antes else "-1"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:  # noqa: BLE001
            log = ""
        d.setdefault("version", {})[self.origen] = sha
        self._guardar(d)
        if antes:
            self.avisar("novedad", sha, f"Bot actualizado ({antes} → {sha}). Novedades:\n{log[:1500] or '—'}")


def registro(desde: datetime, ruta: str | None = None) -> list[dict]:
    try:
        with open(ruta or RUTA, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    return [x for x in d.get("registro", []) if datetime.fromisoformat(x["t"]) >= desde]
