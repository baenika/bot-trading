"""News: busca por qué se mueve el precio, con noticias reales.

Idea sacada de trader-news de day-trader-skill:
- marca MOVING cuando el precio se ha movido un 3 % o más desde el cierre de ayer;
- etiqueta los titulares (resultados, analistas, legal, macro, hackeo...);
- no predice nada: solo dice si el movimiento tiene una noticia detrás.

En el backtest usa el archivo histórico de GDELT de datos/noticias/ (lo baja
descargar_noticias.py). Solo mira titulares publicados ANTES de la decisión,
nunca después, para no hacer trampa. Si no hay archivo para esa fecha, no opina.
"""

from __future__ import annotations

import bisect
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

MOVING_PCT = 3.0
VENTANA_HORAS = 24

# etiqueta -> palabras clave (inglés y español, en minúsculas)
ETIQUETAS = {
    "resultados": ("earnings", "quarterly results", "revenue", "guidance", "resultados", "beneficio"),
    "analistas": ("upgrade", "downgrade", "price target", "analyst", "analista", "recomendación"),
    "legal": ("lawsuit", " sec ", "sues", " court ", "regulator", "investigation", "probe", "demanda",
              "regulación", "multa", " fine "),
    "acuerdo": ("acquisition", "merger", " deal", "partnership", "acquire", "compra de", "fusión"),
    "macro": (" fed ", "federal reserve", "inflation", "cpi", "interest rate", "rate cut", "rate hike",
              "jobs report", "payrolls", "gdp", "tariff", "ecb", "bce", "inflación", "tipos de interés",
              "aranceles", "recession", "recesión"),
    "etf": ("etf inflow", "etf outflow", "spot etf", "etf approval", "etf"),
    "hackeo": ("hack", "exploit", "stolen", "breach", "hackeo", " robo "),
    "geopolítica": (" war ", "missile", "sanction", "invasion", "guerra", "sanciones", "conflict"),
}

# eventos tan graves que, si salen en las últimas 24 h, el Skeptic no compra
GRAVES = ("hack", "exploit", "bankruptcy", "insolvency", "fraud charges", "sec charges", "delisting",
          "quiebra", "fraude", "halted", "suspends withdrawals")

POSITIVAS = ("surge", "soar", "rally", "jump", "record high", "all-time high", "gains", "rises", "climbs",
             "beats", "approval", "approved", "inflow", "bullish", "sube", "dispara", "máximos", "récord")
NEGATIVAS = ("plunge", "crash", "tumble", "slump", "falls", "drops", "sell-off", "selloff", "fear",
             "misses", "outflow", "bearish", "liquidation", " cae ", "desplome", "hundimiento", "pánico")


