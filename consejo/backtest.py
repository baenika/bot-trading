"""Backtest del Consejo: recorre las velas en orden y simula cada decisión.

Todo es simulado: no hay broker, ni claves, ni órdenes reales.

Reglas de ejecución (conservadoras a propósito):
- Una decisión tomada al cierre de la vela i se ejecuta a la apertura de i+1.
- Si en una misma vela se tocan el stop y el objetivo, se asume el stop.
- Si el precio abre más allá del stop (hueco), se sale a la apertura.
- Comisión y deslizamiento en cada lado.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from . import maquina_tiempo, seguridad
from .agentes import charts, news, scout, sentimental, skeptic
from .agentes.risk import GestorRiesgo
from .indicadores import Contexto

CONFIG_DEFECTO = {
    "modo": "backtest",
    "timeframe": "1h",
    "capital_inicial": 10000.0,
    "comision_pct": 0.10,
    "deslizamiento_pct": 0.05,
    "max_barras": 48,
    "espera_barras": 6,
    "retroceso_pct": 25,          # entrada en retroceso: % del camino hacia el stop
    "retroceso_modo": "pct",      # "pct" (retroceso_pct) o "confluencia" (mitad del impulso + nivel roto)
    "caminos_backtest": 1500,
    "caminos_ultima": 10000,
    "permitir_cortos": False,
    "rr_objetivo": 2.0,
    "rr_minimo": 1.8,
    "stop_atr": 1.5,
    "stop_max_atr": 3.0,          # tope del stop; tras una vela grande el mínimo de 5 velas suele llegar hasta aquí
    "z_volumen_min": 2.5,
    "atr_vela_min": 2.0,
    "fomo_atr": 3.0,
    "pausa_tras_perdidas": 3,
    "pausa_horas": 24,
    "fomo_modo": "esperar",       # "esperar": con FOMO solo se entra si retrocede; "vetar": no se entra
    "puntos_para_entrar": 2,
    "puntos_para_esperar": 1,
    "risk_per_trade_pct": 1.0,
    "daily_loss_limit_pct": 3.0,
    "max_drawdown_pct": 15.0,
    "max_open_positions": 3,
    "max_entradas_dia": 3,        # day-trader-skill: como mucho 3 entradas al día
    "max_posicion_pct": 33.0,     # ninguna posición por encima de un tercio de la cuenta
    "efectivo_min_pct": 10.0,     # y siempre un 10 % en efectivo
    "un_disparo_dia": True,       # una sola entrada por señal y moneda al día
    "scout_sobreventa": False,    # alarmas nuevas: solo se activan si el autoajuste demuestra que ganan
    "scout_bandera": False,
    "horas_prohibidas_utc": [],
    "moving_pct": 3.0,
    "ventana_noticias_h": 24,
    "calibracion_max_error": 7.0,
    "calibracion_min_casos": 200,
    "kronos_ruta": "",            # carpeta del repo de Kronos; vacío = sin Kronos
    "kronos_modelo": "NeoQuasar/Kronos-small",
    "kronos_dispositivo": "auto",  # "auto" = GPU NVIDIA si PyTorch la ve; si no, CPU (unas 30 veces más lento)
    "kronos_caminos": 10,
    "kronos_max": 400,            # en backtest, a cuántas alertas como mucho se le pregunta (es lento)
}


def _duracion(timeframe: str) -> timedelta:
    n, u = int(timeframe[:-1] or 1), timeframe[-1]
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}.get(u, timedelta(hours=1))


def toca_objetivo(ctx, i: int, plan, max_barras: int) -> bool | None:
    """¿Llegó antes al objetivo que al stop en las siguientes velas? (lo que predice la máquina del tiempo)."""
    vs = ctx.velas
    if i + max_barras >= len(vs):
        return None
    for k in range(i + 1, i + 1 + max_barras):
        v = vs[k]
        if (plan.dir == "long" and v.l <= plan.stop) or (plan.dir == "short" and v.h >= plan.stop):
            return False                       # si toca los dos en la misma vela, cuenta el stop
        if (plan.dir == "long" and v.h >= plan.objetivo) or (plan.dir == "short" and v.l <= plan.objetivo):
            return True
    return False


class Calibracion:
    """Compara lo que dijo la máquina del tiempo con lo que pasó de verdad.

    Solo cuenta un caso cuando ya ha pasado su horizonte, así que en cada
    momento del backtest solo se usa información que ya se conocía.
    """

    def __init__(self):
        self.pendientes: list[tuple[datetime, float, bool]] = []
        self.tramos = [[0, 0.0, 0] for _ in range(10)]    # casos, suma de p predicha, aciertos

    def anotar(self, t_resuelve, p_tp: float, acierto: bool):
        self.pendientes.append((t_resuelve, p_tp, acierto))

    def avanzar(self, t):
        quedan = []
        for tr, p, ok in self.pendientes:
            if tr <= t:
                b = self.tramos[min(9, int(p // 10))]
                b[0] += 1
                b[1] += p
                b[2] += ok
            else:
                quedan.append((tr, p, ok))
        self.pendientes = quedan

    @property
    def n(self) -> int:
        return sum(b[0] for b in self.tramos)

    def error(self) -> float | None:
        """Error medio en puntos: |% predicho - % real|, ponderado por casos de cada tramo."""
        n = self.n
        if not n:
            return None
        return sum(abs(b[1] / b[0] - 100 * b[2] / b[0]) * b[0] for b in self.tramos if b[0]) / n

    def resumen(self, max_error: float, min_casos: int) -> dict:
        self.avanzar(datetime.max.replace(tzinfo=timezone.utc))
        e = self.error()
        return {"n": self.n, "error_pts": round(e, 1) if e is not None else None,
                "max_error_pts": max_error, "min_casos": min_casos,
                "usable": e is not None and self.n >= min_casos and e < max_error,
                "tramos": [{"pred_%": round(b[1] / b[0], 1), "real_%": round(100 * b[2] / b[0], 1), "n": b[0]}
                           for b in self.tramos if b[0]]}


def _fmt(t) -> str:
    return t.strftime("%Y-%m-%d %H:%M")


def resultado_r(ctx, i: int, plan, max_barras: int) -> float | None:
    """R que habría dado entrar a mercado en i+1 con este plan (sin comisiones)."""
    vs = ctx.velas
    if i + 1 >= len(vs):
        return None
    e = vs[i + 1].o
    riesgo = abs(e - plan.stop)
    if riesgo <= 0 or (plan.dir == "long" and not plan.stop < e < plan.objetivo) or \
       (plan.dir == "short" and not plan.objetivo < e < plan.stop):
        return None
    signo = 1 if plan.dir == "long" else -1
    for k in range(i + 1, min(len(vs), i + 1 + max_barras)):
        v = vs[k]
        if (plan.dir == "long" and v.l <= plan.stop) or (plan.dir == "short" and v.h >= plan.stop):
            return -1.0
        if (plan.dir == "long" and v.h >= plan.objetivo) or (plan.dir == "short" and v.l <= plan.objetivo):
            return abs(plan.objetivo - e) / riesgo
    k = min(len(vs) - 1, i + max_barras)
    return signo * (vs[k].c - e) / riesgo


class Motor:
    def __init__(self, series: dict, cfg: dict | None = None, con_skeptic: bool = True,
                 noticias: dict | None = None):
        self.cfg = {**CONFIG_DEFECTO, **(cfg or {})}
        seguridad.comprobar_config(self.cfg)
        self.con_skeptic = con_skeptic
        self.ctx = {s: Contexto(s, v) for s, v in series.items()}
        self.riesgo = GestorRiesgo(self.cfg)
        self.capital = self.pico = self.inicio_dia = float(self.cfg["capital_inicial"])
        self.dia = None
        self.abiertas: dict[str, dict] = {}
        self.pendientes: dict[str, dict] = {}
        self.operaciones: list[dict] = []
        self.curva: list[tuple[str, float]] = []
        self.pnl_diario: Counter = Counter()
        self.decisiones: Counter = Counter()
        self.vetos: Counter = Counter()          # por qué veta el Skeptic (para ajustar sin adivinar)
        self.puntos: Counter = Counter()         # puntuación de las alertas sin veto
        self.casos: dict[str, list[dict]] = {s: [] for s in series}
        self.rechazadas: list[float] = []
        self.racha = 0
        self.t_pausa = None                       # cuándo empezó la pausa por racha de pérdidas
        self.bots: dict[str, dict] = {}
        self.ultima: dict | None = None
        self.noticias = noticias or {}
        self.calibracion = Calibracion()
        self.calibracion_kronos = Calibracion()
        self.kronos = None
        self.n_kronos = 0
        if self.cfg.get("kronos_ruta"):
            from . import kronos_opinion
            self.kronos = kronos_opinion.cargar(self.cfg["kronos_ruta"], self.cfg["kronos_modelo"],
                                                dispositivo=self.cfg.get("kronos_dispositivo", "auto"))
        self.duracion = _duracion(self.cfg["timeframe"])
        self.ultimo_i: dict[str, int] = {}
        self.entradas_hoy: list[dict] = []    # entradas del día (para el riesgo diario y el disparo único)

    # --- ejecución simulada -------------------------------------------------
    def _coste(self, precio: float, tam: float) -> float:
        return precio * tam * self.cfg["comision_pct"] / 100

    def _abrir(self, s, i, dir_, precio, plan, tipo, t, ficha=None):
        setup = (ficha or {}).get("setup", "movimiento")
        hoy = [e for e in self.entradas_hoy if e["dia"] == t.date().isoformat()]
        ok = self.riesgo.aprobar(self.capital, self.inicio_dia, len(self.abiertas), plan, s in self.abiertas,
                                 riesgo_hoy=sum(e["riesgo"] for e in hoy), entradas_hoy=len(hoy),
                                 exposicion=sum(p.get("usd", p["entry"] * p["size"]) for p in self.abiertas.values()),
                                 ya_disparada=self.cfg.get("un_disparo_dia", True)
                                 and any(e["s"] == s and e["setup"] == setup for e in hoy))
        self.bots["risk"] = {"time": _fmt(t), "text": ok["texto"]}
        if not ok["ok"]:
            self.vetos["(Risk) " + re.sub(r"[\d.,]+", "N", ok["texto"].split(":")[0].split(" (")[0])[:70]] += 1
            return
        if (dir_ == "long" and not plan.stop < precio < plan.objetivo) or \
           (dir_ == "short" and not plan.objetivo < precio < plan.stop):
            return  # el precio ya se salió del plan
        tam = ok["arriesgo"] / abs(precio - plan.stop)
        tam = min(tam, ok["tope_usd"] / precio)
        self.entradas_hoy = hoy + [{"dia": t.date().isoformat(), "s": s, "setup": setup,
                                    "riesgo": abs(precio - plan.stop) * tam}]
        self.capital -= self._coste(precio, tam)
        self.abiertas[s] = {"symbol": s, "dir": dir_, "kind": tipo, "entry": precio, "stop": plan.stop,
                            "tp": plan.objetivo, "size": tam, "i": i, "opened": t,
                            "riesgo": abs(precio - plan.stop) * tam, "t_i": t.isoformat(),
                            "usd": precio * tam, "ficha": ficha or {}}

    def _cerrar(self, s, precio, motivo, t):
        p = self.abiertas.pop(s)
        signo = 1 if p["dir"] == "long" else -1
        bruto = signo * (precio - p["entry"]) * p["size"]
        neto = bruto - self._coste(precio, p["size"])
        self.capital += neto
        r = neto / p["riesgo"] if p["riesgo"] else 0.0
        self.racha = self.racha + 1 if neto < 0 else 0
        if self.racha == self.cfg["pausa_tras_perdidas"]:
            self.t_pausa = t
        self.pnl_diario[t.strftime("%Y-%m-%d")] += neto
        self.operaciones.append({"symbol": s, "dir": p["dir"], "kind": p["kind"],
                                 "opened": str(p["opened"]), "closed": str(t), "entry": p["entry"],
                                 "exit": precio, "pnl": neto, "R": r, "reason": motivo, "stop": p["stop"],
                                 "tp": p["tp"], "size": p["size"], "usd": p.get("usd", p["entry"] * p["size"]),
                                 "ficha": p.get("ficha", {})})

    def _gestionar(self, s, i):
        p = self.abiertas.get(s)
        if not p:
            return
        v = self.ctx[s].velas[i]
        desl = self.cfg["deslizamiento_pct"] / 100
        if p["dir"] == "long":
            if v.l <= p["stop"]:
                self._cerrar(s, min(v.o, p["stop"]) * (1 - desl), "stop loss", v.t)
            elif v.h >= p["tp"]:
                self._cerrar(s, max(v.o, p["tp"]) if v.o > p["tp"] else p["tp"], "take profit", v.t)
            elif i - p["i"] >= self.cfg["max_barras"]:
                self._cerrar(s, v.c * (1 - desl), "tiempo máximo", v.t)
        else:
            if v.h >= p["stop"]:
                self._cerrar(s, max(v.o, p["stop"]) * (1 + desl), "stop loss", v.t)
            elif v.l <= p["tp"]:
                self._cerrar(s, min(v.o, p["tp"]) if v.o < p["tp"] else p["tp"], "take profit", v.t)
            elif i - p["i"] >= self.cfg["max_barras"]:
                self._cerrar(s, v.c * (1 + desl), "tiempo máximo", v.t)

    def _pendiente(self, s, i):
        o = self.pendientes.get(s)
        if not o:
            return
        v = self.ctx[s].velas[i]
        if o["tipo"] == "mercado":
            del self.pendientes[s]
            desl = self.cfg["deslizamiento_pct"] / 100
            precio = v.o * (1 + desl if o["plan"].dir == "long" else 1 - desl)
            self._abrir(s, i, o["plan"].dir, precio, o["plan"], "mercado", v.t, o.get("ficha"))
        else:  # retroceso: orden límite al precio de espera
            plan = o["plan"]
            toca = v.l <= plan.precio_espera if plan.dir == "long" else v.h >= plan.precio_espera
            if toca:
                del self.pendientes[s]
                self._abrir(s, i, plan.dir, plan.precio_espera, plan, "retroceso", v.t, o.get("ficha"))
            elif i - o["i"] >= self.cfg["espera_barras"]:
                del self.pendientes[s]

    # --- el Consejo ---------------------------------------------------------
    def _consejo(self, s, i, ultima_vela: bool):
        ctx, cfg = self.ctx[s], self.cfg
        alerta = scout.escanear(ctx, i, cfg)
        if not alerta:
            return
        # la pausa tras una racha de pérdidas dura un tiempo; después se vuelve a empezar de cero
        # (si no, sin operaciones nuevas la racha nunca se reinicia y el bot se queda parado para siempre)
        if self.racha >= cfg["pausa_tras_perdidas"] and self.t_pausa is not None and \
                alerta.t - self.t_pausa >= timedelta(hours=cfg["pausa_horas"]):
            self.racha, self.t_pausa = 0, None
        t = _fmt(alerta.t)
        h = 12
        ret_h = (ctx.velas[i + h].c / ctx.velas[i].c - 1) * 100 if i + h < len(ctx.velas) else None
        plan = charts.planificar(ctx, alerta, cfg)
        caminos = cfg["caminos_ultima"] if ultima_vela else cfg["caminos_backtest"]
        sim = maquina_tiempo.simular(ctx.rend[max(1, i - 1000):i + 1], plan.entrada, plan.stop, plan.objetivo,
                                     plan.dir, horizonte=cfg["max_barras"], caminos=caminos, semilla=i)
        estudio = sentimental.estudio_historico(self.casos[s], alerta, i, h)
        hist = self.historial_senal(s, alerta, i)
        self.casos[s].append({"i": i, "dir": alerta.dir, "fuerza": alerta.fuerza, "ret_h": ret_h,
                              "setup": alerta.setup, "plan": plan})
        cierre = alerta.t + self.duracion           # la decisión se toma al cierre de la vela
        noticias = news.opinar(alerta, ctx, self.noticias.get(s), cierre, cfg)
        senti = sentimental.opinar(ctx, alerta, self.racha, estudio, sim, cfg, noticias)

        # calibración de la máquina del tiempo: solo con casos ya resueltos
        self.calibracion.avanzar(cierre)
        ok = toca_objetivo(ctx, i, plan, cfg["max_barras"])
        if ok is not None and sim.get("paths"):
            self.calibracion.anotar(ctx.velas[i + cfg["max_barras"]].t + self.duracion, sim["p_tp"], ok)
        err = self.calibracion.error()
        calibrada = (err is not None and err < cfg["calibracion_max_error"]
                     and self.calibracion.n >= cfg["calibracion_min_casos"])

        # Kronos: segunda opinión, con su propia calibración (solo vota si demuestra que acierta)
        kron, kron_ok = {}, False
        if self.kronos is not None and self.n_kronos < cfg["kronos_max"]:
            from . import kronos_opinion
            self.n_kronos += 1
            kron = kronos_opinion.probabilidad(
                kronos_opinion.futuros(self.kronos, ctx.velas[:i + 1], self.duracion, cfg["max_barras"],
                                       cfg["kronos_caminos"]), plan.stop, plan.objetivo, plan.dir)
            kron["breakeven"] = sim.get("breakeven", 100 / (1 + plan.rr))
            self.calibracion_kronos.avanzar(cierre)
            if ok is not None:
                self.calibracion_kronos.anotar(ctx.velas[i + cfg["max_barras"]].t + self.duracion, kron["p_tp"], ok)
            ek = self.calibracion_kronos.error()
            kron_ok = (ek is not None and ek < cfg["calibracion_max_error"]
                       and self.calibracion_kronos.n >= cfg["calibracion_min_casos"])
            senti["texto"] += (f"\n🤖 Kronos ({'calibrado' if kron_ok else 'solo informativo'}): en {kron['paths']} "
                               f"futuros toca el objetivo primero el {kron['p_tp']:.0f}% y el stop el {kron['p_sl']:.0f}%.")

        if self.con_skeptic:
            dec = skeptic.decidir(ctx, alerta, plan, senti, estudio, sim, noticias, cfg, calibrada, kron, kron_ok)
        else:  # comparación: entra en todo lo que permite la config
            veto = alerta.dir == "short" and not cfg["permitir_cortos"]
            d = "PASAR" if veto else ("COMPRAR" if alerta.dir == "long" else "VENDER")
            dec = {"decision": d, "score": 0, "pros": [], "cons": [], "vetoes": [], "texto": d}
        self.decisiones[dec["decision"]] += 1
        for v in dec["vetoes"]:
            self.vetos[re.sub(r"[\d.,]+", "N", v.split(": ")[0])[:60]] += 1
        if len(dec["vetoes"]) == 1:
            self.vetos["(solo) " + re.sub(r"[\d.,]+", "N", dec["vetoes"][0].split(": ")[0])[:52]] += 1
        if senti.get("fomo"):
            self.vetos["(aviso, no veto) FOMO: esperar retroceso"] += 1
        if not dec["vetoes"]:
            self.puntos[round(dec["score"])] += 1

        # ficha: lo que vio el Consejo al decidir (se guarda con la operación para aprender de ella)
        ficha = {"setup": alerta.setup, "decision": dec["decision"], "score": dec["score"], "pros": dec["pros"][:6], "cons": dec["cons"][:6],
                 "fuerza": alerta.fuerza, "rr": round(plan.rr, 2), "temperatura": senti.get("temperatura"),
                 "fomo": bool(senti.get("fomo")), "moving": bool(noticias.get("moving")),
                 "etiquetas": noticias.get("etiquetas", []), "p_tp_sim": sim.get("p_tp"),
                 "sim_calibrada": calibrada, "p_tp_kronos": (kron or {}).get("p_tp"), "hora_utc": alerta.t.hour,
                 "historial": hist}
        if dec["decision"] in ("COMPRAR", "VENDER"):
            self.pendientes[s] = {"tipo": "mercado", "plan": plan, "i": i, "t_i": ctx.velas[i].t.isoformat(),
                                  "ficha": ficha}
        elif dec["decision"] == "ESPERAR":
            self.pendientes[s] = {"tipo": "retroceso", "plan": plan, "i": i, "t_i": ctx.velas[i].t.isoformat(),
                                  "ficha": ficha}
        else:
            r = resultado_r(ctx, i, plan, cfg["max_barras"])
            if r is not None and not (alerta.dir == "short" and not cfg["permitir_cortos"]):
                self.rechazadas.append(r)

        self.bots.update({
            "scout": {"time": t, "text": alerta.texto(cfg["timeframe"]) + (
                f"\n📜 Esta señal salió {hist['n']} veces antes en {s}: {hist['objetivo']} al objetivo, "
                f"{hist['stop']} al stop y {hist['nada']} sin llegar a ninguno." if hist["n"] else
                "\n📜 Primera vez que sale esta señal aquí: sin historial.")},
            "news": {"time": t, "text": noticias["texto"]},
            "chart": {"time": t, "text": plan.texto()},
            "sentimental": {"time": t, "text": senti["texto"]},
            "skeptic": {"time": t, "text": dec["texto"]},
        })
        self.ultima = {"time": t, "symbol": s, "decision": dec["decision"], "score": dec["score"],
                       "pros": dec["pros"], "cons": dec["cons"], "vetoes": dec["vetoes"], "dir": alerta.dir,
                       "entry": plan.entrada, "sl": plan.stop, "tp": plan.objetivo,
                       "wait_price": plan.precio_espera if dec["decision"] == "ESPERAR" else None,
                       "temperature": senti["temperatura"], "sim": sim, "kronos": kron or None}

    # --- bucle principal ------------------------------------------------------
    def paso(self, s: str, i: int, ultima_vela: bool = False) -> None:
        """Procesa la vela i del símbolo s: gestiona posiciones, órdenes y consulta al Consejo."""
        t = self.ctx[s].velas[i].t
        dia = t.date()
        if dia != self.dia:
            self.dia, self.inicio_dia = dia, self.capital
        self._gestionar(s, i)
        self._pendiente(s, i)
        self._consejo(s, i, ultima_vela)
        self.pico = max(self.pico, self.capital)
        self.ultimo_i[s] = i
        if not self.riesgo.parado:
            self.riesgo.revisar_cuenta(self.capital, self.pico)
            if self.riesgo.parado:  # corte de drawdown: se cierra todo y se anula lo pendiente
                self.pendientes.clear()
                for sim in list(self.abiertas):
                    v = self.ctx[sim].velas[self.ultimo_i.get(sim, i)]
                    self._cerrar(sim, v.c, "corte de drawdown", v.t)

    def historial_senal(self, s: str, alerta, i: int) -> dict:
        """Las veces que esta misma señal saltó antes en este símbolo y ya se sabe cómo acabó
        (solo casos cerrados antes de la vela i: nada del futuro)."""
        ctx, mb = self.ctx[s], self.cfg["max_barras"]
        n = obj = stop = 0
        for c in self.casos[s]:
            if c.get("setup", "movimiento") != alerta.setup or c["dir"] != alerta.dir or c["i"] + mb >= i \
                    or c.get("plan") is None:
                continue
            if "res" not in c:
                c["res"] = toca_objetivo(ctx, c["i"], c["plan"], mb)
                if c["res"] is False:   # ¿stop o ninguno de los dos?
                    vs = ctx.velas[c["i"] + 1:c["i"] + 1 + mb]
                    p = c["plan"]
                    tocó = any(v.l <= p.stop for v in vs) if p.dir == "long" else any(v.h >= p.stop for v in vs)
                    c["res"] = False if tocó else "nada"
            n += 1
            obj += c["res"] is True
            stop += c["res"] is False
        return {"n": n, "objetivo": obj, "stop": stop, "nada": n - obj - stop}

    def precalentar(self, s: str, hasta: int) -> None:
        """Rellena el estudio histórico de Sentimental con las alertas pasadas, sin operar."""
        ctx, h = self.ctx[s], 12
        self.casos[s] = []
        for i in range(hasta + 1):
            a = scout.escanear(ctx, i, self.cfg)
            if a:
                ret_h = (ctx.velas[i + h].c / ctx.velas[i].c - 1) * 100 if i + h < len(ctx.velas) else None
                self.casos[s].append({"i": i, "dir": a.dir, "fuerza": a.fuerza, "ret_h": ret_h,
                                      "setup": a.setup, "plan": charts.planificar(ctx, a, self.cfg)})

    def correr(self) -> "Motor":
        eventos = sorted((v.t, s, i) for s, c in self.ctx.items() for i, v in enumerate(c.velas))
        ultimo_t = eventos[-1][0] if eventos else None
        for n, (t, s, i) in enumerate(eventos):
            self.paso(s, i, ultima_vela=(t == ultimo_t))
            if n % (10 * len(self.ctx)) == 0 or t == ultimo_t:
                self.curva.append((_fmt(t), round(self.capital, 2)))
        return self

    # --- guardar y recuperar el estado (para el modo papel en vivo) -----------
    def exportar(self) -> dict:
        def pos(p):
            return {**p, "opened": p["opened"].isoformat(), "i": None}
        return {
            "capital": self.capital, "pico": self.pico, "inicio_dia": self.inicio_dia,
            "dia": self.dia.isoformat() if self.dia else None,
            "abiertas": {s: pos(p) for s, p in self.abiertas.items()},
            "pendientes": {s: {"tipo": o["tipo"], "plan": asdict(o["plan"]), "t_i": o["t_i"], "ficha": o.get("ficha", {})}
                           for s, o in self.pendientes.items()},
            "operaciones": self.operaciones, "curva": self.curva, "pnl_diario": dict(self.pnl_diario),
            "decisiones": dict(self.decisiones), "rechazadas": self.rechazadas, "racha": self.racha,
            "t_pausa": self.t_pausa.isoformat() if self.t_pausa else None,
            "parado": self.riesgo.parado, "bots": self.bots, "ultima": self.ultima,
            "entradas_hoy": self.entradas_hoy,
            "visto": {s: self.ctx[s].velas[i].t.isoformat() for s, i in self.ultimo_i.items() if i >= 0},
        }

    def _indice(self, s: str, t_iso: str) -> int:
        """Índice de la vela con esa fecha en la serie actual (o uno muy atrás si ya no está)."""
        t = datetime.fromisoformat(t_iso)
        vs = self.ctx[s].velas
        for k in range(len(vs) - 1, -1, -1):
            if vs[k].t <= t:
                return k
        return -10 ** 6

    def restaurar(self, d: dict) -> "Motor":
        from datetime import date
        self.capital, self.pico, self.inicio_dia = d["capital"], d["pico"], d["inicio_dia"]
        self.dia = date.fromisoformat(d["dia"]) if d.get("dia") else None
        self.abiertas = {}
        for s, p in d.get("abiertas", {}).items():
            if s in self.ctx:
                self.abiertas[s] = {**p, "opened": datetime.fromisoformat(p["opened"]),
                                    "i": self._indice(s, p["t_i"])}
        self.pendientes = {s: {"tipo": o["tipo"], "plan": charts.Plan(**o["plan"]), "i": self._indice(s, o["t_i"]),
                               "t_i": o["t_i"], "ficha": o.get("ficha", {})}
                           for s, o in d.get("pendientes", {}).items() if s in self.ctx}
        self.operaciones = d.get("operaciones", [])
        self.curva = [tuple(x) for x in d.get("curva", [])]
        self.pnl_diario = Counter(d.get("pnl_diario", {}))
        self.decisiones = Counter(d.get("decisiones", {}))
        self.rechazadas = d.get("rechazadas", [])
        self.racha = d.get("racha", 0)
        self.t_pausa = datetime.fromisoformat(d["t_pausa"]) if d.get("t_pausa") else None
        self.riesgo.parado = d.get("parado", False)
        self.bots = d.get("bots", {})
        self.entradas_hoy = d.get("entradas_hoy", [])
        self.ultima = d.get("ultima")
        for s, t in d.get("visto", {}).items():
            if s in self.ctx:
                self.ultimo_i[s] = self._indice(s, t)
        return self

    # --- resultados -------------------------------------------------------------
    def estadisticas(self) -> dict:
        ops = self.operaciones
        ini = self.cfg["capital_inicial"]
        gan = sum(o["pnl"] for o in ops if o["pnl"] > 0)
        per = -sum(o["pnl"] for o in ops if o["pnl"] < 0)
        pico, dd = ini, 0.0
        for _, e in self.curva:
            pico = max(pico, e)
            dd = min(dd, (e / pico - 1) * 100)
        diarios = list(self.pnl_diario.values())
        sharpe = 0.0
        if len(diarios) > 1:
            m = sum(diarios) / len(diarios)
            sd = math.sqrt(sum((x - m) ** 2 for x in diarios) / (len(diarios) - 1))
            sharpe = m / sd * math.sqrt(365) if sd else 0.0
        return {
            "trades": len(ops),
            "max_dd_pct": dd,
            "return_pct": (self.capital / ini - 1) * 100,
            "win_rate": 100 * sum(o["pnl"] > 0 for o in ops) / len(ops) if ops else 0.0,
            "avg_R": sum(o["R"] for o in ops) / len(ops) if ops else 0.0,
            "profit_factor": gan / per if per else (float("inf") if gan else 0.0),
            "sharpe": sharpe,
            "best": max((o["pnl"] for o in ops), default=0.0),
            "worst": min((o["pnl"] for o in ops), default=0.0),
        }


def _comprar_y_mantener(series: dict) -> float:
    rs = [(v[-1].c / v[0].c - 1) * 100 for v in series.values() if v]
    return sum(rs) / len(rs) if rs else 0.0


def examen(st: dict, comprar_y_mantener: float) -> list[dict]:
    """Los criterios de David antes de plantearse siquiera el dinero real (del Consejo v5).
    Sin ninguna operación cerrada no hay nada que juzgar: las casillas salen «sin datos» (ok None)."""
    sin = not st["trades"]
    return [
        {"criterio": "100 operaciones o más", "ok": st["trades"] >= 100, "valor": st["trades"]},
        {"criterio": "profit factor mayor de 1,3", "ok": None if sin else st["profit_factor"] > 1.3,
         "valor": round(st["profit_factor"], 2) if math.isfinite(st["profit_factor"]) else 999},
        {"criterio": "drawdown menor del 20 %", "ok": None if sin else st["max_dd_pct"] > -20, "valor": round(st["max_dd_pct"], 1)},
        {"criterio": "gana a comprar y mantener", "ok": None if sin else st["return_pct"] > comprar_y_mantener,
         "valor": f"{st['return_pct']:+.1f}% frente a {comprar_y_mantener:+.1f}%"},
    ]


def ejecutar(series: dict, cfg: dict | None = None, fuente: str = "backtest", periodo: str | None = None,
             noticias: dict | None = None) -> dict:
    """Corre el Consejo y la versión sin Skeptic, y devuelve el JSON del panel."""
    m = Motor(series, cfg, noticias=noticias).correr()
    sin = Motor(series, cfg, con_skeptic=False, noticias=noticias).correr()
    return estado_panel(m, series, fuente, periodo, sin, noticias)


def _iso(t) -> str:
    return (t if isinstance(t, str) else t.isoformat()).replace(" ", "T")


def grafico(m: "Motor", series: dict, max_velas: int = 1500) -> dict:
    """Velas recientes de cada símbolo con sus operaciones (zonas de stop y objetivo, y tamaño en $)."""
    out = {}
    for s, vs in series.items():
        vs = vs[-max_velas:]
        if not vs:
            continue
        desde = vs[0].t.isoformat()
        r = lambda x: float(f"{x:.6g}")  # noqa: E731
        ops = [{"opened": _iso(o["opened"]), "closed": _iso(o["closed"]), "dir": o["dir"], "entry": r(o["entry"]),
                "exit": r(o["exit"]), "stop": r(o.get("stop", o["entry"])), "tp": r(o.get("tp", o["entry"])),
                "usd": round(o.get("usd", 0), 2), "pnl": round(o["pnl"], 2), "R": round(o["R"], 2),
                "reason": o["reason"], "kind": o.get("kind", "")}
               for o in m.operaciones if o["symbol"] == s and _iso(o["closed"]) >= desde]
        pos = [{"opened": _iso(p["opened"]), "dir": p["dir"], "entry": r(p["entry"]), "stop": r(p["stop"]),
                "tp": r(p["tp"]), "usd": round(p.get("usd", p["entry"] * p["size"]), 2)}
               for p in m.abiertas.values() if p["symbol"] == s]
        out[s] = {"candles": [[v.t.isoformat(), r(v.o), r(v.h), r(v.l), r(v.c)] for v in vs],
                  "trades": ops, "positions": pos}
    return out


def estado_panel(m: "Motor", series: dict, fuente: str, periodo: str | None, sin: "Motor | None" = None,
                 noticias: dict | None = None) -> dict:
    """JSON del panel a partir de un motor (y, si hay, de su versión sin Skeptic)."""
    st = m.estadisticas()
    sc = sin.estadisticas() if sin else None
    ultimo = m.curva[-1][0] if m.curva else None
    hoy = ultimo[:10] if ultimo else None
    pf = lambda x: round(x, 3) if math.isfinite(x) else 999.0
    return {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": fuente,
        "mode": "paper",
        "stopped": m.riesgo.parado,
        "symbols": list(series),
        "timeframe": m.cfg["timeframe"],
        "period": periodo or "sintético",
        "start_equity": m.cfg["capital_inicial"],
        "equity": m.capital,
        "peak": m.pico,
        "day_start": m.inicio_dia,
        "today_pnl": m.pnl_diario.get(hoy, 0.0) if hoy else 0.0,
        "curve": m.curva,
        "compare_curve": sin.curva if sin else None,
        "daily_pnl": sorted(([d, round(x, 2)] for d, x in m.pnl_diario.items()))[-30:],
        "positions": [{**p, "opened": str(p["opened"])} for p in m.abiertas.values()],
        "trades": [{k: v for k, v in o.items() if k != "ficha"} for o in reversed(m.operaciones)][:30],
        "chart": grafico(m, series),
        "stats": {k: (pf(v) if k == "profit_factor" else v) for k, v in st.items()
                  if k in ("trades", "max_dd_pct", "return_pct", "win_rate", "avg_R", "profit_factor")},
        "limits": m.riesgo.limites(),
        "bots": m.bots,
        "last_decision": m.ultima,
        "decisions": dict(m.decisiones),
        "veto_counts": dict(m.vetos.most_common(14)),
        "score_counts": {str(k): v for k, v in sorted(m.puntos.items())},
        "calibration_kronos": m.calibracion_kronos.resumen(m.cfg["calibracion_max_error"], m.cfg["calibracion_min_casos"])
        if m.kronos is not None else None,
        "exam": examen(st, _comprar_y_mantener(series)),
        "calibration": m.calibracion.resumen(m.cfg["calibracion_max_error"], m.cfg["calibracion_min_casos"]),
        "news_coverage": {s: len(a.titulares) for s, a in (noticias or {}).items()},
        "skeptic_saved": {
            "rejected": len(m.rechazadas),
            "would_lose": sum(r < 0 for r in m.rechazadas),
            "R_avoided": sum(r for r in m.rechazadas if r < 0),
            "R_all_rejected": sum(m.rechazadas),
            "buy_hold_pct": _comprar_y_mantener(series),
        },
        "compare_stats": {
            "capital_final": sin.capital, "rentabilidad_%": sc["return_pct"], "max_drawdown_%": sc["max_dd_pct"],
            "trades": sc["trades"], "sharpe": sc["sharpe"], "acierto_%": sc["win_rate"], "R_medio": sc["avg_R"],
            "profit_factor": pf(sc["profit_factor"]), "mejor_trade": sc["best"], "peor_trade": sc["worst"],
        } if sin else None,
    }
