"""Averigua el chat_id del GRUPO y de tu CHAT PRIVADO con el bot.

1. Añade los 6 bots al grupo y escribe en el grupo:  /start@NOMBRE_DE_TU_BOT_RISK
2. Abre el bot Risk en privado (búscalo por su nombre) y pulsa INICIAR o escribe /start
3. Ejecuta:  python get_chat_id.py TOKEN_DEL_BOT_RISK
"""
import sys

import requests

if len(sys.argv) < 2:
    raise SystemExit("Uso: python get_chat_id.py TOKEN_DEL_BOT")
r = requests.get(f"https://api.telegram.org/bot{sys.argv[1]}/getUpdates", timeout=20).json()
groups, private = {}, {}
for u in r.get("result", []):
    msg = u.get("message") or u.get("my_chat_member") or {}
    chat = msg.get("chat", {})
    if chat.get("type") in ("group", "supergroup"):
        groups[chat["id"]] = chat.get("title")
    elif chat.get("type") == "private":
        private[chat["id"]] = chat.get("first_name", "")
for cid, title in groups.items():
    print(f"GRUPO '{title}'  → telegram.chat_id: {cid}")
for cid, name in private.items():
    print(f"PRIVADO con {name} → telegram.private_chat_id: {cid}")
if not groups and not private:
    print("No veo nada. Escribe /start al bot (en el grupo y en privado) y vuelve a ejecutarlo.")
