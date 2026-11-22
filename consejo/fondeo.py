"""Examen de cuenta fondeada (reglas tipo FTMO), solo sobre operaciones SIMULADAS.

Sirve para saber si una estrategia del backtest o del papel habría pasado el reto de una
empresa de fondeo, antes de pensar siquiera en llevarla allí. No se conecta a ninguna cuenta.

Reglas por defecto (las públicas del reto de 2 fases de FTMO y parecidas; se pueden cambiar):
- Fase 1: ganar un 10 % del saldo inicial. Fase 2: ganar un 5 %.
- Pérdida diaria máxima: 5 % del saldo inicial (contando desde el saldo con el que empieza el día).
- Pérdida total máxima: 10 % del saldo inicial (el saldo nunca puede bajar del 90 %).
- Mínimo 4 días con operaciones en cada fase. Sin límite de tiempo.

Cómo se mide: se empieza el reto el primer día de cada mes de la muestra y se recorren las
operaciones cerradas en orden. Con operaciones cerradas el flotante no se ve, así que el
resultado real puede ser algo peor: por eso la fábrica arriesga solo el 1 % por operación.
"""

from __future__ import annotations

from datetime import datetime

PERFILES = {
    "ftmo_2fases": {"nombre": "Reto de 2 fases (tipo FTMO)", "fases": [10.0, 5.0], "perdida_diaria": 5.0,
                    "perdida_max": 10.0, "min_dias": 4},
    "1fase": {"nombre": "Reto de 1 fase", "fases": [10.0], "perdida_diaria": 3.0, "perdida_max": 6.0,
              "min_dias": 0},
}


def _t(x) -> datetime:
    if isinstance(x, datetime):
        return x
    return datetime.fromisoformat(str(x).replace("Z", "+00:00").replace(" ", "T", 1))


def _fase(ops: list[tuple[datetime, float]], k0: int, objetivo: float, reglas: dict) -> tuple[str, int, int]:
    """Recorre desde la operación k0 con saldo 100. Devuelve (resultado, índice siguiente, días)."""
    saldo, dia, saldo_dia, dias = 100.0, None, 100.0, set()
    for k in range(k0, len(ops)):
        t, pct = ops[k]
        d = t.date()
        if d != dia:
            dia, saldo_dia = d, saldo
        saldo += pct
        dias.add(d)
        if saldo_dia - saldo >= reglas["perdida_diaria"]:
            return "pérdida diaria", k + 1, len(dias)
        if 100.0 - saldo >= reglas["perdida_max"]:
            return "pérdida máxima", k + 1, len(dias)
        if objetivo and saldo - 100.0 >= objetivo and len(dias) >= reglas["min_dias"]:
            return "aprobada", k + 1, len(dias)
    return "sin terminar", len(ops), len(dias)


def examen(operaciones: list[dict], capital: float, perfil: str = "ftmo_2fases",
           clave_t: str = "closed", clave_pnl: str = "pnl") -> dict:
    """`operaciones`: dicts con fecha de cierre y beneficio neto en $.

    Se pasan a % del capital inicial, así vale igual para la cuenta de 10.000 $ del Consejo que para
    la de 100.000 $ de la fábrica (el reto se mide en %)."""
    reglas = PERFILES[perfil]
    ops = sorted(((_t(o[clave_t]), 100 * o[clave_pnl] / capital) for o in operaciones), key=lambda x: x[0])
    if not ops:
        return {"perfil": reglas["nombre"], "intentos": 0, "texto": "Sin operaciones: nada que examinar."}
    meses = sorted({(t.year, t.month) for t, _ in ops})
    intentos, aprobadas, fallos, dias_ok, vida, en_curso = 0, 0, {}, [], [], 0
    for y, m in meses:
        k = next(i for i, o in enumerate(ops) if (o[0].year, o[0].month) >= (y, m))
        intentos += 1
        dias_tot, ok = 0, True
        for objetivo in reglas["fases"]:
            res, k, dias = _fase(ops, k, objetivo, reglas)
            dias_tot += dias
            if res != "aprobada":
                if res == "sin terminar":
                    en_curso += 1
                else:
                    fallos[res] = fallos.get(res, 0) + 1
                ok = False
                break
        if ok:
            aprobadas += 1
            dias_ok.append(dias_tot)
            # ya fondeada: ¿cuántas operaciones aguanta sin romper las reglas?
            res, k2, dias = _fase(ops, k, 0.0, reglas)
            vida.append(dias if res != "sin terminar" else None)
    pct = 100 * aprobadas / intentos
    dias_ok.sort()
    med = dias_ok[len(dias_ok) // 2] if dias_ok else None
    quemadas = sum(1 for v in vida if v is not None)
    txt = (f"{reglas['nombre']}: aprobaría {aprobadas} de {intentos} intentos ({pct:.0f} %) empezando cada mes"
           + (f"; mediana de {med} días con operaciones para pasarlo" if med else "")
           + (". Suspendidas: " + ", ".join(f"{v} por {k}" for k, v in sorted(fallos.items())) if fallos else "")
           + (f". {en_curso} {'seguía' if en_curso == 1 else 'seguían'} en curso al acabarse los datos" if en_curso else "")
           + (f". Ya fondeada, {quemadas} de {len(vida)} acabarían rompiendo una regla dentro de la muestra"
              if vida else "") + ".")
    return {"perfil": reglas["nombre"], "intentos": intentos, "aprobadas": aprobadas, "pct_aprobadas": round(pct, 1),
            "fallos": fallos, "en_curso": en_curso, "mediana_dias": med, "fondeadas_que_rompen": quemadas, "fondeadas": len(vida),
            "texto": txt}
