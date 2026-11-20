"""Conecta TU bot de Telegram (el que creaste con BotFather) para que el Consejo te escriba.

    py configurar_telegram.py

1. Antes, escribe cualquier cosa (por ejemplo «hola») al bot en Telegram.
2. Copia el token del mensaje de BotFather y pulsa Enter cuando te lo pida: lo lee del
   portapapeles (o pégalo con clic derecho). No se muestra ni se imprime nunca.
3. Guarda telegram.json en esta carpeta (está en .gitignore: nunca se sube a GitHub)
   y te manda un mensaje de prueba.

El bot solo ENVÍA mensajes de texto. No lee tus chats ni toca dinero.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

RAIZ = os.path.dirname(os.path.abspath(__file__))


def _api(token: str, metodo: str, datos: dict | None = None) -> dict:
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{metodo}",
                                 data=json.dumps(datos).encode() if datos else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def chat_privado(updates: dict) -> tuple[str, str] | None:
    """El último chat privado que escribió al bot: (chat_id, nombre)."""
    for u in reversed(updates.get("result", [])):
        chat = (u.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private":
            return str(chat["id"]), chat.get("first_name", "")
    return None


FORMATO = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")


def limpiar(texto: str) -> str:
    """Quita espacios, saltos de línea y caracteres de control (Ctrl+V en una entrada oculta mete \\x16)."""
    for trozo in texto.split():
        trozo = "".join(ch for ch in trozo if ch.isprintable())
        if FORMATO.fullmatch(trozo):
            return trozo
    return ""


def portapapeles() -> str:
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True,
                              text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return ""


def pedir_token() -> str:
    for _ in range(3):
        crudo = getpass.getpass("Copia el token de BotFather y pulsa Enter (o pégalo con clic derecho; no se verá): ")
        token = limpiar(crudo) or (limpiar(portapapeles()) if not crudo.strip() else "")
        if token:
            return token
        print(f"Eso no parece un token (recibidos {len(crudo)} caracteres). Tiene la forma 123456789:AAH...; "
              "cópialo entero del mensaje de BotFather y prueba otra vez.")
    return ""


def main() -> int:
    token = pedir_token()
    if not token:
        return 1
    try:
        yo = _api(token, "getMe")["result"]
    except urllib.error.HTTPError as e:
        print("Telegram dice que ese token no es válido (¿lo revocaste?). Pide uno nuevo con /token en BotFather."
              if e.code in (401, 404) else f"Telegram respondió con error {e.code}. Prueba en un minuto.")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"No hay conexión con Telegram ({type(e).__name__}). Revisa internet y vuelve a ejecutarlo.")
        return 1
    chat = chat_privado(_api(token, "getUpdates"))
    if chat is None:
        print(f"Escribe «hola» a @{yo['username']} en Telegram y vuelve a ejecutar esto.")
        return 1
    ruta = os.path.join(RAIZ, "telegram.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"chat_id": chat[0], "token": token}, f, indent=2)
    _api(token, "sendMessage", {"chat_id": chat[0], "text": "✅ El Consejo ya puede escribirte aquí. "
                                "Todo es simulado: dinero ficticio, sin órdenes reales."})
    print(f"Listo: @{yo['username']} escribirá a {chat[1] or 'tu chat'}. Configuración en telegram.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
