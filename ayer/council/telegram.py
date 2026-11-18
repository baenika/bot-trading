"""Envío al grupo de Telegram: cada bot habla con su propio token, así en el grupo
se ven 6 bots distintos conversando. Sin tokens, imprime la conversación en la consola."""
import time

import requests

NAMES = {"scout": "🔭 Scout", "news": "📰 Noticias", "chart": "📈 Chart",
         "sentimental": "🧊 Sentimental", "skeptic": "🤨 Skeptic", "risk": "🛡️ Risk"}


class TelegramGroup:
    def __init__(self, cfg: dict):
        self.chat_id = str(cfg.get("chat_id", "")).strip()
        self.tokens = {k: (v or "").strip() for k, v in (cfg.get("tokens") or {}).items()}
        self.delay = cfg.get("delay_seconds", 1.2)

    def _enabled(self, bot):
        return bool(self.chat_id and self.tokens.get(bot) and "PON_AQUI" not in self.tokens[bot])

    def send(self, bot: str, text: str):
        if not self._enabled(bot):
            plain = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
            print(f"\n[{NAMES.get(bot, bot)}]\n{plain}")
            return
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.tokens[bot]}/sendMessage",
                              json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                    "disable_web_page_preview": True}, timeout=20)
            if r.status_code == 429:
                time.sleep(r.json().get("parameters", {}).get("retry_after", 5))
                return self.send(bot, text)
            r.raise_for_status()
        except Exception as e:
            print(f"[telegram] error enviando como {bot}: {e}")
        time.sleep(self.delay)

    def conversation(self, msgs):
        for bot, text in msgs:
            self.send(bot, text)
