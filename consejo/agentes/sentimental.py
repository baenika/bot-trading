"""Sentimental: frena la operación cuando huele a FOMO o a venganza."""

from __future__ import annotations


def opinar(ctx, alerta, racha_perdidas: int, estudio: dict | None, sim: dict | None,
           cfg: dict, noticias: dict | None = None) -> dict:
    i = alerta.i
    a = ctx.atr[i] or 1e-9
    distancia = (ctx.velas[i].c - ctx.ema20[i]) / a
    if alerta.dir == "short":
        distancia = -distancia
    motivos, bloquea = [], None

    temp = 50 + 8 * distancia + 5 * racha_perdidas

    # el ruido de los medios también calienta: mucha noticia y muy eufórica = FOMO de manual
    n = noticias or {}
    vol_rel, tono = n.get("volumen_rel"), n.get("tono_ayer")
    lado = 1 if alerta.dir == "long" else -1
    if vol_rel and vol_rel >= cfg.get("bombo_volumen", 3.0):
        temp += 10
        motivos.append(f"ayer se habló de esto x{vol_rel:.1f} más de lo normal: hay bombo")
    if tono is not None and tono * lado >= cfg.get("bombo_tono", 3.0):
        temp += 8
        motivos.append(f"los medios están eufóricos (tono {tono:+.1f}): cuidado con llegar tarde")
    elif tono is not None and tono * lado <= -cfg.get("bombo_tono", 3.0):
        motivos.append(f"los medios están muy negativos (tono {tono:+.1f}): entrar contra el miedo pide stop firme")
    temp = max(0.0, min(100.0, temp))

    if distancia > cfg.get("fomo_atr", 3.0):
        motivos.append(f"el precio está a {distancia:.1f} ATR de su media: perseguirlo es FOMO")
        bloquea = "FOMO: el precio ya se ha ido"
    if racha_perdidas >= cfg.get("pausa_tras_perdidas", 3):
        motivos.append(f"{racha_perdidas} pérdidas seguidas: pausa para no operar por venganza")
        bloquea = bloquea or "en racha de pérdidas"

    cuadros = round(temp / 20)
    lineas = [f"Temperatura {'🟦' * cuadros}{'⬜' * (5 - cuadros)} {temp:.0f}/100 → "
              + ("NO entrar ahora, es emoción" if bloquea
                 else "algo caliente, con cuidado" if temp >= 65 else "cabeza fría, se puede valorar")]
    lineas += [f"• {m}" for m in motivos]
    if estudio and estudio.get("n"):
        lineas.append(f"📊 Estudio: {estudio['n']} casos parecidos en el historial → a {estudio['h']} velas "
                      f"acierta {estudio['acierto']:.0f}%, media {estudio['media']:+.2f}%")
    if sim and sim.get("paths"):
        lineas.append(f"🕰️ Máquina del tiempo (solo informativo): en {sim['paths']:,} futuros posibles toca el "
                      f"objetivo primero el {sim['p_tp']:.0f}% de las veces y el stop el {sim['p_sl']:.0f}%."
                      .replace(",", "."))
    return {"temperatura": temp, "bloquea": bloquea, "texto": "\n".join(lineas)}


def estudio_historico(casos: list[dict], alerta, i_actual: int, h: int = 12) -> dict:
    """Mira alertas pasadas del mismo lado (ya resueltas) y qué pasó h velas después."""
    parecidos = [c for c in casos
                 if c["dir"] == alerta.dir and c["i"] + h <= i_actual and abs(c["fuerza"] - alerta.fuerza) <= 25]
    if not parecidos:
        return {"n": 0, "h": h}
    rs = [c["ret_h"] for c in parecidos if c.get("ret_h") is not None]
    if not rs:
        return {"n": 0, "h": h}
    if alerta.dir == "short":
        rs = [-r for r in rs]
    return {"n": len(rs), "h": h, "acierto": 100 * sum(r > 0 for r in rs) / len(rs),
            "media": sum(rs) / len(rs)}
