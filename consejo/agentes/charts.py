"""Charts: convierte una alerta en un plan con entrada, stop y objetivo."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Plan:
    dir: str
    entrada: float
    stop: float
    objetivo: float
    precio_espera: float     # entrada mejor si el precio retrocede
    rr: float                # relación objetivo/riesgo planeada
    rr_real: float           # hasta el primer nivel que estorba
    nivel_estorbo: float | None

    @property
    def riesgo(self) -> float:
        return abs(self.entrada - self.stop)

    def texto(self) -> str:
        lado = "LONG" if self.dir == "long" else "SHORT"
        s = (f"Plan {lado}\nEntrada: {self.entrada:.2f}\n"
             f"Stop loss: {self.stop:.2f} ({(self.stop / self.entrada - 1) * 100:+.2f}%)\n"
             f"Take profit: {self.objetivo:.2f} (R:R {self.rr:.1f})\n"
             f"Entrada mejor si retrocede: {self.precio_espera:.2f}")
        if self.nivel_estorbo is not None:
            tipo = "resistencia" if self.dir == "long" else "soporte"
            s += f"\n• hay {tipo} en {self.nivel_estorbo:.2f} antes del objetivo (R:R {self.rr_real:.1f})"
        return s


def _niveles(ctx, i: int, n: int = 150, ala: int = 3):
    """Máximos y mínimos locales de las últimas n velas (pivotes)."""
    altos, bajos = [], []
    vs = ctx.velas
    for k in range(max(ala, i - n), i - ala):
        if all(vs[k].h >= vs[j].h for j in range(k - ala, k + ala + 1)):
            altos.append(vs[k].h)
        if all(vs[k].l <= vs[j].l for j in range(k - ala, k + ala + 1)):
            bajos.append(vs[k].l)
    return altos, bajos


def planificar(ctx, alerta, cfg: dict) -> Plan:
    i = alerta.i
    e = alerta.precio
    a = ctx.atr[i]
    rr = cfg.get("rr_objetivo", 2.0)
    mult = cfg.get("stop_atr", 1.5)
    if alerta.dir == "long":
        stop = min(e - mult * a, ctx.min_previo(i + 1, 5))
        stop = max(stop, e - 3 * a)          # nunca un stop absurdo
        obj = e + rr * (e - stop)
        espera = e - 0.25 * (e - stop)
        altos, _ = _niveles(ctx, i)
        estorbo = min((x for x in altos if e * 1.001 < x < obj), default=None)
    else:
        stop = max(e + mult * a, ctx.max_previo(i + 1, 5))
        stop = min(stop, e + 3 * a)
        obj = e - rr * (stop - e)
        espera = e + 0.25 * (stop - e)
        _, bajos = _niveles(ctx, i)
        estorbo = max((x for x in bajos if obj < x < e * 0.999), default=None)
    riesgo = abs(e - stop) or 1e-9
    rr_real = abs(estorbo - e) / riesgo if estorbo is not None else rr
    return Plan(alerta.dir, e, stop, obj, espera, rr, rr_real, estorbo)
