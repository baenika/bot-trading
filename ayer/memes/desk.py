"""Los 6 bots de la MESA MEMECOINS. Mismos personajes que la Mesa Cripto, otro trabajo:

  🔭 Scout       → encuentra pares nuevos (30 min – 72 h, capitalización 300k – 20M)
  📰 On-chain    → (puesto de Noticias) seguridad del token y reglas de descarte ("kills")
  📈 Analista    → (puesto de Chart) puntúa 0–100 y pone el plan: stop −6%, trailing desde +12%
  🧊 Sentimental → frena el FOMO: si ya ha subido una barbaridad, llegar tarde es comprar el techo
  🤨 Skeptic     → decide: solo pasa lo que supera 70 puntos y no tiene vetos
  🛡️ Risk        → tamaño (máx. 8%), límite diario, máximo de posiciones
"""
from dataclasses import dataclass, field

DEFAULTS = {
    "min_age_min": 30, "max_age_min": 72 * 60, "min_mcap": 300_000, "max_mcap": 20_000_000,
    "min_liq": 40_000, "min_vol_h1": 50_000, "max_top10_pct": 25, "max_dev_pct": 3,
    "max_bundled_pct": 10, "max_chg_h1_already": 80, "min_lp_locked_pct": 90,
    "require_safety_data": True, "min_score": 70,
    "fomo_chg_m5": 25, "fomo_buy_ratio": 4.0,
    "stop_pct": 6, "trail_start_pct": 12, "trail_pct": 8, "max_hold_min": 360,
    "max_position_pct": 8, "risk_cap_pct": 1.0, "max_open": 4, "daily_loss_pct": 6,
    "max_drawdown_pct": 25, "fee_pct": 1.0, "max_impact_pct": 3.0,
}


@dataclass
class Review:
    c: dict
    kills: list = field(default_factory=list)       # reglas que lo descartan
    unknown: list = field(default_factory=list)     # datos que faltan
    score: float = 0.0
    parts: dict = field(default_factory=dict)
    fomo: list = field(default_factory=list)
    decision: str = "PASAR"
    reasons: list = field(default_factory=list)


def _r(x, d=0):
    return "—" if x is None else f"{x:,.{d}f}".replace(",", ".")


