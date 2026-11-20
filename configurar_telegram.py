"""Conecta TU bot de Telegram (el que creaste con BotFather) para que el Consejo te escriba.

    py configurar_telegram.py

1. Antes, escribe cualquier cosa (por ejemplo «hola») al bot en Telegram.
2. Pega el token cuando te lo pida. No se ve al escribir y no se imprime nunca.
3. Guarda telegram.json en esta carpeta (está en .gitignore: nunca se sube a GitHub)
   y te manda un mensaje de prueba.

El bot solo ENVÍA mensajes de texto. No lee tus chats ni toca dinero.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
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


def main() -> int:
    token = getpass.getpass("Pega el token del bot (no se verá): ").strip()
    try:
        yo = _api(token, "getMe")["result"]
    except Exception:  # noqa: BLE001
        print("Ese token no funciona. Cópialo otra vez del mensaje de BotFather.")
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
