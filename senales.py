"""Señales de un canal de Telegram frente a velas REALES: ¿lo que dice el canal se cumplió?

    py senales.py                                   # busca el result.json más nuevo en Descargas\\Telegram Desktop
    py senales.py "C:\\...\\ChatExport_...\\result.json"
    py senales.py result.json --desde 2026-01-01 --validez 120 --telegram

Lee el JSON que exporta Telegram Desktop (Exportar historial del chat → JSON). Solo LEE ese
archivo: no se conecta a Telegram, no usa claves de cuenta y no opera.

Para cada señal (BUY/SELL, zona de entrada, SL, TP1-TP3):
- velas de 1 minuto reales: oro (XAUUSD) de Dukascopy con precio de compra y de venta (así
  el spread cuenta); BTC de Binance (BTCUSDT);
- empieza a mirar en el minuto SIGUIENTE al mensaje (sin mirar el pasado del mensaje);
- entra si el precio toca el primer precio de la zona antes de `--validez` minutos;
- si en un mismo minuto se tocan el SL y un TP, cuenta el SL (lo prudente);
- prueba 4 formas de gestionarla: todo a TP1, todo a TP2, todo a TP3 y "tercios" (1/3 en cada
  TP y SL a la entrada tras el TP1);
- compara lo que el canal anunció ("TP3 HIT", "SL") con lo que dicen las velas.
Las señales editadas después de publicarse se marcan: el SL o la entrada pudieron cambiar a toro pasado.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import lzma
import math
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

RAIZ = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(RAIZ, "datos", "senales")
UA = "Mozilla/5.0 (bot-trading; solo lectura)"
HORIZONTE_DIAS = 5
GESTIONES = ("TP1", "TP2", "TP3", "tercios")

# símbolo del canal -> (fuente, símbolo en la fuente)
FUENTES = {"XAUUSD": ("dukascopy", "XAUUSD"), "GOLD": ("dukascopy", "XAUUSD"), "XAU": ("dukascopy", "XAUUSD"),
           "ORO": ("dukascopy", "XAUUSD"), "BTCUSD": ("binance", "BTCUSDT"), "BTCUSDT": ("binance", "BTCUSDT"),
           "BTC": ("binance", "BTCUSDT"), "ETHUSD": ("binance", "ETHUSDT"), "ETHUSDT": ("binance", "ETHUSDT")}


# --- leer el export de Telegram -----------------------------------------------------------

def _texto(m: dict) -> str:
    t = m.get("text", "")
    if isinstance(t, list):
        t = "".join(x if isinstance(x, str) else x.get("text", "") for x in t)
    return t or ""


def _num(s: str) -> float:
    s = s.strip().rstrip(".")
    if "," in s and "." not in s:
        s = s.replace(",", "") if len(s.split(",")[-1]) == 3 else s.replace(",", ".")
    return float(s.replace(",", ""))


def _completar(x: float, ref: float) -> float:
    """«SELL XAUUSD AHORA 94 - 96» con SL 5204 significa 5194-5196: completa las cifras que faltan."""
    if x <= 0 or x * 10 > ref:
        return x
    escala = 10 ** len(str(int(x)))
    base = ref - ref % escala
    return min((base - escala + x, base + x, base + escala + x), key=lambda v: abs(v - ref))


def _cuando(m: dict) -> datetime:
    if m.get("date_unixtime"):
        return datetime.fromtimestamp(int(m["date_unixtime"]), tz=timezone.utc)
    # sin unixtime la fecha va en hora local del ordenador que exportó: se trata como Madrid aprox.
    return datetime.fromisoformat(m["date"]).replace(tzinfo=timezone.utc) - timedelta(hours=1)


_NUM = r"(\d[\d.,]*)"
_RE_A = re.compile(r"\b(BUY|SELL)\b(?:\s+(?:LIMIT|STOP|NOW|AHORA))?\s+([A-Z]{3,7})\b(?:\s+(?:LIMIT|STOP|NOW|AHORA))?\s*\(?\s*@?\s*" + _NUM
                   + r"(?:\s*[-–/]\s*" + _NUM + r")?", re.I)
_RE_B = re.compile(r"\b([A-Z]{3,7})\s+(BUY|SELL)\b(?:\s+(?:LIMIT|STOP|NOW|AHORA))?\s*\(?\s*@?\s*" + _NUM
                   + r"(?:\s*[-–/]\s*" + _NUM + r")?", re.I)
_RE_SL = re.compile(r"\bSL\s*[:=]?\s*" + _NUM, re.I)
_RE_TP = re.compile(r"\bTP\s*(\d)\s*[:=]?\s*" + _NUM, re.I)
_RE_TP_HIT = re.compile(r"\bTP\s*(\d)\b[^\n\d]{0,12}?(HIT|✅|alcanzad|tocad|conseguid|done)", re.I)
_RE_SL_HIT = re.compile(r"(^\s*SL\s*(HIT|✅|❌|tocado)?\s*$|\bSL\s+HIT\b|stop\s*loss\s+(hit|tocado))", re.I | re.M)


@dataclass
class Senal:
    id: int
    t: datetime
    simbolo: str
    dir: str                  # "long" | "short"
    entrada: float            # primer precio de la zona
    zona: tuple[float, float]
    sl: float
    tps: list[float]
    texto: str
    editada: bool = False
    canal_tp: int = 0          # mayor TP que el canal dice haber tocado
    canal_sl: bool = False     # el canal dice que saltó el SL
    res: dict = field(default_factory=dict)


def leer_senal(m: dict) -> Senal | None:
    txt = _texto(m)
    a = _RE_A.search(txt)
    if a:
        lado, sim, p1, p2 = a.group(1), a.group(2), a.group(3), a.group(4)
    else:
        b = _RE_B.search(txt)
        if not b:
            return None
        sim, lado, p1, p2 = b.group(1), b.group(2), b.group(3), b.group(4)
    sl = _RE_SL.search(txt)
    tps = {int(n): _num(v) for n, v in _RE_TP.findall(txt)}
    if not sl or not tps:
        return None
    ref = _num(sl.group(1))
    e1, e2 = _completar(_num(p1), ref), _completar(_num(p2), ref) if p2 else None
    s = Senal(int(m.get("id", 0)), _cuando(m), sim.upper(), "long" if lado.upper() == "BUY" else "short",
              e1, (e1, e2 if e2 else e1), ref, [_completar(tps[k], ref) for k in sorted(tps)], txt,
              editada=bool(m.get("edited")))
    d = 1 if s.dir == "long" else -1
    if (s.entrada - s.sl) * d <= 0 or any((tp - s.entrada) * d <= 0 for tp in s.tps):
        return None            # incoherente (SL o TP al otro lado)
    _anotar_aviso(s, txt[sl.end():] if sl else "")
    return s


def _anotar_aviso(s: Senal, txt: str) -> None:
    for n, _ in _RE_TP_HIT.findall(txt):
        s.canal_tp = max(s.canal_tp, int(n))
    if _RE_SL_HIT.search(txt):
        s.canal_sl = True


def leer_export(ruta: str, desde: datetime | None = None) -> list[Senal]:
    with open(ruta, encoding="utf-8") as f:
        js = json.load(f)
    senales: list[Senal] = []
    por_id: dict[int, Senal] = {}
    for m in js.get("messages", []):
        if m.get("type") != "message":
            continue
        s = leer_senal(m)
        if s:
            senales.append(s)
            por_id[s.id] = s
            continue
        txt = _texto(m)
        if not (_RE_TP_HIT.search(txt) or _RE_SL_HIT.search(txt)):
            continue
        objetivo = por_id.get(m.get("reply_to_message_id"))
        if objetivo is None and senales and len(txt) <= 80:
            t = _cuando(m)
            if timedelta(0) <= t - senales[-1].t <= timedelta(hours=48):
                objetivo = senales[-1]
        if objetivo is not None:
            _anotar_aviso(objetivo, txt)
    return [s for s in senales if desde is None or s.t >= desde]


# --- velas de 1 minuto reales ---------------------------------------------------------------

@dataclass
class Minuto:
    t: datetime
    bid: tuple[float, float, float, float]    # o, h, l, c
    ask: tuple[float, float, float, float]


AVISOS: list[str] = []
PAUSA_DUKASCOPY = 3.0           # Dukascopy corta la IP si se le piden muchos archivos seguidos
DIVISORES = {"XAUUSD": 1000.0}  # Dukascopy guarda precios enteros: oro ×1000, divisas ×100000
_ultima: dict[str, float] = {}


class SinDatos(Exception):
    pass


def _get(url: str) -> bytes | None:
    host = url.split("/")[2]
    if "dukascopy" in host:
        espera = _ultima.get(host, 0) + PAUSA_DUKASCOPY - time.time()
        if espera > 0:
            time.sleep(espera)
    for k in range(5):
        _ultima[host] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep((60 if e.code == 429 else 5) * 2 ** k)
        except Exception:  # noqa: BLE001
            time.sleep(5 * 2 ** k)
    raise SinDatos(url)


def decodificar_bi5(raw: bytes, dia: date, divisor: float) -> list[tuple[datetime, float, float, float, float]]:
    """Velas de 1 min de Dukascopy: 24 bytes cada una (segundos, apertura, cierre, mínimo, máximo, volumen)."""
    if not raw:
        return []
    datos = lzma.decompress(raw)
    base = datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc)
    out = []
    for i in range(0, len(datos) - 23, 24):
        seg, o, c, lo, hi, _vol = struct.unpack(">5if", datos[i:i + 24])
        out.append((base + timedelta(seconds=seg), o / divisor, hi / divisor, lo / divisor, c / divisor))
    return out


def _cache(ruta: str, url: str) -> bytes | None:
    if os.path.exists(ruta):
        with open(ruta, "rb") as f:
            return f.read()
    try:
        raw = _get(url)
    except SinDatos:
        AVISOS.append(f"no se pudo bajar {url} (se salta ese día)")
        print(AVISOS[-1])
        return None
    if raw is not None:
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "wb") as f:
            f.write(raw)
    return raw


def minutos_dukascopy(simbolo: str, dia: date, referencia: float, lados_=("BID", "ASK")) -> list[Minuto]:
    lados = {}
    for lado in lados_:
        url = (f"https://datafeed.dukascopy.com/datafeed/{simbolo}/{dia.year}/{dia.month - 1:02d}/"
               f"{dia.day:02d}/{lado}_candles_min_1.bi5")
        raw = _cache(os.path.join(CACHE, "dukascopy", simbolo, f"{dia}_{lado}.bi5"), url)
        if not raw:
            return []
        velas = decodificar_bi5(raw, dia, DIVISORES.get(simbolo, 100000.0))
        if velas and not 0.7 < velas[0][4] / referencia < 1.3:
            raise ValueError(f"{simbolo} {dia}: precio decodificado {velas[0][4]:g} y la señal habla de "
                             f"{referencia:g}. La escala de Dukascopy no cuadra; revisa DIVISORES.")
        lados[lado] = {t: v for t, *v in velas}
    bid = lados["BID"]
    ask = lados.get("ASK", bid)
    return [Minuto(t, tuple(bid[t]), tuple(ask.get(t, bid[t]))) for t in sorted(bid)]


def dias_posibles(s: Senal, dias: int) -> set[date] | None:
    """Filtro rápido con velas DIARIAS (oro: futuros GC=F de Yahoo; cripto: Binance) para no bajar
    cientos de días de 1 minuto. Margen del 1,5 % porque el futuro cotiza algo por encima del contado."""
    fuente = FUENTES.get(s.simbolo)
    try:
        if fuente and fuente[0] == "binance":
            ks = json.loads(_get(f"https://data-api.binance.vision/api/v3/klines?symbol={fuente[1]}"
                                 f"&interval=1d&limit={min(dias + 2, 1000)}") or b"[]")
            filas = [(datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc).date(), float(k[2]), float(k[3]))
                     for k in ks]
        elif fuente and fuente[1] == "XAUUSD":
            import descargar_datos
            js = json.loads(_get("https://query2.finance.yahoo.com/v8/finance/chart/GC%3DF?range=2y&interval=1d")
                            or b"{}")
            filas = [(datetime.fromisoformat(f[0]).date(), float(f[2]), float(f[3]))
                     for f in descargar_datos.yahoo_a_filas(js)]
        else:
            return None
    except Exception as e:  # noqa: BLE001
        print(f"Sin filtro diario ({e}): miro todos los días")
        return None
    margen = 0.015 if fuente[0] == "dukascopy" else 0.002
    ok = set()
    for d, hi, lo in filas:
        if lo * (1 - margen) <= s.entrada <= hi * (1 + margen):
            ok |= {d - timedelta(days=1), d, d + timedelta(days=1)}
    return ok


def minutos_binance(simbolo: str, dia: date) -> list[Minuto]:
    ruta = os.path.join(CACHE, "binance", simbolo, f"{dia}.json")
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            ks = json.load(f)
    else:
        ini = int(datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc).timestamp() * 1000)
        ks = []
        for parte in range(2):
            a = ini + parte * 1000 * 60_000
            b = min(ini + 1440 * 60_000, a + 1000 * 60_000) - 1
            raw = _get(f"https://data-api.binance.vision/api/v3/klines?symbol={simbolo}&interval=1m"
                       f"&startTime={a}&endTime={b}&limit=1000")
            ks += json.loads(raw or b"[]")
        if dia < datetime.now(timezone.utc).date():
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(ks, f)
    out = []
    for k in ks:
        v = (float(k[1]), float(k[2]), float(k[3]), float(k[4]))
        out.append(Minuto(datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc), v, v))
    return out


def minutos_para(s: Senal, dias: int = HORIZONTE_DIAS) -> list[Minuto] | None:
    fuente = FUENTES.get(s.simbolo)
    if fuente is None:
        return None
    hoy = datetime.now(timezone.utc).date()
    out: list[Minuto] = []
    for k in range(dias + 1):
        dia = s.t.date() + timedelta(days=k)
        if dia >= hoy and fuente[0] == "dukascopy":
            break            # Dukascopy publica el día cuando termina
        if dia > hoy:
            break
        out += (minutos_dukascopy(fuente[1], dia, s.entrada) if fuente[0] == "dukascopy"
                else minutos_binance(fuente[1], dia))
    return out


# --- capturas sin fecha: buscar en qué día encajan con las velas reales ---------------------

def _ultimo_domingo(anio: int, mes: int) -> date:
    d = date(anio, mes + 1, 1) - timedelta(days=1) if mes < 12 else date(anio, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def madrid_a_utc(dia: date, hora: str) -> datetime:
    """Hora de Madrid -> UTC (verano: del último domingo de marzo al último de octubre)."""
    hh, mm = (int(x) for x in hora.split(":"))
    verano = _ultimo_domingo(dia.year, 3) <= dia < _ultimo_domingo(dia.year, 10)
    return datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=timezone.utc) - timedelta(hours=2 if verano else 1)


def _encaja(s: Senal, precio: float, tol_r: float) -> bool:
    lo, hi = min(s.zona), max(s.zona)
    margen = tol_r * abs(s.entrada - s.sl)
    return lo - margen <= precio <= hi + margen


def buscar_fechas(s: Senal, hora: str, dias: int = 365, ventana_min: int = 0, tol_r: float = 0.5,
                  minutos_de=None) -> list[datetime]:
    """Días (hacia atrás desde ayer) en los que, a esa hora de Madrid, el precio estaba en la zona de la señal.

    Con `ventana_min` > 0 (la hora que se ve es la de edición), vale cualquier minuto de esa ventana
    anterior y se devuelve el primero que encaja.
    """
    filtro = None
    if minutos_de is None:
        minutos_de = lambda dia: minutos_para_dia(s, dia, ("BID",))  # noqa: E731 - para buscar basta el BID
        filtro = dias_posibles(s, dias)
    hoy = datetime.now(timezone.utc).date()
    out = []
    for k in range(1, dias + 1):
        dia = hoy - timedelta(days=k)
        if filtro is not None and dia not in filtro:
            continue
        t1 = madrid_a_utc(dia, hora)
        t0 = t1 - timedelta(minutes=ventana_min)
        ms = []
        for d in sorted({t0.date(), t1.date()}):
            ms += minutos_de(d)
        for m in ms:
            if t0 <= m.t <= t1 and _encaja(s, m.bid[3], tol_r):
                out.append(m.t)
                break
    return out


def minutos_para_dia(s: Senal, dia: date, lados=("BID", "ASK")) -> list[Minuto]:
    fuente = FUENTES.get(s.simbolo)
    if fuente is None:
        return []
    return (minutos_dukascopy(fuente[1], dia, s.entrada, lados) if fuente[0] == "dukascopy"
            else minutos_binance(fuente[1], dia))


def capturas(ruta: str, dias: int, validez: int) -> str:
    """Evalúa señales copiadas de capturas (sin fecha): busca los días posibles y simula cada uno."""
    with open(ruta, encoding="utf-8") as f:
        lista = json.load(f)
    lin = ["# Señales de las capturas frente a velas reales", "",
           "Las capturas no tienen fecha: busco los días (último año) en los que a esa hora de Madrid el precio "
           "estaba en la zona de entrada, y simulo cada uno con velas de 1 minuto.", ""]
    for n, c in enumerate(lista, 1):
        s = leer_senal({"id": n, "date_unixtime": "0", "text": c["texto"], "edited": c.get("editada")})
        if s is None:
            lin += [f"## {n}. No entiendo la señal", "", c["texto"], ""]
            continue
        _anotar_aviso(s, c.get("canal", ""))
        vent = c.get("ventana_min", 360 if c.get("editada") else 0)
        fechas = buscar_fechas(s, c["hora"], dias, vent)
        lado = "BUY" if s.dir == "long" else "SELL"
        lin += [f"## {n}. {lado} {s.simbolo} {s.zona[0]:g}-{s.zona[1]:g} · SL {s.sl:g} · TP "
                + " / ".join(f"{x:g}" for x in s.tps) + f" · {c['hora']} de Madrid"
                + (" (hora de edición)" if c.get("editada") else ""), ""]
        if not fechas:
            lin += ["Ningún día del último año encaja con esa hora y esa zona.", ""]
            continue
        lin.append(f"Días que encajan: {len(fechas)}" + (" (ambiguo: miro todos)" if len(fechas) > 1 else ""))
        for t in fechas[:6]:
            s.t = t - timedelta(minutes=1) if vent else t
            r = simular(s, minutos_para(s), validez)
            if r.get("estado") != "entró":
                lin.append(f"- {t:%d/%m/%Y %H:%M} UTC: {r.get('estado')}")
                continue
            lin.append(f"- {t:%d/%m/%Y %H:%M} UTC: entra a {r['entrada_real']:g}; velas: "
                       f"{'TP' + str(r['tp_real']) if r['tp_real'] else 'ningún TP'}"
                       f"{' y luego SL' if r['sl_real'] and r['tp_real'] else (' → SL' if r['sl_real'] else '')} · "
                       + " · ".join(f"{g} {r[g]['R']:+.2f} R" for g in GESTIONES)
                       + (f" · el canal dijo {'TP' + str(s.canal_tp) if s.canal_tp else 'SL'}"
                          if s.canal_tp or s.canal_sl else ""))
        lin.append("")
    if AVISOS:
        lin += ["", f"Aviso: {len(AVISOS)} archivos de velas no se pudieron bajar y esos días se saltaron."]
    lin.append("Con 5-6 señales no se puede sacar ninguna conclusión: es solo para ver cómo funciona la comprobación. "
               "La de verdad llega con el export completo del canal.")
    return "\n".join(lin)


# --- simular -------------------------------------------------------------------------------

def simular(s: Senal, minutos: list[Minuto], validez_min: int = 120,
            horizonte: timedelta = timedelta(days=HORIZONTE_DIAS)) -> dict:
    """Recorre las velas de 1 min desde el minuto siguiente al mensaje. Devuelve el resultado de cada gestión."""
    d = 1 if s.dir == "long" else -1
    desde = s.t.replace(second=0, microsecond=0) + timedelta(minutes=1)
    ms = [m for m in minutos if m.t >= desde]
    res = {"estado": "sin datos", "entrada_real": None, "t_entrada": None, "tp_real": 0, "sl_real": False}
    if not ms:
        return res
    # entrada: un largo compra al precio de venta (ask); un corto vende al de compra (bid)
    i0 = None
    for i, m in enumerate(ms):
        if m.t - desde > timedelta(minutes=validez_min):
            break
        if d == 1 and m.ask[2] <= s.entrada:
            i0, px = i, min(s.entrada, m.ask[0])
            break
        if d == -1 and m.bid[1] >= s.entrada:
            i0, px = i, max(s.entrada, m.bid[0])
            break
    if i0 is None:
        res["estado"] = "no entró" if ms[-1].t - desde > timedelta(minutes=validez_min) else "sin datos"
        return res
    res.update(estado="entró", entrada_real=px, t_entrada=ms[i0].t)
    riesgo = abs(px - s.sl)
    tps = (s.tps + [s.tps[-1]] * 3)[:3]
    planes = {"TP1": [(1.0, tps[0])], "TP2": [(1.0, tps[1])], "TP3": [(1.0, tps[2])],
              "tercios": [(1 / 3, tps[0]), (1 / 3, tps[1]), (1 / 3, tps[2])]}
    fin = ms[i0].t + horizonte
    for nombre, partes in planes.items():
        sl, pend, r, salida = s.sl, list(partes), 0.0, "tiempo"
        for k in range(i0, len(ms)):
            m = ms[k]
            cierre_lado = m.bid if d == 1 else m.ask        # para salir se cruza el otro lado
            if m.t > fin:
                ult = ms[k - 1].bid[3] if d == 1 else ms[k - 1].ask[3]
                r += sum(f * (ult - px) * d for f, _ in pend)
                pend = []
                break
            adverso = cierre_lado[2] <= sl if d == 1 else cierre_lado[1] >= sl
            if adverso:
                px_sl = min(sl, cierre_lado[0]) if d == 1 else max(sl, cierre_lado[0])
                r += sum(f * (px_sl - px) * d for f, _ in pend)
                salida = "BE" if sl != s.sl else "SL"
                pend = []
                break
            if k == i0:
                continue                                      # en la vela de entrada no se cuenta el TP
            quedan = []
            for f, tp in pend:
                if (cierre_lado[1] >= tp) if d == 1 else (cierre_lado[2] <= tp):
                    r += f * (tp - px) * d
                    salida = f"TP{tps.index(tp) + 1}"
                    if nombre == "tercios":
                        sl = px                                # tras el primer TP, SL a la entrada
                else:
                    quedan.append((f, tp))
            pend = quedan
            if not pend:
                break
        if pend:      # se acabaron los datos sin cerrar
            salida = "abierta"
            ult = ms[-1].bid[3] if d == 1 else ms[-1].ask[3]
            r += sum(f * (ult - px) * d for f, _ in pend)
        res[nombre] = {"R": r / riesgo if riesgo else 0.0, "dolares": r, "salida": salida}
    # el mayor TP que tocó el precio antes del SL original (para comparar con lo que dice el canal)
    for k in range(i0, len(ms)):
        m = ms[k]
        lado = m.bid if d == 1 else m.ask
        if m.t > fin or ((lado[2] <= s.sl) if d == 1 else (lado[1] >= s.sl)):
            res["sl_real"] = m.t <= fin
            break
        if k > i0:
            for n, tp in enumerate(s.tps, 1):
                if ((lado[1] >= tp) if d == 1 else (lado[2] <= tp)):
                    res["tp_real"] = max(res["tp_real"], n)
        if res["tp_real"] == len(s.tps):
            break
    return res


def estadisticas(rs: list[float]) -> dict:
    if not rs:
        return {"n": 0}
    gan = sum(r for r in rs if r > 0)
    per = -sum(r for r in rs if r < 0)
    pico = acum = dd = 0.0
    for r in rs:
        acum += r
        pico = max(pico, acum)
        dd = max(dd, pico - acum)
    return {"n": len(rs), "acierto": 100 * sum(r > 0 for r in rs) / len(rs), "R_total": sum(rs),
            "R_medio": sum(rs) / len(rs), "pf": gan / per if per else float("inf"), "dd_R": dd}


def veredicto_canal(s: Senal) -> str:
    """Compara lo que anunció el canal con lo que hicieron las velas."""
    r = s.res
    if r.get("estado") != "entró" or (not s.canal_tp and not s.canal_sl):
        return ""
    if s.canal_tp and r["tp_real"] >= s.canal_tp:
        return "cuadra"
    if s.canal_tp and r["tp_real"] < s.canal_tp:
        return f"el canal dice TP{s.canal_tp}, las velas llegan a {'TP' + str(r['tp_real']) if r['tp_real'] else 'ningún TP'}"
    if s.canal_sl and r["sl_real"]:
        return "cuadra"
    return "el canal dice SL, las velas no lo tocan"


# --- informe -------------------------------------------------------------------------------

def informe(senales: list[Senal], origen: str) -> str:
    f = lambda x, d=2: "-" if x is None else ("∞" if x == float("inf") else f"{x:.{d}f}")  # noqa: E731
    entradas = [s for s in senales if s.res.get("estado") == "entró"]
    lin = [f"# Señales del canal frente a velas reales", "",
           f"Archivo: {origen}. {len(senales)} señales leídas, de "
           f"{min(s.t for s in senales):%d/%m/%Y} a {max(s.t for s in senales):%d/%m/%Y}." if senales else "", ""]
    estados: dict[str, int] = {}
    for s in senales:
        estados[s.res.get("estado", "sin datos")] = estados.get(s.res.get("estado", "sin datos"), 0) + 1
    lin.append("Qué pasó con cada señal: " + ", ".join(f"{k} ({v})" for k, v in sorted(estados.items())) + ".")
    lin += ["", "## Si las hubieras seguido todas (velas de 1 minuto, con spread)", "",
            "| Gestión | Operaciones | Acierto | R total | R medio | Profit factor | Peor racha (R) |",
            "|---|---|---|---|---|---|---|"]
    mejores = {}
    for g in GESTIONES:
        st = estadisticas([s.res[g]["R"] for s in entradas if g in s.res])
        mejores[g] = st
        if st["n"]:
            lin.append(f"| {g} | {st['n']} | {st['acierto']:.0f}% | {st['R_total']:+.1f} | {st['R_medio']:+.2f} | "
                       f"{f(st['pf'])} | {st['dd_R']:.1f} |")
    lin += ["", "R = cuántas veces el riesgo (entrada→SL). +1 R = ganaste lo que arriesgabas.", ""]
    por_sim: dict[str, list[Senal]] = {}
    for s in entradas:
        por_sim.setdefault(s.simbolo, []).append(s)
    if len(por_sim) > 1:
        lin += ["## Por activo (gestión «tercios»)", ""]
        for sim, ss in sorted(por_sim.items()):
            st = estadisticas([s.res["tercios"]["R"] for s in ss])
            lin.append(f"- {sim}: {st['n']} operaciones, {st['R_total']:+.1f} R, PF {f(st['pf'])}")
        lin.append("")
    edit = [s for s in entradas if s.editada]
    if edit:
        st_e = estadisticas([s.res["tercios"]["R"] for s in edit])
        st_n = estadisticas([s.res["tercios"]["R"] for s in entradas if not s.editada])
        lin += ["## Señales editadas después de publicarse", "",
                f"{len(edit)} de {len(entradas)} se editaron. Editadas: {st_e['R_total']:+.1f} R; sin editar: "
                f"{st_n.get('R_total', 0):+.1f} R. Si las editadas salen mucho mejor, puede que se retocaran a toro "
                "pasado (o solo se les añadió el «TP HIT»).", ""]
    con_aviso = [s for s in entradas if veredicto_canal(s)]
    if con_aviso:
        cuadran = [s for s in con_aviso if veredicto_canal(s) == "cuadra"]
        lin += ["## ¿Lo que anuncia el canal es verdad?", "",
                f"De {len(con_aviso)} señales con resultado anunciado, {len(cuadran)} cuadran con las velas "
                f"({100 * len(cuadran) / len(con_aviso):.0f}%).", ""]
        for s in [s for s in con_aviso if s not in cuadran][:15]:
            lin.append(f"- {s.t:%d/%m %H:%M} UTC {s.simbolo} {'BUY' if s.dir == 'long' else 'SELL'} "
                       f"{s.entrada:g}: {veredicto_canal(s)}")
        lin.append("")
    ganadora = max((g for g in GESTIONES if mejores[g].get("n")), key=lambda g: mejores[g]["R_total"], default=None)
    lin += ["## Qué haría", ""]
    if not entradas:
        lin.append("No hay ninguna señal con velas para comprobarla.")
    elif len(entradas) < 30:
        lin.append(f"Solo {len(entradas)} operaciones: aún no se puede sacar una conclusión (hacen falta 30 o más).")
    else:
        st = mejores[ganadora]
        if st["pf"] > 1.3 and st["R_total"] > 0:
            lin.append(f"Con {st['n']} operaciones, seguirlas con la gestión «{ganadora}» habría ganado "
                       f"{st['R_total']:+.1f} R (PF {f(st['pf'])}). Tiene pinta de ventaja real, pero mira la peor "
                       f"racha ({st['dd_R']:.1f} R seguidos): con un 1 % por operación serían un {st['dd_R']:.0f} %.")
        else:
            lin.append(f"Con {st['n']} operaciones, ninguna gestión pasa de profit factor 1,3 (la mejor, «{ganadora}», "
                       f"{f(st['pf'])} y {st['R_total']:+.1f} R). Seguir el canal a ciegas no parece rentable.")
    lin += ["", "Fuentes: oro, velas de 1 min de Dukascopy (precio de compra y de venta); BTC, Binance BTCUSDT. "
            "Solo es una comprobación con dinero ficticio."]
    return "\n".join(x for x in lin if x is not None)


def buscar_export() -> str | None:
    casa = os.path.expanduser("~")
    rutas = glob.glob(os.path.join(casa, "Downloads", "Telegram Desktop", "**", "result.json"), recursive=True)
    rutas += glob.glob(os.path.join(casa, "Descargas", "Telegram Desktop", "**", "result.json"), recursive=True)
    return max(rutas, key=os.path.getmtime) if rutas else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", nargs="?", help="result.json de Telegram Desktop")
    ap.add_argument("--desde", help="AAAA-MM-DD: solo señales desde esa fecha")
    ap.add_argument("--validez", type=int, default=120, help="minutos para que el precio llegue a la entrada")
    ap.add_argument("--telegram", nargs="?", const=os.path.join(RAIZ, "telegram.json"),
                    help="manda el resumen por Telegram (telegram.json)")
    ap.add_argument("--capturas", help="JSON con señales copiadas de capturas (sin fecha)")
    ap.add_argument("--dias", type=int, default=365, help="con --capturas: cuántos días hacia atrás buscar")
    a = ap.parse_args(argv)
    if a.capturas:
        texto = capturas(a.capturas, a.dias, a.validez)
        os.makedirs(os.path.join(RAIZ, "salida"), exist_ok=True)
        with open(os.path.join(RAIZ, "salida", "senales_capturas.md"), "w", encoding="utf-8") as fh:
            fh.write(texto)
        print(texto + "\n\nGuardado en salida/senales_capturas.md")
        return 0
    ruta = a.export or buscar_export()
    if not ruta or not os.path.exists(ruta):
        print("No encuentro el result.json. Exporta el canal con Telegram Desktop en formato JSON y pásame la ruta.")
        return 1
    desde = datetime.fromisoformat(a.desde).replace(tzinfo=timezone.utc) if a.desde else None
    senales = leer_export(ruta, desde)
    print(f"{len(senales)} señales en {ruta}")
    for n, s in enumerate(senales, 1):
        ms = minutos_para(s)
        s.res = simular(s, ms, a.validez) if ms is not None else {"estado": f"activo sin velas ({s.simbolo})"}
        if n % 20 == 0:
            print(f"  {n}/{len(senales)}")
    texto = informe(senales, os.path.basename(os.path.dirname(ruta)) or ruta)
    os.makedirs(os.path.join(RAIZ, "salida"), exist_ok=True)
    with open(os.path.join(RAIZ, "salida", "senales_informe.md"), "w", encoding="utf-8") as fh:
        fh.write(texto)
    with open(os.path.join(RAIZ, "salida", "senales.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["fecha_utc", "simbolo", "lado", "entrada", "sl", "tps", "editada", "estado", "entrada_real",
                    *(f"R_{g}" for g in GESTIONES), "canal_dice", "velas_dicen", "veredicto"])
        for s in senales:
            r = s.res
            w.writerow([f"{s.t:%Y-%m-%d %H:%M}", s.simbolo, "BUY" if s.dir == "long" else "SELL", s.entrada, s.sl,
                        "/".join(f"{x:g}" for x in s.tps), "sí" if s.editada else "", r.get("estado"),
                        r.get("entrada_real") or "",
                        *(f"{r[g]['R']:.2f}" if g in r else "" for g in GESTIONES),
                        f"TP{s.canal_tp}" if s.canal_tp else ("SL" if s.canal_sl else ""),
                        (f"TP{r['tp_real']}" if r.get("tp_real") else ("SL" if r.get("sl_real") else ""))
                        if r.get("estado") == "entró" else "", veredicto_canal(s)])
    print(texto)
    print("\nGuardado en salida/senales_informe.md y salida/senales.csv")
    if a.telegram:
        from consejo.telegram import Grupo
        Grupo(a.telegram).enviar("risk", texto[:3900])
    return 0


if __name__ == "__main__":
    sys.exit(main())
