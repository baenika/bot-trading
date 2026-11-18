"""🧊 SENTIMENTAL — mide la emoción del mercado para que el sistema NO se contagie.

Una persona ve una vela verde enorme y compra por miedo a quedarse fuera (FOMO), o ve una roja
y vende por pánico. Este bot detecta esas situaciones y las contrasta con DATOS:

  • Sobre-extensión: cuántos ATR está el precio lejos de su EMA20 (perseguir el precio).
  • RSI extremo y rachas de velas del mismo color (euforia / pánico).
  • Índice Fear & Greed del mercado cripto.
  • Estado propio: después de varias pérdidas seguidas, bloquea (anti "revenge trading").
  • ESTUDIO HISTÓRICO: busca en el pasado los casos parecidos al actual y mira qué hizo
    el precio después. Decide con la estadística, no con la vela de ahora.
"""
import numpy as np

from ..models import SentimentReport


class SentimentalBot:
    name, emoji = "Sentimental", "🧊"

    def __init__(self, cfg: dict):
        self.max_ext_atr = cfg.get("max_ext_atr", 3.0)
        self.rsi_hot = cfg.get("rsi_hot", 75)
        self.rsi_cold = cfg.get("rsi_cold", 25)
        self.max_streak = cfg.get("max_streak", 5)
        self.loss_streak_block = cfg.get("loss_streak_block", 3)
        self.study_horizon = cfg.get("study_horizon", 12)
        self.study_min_cases = cfg.get("study_min_cases", 15)
        self.sim_paths = cfg.get("sim_paths", 10000)
        self.sim_horizon = cfg.get("sim_horizon", 48)
        self.sim_lookback = cfg.get("sim_lookback", 1000)
        self.sim_block = cfg.get("sim_block", 6)
        self.rng = np.random.default_rng(cfg.get("sim_seed", 42))

    def time_machine(self, df, setup) -> dict:
        """🕰️ Máquina del tiempo — SOLO INFORMATIVA, no cambia ninguna decisión.
        Repite el futuro miles de veces juntando trozos REALES del pasado reciente (bloques de velas
        con su máximo y su mínimo, no un modelo matemático inventado) y cuenta qué toca antes:
        el objetivo o el stop."""
        if setup is None or len(df) < 120:
            return {}
        h = df.iloc[-self.sim_lookback:]
        o = h["open"].values
        up = h["high"].values / o - 1
        dn = h["low"].values / o - 1
        cc = h["close"].values / o - 1
        n, B, H, N = len(o), self.sim_block, self.sim_horizon, self.sim_paths
        nb = -(-H // B)
        starts = self.rng.integers(0, n - B, size=(N, nb))
        idx = (starts[:, :, None] + np.arange(B)).reshape(N, -1)[:, :H]
        lr = np.log1p(cc[idx])
        path_open = np.exp(np.concatenate([np.zeros((N, 1)), np.cumsum(lr, axis=1)[:, :-1]], axis=1))
        hi = path_open * (1 + up[idx])
        lo = path_open * (1 + dn[idx])
        e = setup.entry
        if setup.direction == "long":
            tp_hit, sl_hit = hi >= setup.tp / e, lo <= setup.sl / e
        else:
            tp_hit, sl_hit = lo <= setup.tp / e, hi >= setup.sl / e
        first = lambda m: np.where(m.any(axis=1), m.argmax(axis=1), H + 1)
        t_tp, t_sl = first(tp_hit), first(sl_hit)
        tp_first = (t_tp < t_sl) & (t_tp <= H)
        sl_first = (t_sl <= t_tp) & (t_sl <= H)      # si caen en la misma vela, cuenta como stop
        p_tp, p_sl = tp_first.mean() * 100, sl_first.mean() * 100
        be = 100 / (1 + setup.rr)                    # % de aciertos necesario para no perder
        return {"p_tp": round(p_tp, 1), "p_sl": round(p_sl, 1), "p_none": round(100 - p_tp - p_sl, 1),
                "breakeven": round(be, 1), "paths": N, "horizon": H,
                "median_bars_tp": int(np.median(t_tp[tp_first])) if tp_first.any() else None}

    def study(self, df, direction: str) -> dict:
        """Casos del pasado con un movimiento parecido: ¿qué pasó en las siguientes N velas?"""
        h = self.study_horizon
        n = len(df)
        if n < h + 60:
            return {"cases": 0}
        cur = df.iloc[-1]
        sign = 1 if direction == "long" else -1
        body = np.sign(df["close"].values - df["open"].values)
        cond = ((df["vol_z"].values >= 1.5) | (df["range_atr"].values >= 1.5)) & (body == sign)
        cond &= np.abs(df["ext_atr"].values - cur["ext_atr"]) < 1.0
        # solo velas cuyo resultado ya se conoce (sin mirar al futuro)
        cond[n - h - 1:] = False
        cond[:50] = False
        idx = np.where(cond)[0]
        if len(idx) == 0:
            return {"cases": 0}
        close = df["close"].values
        fwd = sign * (close[idx + h] / close[idx] - 1) * 100
        return {"cases": int(len(idx)), "win_rate": float((fwd > 0).mean() * 100),
                "avg_ret": float(fwd.mean()), "median_ret": float(np.median(fwd)), "horizon": h}

    def analyze(self, df, direction: str, fear_greed=None, loss_streak: int = 0, setup=None) -> SentimentReport:
        row = df.iloc[-1]
        sign = 1 if direction == "long" else -1
        flags, temp = [], 30.0

        ext = float(row["ext_atr"]) * sign
        if ext > self.max_ext_atr * 1.5:
            temp += 35
            flags.append(f"el precio está a {ext:.1f} ATR de su media: subida vertical, perseguirla es FOMO")
        elif ext > self.max_ext_atr:
            temp += 25
            flags.append(f"el precio está a {ext:.1f} ATR de su media: perseguirlo es FOMO")
        elif ext > self.max_ext_atr * 0.66:
            temp += 15
            flags.append(f"algo estirado ({ext:.1f} ATR de la EMA20)")

        r = float(row["rsi"])
        if (direction == "long" and r > self.rsi_hot) or (direction == "short" and r < self.rsi_cold):
            temp += 15
            flags.append(f"RSI {r:.0f}: zona de {'euforia' if direction == 'long' else 'pánico'}")

        streak = float(row["streak"]) * sign
        if streak >= self.max_streak:
            temp += 10
            flags.append(f"{int(streak)} velas seguidas en la misma dirección")

        if fear_greed is not None:
            if direction == "long" and fear_greed >= 75:
                temp += 10
                flags.append(f"Fear & Greed {fear_greed}: codicia extrema, la masa ya ha comprado")
            elif direction == "short" and fear_greed <= 25:
                temp += 10
                flags.append(f"Fear & Greed {fear_greed}: miedo extremo, vender aquí suele ser tarde")
            elif direction == "long" and fear_greed <= 25:
                flags.append(f"Fear & Greed {fear_greed}: miedo extremo (dato a favor de comprar)")

        st = self.study(df, direction)
        if st.get("cases", 0) >= self.study_min_cases:
            if st["win_rate"] < 45 or st["avg_ret"] < 0:
                temp += 15
                flags.append(f"históricamente este patrón sale mal: {st['win_rate']:.0f}% aciertos")
            elif st["win_rate"] > 55 and st["avg_ret"] > 0:
                temp -= 10

        temp = float(max(0, min(100, temp)))
        if loss_streak >= self.loss_streak_block:
            rec, label = "bloquear", "en racha de pérdidas"
            flags.append(f"{loss_streak} pérdidas seguidas: pausa para no operar por venganza")
        elif temp >= 80:
            rec, label = "bloquear", "FOMO" if direction == "long" else "pánico"
        elif temp >= 50:
            rec, label = "esperar_retroceso", "caliente"
        else:
            rec, label = "ok", "frío" if temp < 35 else "neutral"
        return SentimentReport(temperature=temp, label=label, recommendation=rec, flags=flags,
                               study=st, fear_greed=fear_greed, sim=self.time_machine(df, setup))

    def message(self, r: SentimentReport) -> str:
        bar = "🟦" * int(r.temperature // 20) + "⬜" * (5 - int(r.temperature // 20))
        rec = {"ok": "puedo actuar con la cabeza fría", "esperar_retroceso": "no persigas, espera retroceso",
               "bloquear": "NO entrar ahora, es emoción"}[r.recommendation]
        lines = [f"Temperatura {bar} {r.temperature:.0f}/100 → <b>{r.label}</b>: {rec}"]
        lines += [f"• {f}" for f in r.flags]
        st = r.study
        if st.get("cases", 0):
            lines.append(f"📊 Estudio: {st['cases']} casos parecidos en el historial → a {st['horizon']} velas "
                         f"acierta {st['win_rate']:.0f}%, media {st['avg_ret']:+.2f}%")
        else:
            lines.append("📊 Estudio: pocos casos parecidos, sin estadística fiable")
        sm = r.sim
        if sm:
            verdict = "por encima" if sm["p_tp"] > sm["breakeven"] else "por debajo"
            lines.append(f"🕰️ Máquina del tiempo (solo informativo): en {sm['paths']:,} futuros posibles "
                         f"toca el objetivo antes que el stop el {sm['p_tp']:.0f}%, el stop el {sm['p_sl']:.0f}%, "
                         f"ninguno en {sm['horizon']} velas el {sm['p_none']:.0f}%. Para no perder hace falta "
                         f"un {sm['breakeven']:.0f}%: está {verdict}.".replace(",", "."))
        return "\n".join(lines)
