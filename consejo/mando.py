"""Mando por Telegram: el mismo bot que avisa también obedece unos pocos comandos.

Solo obedece a David: su chat privado (el de telegram.json) y, dentro del grupo vinculado, solo a
los mensajes y botones que pulsa él. Comandos y botones:
    /panel              botones
    /analizar [activos] análisis ahora (por defecto BTC, ETH, S&P 500, Nasdaq y oro; p. ej. /analizar oro btc)
    /cartera            capital ficticio, posiciones abiertas y últimas operaciones (cripto y memes)
    /parametros         la configuración con la que está operando en papel
    /avisos             silencia o reactiva los avisos de decisiones (el bot sigue trabajando igual)
    /dashboard          enlace al panel en tiempo real (en el wifi de casa)
    /vincular           (escrito por David DENTRO de un grupo) desde ahora los avisos van a ese grupo
    /desvincular        los avisos vuelven al chat privado

No hay ningún comando que compre, venda o mueva dinero: todo es papel.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PARAMETROS = [("timeframe", "Velas"), ("capital_inicial", "Capital ficticio ($)"),
              ("risk_per_trade_pct", "Riesgo por operación (%)"), ("rr_minimo", "R:R mínimo"),
              ("stop_atr", "Stop (ATR)"), ("stop_max_atr", "Stop máximo (ATR)"), ("retroceso_pct", "Retroceso para entrar (%)"),
              ("espera_barras", "Velas esperando el retroceso"), ("max_barras", "Velas máximas por operación"), ("puntos_para_entrar", "Puntos para comprar"),
              ("puntos_para_esperar", "Puntos para esperar retroceso"), ("max_open_positions", "Posiciones a la vez"),
              ("daily_loss_limit_pct", "Pérdida máxima diaria (%)"), ("max_drawdown_pct", "Pérdida máxima total (%)"),
              ("z_volumen_min", "Volumen anómalo (σ)"), ("atr_vela_min", "Vela grande (ATR)"), ("comision_pct", "Comisión (%)"), ("moving_pct", "MOVING (%)"), ("fomo_atr", "FOMO (ATR)"),
              ("fomo_modo", "Con FOMO"), ("pausa_tras_perdidas", "Pérdidas seguidas para pausar"),
              ("pausa_horas", "Horas de pausa"), ("permitir_cortos", "Cortos")]


def _botones(avisos: bool, url_panel: str = "") -> dict:
    filas = [[{"text": "📊 Panel en tiempo real", "url": url_panel}]] if url_panel.startswith("http") else []
    return {"inline_keyboard": filas + [
        [{"text": "🔍 Analizar ahora", "callback_data": "/analizar"}, {"text": "💼 Cartera", "callback_data": "/cartera"}],
        [{"text": "⚙️ Parámetros", "callback_data": "/parametros"},
         {"text": "🔔 Avisos ON" if avisos else "🔕 Avisos OFF", "callback_data": "/avisos"}],
        [{"text": "📊 Enlace del panel", "callback_data": "/dashboard"}],
        [{"text": "🥇 Oro", "callback_data": "/analizar oro"}, {"text": "₿ BTC", "callback_data": "/analizar btc"},
         {"text": "📈 Nasdaq", "callback_data": "/analizar nasdaq"}]]}


def texto_cartera(ruta_cripto: str, ruta_memes: str) -> str:
    lin = []
    if os.path.exists(ruta_cripto):
        with open(ruta_cripto, encoding="utf-8") as f:
            e = json.load(f)
        ops = e.get("operaciones", [])
        lin.append(f"💼 Cripto (papel desde {str(e.get('inicio', ''))[:10]}): {e.get('capital', 0):,.2f} $ ficticios · "
                   f"{len(ops)} operaciones cerradas")
        for s, p in (e.get("abiertas") or {}).items():
            lin.append(f"  • {s}: entrada {p['entry']:.4g} · stop {p['stop']:.4g} · objetivo {p['tp']:.4g}")
        for o in ops[-3:]:
            lin.append(f"  {'🟢' if o['pnl'] > 0 else '🔴'} {o['symbol']} {o['pnl']:+.2f} $ ({o.get('R', 0):+.2f}R)")
        if not e.get("abiertas"):
            lin.append("  Sin posiciones abiertas.")
    if os.path.exists(ruta_memes):
        with open(ruta_memes, encoding="utf-8") as f:
            b = json.load(f).get("broker", {})
        pos = b.get("positions") or {}
        cash = b.get("cash", 0) + sum(p.get("usd", 0) for p in pos.values())
        lin.append(f"🪙 Memes: unos {cash:,.2f} $ ficticios · {len(pos)} abiertas · {len(b.get('trades', []))} cerradas")
    return "\n".join(lin) or "Todavía no hay cartera en papel."


def texto_parametros(cfg: dict) -> str:
    lin = ["⚙️ Parámetros del Consejo (papel)"]
    lin += [f"• {nombre}: {cfg[k]}" for k, nombre in PARAMETROS if k in cfg]
    lin.append("Se cambian en el repo, juntos; desde Telegram solo se consultan.")
    return "\n".join(lin)


class Mando:
    def __init__(self, grupo, cfg: dict, ruta_estado: str, ruta_memes: str | None = None, url_panel: str = "",
                 analista=None):
        self.grupo, self.cfg, self.ruta_estado, self.url_panel = grupo, cfg, ruta_estado, url_panel
        self.analista = analista     # analista("oro btc") -> texto; None = solo hace la pasada
        self.ruta_memes = ruta_memes or os.path.join(RAIZ, "salida", "papel_memes.json")
        self.token = grupo.comun or grupo.tokens.get("risk", "")
        self.chat = str(grupo.chat_id)
        self.offset = 0
        self.activo = bool(self.token and self.chat)

    def _api(self, metodo: str, datos: dict, timeout: int = 10) -> dict:
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{metodo}",
                                     data=json.dumps(datos).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def _decir(self, texto: str, botones: bool = False, chat: str | None = None) -> None:
        datos = {"chat_id": chat or self.grupo.destino, "text": texto[:4000]}
        if botones:
            datos["reply_markup"] = _botones(self.grupo.silencio is False, self.url_panel)
        try:
            self._api("sendMessage", datos)
        except Exception as e:  # noqa: BLE001
            print(f"[mando] {e}")

    def _de_david(self, upd: dict) -> tuple[str, str] | None:
        """(chat, texto) si el mensaje o botón es de David en su chat privado o en el grupo vinculado."""
        cb = upd.get("callback_query")
        msg = upd.get("message") or (cb or {}).get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        quien = str(((cb or msg).get("from") or {}).get("id", ""))
        texto = ((cb or {}).get("data") or msg.get("text") or "").strip()
        if chat == self.chat:
            return chat, texto
        es_grupo = (msg.get("chat") or {}).get("type") in ("group", "supergroup") or chat.startswith("-")
        if es_grupo and quien == self.chat and (chat == self.grupo.grupo_id or texto.startswith("/vincular")):
            return chat, texto
        return None

    def atender(self, upd: dict) -> str | None:
        """Procesa una actualización. Devuelve "analizar" si hay que hacer una pasada ya."""
        de = self._de_david(upd)
        if de is None:
            return None                      # solo obedece a David
        chat, texto = de
        cb = upd.get("callback_query")
        if cb:
            try:
                self._api("answerCallbackQuery", {"callback_query_id": cb["id"]})
            except Exception:  # noqa: BLE001
                pass
        palabras = texto.split()
        orden = (palabras[0] if palabras else "").split("@")[0].lower()
        resto = " ".join(palabras[1:])
        if orden in ("/start", "/panel"):
            self._decir("🏛️ El Consejo (papel, dinero ficticio). ¿Qué quieres ver?", botones=True, chat=chat)
        elif orden == "/analizar":
            if self.analista is None:
                self._decir("🔍 Analizo ahora con las últimas velas cerradas…", chat=chat)
                return "analizar"
            self._decir(f"🔍 Analizando {resto or 'BTC, ETH, S&P 500, Nasdaq y oro'}…", chat=chat)
            try:
                self._decir(self.analista(resto), chat=chat)
            except Exception as e:  # noqa: BLE001
                self._decir(f"No he podido analizar ahora ({type(e).__name__}). Lo intento en la próxima.", chat=chat)
            return None if resto else "analizar"
        elif orden == "/cartera":
            self._decir(texto_cartera(self.ruta_estado, self.ruta_memes), chat=chat)
        elif orden == "/parametros":
            self._decir(texto_parametros(self.cfg), chat=chat)
        elif orden == "/avisos":
            self.grupo.silencio = not self.grupo.silencio
            self._decir("🔕 Avisos de decisiones en silencio. El Consejo sigue trabajando y apuntando todo."
                        if self.grupo.silencio else "🔔 Avisos activados.", botones=True, chat=chat)
        elif orden == "/vincular":
            if chat == self.chat:
                self._decir("Escribe /vincular DENTRO del grupo (con el bot añadido) para mandar ahí los avisos.",
                            chat=chat)
            else:
                self.grupo.vincular_grupo(chat)
                self._decir("✅ Grupo vinculado: a partir de ahora los avisos y las opciones de compra llegan "
                            "aquí. Solo obedezco tus comandos. Escribe /panel para los botones.", chat=chat)
        elif orden == "/desvincular":
            self.grupo.vincular_grupo("")
            self._decir("Grupo desvinculado: los avisos vuelven a tu chat privado.", chat=chat)
        elif orden == "/dashboard":
            self._decir(f"📊 Panel en tiempo real: {self.url_panel}\nSe abre con el móvil en el mismo wifi que la "
                        "torre, y la torre encendida." if self.url_panel else
                        "El panel web no está abierto (en el servidor no se abre a internet a propósito). "
                        "Usa /cartera y /analizar; en la torre: py dashboard_server.py --red",
                        chat=chat)
        elif orden.startswith("/"):
            self._decir("No conozco ese comando. Prueba /panel.", chat=chat)
        return None

    def esperar(self, segundos: float) -> bool:
        """Espera escuchando comandos. Devuelve True si hay que analizar ya."""
        fin = time.time() + segundos
        if not self.activo:
            time.sleep(segundos)
            return False
        while time.time() < fin:
            espera = int(max(1, min(25, fin - time.time())))
            try:
                r = self._api("getUpdates", {"offset": self.offset, "timeout": espera,
                                             "allowed_updates": ["message", "callback_query"]}, timeout=espera + 10)
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    print("[mando] otro programa está leyendo este bot (409): cierra el otro para usar los comandos")
                time.sleep(min(30, max(0, fin - time.time())))
                continue
            except Exception:  # noqa: BLE001 - sin red se sigue esperando
                time.sleep(min(10, max(0, fin - time.time())))
                continue
            analizar = False
            for u in r.get("result", []):
                self.offset = max(self.offset, u["update_id"] + 1)
                analizar = self.atender(u) == "analizar" or analizar
            if analizar:
                return True
        return False
