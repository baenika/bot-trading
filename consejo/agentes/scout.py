"""Scout: detecta movimientos raros (volumen, velas enormes, rupturas)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Alerta:
    simbolo: str
    i: int
    t: object
    dir: str          # "long" o "short"
    precio: float
    fuerza: float     # 0-100
    motivos: list[str] = field(default_factory=list)
    setup: str = "movimiento"   # movimiento | sobreventa | bandera

    def texto(self, timeframe: str) -> str:
        flecha = "🟢 al alza" if self.dir == "long" else "🔴 a la baja"
        lineas = [f"{self.simbolo} {timeframe} — movimiento grande {flecha} (fuerza {self.fuerza:.0f}/100)"]
        lineas += [f"• {m}" for m in self.motivos]
        lineas.append(f"Precio: {self.precio:.2f}. ¿Noticias, por qué se mueve?")
        return "\n".join(lineas)


def escanear(ctx, i: int, cfg: dict) -> Alerta | None:
    """Mira la vela i (ya cerrada) y devuelve una alerta si es un movimiento raro."""
    if i < 120:
        return None
    v = ctx.velas[i]
    if v.t.hour in (cfg.get("horas_prohibidas_utc") or []):
        return None
    a = ctx.atr[i - 1] or 1e-9
    cuerpo = abs(v.c - v.o)
    tam_atr = cuerpo / a
    z = ctx.z_volumen(i)
    dir_ = "long" if v.c >= v.o else "short"
    motivos, puntos = [], 0.0

    if z >= cfg.get("z_volumen_min", 2.5):
        motivos.append(f"volumen {z:.1f}σ por encima de lo normal")
        puntos += min(z, 6) * 10
    if tam_atr >= cfg.get("atr_vela_min", 2.0):
        motivos.append(f"vela de {tam_atr:.1f} ATR ({(v.c / v.o - 1) * 100:+.2f}%)")
        puntos += min(tam_atr, 6) * 8
    alto, bajo = ctx.max_previo(i, 20), ctx.min_previo(i, 20)
    if dir_ == "long" and v.c > alto:
        motivos.append(f"rompe el máximo de 20 velas ({alto:.2f})")
        puntos += 15
    elif dir_ == "short" and v.c < bajo:
        motivos.append(f"rompe el mínimo de 20 velas ({bajo:.2f})")
        puntos += 15
    if ctx.compresion(i):
        motivos.append("sale de una compresión de volatilidad")
        puntos += 10

    # Hace falta volumen raro o vela enorme, y al menos dos señales en total.
    fuerte = z >= cfg.get("z_volumen_min", 2.5) or tam_atr >= cfg.get("atr_vela_min", 2.0)
    if fuerte and len(motivos) >= 2:
        return Alerta(ctx.simbolo, i, v.t, dir_, v.c, min(100.0, puntos), motivos)
    if cfg.get("scout_sobreventa") and ctx.rsi[i - 1] < 30 and v.c > v.o and v.c > ctx.velas[i - 1].c:
        return Alerta(ctx.simbolo, i, v.t, "long", v.c, 45.0,
                      [f"RSI {ctx.rsi[i - 1]:.0f} (sobreventa) y la vela rebota al alza"], "sobreventa")
    if cfg.get("scout_bandera"):
        b = bandera(ctx, i)
        if b:
            return Alerta(ctx.simbolo, i, v.t, "long", v.c, 50.0, [b], "bandera")
    return None


def bandera(ctx, i: int) -> str | None:
    """Bandera alcista (como el scanner de day-trader-skill, pero medida en ATR porque son velas de 1 h):
    subida de más de 4 ATR en las 10 velas anteriores a las 5 últimas, esas 5 en un rango de menos de
    1,5 ATR, y la vela actual cierra por encima de ese rango."""
    if i < 20:
        return None
    vs, a = ctx.velas, ctx.atr[i - 1] or 1e-9
    asta = vs[i - 6].c - vs[i - 16].c
    alto = max(x.h for x in vs[i - 5:i])
    bajo = min(x.l for x in vs[i - 5:i])
    if asta > 4 * a and alto - bajo < 1.5 * a and vs[i].c > alto:
        return f"bandera alcista: subida de {asta / a:.1f} ATR, pausa estrecha y rompe por arriba"
    return None