def _fecha(txt: str) -> datetime:
    """Acepta '20240115T103000Z' (GDELT) o ISO."""
    txt = txt.strip()
    if len(txt) >= 15 and txt[8] == "T" and txt[:8].isdigit():
        return datetime.strptime(txt[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    d = datetime.fromisoformat(txt.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def etiquetar(titulo: str) -> list[str]:
    t = f" {titulo.lower()} "
    return [e for e, palabras in ETIQUETAS.items() if any(p in t for p in palabras)]


def tono_titular(titulo: str) -> int:
    t = f" {titulo.lower()} "
    return sum(p in t for p in POSITIVAS) - sum(p in t for p in NEGATIVAS)


def es_grave(titulo: str) -> bool:
    t = f" {titulo.lower()} "
    return any(p in t for p in GRAVES)


@dataclass
class Archivo:
    """Noticias históricas de un símbolo, ordenadas por fecha."""
    simbolo: str
    titulares: list[dict] = field(default_factory=list)     # {"t": datetime, "titulo", "dominio"}
    dias: dict[str, dict] = field(default_factory=dict)      # "AAAA-MM-DD" -> {"tono", "volumen"}
    cubre: list[tuple[datetime, datetime]] = field(default_factory=list)

    def __post_init__(self):
        self.titulares.sort(key=lambda x: x["t"])
        self._ts = [x["t"] for x in self.titulares]

    @classmethod
    def desde_json(cls, d: dict) -> "Archivo":
        tit = [{"t": _fecha(x["t"]), "titulo": x.get("titulo", ""), "dominio": x.get("dominio", "")}
               for x in d.get("titulares", []) if x.get("t")]
        cubre = [(_fecha(a), _fecha(b)) for a, b in d.get("cubre", [])]
        return cls(d.get("simbolo", ""), tit, d.get("dias", {}), cubre)

    @classmethod
    def cargar(cls, ruta: str) -> "Archivo | None":
        if not os.path.exists(ruta):
            return None
        with open(ruta, encoding="utf-8") as f:
            return cls.desde_json(json.load(f))

    def cubre_fecha(self, t: datetime) -> bool:
        return any(a <= t <= b for a, b in self.cubre)

    def antes_de(self, t: datetime, horas: int = VENTANA_HORAS) -> list[dict]:
        """Titulares publicados en las `horas` anteriores a t (nunca después)."""
        a = bisect.bisect_left(self._ts, t - timedelta(hours=horas))
        b = bisect.bisect_left(self._ts, t)          # estrictamente antes de t
        return self.titulares[a:b]

    def dias_previos(self, t: datetime, n: int = 30) -> list[tuple[str, dict]]:
        """Datos diarios (tono, volumen) de días YA terminados antes de t."""
        hoy = t.strftime("%Y-%m-%d")
        previos = sorted(k for k in self.dias if k < hoy)
        return [(k, self.dias[k]) for k in previos[-n:]]


def cambio_vs_ayer(ctx, i: int) -> float | None:
    """% de cambio del cierre de la vela i frente al último cierre del día anterior."""
    vs = ctx.velas
    dia = vs[i].t.date()
    k = i
    while k >= 0 and vs[k].t.date() == dia:
        k -= 1
    if k < 0:
        return None
    return (vs[i].c / vs[k].c - 1) * 100


def opinar(alerta, ctx=None, archivo: Archivo | None = None, cierre: datetime | None = None,
           cfg: dict | None = None) -> dict:
    """Devuelve {"veto", "pro", "contra", "moving", "etiquetas", "texto", ...} para el Skeptic.

    `cierre` es el momento de la decisión (cierre de la vela). Solo se usan
    titulares con fecha anterior a ese momento.
    """
    cfg = cfg or {}
    res = {"veto": None, "pro": None, "contra": None, "moving": False, "etiquetas": [],
           "titulares": [], "tono_ayer": None, "volumen_rel": None}
    cambio = cambio_vs_ayer(ctx, alerta.i) if ctx is not None else None
    umbral = cfg.get("moving_pct", MOVING_PCT)
    res["moving"] = cambio is not None and abs(cambio) >= umbral
    cab = (f"🚨 MOVING {cambio:+.1f}% desde el cierre de ayer" if res["moving"]
           else f"Cambio desde el cierre de ayer: {cambio:+.1f}%" if cambio is not None else "")

    if archivo is None or cierre is None:
        res["texto"] = "\n".join(x for x in (cab, "Sin archivo de noticias para este símbolo: no opino.") if x)
        return res
    if not archivo.cubre_fecha(cierre):
        res["texto"] = "\n".join(x for x in (cab, "El archivo de noticias no cubre esta fecha: no opino.") if x)
        return res

    tit = archivo.antes_de(cierre, cfg.get("ventana_noticias_h", VENTANA_HORAS))
    res["titulares"] = [x["titulo"] for x in tit[-5:]]
    etiquetas = sorted({e for x in tit for e in etiquetar(x["titulo"])})
    res["etiquetas"] = etiquetas
    tono = sum(tono_titular(x["titulo"]) for x in tit)
    graves = [x["titulo"] for x in tit if es_grave(x["titulo"])]

    previos = archivo.dias_previos(cierre, 30)
    if previos:
        ayer = previos[-1][1]
        res["tono_ayer"] = ayer.get("tono")
        vols = [d.get("volumen", 0) for _, d in previos[:-1] if d.get("volumen")]
        if vols and ayer.get("volumen"):
            res["volumen_rel"] = ayer["volumen"] / (sum(vols) / len(vols))

    lado = 1 if alerta.dir == "long" else -1
    if graves and lado == 1:
        res["veto"] = f"noticia grave en las últimas 24 h: «{graves[-1][:90]}»"
    elif res["moving"] or alerta.fuerza >= 70:
        if not tit:
            res["contra"] = "el precio se mueve fuerte pero no hay ninguna noticia detrás: puede ser ruido"
        elif tono * lado > 0:
            res["pro"] = f"hay noticias detrás y van a favor ({', '.join(etiquetas) or 'sin etiqueta'})"
        elif tono * lado < 0:
            res["contra"] = "las noticias de las últimas 24 h van en contra del movimiento"

    lineas = [cab] if cab else []
    if tit:
        lineas.append(f"📰 {len(tit)} titulares en las últimas {cfg.get('ventana_noticias_h', VENTANA_HORAS)} h"
                      + (f" · etiquetas: {', '.join(etiquetas)}" if etiquetas else ""))
        lineas += [f"• {x['titulo'][:110]}" for x in tit[-3:]]
    else:
        lineas.append("📰 Ningún titular en las últimas 24 h.")
    if res["tono_ayer"] is not None:
        lineas.append(f"Tono medio de ayer en los medios: {res['tono_ayer']:+.1f}"
                      + (f" · volumen de noticias x{res['volumen_rel']:.1f} sobre lo normal"
                         if res["volumen_rel"] else ""))
    for clave, icono in (("veto", "⛔"), ("pro", "➕"), ("contra", "➖")):
        if res[clave]:
            lineas.append(f"{icono} {res[clave]}")
    res["texto"] = "\n".join(lineas)
    return res
