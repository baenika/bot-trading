"""Tu chat privado con el bot: avisos importantes y comandos.

Comandos (solo responden a TU chat, nadie más puede controlarlo):
  /panel     → imagen del panel con capital, curva y último veredicto
  /estado    → resumen en texto
  /posiciones→ posiciones abiertas
  /stop      → interruptor: no se abren más operaciones
  /reanudar  → vuelve a operar
  /ayuda     → lista de comandos
"""
import requests

from . import panel_image

HELP = ("<b>Comandos</b>\n/panel — imagen del panel\n/memes — panel de la Mesa Memecoins\n/estado — resumen\n/posiciones — posiciones abiertas\n"
        "/stop — parar (no abre más operaciones)\n/reanudar — volver a operar\n/ayuda — esta lista")


class PrivateChat:
    def __init__(self, cfg: dict):
        t = cfg.get("telegram", {})
        self.token = (t.get("private_bot_token") or (t.get("tokens") or {}).get("risk") or "").strip()
        self.chat_id = str(t.get("private_chat_id", "")).strip()
        self.offset = None

    @property
    def enabled(self):
        return bool(self.token and self.chat_id and "PON_AQUI" not in self.token)

    def _api(self, method, **kw):
        return requests.post(f"https://api.telegram.org/bot{self.token}/{method}", timeout=30, **kw)

    def send(self, text):
        if not self.enabled:
            return
        try:
            self._api("sendMessage", json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                           "disable_web_page_preview": True})
        except Exception as e:
            print(f"[privado] {e}")

    def send_panel(self, snap):
        if not self.enabled:
            return
        try:
            self._api("sendPhoto", data={"chat_id": self.chat_id, "caption": panel_image.caption(snap),
                                         "parse_mode": "HTML"},
                      files={"photo": ("panel.png", panel_image.render(snap), "image/png")})
        except Exception as e:
            print(f"[privado] {e}")

    def poll(self):
        """Devuelve la lista de comandos nuevos enviados desde TU chat."""
        if not self.enabled:
            return []
        try:
            params = {"timeout": 0, "allowed_updates": '["message"]'}
            if self.offset is not None:
                params["offset"] = self.offset
            r = requests.get(f"https://api.telegram.org/bot{self.token}/getUpdates", params=params, timeout=20)
            out = []
            for u in r.json().get("result", []):
                self.offset = u["update_id"] + 1
                m = u.get("message") or {}
                if str(m.get("chat", {}).get("id")) != self.chat_id:
                    continue  # ignora a cualquiera que no seas tú
                txt = (m.get("text") or "").strip().split("@")[0].lower()
                if txt.startswith("/"):
                    out.append(txt.split()[0])
            return out
        except Exception as e:
            print(f"[privado] {e}")
            return []

    def handle(self, cmds, snap, state):
        """state: dict con la clave 'stopped' (se guarda entre reinicios)."""
        for c in cmds:
            if c in ("/panel", "/start"):
                self.send_panel(snap)
            elif c == "/memes":
                import json, os
                if os.path.exists("dashboard_memes.json"):
                    with open("dashboard_memes.json") as f:
                        self.send_panel(json.load(f))
                else:
                    self.send("La Mesa Memecoins no está en marcha (python memes_live.py).")
            elif c == "/estado":
                self.send(panel_image.caption(snap))
            elif c == "/posiciones":
                P = snap.get("positions", [])
                self.send("Sin posiciones abiertas." if not P else "\n".join(
                    f"<b>{p['symbol']}</b> {p['dir']} · entrada {p['entry']:.4g} · SL {p['sl']:.4g} · "
                    f"TP {p['tp']:.4g}" + (f" · {p['pnl']:+.2f} $" if p.get("pnl") is not None else " · esperando")
                    for p in P))
            elif c == "/stop":
                state["stopped"] = True
                self.send("⛔ <b>Parado.</b> No se abrirá ninguna operación nueva. "
                          "Las abiertas siguen con su stop y su objetivo. /reanudar para volver.")
            elif c == "/reanudar":
                state["stopped"] = False
                self.send("✅ Reanudado. El consejo vuelve a operar.")
            else:
                self.send(HELP)