class MemeDesk:
    def __init__(self, cfg: dict):
        self.p = {**DEFAULTS, **(cfg or {})}

    # 🔭 SCOUT ---------------------------------------------------------------
    def scout(self, c):
        p, age, m = self.p, c.get("age_min"), c.get("mcap")
        if age is None or m is None or c.get("price") is None:
            return False
        return p["min_age_min"] <= age <= p["max_age_min"] and p["min_mcap"] <= m <= p["max_mcap"]

    # 📰 ON-CHAIN: reglas de descarte ----------------------------------------
    def onchain(self, c, s) -> Review:
        p, rv = self.p, Review(c)
        check_safety = s is not None
        s = s or {}
        def kill(cond, label):
            if cond:
                rv.kills.append(label)
        def need(val, label):
            if not check_safety:
                return False
            if val is None:
                rv.unknown.append(label)
                return False
            return True
        if need(s.get("mint_revoked"), "mint"):
            kill(not s["mint_revoked"], "mint sin revocar")
        if need(s.get("freeze_revoked"), "freeze"):
            kill(not s["freeze_revoked"], "freeze sin revocar")
        if need(s.get("lp_locked_pct"), "liquidez bloqueada"):
            kill(s["lp_locked_pct"] < p["min_lp_locked_pct"], "liquidez no bloqueada/quemada")
        kill((c.get("liq") or 0) < p["min_liq"], f"liquidez < {p['min_liq']//1000}k")
        kill((c.get("vol_h1") or 0) < p["min_vol_h1"], f"volumen 1h < {p['min_vol_h1']//1000}k")
        if need(s.get("top10_pct"), "top 10"):
            kill(s["top10_pct"] > p["max_top10_pct"], f"top 10 > {p['max_top10_pct']}%")
        if s.get("dev_pct") is not None:
            kill(s["dev_pct"] > p["max_dev_pct"], f"creador > {p['max_dev_pct']}%")
        if s.get("bundled_pct") is not None:
            kill(s["bundled_pct"] > p["max_bundled_pct"], f"lanzamiento coordinado > {p['max_bundled_pct']}%")
        kill(bool(s.get("rugged")), "marcado como rug")
        kill((c.get("chg_h1") or 0) > p["max_chg_h1_already"], f"ya +{p['max_chg_h1_already']}% en 1h")
        if check_safety and p["require_safety_data"] and rv.unknown:
            rv.kills.append("faltan datos de seguridad")
        return rv

    # 📈 ANALISTA: puntuación 0-100 ------------------------------------------
    def analyst(self, rv: Review):
        c = rv.c
        def clamp(x):
            return max(0.0, min(1.0, x))
        b1, s1 = c.get("buys_h1") or 0, c.get("sells_h1") or 0
        b5, s5 = c.get("buys_m5") or 0, c.get("sells_m5") or 0
        ratio = b1 / max(1, s1)
        mom = clamp(((c.get("chg_h1") or 0) + 10) / 50) * 0.6 + clamp(((c.get("chg_m5") or 0) + 3) / 15) * 0.4
        flow = clamp((ratio - 0.8) / 1.2)
        activity = clamp((b1 + s1) / 800)
        depth = clamp(((c.get("liq") or 0) / max(1, c.get("mcap") or 1)) / 0.15)
        accel = clamp(((c.get("vol_m5") or 0) * 12 / max(1, c.get("vol_h1") or 1) - 0.5) / 1.5)
        social = 1.0 if (c.get("socials") or 0) >= 2 else 0.5 if (c.get("socials") or 0) == 1 else 0.0
        parts = {"momentum": 25 * mom, "compras/ventas": 20 * flow, "actividad": 15 * activity,
                 "liquidez/capitalización": 15 * depth, "aceleración": 15 * accel, "redes": 10 * social}
        rv.parts = {k: round(v, 1) for k, v in parts.items()}
        rv.score = round(sum(parts.values()), 1)
        rv.parts["_ratio"] = round(ratio, 2)
        rv.parts["_m5"] = (b5, s5)
        return rv

    # 🧊 SENTIMENTAL ---------------------------------------------------------
    def sentimental(self, rv: Review):
        c, p = rv.c, self.p
        if (c.get("chg_m5") or 0) >= p["fomo_chg_m5"]:
            rv.fomo.append(f"+{c['chg_m5']:.0f}% en 5 minutos: vela vertical, es FOMO")
        b5, s5 = c.get("buys_m5") or 0, c.get("sells_m5") or 0
        if s5 and b5 / s5 >= p["fomo_buy_ratio"] and b5 > 40:
            rv.fomo.append(f"{b5} compras contra {s5} ventas en 5 min: frenesí, suele venir el volcado")
        if (c.get("chg_h6") or 0) > 300:
            rv.fomo.append(f"+{c['chg_h6']:.0f}% en 6h: los primeros ya están vendiendo")
        return rv

    # 🤨 SKEPTIC -------------------------------------------------------------
    def skeptic(self, rv: Review):
        if rv.kills:
            rv.decision, rv.reasons = "PASAR", [f"⛔ {k}" for k in rv.kills]
        elif rv.fomo:
            rv.decision, rv.reasons = "PASAR", [f"⛔ {f}" for f in rv.fomo]
        elif rv.score < self.p["min_score"]:
            rv.decision, rv.reasons = "PASAR", [f"⛔ puntuación {rv.score:.0f} < {self.p['min_score']}"]
        else:
            rv.decision, rv.reasons = "COMPRAR", [f"➕ puntuación {rv.score:.0f}/100", "➕ pasa todas las reglas"]
        return rv

    def cheap_ok(self, c):
        """Filtro barato (sin llamar a RugCheck): liquidez, volumen y subida previa."""
        p = self.p
        return ((c.get("liq") or 0) >= p["min_liq"] and (c.get("vol_h1") or 0) >= p["min_vol_h1"]
                and (c.get("chg_h1") or 0) <= p["max_chg_h1_already"])

    def review(self, c, safety) -> Review:
        rv = self.onchain(c, safety)
        self.analyst(rv)
        self.sentimental(rv)
        return self.skeptic(rv)

    # 🛡️ RISK ---------------------------------------------------------------
    def size(self, equity, c):
        """Hasta 8% del capital, pero arriesgando como máximo 1% (stop −6% ⇒ ~16%, se queda en 8%)
        y sin mover el precio más de un 3% (impacto según la liquidez)."""
        p = self.p
        by_pct = equity * p["max_position_pct"] / 100
        by_risk = equity * p["risk_cap_pct"] / 100 / (p["stop_pct"] / 100)
        by_liq = (c.get("liq") or 0) * p["max_impact_pct"] / 100 / 2
        return max(0.0, min(by_pct, by_risk, by_liq))

    # mensajes para el grupo -------------------------------------------------
    def messages(self, rv: Review, size=None):
        c = rv.c
        head = f"<b>${c.get('symbol')}</b>"
        msgs = [("scout", f"🪙 {head} · {(c.get('age_min') or 0)/60:.1f} h de vida · capitalización "
                          f"{_r(c.get('mcap'))} $ · liquidez {_r(c.get('liq'))} $\n"
                          f"1h: {c.get('chg_h1') or 0:+.0f}% · volumen {_r(c.get('vol_h1'))} $")]
        safe = "✅ pasa los filtros de seguridad" if not rv.kills else "Descartes:\n" + "\n".join(f"⛔ {k}" for k in rv.kills)
        msgs.append(("news", f"🪙 On-chain {head}: {safe}"))
        parts = " · ".join(f"{k} {v:.0f}" for k, v in rv.parts.items() if not k.startswith("_"))
        msgs.append(("chart", f"🪙 Puntuación {head}: <b>{rv.score:.0f}/100</b>\n{parts}\n"
                              f"Plan: stop −{self.p['stop_pct']}%, trailing desde +{self.p['trail_start_pct']}% "
                              f"(a {self.p['trail_pct']}% del máximo)"))
        msgs.append(("sentimental", "🪙 " + ("\n".join(rv.fomo) if rv.fomo else "Sin FOMO: entraría con la cabeza fría")))
        icon = "✅" if rv.decision == "COMPRAR" else "❌"
        msgs.append(("skeptic", f"🪙 {icon} <b>{rv.decision}</b> {head}\n" + "\n".join(rv.reasons)))
        if size is not None:
            msgs.append(("risk", f"🪙 Tamaño {head}: {size:,.2f} $ (papel)"))
        return msgs
