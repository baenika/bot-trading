"""Manda el panel a tu chat privado de Telegram ahora mismo.

  python panel_telegram.py              → datos en vivo si existen; si no, el último backtest
  python panel_telegram.py --backtest   → siempre el último backtest
  python panel_telegram.py --guardar    → no envía nada, guarda panel.png para verlo
"""
import argparse
import json
import os

import yaml

from council import panel_image
from council.private_chat import PrivateChat

ap = argparse.ArgumentParser()
ap.add_argument("--config", default="config.yaml")
ap.add_argument("--backtest", action="store_true")
ap.add_argument("--guardar", action="store_true")
a = ap.parse_args()

cfg = yaml.safe_load(open(a.config if os.path.exists(a.config) else "config.example.yaml"))
paths = ["results/dashboard.json"] if a.backtest else ["dashboard.json", "results/dashboard.json"]
path = next((p for p in paths if os.path.exists(p)), None)
if not path:
    raise SystemExit("No hay datos. Lanza antes: python backtest.py")
snap = json.load(open(path))

if a.guardar:
    with open("panel.png", "wb") as f:
        f.write(panel_image.render(snap))
    print("Guardado en panel.png")
else:
    pc = PrivateChat(cfg)
    if not pc.enabled:
        raise SystemExit("Falta telegram.private_chat_id (y un token) en config.yaml. Mira el README, paso 3.")
    pc.send_panel(snap)
    print("Panel enviado a tu chat de Telegram.")
