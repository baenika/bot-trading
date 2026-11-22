"""Grupo de Telegram del Consejo: cada bot habla con su propio token.

Solo ENVÍA mensajes de texto a tu grupo. No lee nada ni toca dinero.
Los tokens van en `telegram.json` (en tu ordenador, nunca en GitHub). Con un solo bot basta
(lo crea `py configurar_telegram.py`): {"chat_id": "...", "token": "..."}; cada mensaje lleva
delante el nombre del bot que habla. O un bot por agente:

    {"chat_id": "-100...", "tokens": {"scout": "...", "news": "...", "chart": "...",
                                       "sentimental": "...", "skeptic": "...", "risk": "..."}}

Con un grupo vinculado (`/vincular` escrito por ti dentro del grupo, ver consejo/mando.py) se añade
"grupo_id": "-100..." y desde entonces los avisos van al grupo en vez de a tu chat privado.

Sin ese archivo, la conversación se imprime en la consola.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

NOMBRES = {"scout": "🔭 Scout", "news": "📰 News", "chart": "📈 Charts",
           "sentimental": "🧊 Sentimental", "skeptic": "🤨 Skeptic", "risk": "🛡️ Risk"}


def _migrado(e: urllib.error.HTTPError) -> str:
    """Si Telegram dice que el grupo se convirtió en supergrupo, devuelve su número nuevo."""
    try:
        return str(json.loads(e.read() or b"{}").get("parameters", {}).get("migrate_to_chat_id", "") or "")
    except Exception:  # noqa: BLE001
        return ""


class Grupo:
    def __init__(self, ruta: str | None = None, pausa: float = 1.2):
        self.chat_id, self.tokens, self.pausa, self.comun = "", {}, pausa, ""
        self.ruta, self.grupo_id = ruta, ""
        self.silencio = False          # /avisos en Telegram: calla los debates (no las entradas ni los cierres)
        if ruta and os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as f:
                cfg = json.load(f)
            self.chat_id = str(cfg.get("chat_id", "")).strip()
            self.grupo_id = str(cfg.get("grupo_id", "") or "").strip()
            self.tokens = {k: (v or "").strip() for k, v in (cfg.get("tokens") or {}).items()}
            self.comun = (cfg.get("token") or "").strip()
            if self.comun:
                for bot in NOMBRES:
                    self.tokens.setdefault(bot, self.comun)

    @property
    def destino(self) -> str:
        """Adónde van los avisos: el grupo si está vinculado; si no, tu chat privado."""
        return self.grupo_id or self.chat_id

    def vincular_grupo(self, grupo_id: str) -> None:
        """Guarda el grupo en telegram.json (sin tocar el token) para que sobreviva a los reinicios."""
        self.grupo_id = str(grupo_id or "").strip()
        if not self.ruta or not os.path.exists(self.ruta):
            return
        with open(self.ruta, encoding="utf-8") as f:
            cfg = json.load(f)
        if self.grupo_id:
            cfg["grupo_id"] = self.grupo_id
        else:
            cfg.pop("grupo_id", None)
        tmp = self.ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, self.ruta)

    def activo(self, bot: str) -> bool:
        tok = self.tokens.get(bot, "")
        return bool(self.chat_id and tok and "PON_AQUI" not in tok)

    def enviar(self, bot: str, texto: str) -> None:
        if not self.activo(bot):
            print(f"\n[{NOMBRES.get(bot, bot)}]\n{texto}")
            return
        if self.comun and self.tokens[bot] == self.comun:
            texto = f"{NOMBRES.get(bot, bot)}\n{texto}"
        self._mandar(self.tokens[bot], texto)

    def _mandar(self, token: str, texto: str) -> None:
        datos = json.dumps({"chat_id": self.destino, "text": texto[:4000],
                            "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=datos,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=20).read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5)
                return self._mandar(token, texto)
            nuevo = _migrado(e)
            if nuevo and self.grupo_id:      # el grupo pasó a supergrupo y cambió de número
                self.vincular_grupo(nuevo)
                return self._mandar(token, texto)
            print(f"[telegram] error {e.code}")
        except Exception as e:  # noqa: BLE001 - sin red no se para el bot
            print(f"[telegram] {e}")
        time.sleep(self.pausa)

    def conversacion(self, mensajes: list[tuple[str, str]]) -> None:
        if self.silencio:
            return
        if self.comun and self.chat_id:
            # un solo bot: todo el debate en UN mensaje, cada agente con su nombre
            texto = "\n\n".join(f"{NOMBRES.get(b, b)}\n{t}" for b, t in mensajes)
            return self._mandar(self.comun, texto)
        for bot, texto in mensajes:
            self.enviar(bot, texto)
