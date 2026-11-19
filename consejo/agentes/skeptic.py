"""Skeptic: el que lleva la contraria. Puede vetar cualquier operación."""

from __future__ import annotations

COMPRAR, VENDER, ESPERAR, PASAR = "COMPRAR", "VENDER", "ESPERAR", "PASAR"


def decidir(ctx, alerta, plan, sentimental: dict, estudio: dict, sim: dict, noticias: dict,
            cfg: dict, calibrada: bool = False) -> dict:
    i = alerta.i
    pros, contras, vetos = [], [], []
    puntos = 0.0

    if alerta.dir == "short" and not cfg.get("permitir_cortos", False):
        vetos.append("los cortos están desactivados en la config")
    rr_min = cfg.get("rr_minimo", 1.8)
    if plan.rr_real < rr_min:
        vetos.append(f"R:R real {plan.rr_real:.1f} < {rr_min}: hay un nivel que tapa el objetivo")
    if sentimental.get("bloquea"):
        vetos.append(f"Sentimental bloquea: {sentimental['bloquea']}")
    if noticias.get("veto"):
        vetos.append(f"News: {noticias['veto']}")

    if alerta.fuerza >= 70:
        pros.append(f"movimiento fuerte ({alerta.fuerza:.0f}/100)")
        puntos += 1
    if estudio.get("n", 0) >= 10:
        if estudio["acierto"] >= 55:
            pros.append(f"en el pasado esto funcionó el {estudio['acierto']:.0f}% de las veces")
            puntos += 1
        elif estudio["acierto"] < 45:
            contras.append(f"en el pasado solo funcionó el {estudio['acierto']:.0f}% de las veces")
            puntos -= 1
    # La máquina del tiempo solo vota si el backtest ha demostrado que acierta
    # (error de calibración por debajo de ~7 puntos). Si no, es solo informativa.
    if sim.get("paths") and calibrada:
        margen = sim["p_tp"] - sim["breakeven"]
        if margen >= 5:
            pros.append(f"la máquina del tiempo (calibrada) da {sim['p_tp']:.0f}% al objetivo "
                        f"(hace falta {sim['breakeven']:.0f}%)")
            puntos += 1
        elif margen < 0:
            contras.append(f"la máquina del tiempo (calibrada) da solo {sim['p_tp']:.0f}% al objetivo "
                           f"(hace falta {sim['breakeven']:.0f}%)")
            puntos -= 1
    if noticias.get("pro"):
        pros.append(f"News: {noticias['pro']}")
        puntos += 1
    if noticias.get("contra"):
        contras.append(f"News: {noticias['contra']}")
        puntos -= 1
    tendencia_up = ctx.velas[i].c > ctx.ema200[i] and ctx.ema50[i] > ctx.ema200[i]
    tendencia_down = ctx.velas[i].c < ctx.ema200[i] and ctx.ema50[i] < ctx.ema200[i]
    if (alerta.dir == "long" and tendencia_up) or (alerta.dir == "short" and tendencia_down):
        pros.append("a favor de la tendencia de fondo")
        puntos += 1
    elif (alerta.dir == "long" and tendencia_down) or (alerta.dir == "short" and tendencia_up):
        contras.append("contra la tendencia de fondo: nadando a contracorriente")
        puntos -= 1
    puntos -= 0.5 * len(vetos)

    if sentimental.get("fomo"):
        contras.append("Sentimental: el precio está estirado, no se persigue (como mucho, ESPERAR al retroceso)")
    if vetos:
        decision = PASAR
    elif puntos >= cfg.get("puntos_para_entrar", 2) and not sentimental.get("fomo"):
        decision = COMPRAR if alerta.dir == "long" else VENDER
    elif puntos >= cfg.get("puntos_para_esperar", 1):
        decision = ESPERAR
    else:
        decision = PASAR

    icono = {"COMPRAR": "✅", "VENDER": "✅", "ESPERAR": "⏳", "PASAR": "❌"}[decision]
    lineas = [f"{icono} {decision} (puntuación {puntos:+.1f})"]
    if vetos:
        lineas.append("Vetos:")
        lineas += [f"⛔ {v}" for v in vetos]
    lineas += [f"➕ {p}" for p in pros] + [f"➖ {c}" for c in contras]
    if decision == ESPERAR:
        lineas.append(f"Espero a que retroceda a {plan.precio_espera:.2f}")
    return {"decision": decision, "score": round(puntos, 2), "pros": pros, "cons": contras,
            "vetoes": vetos, "texto": "\n".join(lineas)}
