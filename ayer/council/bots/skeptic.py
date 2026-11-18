"""🤨 SKEPTIC — el que más manda. Discute cada trade y decide: COMPRAR / ESPERAR / PASAR.

Parte de "no": un trade tiene que ganarse la aprobación. Junta argumentos a favor y en contra
de todos los demás bots, aplica VETOS (cualquiera mata el trade) y puntúa el resto.

  • PASAR   → algún veto, o la puntuación no llega.
  • ESPERAR → la idea es buena pero el momento no (precio estirado, FOMO): pone una orden
              en el precio de retroceso y solo entra si el precio vuelve ahí.
  • COMPRAR / VENDER → todo cuadra.
"""
from ..models import Verdict


class Skeptic:
    name, emoji = "Skeptic", "🤨"

    def __init__(self, cfg: dict):
        self.min_rr = cfg.get("min_rr", 1.8)
        self.approve_score = cfg.get("approve_score", 3.0)
        self.wait_score = cfg.get("wait_score", 1.0)
        self.max_risk_pct = cfg.get("max_risk_pct", 6.0)
        self.min_study_win = cfg.get("min_study_win", 45)
        self.min_study_cases = cfg.get("min_study_cases", 15)
        self.allow_short = cfg.get("allow_short", False)

    def judge(self, df, scout, news, setup, senti) -> Verdict:
        row = df.iloc[-1]
        long = setup.direction == "long"
        pros, cons, vetoes, score = [], [], [], 0.0

        # --- vetos ---
        if not long and not self.allow_short:
            vetoes.append("los cortos están desactivados en la config")
        rr_eff = min(setup.rr, setup.rr_to_level) if setup.rr_to_level is not None else setup.rr
        if rr_eff < self.min_rr:
            vetoes.append(f"R:R real {rr_eff:.1f} < {self.min_rr}: hay un nivel que tapa el objetivo")
        if setup.risk_pct > self.max_risk_pct:
            vetoes.append(f"stop demasiado lejos ({setup.risk_pct:.1f}%)")
        if senti.recommendation == "bloquear":
            vetoes.append(f"Sentimental bloquea: {senti.label}")
        if news.available and news.alignment == "contradice" and abs(news.sentiment) >= 0.4:
            vetoes.append("las noticias van claramente en contra")
        st = senti.study
        if st.get("cases", 0) >= self.min_study_cases and st["win_rate"] < self.min_study_win:
            vetoes.append(f"el estudio histórico da {st['win_rate']:.0f}% de aciertos: no hay ventaja")

        # --- tendencia de fondo ---
        trend_up = row["close"] > row["ema200"] and row["ema50"] > row["ema200"]
        trend_dn = row["close"] < row["ema200"] and row["ema50"] < row["ema200"]
        if (long and trend_up) or (not long and trend_dn):
            score += 1.5
            pros.append("a favor de la tendencia de fondo (EMA50/EMA200)")
        elif (long and trend_dn) or (not long and trend_up):
            score -= 2
            cons.append("contra la tendencia de fondo: nadando a contracorriente")
        else:
            cons.append("tendencia de fondo indefinida")

        # --- fuerza del movimiento ---
        if scout.strength >= 70:
            score += 1
            pros.append(f"movimiento fuerte ({scout.strength:.0f}/100)")
        elif scout.strength < 55:
            score -= 0.5
            cons.append(f"movimiento justito ({scout.strength:.0f}/100)")
        if scout.metrics.get("vol_z", 0) < 1.5:
            score -= 0.5
            cons.append("sin volumen que lo respalde")

        # --- R:R y niveles ---
        if rr_eff >= 2.5:
            score += 1
            pros.append(f"R:R {rr_eff:.1f}")
        elif rr_eff >= self.min_rr:
            score += 0.5
            pros.append(f"R:R correcto ({rr_eff:.1f})")

        # --- noticias ---
        if news.available:
            if news.alignment == "confirma":
                score += 1
                pros.append("hay una noticia que explica el movimiento")
            elif news.alignment == "sin_catalizador":
                score -= 0.5
                cons.append("se mueve sin noticia: puede ser un barrido de liquidez")
            elif news.alignment == "contradice":
                score -= 1
                cons.append("noticias y precio no cuadran")

        # --- estudio histórico ---
        if st.get("cases", 0) >= self.min_study_cases:
            if st["win_rate"] >= 55 and st["avg_ret"] > 0:
                score += 1.5
                pros.append(f"en el pasado esto funcionó el {st['win_rate']:.0f}% de las veces")
            elif st["avg_ret"] <= 0:
                score -= 1
                cons.append(f"en el pasado la media fue {st['avg_ret']:+.2f}%")
        else:
            cons.append("no hay suficientes casos históricos para fiarse")

        # --- emoción ---
        if senti.recommendation == "ok":
            score += 0.5
            pros.append("sin FOMO ni pánico")
        elif senti.recommendation == "esperar_retroceso":
            cons.append("el precio está caliente: entrar ahora es perseguir")

        if vetoes:
            decision, wait = "PASAR", None
        elif score >= self.approve_score and senti.recommendation == "ok":
            decision, wait = ("COMPRAR" if long else "VENDER"), None
        elif score >= self.wait_score:
            decision, wait = "ESPERAR", setup.pullback_entry
        else:
            decision, wait = "PASAR", None
        return Verdict(decision=decision, score=round(score, 2), pros=pros, cons=cons, vetoes=vetoes,
                       wait_price=wait)

    def message(self, v: Verdict) -> str:
        icon = {"COMPRAR": "✅", "VENDER": "✅", "ESPERAR": "⏳", "PASAR": "❌"}[v.decision]
        lines = [f"{icon} <b>{v.decision}</b> (puntuación {v.score:+.1f})"]
        if v.vetoes:
            lines.append("Vetos:")
            lines += [f"⛔ {x}" for x in v.vetoes]
        if v.pros:
            lines += [f"➕ {x}" for x in v.pros]
        if v.cons:
            lines += [f"➖ {x}" for x in v.cons]
        if v.decision == "ESPERAR" and v.wait_price:
            lines.append(f"Solo entro si vuelve a {v.wait_price:.2f}. Si no, se cancela.")
        return "\n".join(lines)
