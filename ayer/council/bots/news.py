"""📰 NOTICIAS — busca POR QUÉ se mueve la gráfica.

En vivo lee titulares RSS de las últimas horas, se queda con los que hablan del activo o
de macro (Fed, IPC, ETF, SEC...) y mide si el tono confirma o contradice el movimiento.
Si hay una clave de Anthropic en la config, usa Claude para leer los titulares y explicar
la causa; si no, usa un diccionario de palabras.

En backtest NO hay noticias históricas gratuitas, así que este bot se declara "sin datos"
y no suma ni resta. Así el backtest no se inventa nada.
"""
import json
import re

import requests

from ..models import NewsReport

ASSET_WORDS = {
    "BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "ether", "eth"], "SOL": ["solana", "sol"],
    "BNB": ["bnb", "binance"], "XRP": ["xrp", "ripple"], "DOGE": ["dogecoin", "doge"],
}
MACRO_WORDS = ["fed", "fomc", "powell", "rate", "rates", "inflation", "cpi", "ppi", "jobs", "payroll",
               "sec", "etf", "tariff", "recession", "treasury", "regulation", "crypto market"]
POS = ["surge", "soar", "rally", "jump", "gain", "record", "high", "approval", "approve", "inflow",
       "bull", "adopt", "buy", "upgrade", "beat", "rise", "rises", "climb", "breakout", "cut rates"]
NEG = ["plunge", "crash", "drop", "fall", "falls", "slump", "hack", "exploit", "lawsuit", "ban",
       "outflow", "bear", "sell-off", "selloff", "liquidat", "fear", "reject", "delay", "probe",
       "fraud", "bankrupt", "hike", "dump", "decline"]


class NewsBot:
    name, emoji = "Noticias", "📰"

    def __init__(self, cfg: dict, llm_cfg: dict | None = None):
        self.feeds = cfg.get("feeds", [])
        self.max_age_hours = cfg.get("max_age_hours", 12)
        self.llm = llm_cfg or {}

    # ---------- utilidades ----------
    @staticmethod
    def _base(symbol: str) -> str:
        return re.sub(r"(USDT|USDC|BUSD|EUR|USD)$", "", symbol)

    def _relevant(self, symbol, headlines):
        words = ASSET_WORDS.get(self._base(symbol), [self._base(symbol).lower()])
        rel = []
        for h in headlines:
            text = f" {h['title']} {h['summary']} ".lower()
            if any(re.search(rf"\b{re.escape(w)}\b", text) for w in words + MACRO_WORDS):
                rel.append(h)
        return rel[:12]

    @staticmethod
    def _lexicon_score(headlines) -> float:
        score = 0
        for h in headlines:
            t = h["title"].lower()
            score += sum(w in t for w in POS) - sum(w in t for w in NEG)
        return max(-1.0, min(1.0, score / max(3, len(headlines))))

    def _ask_claude(self, symbol, move_pct, direction, headlines):
        key = self.llm.get("anthropic_api_key")
        if not key:
            return None
        listing = "\n".join(f"- {h['title']}" for h in headlines)
        prompt = (
            f"{symbol} se ha movido {move_pct:+.2f}% en la última vela ({direction}). Titulares recientes:\n"
            f"{listing}\n\nResponde SOLO con JSON: {{\"sentiment\": número de -1 a 1 para {symbol}, "
            "\"alignment\": \"confirma\"|\"contradice\"|\"sin_catalizador\", "
            "\"explanation\": \"1-2 frases en español: qué noticia explica el movimiento, o que no hay ninguna\"}"
        )
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": self.llm.get("model", "claude-haiku-5-5"), "max_tokens": 300,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30,
            )
            r.raise_for_status()
            text = r.json()["content"][0]["text"]
            return json.loads(text[text.find("{"): text.rfind("}") + 1])
        except Exception:
            return None

    # ---------- análisis ----------
    def analyze(self, symbol: str, scout, headlines=None, live: bool = True) -> NewsReport:
        if not live:
            return NewsReport(available=False, explanation="Backtest: sin noticias históricas, no opino.")
        from ..data import fetch_headlines
        headlines = headlines if headlines is not None else fetch_headlines(self.feeds, self.max_age_hours)
        rel = self._relevant(symbol, headlines)
        move = scout.metrics.get("body_pct", 0.0)
        if not rel:
            return NewsReport(available=True, alignment="sin_catalizador", sentiment=0.0,
                              explanation="No encuentro ninguna noticia que explique el movimiento. "
                                          "Puede ser técnico, liquidaciones o una ballena.")
        ai = self._ask_claude(symbol, move, scout.direction, rel)
        if ai:
            return NewsReport(available=True, alignment=ai.get("alignment", "sin_catalizador"),
                              sentiment=float(ai.get("sentiment", 0)), explanation=ai.get("explanation", ""),
                              headlines=[h["title"] for h in rel[:3]])
        s = self._lexicon_score(rel)
        want = 1 if scout.direction == "long" else -1
        if abs(s) < 0.15:
            align, expl = "sin_catalizador", "Hay titulares, pero ninguno empuja claramente en una dirección."
        elif (s > 0) == (want > 0):
            align, expl = "confirma", "El tono de las noticias va en la misma dirección que la vela."
        else:
            align, expl = "contradice", "Las noticias van en contra del movimiento: sospechoso."
        return NewsReport(available=True, alignment=align, sentiment=round(s, 2), explanation=expl,
                          headlines=[h["title"] for h in rel[:3]])

    def message(self, r: NewsReport) -> str:
        if not r.available:
            return f"<i>{r.explanation}</i>"
        icon = {"confirma": "✅", "contradice": "⚠️", "sin_catalizador": "❔"}.get(r.alignment, "❔")
        lines = [f"{icon} <b>{r.alignment.replace('_', ' ')}</b> (tono {r.sentiment:+.2f})", r.explanation]
        lines += [f"• {h}" for h in r.headlines]
        return "\n".join(lines)
