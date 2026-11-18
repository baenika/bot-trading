"""El consejo: hace hablar a los 6 bots en orden y devuelve la conversación y la decisión.

Orden del chat:  🔭 Scout → 📰 Noticias → 📈 Chart → 🧊 Sentimental → 🤨 Skeptic → 🛡️ Risk
"""
from .bots.chart import ChartBot
from .bots.news import NewsBot
from .bots.risk import RiskManager
from .bots.scout import Scout
from .bots.sentimental import SentimentalBot
from .bots.skeptic import Skeptic
from .models import Verdict


class Council:
    def __init__(self, cfg: dict, skeptic_enabled: bool = True):
        b = cfg.get("bots", {})
        self.scout = Scout(b.get("scout", {}))
        self.news = NewsBot(b.get("news", {}), cfg.get("llm", {}))
        self.chart = ChartBot(b.get("chart", {}))
        self.senti = SentimentalBot(b.get("sentimental", {}))
        self.skeptic = Skeptic(b.get("skeptic", {}))
        self.risk = RiskManager(b.get("risk", {}))
        self.skeptic_enabled = skeptic_enabled

    def deliberate(self, symbol, tf, df, *, live=False, fear_greed=None, loss_streak=0, headlines=None):
        """df ya con indicadores y SOLO hasta la última vela cerrada. Devuelve None si el Scout no ve nada."""
        scout = self.scout.analyze(df)
        if not scout.alert:
            return None
        msgs = [("scout", self.scout.message(symbol, tf, scout))]

        news = self.news.analyze(symbol, scout, headlines=headlines, live=live)
        msgs.append(("news", self.news.message(news)))

        setup = self.chart.plan(df, scout.direction)
        msgs.append(("chart", self.chart.message(setup)))

        senti = self.senti.analyze(df, scout.direction, fear_greed=fear_greed, loss_streak=loss_streak, setup=setup)
        msgs.append(("sentimental", self.senti.message(senti)))

        if self.skeptic_enabled:
            verdict = self.skeptic.judge(df, scout, news, setup, senti)
        else:  # modo comparación: se ejecuta todo lo que encuentra el Scout
            verdict = Verdict(decision="COMPRAR" if setup.direction == "long" else "VENDER", score=0,
                              pros=["(Skeptic desactivado)"])
            if setup.direction == "short" and not self.skeptic.allow_short:
                verdict = Verdict(decision="PASAR", score=0, vetoes=["cortos desactivados"])
        msgs.append(("skeptic", self.skeptic.message(verdict)))

        return {"scout": scout, "news": news, "setup": setup, "senti": senti, "verdict": verdict,
                "messages": msgs}
