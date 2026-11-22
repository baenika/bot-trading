"""Modo PAPEL en vivo: el Consejo vigila el mercado de verdad con dinero ficticio.

    py papel.py --mesa cripto              # se queda vigilando (mira cada 5 minutos)
    py papel.py --mesa mercados --una-vez  # una sola pasada (para el Programador de tareas)
    py papel.py --prueba-telegram          # cada bot saluda en el grupo
    py papel.py --mesa mercados --sin-mando  # segunda mesa a la vez: avisa, pero no lee comandos

- Precios: Binance (cripto) y Yahoo (mercados), públicos y sin cuenta.
- Noticias: Hacker News (y GDELT si responde), las de las últimas 24 h, solo cuando el Scout da una alerta.
- Opera solo con velas CERRADAS y guarda todo en salida/papel_<mesa>.json.
- El panel lo enseña en http://localhost:8000/?fuente=papel
- Los 6 bots hablan en tu grupo de Telegram si existe telegram.json (ver consejo/telegram.py).

NO HAY DINERO REAL: no hay broker, ni claves, ni órdenes. Todo es una simulación.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from consejo import analisis, backtest, datos, mesas, seguridad
from consejo.agentes import news
from consejo.mando import Mando
from consejo.telegram import NOMBRES, Grupo

RAIZ = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; bot-trading/0.1)"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def velas_binance(simbolo: str, intervalo: str = "1h", n: int = 1000) -> list[datos.Vela]:
    js = json.loads(_get(f"https://data-api.binance.vision/api/v3/klines?symbol={simbolo}"
                         f"&interval={intervalo}&limit={n}"))
    return [datos.Vela(datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc), float(k[1]), float(k[2]),
                       float(k[3]), float(k[4]), float(k[5])) for k in js]


def velas_yahoo(simbolo: str, intervalo: str = "1h") -> list[datos.Vela]:
    import descargar_datos
    js = json.loads(_get(f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(simbolo)}"
                         f"?range=180d&interval={intervalo}"))
    return [datos.Vela(datetime.fromisoformat(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[4]),
                       float(f[5] or 0)) for f in descargar_datos.yahoo_a_filas(js)]


def solo_cerradas(vs: list[datos.Vela], duracion: timedelta, ahora: datetime) -> list[datos.Vela]:
    """Quita la vela que todavía se está formando."""
    return [v for v in vs if v.t + duracion <= ahora]


class NoticiasEnVivo(dict):
    """Baja los titulares de las últimas 24 h solo cuando News los pide (CoinDesk y Cointelegraph para cripto,
    Hacker News; GDELT si no hay nada)."""

    def __init__(self, ahora: datetime):
        super().__init__()
        self.ahora = ahora

    def _hn(self, s: str) -> list[dict] | None:
        import descargar_noticias as dn
        if s not in dn.HN_CONSULTAS:
            return None
        filas, desde = [], int((self.ahora - timedelta(days=1)).timestamp())
        try:
            for q in dn.HN_CONSULTAS[s]:
                url = dn.HN_API + "?" + urllib.parse.urlencode({
                    "query": f'"{q}"', "tags": "story", "hitsPerPage": 200, "typoTolerance": "false",
                    "numericFilters": f"created_at_i>={desde}"})
                filas += dn.hn_a_filas(json.loads(_get(url)), [q], min_puntos=3)
        except Exception as e:  # noqa: BLE001 - sin noticias, News no opina
            print(f"{s}: sin noticias de Hacker News ({e})")
            return None
        return filas

    def _rss(self, s: str) -> list[dict] | None:
        """CoinDesk y Cointelegraph: mejores fuentes de cripto que Hacker News. Se guardan en
        datos/noticias/rss/ para que, con el tiempo, el backtest también pueda usarlas."""
        import descargar_noticias as dn
        if s not in dn.RSS_TERMINOS:
            return None
        filas, desde = [], self.ahora - timedelta(days=1)
        for dominio, url in dn.RSS_CRIPTO.items():
            try:
                filas += dn.rss_a_filas(_get(url).decode("utf-8", "replace"), dominio, dn.RSS_TERMINOS[s])
            except Exception as e:  # noqa: BLE001
                print(f"{s}: sin RSS de {dominio} ({e})")
        if not filas:
            return None
        ruta = os.path.join(RAIZ, "datos", "noticias", "rss", f"{s}.jsonl")
        try:
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            vistos = set()
            if os.path.exists(ruta):
                with open(ruta, encoding="utf-8") as f:
                    vistos = {json.loads(x)["titulo"] for x in f if x.strip()}
            with open(ruta, "a", encoding="utf-8") as f:
                for x in filas:
                    if x["titulo"] not in vistos:
                        f.write(json.dumps(x, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return [x for x in filas if desde <= datetime.fromisoformat(x["t"]) < self.ahora]

    def get(self, s, defecto=None):
        if s not in self:
            rss, hn = self._rss(s), self._hn(s)
            filas = None if rss is None and hn is None else (rss or []) + (hn or [])
            if filas is None and s in mesas.NOTICIAS:
                import descargar_noticias as dn
                js = dn._pedir({"query": dn._consulta(s), "mode": "artlist", "format": "json",
                                "maxrecords": 100, "timespan": "1d"}, reintentos=1)
                filas = dn.titulares_a_filas(js) if js else None
            self[s] = news.Archivo.desde_json({
                "simbolo": s, "titulares": filas or [], "dias": {},
                "cubre": [[(self.ahora - timedelta(days=1)).isoformat(), self.ahora.isoformat()]]
                if filas is not None else []})
        return super().get(s, defecto)


def mensajes_de_decision(m: backtest.Motor) -> list[tuple[str, str]]:
    sim = m.ultima["symbol"]
    out = [(b, f"{sim} · {m.bots[k]['text']}") for b, k in
           (("scout", "scout"), ("news", "news"), ("chart", "chart"), ("sentimental", "sentimental"),
            ("skeptic", "skeptic")) if k in m.bots]
    return out


def texto_opcion(u: dict, mesa: str) -> str:
    """Aviso corto de «opción de compra» cuando el Consejo dice COMPRAR o ESPERAR un retroceso."""
    nombre = analisis.nombre(u["symbol"])
    sin_ventaja = "\n(Mesa de mercados: en el backtest no tiene ventaja; es para comprobar la configuración.)" \
        if mesa == "mercados" else ""
    if u.get("decision") == "COMPRAR":
        return (f"🟢 OPCIÓN DE COMPRA · {nombre}\nEntrada {u['entry']:.6g} · stop {u['sl']:.6g} · objetivo "
                f"{u['tp']:.6g} · puntos {u.get('score', 0):+.0f}\nEl papel entra solo; tú no tienes que hacer "
                f"nada (dinero ficticio).{sin_ventaja}")
    if u.get("decision") == "ESPERAR" and u.get("wait_price"):
        return (f"🟡 POSIBLE COMPRA · {nombre}: el Consejo espera un retroceso a {u['wait_price']:.6g} "
                f"(stop {u['sl']:.6g}, objetivo {u['tp']:.6g}). Si no baja, no entra.{sin_ventaja}")
    return ""


def bajar_velas(s: str, timeframe: str = "1h") -> list[datos.Vela]:
    """Velas cerradas de cualquier activo de las dos mesas (para /analizar)."""
    vs = velas_binance(s, timeframe) if s in mesas.CRIPTO else velas_yahoo(s, timeframe)
    return solo_cerradas(vs, backtest._duracion(timeframe), datetime.now(timezone.utc))


def pasada(mesa: str, cfg: dict, grupo: Grupo, ruta_estado: str, avisos=None) -> None:
    ahora = datetime.now(timezone.utc)
    dur = backtest._duracion(cfg["timeframe"])
    lista = mesas.CRIPTO if mesa == "cripto" else mesas.MERCADOS
    series = {}
    for s in lista:
        try:
            vs = velas_binance(s, cfg["timeframe"]) if mesa == "cripto" else velas_yahoo(s, cfg["timeframe"])
            vs = solo_cerradas(vs, dur, ahora)
            if len(vs) > 250:
                series[s] = vs
        except Exception as e:  # noqa: BLE001 - un símbolo caído no para el resto
            print(f"{s}: sin datos ({e})")
            if avisos:
                avisos.avisar("datos", s, f"No llegan las velas de {s} ({type(e).__name__}). Lo reintento cada pasada.")
    if not series:
        print("Sin datos de ningún símbolo; lo intento en la próxima pasada.")
        return
    for s, vs in series.items():
        if avisos and ahora - vs[-1].t > dur * 3 + timedelta(hours=1) and mesa == "cripto":
            avisos.avisar("datos", f"{s}:viejas", f"Las últimas velas de {s} son de {vs[-1].t:%d/%m %H:%M} UTC: "
                                                  "los datos llegan con retraso.")

    m = backtest.Motor(series, cfg, noticias=NoticiasEnVivo(ahora))
    estado = {}
    if os.path.exists(ruta_estado):
        with open(ruta_estado, encoding="utf-8") as f:
            estado = json.load(f)
        m.restaurar(estado)

    inicio = estado.get("inicio", ahora.isoformat())
    eventos = []
    for s, vs in series.items():
        desde = m.ultimo_i.get(s)
        if desde is None or desde < 0:   # primera vez (o mucho tiempo apagado): no se opera con velas viejas
            m.ultimo_i[s] = len(vs) - 1
            print(f"{s}: empiezo a vigilar desde {vs[-1].t:%Y-%m-%d %H:%M}")
            continue
        m.precalentar(s, desde)
        for i in range(max(desde + 1, 0), len(vs)):
            eventos.append((vs[i].t, s, i))

    for t, s, i in sorted(eventos):
        antes_ops, antes_abiertas, antes_ultima = len(m.operaciones), set(m.abiertas), m.ultima
        m.paso(s, i, ultima_vela=True)
        if m.ultima is not antes_ultima and m.ultima and m.ultima.get("symbol") == s:
            aviso = texto_opcion(m.ultima, mesa)
            if aviso:
                grupo.enviar("risk", aviso)          # llega aunque los debates estén en silencio
            grupo.conversacion(mensajes_de_decision(m))
        for p in [m.abiertas[x] for x in set(m.abiertas) - antes_abiertas]:
            grupo.enviar("risk", f"📥 Entrada {p['symbol']} a {p['entry']:.4g} · stop {p['stop']:.4g} · "
                                 f"objetivo {p['tp']:.4g} (dinero ficticio)")
        for o in m.operaciones[antes_ops:]:
            grupo.enviar("risk", f"{'🟢' if o['pnl'] > 0 else '🔴'} Cerrado {o['symbol']} por {o['reason']}: "
                                 f"{o['pnl']:+.2f} ({o['R']:+.2f}R) · capital ficticio {m.capital:,.2f}")
        punto = (backtest._fmt(t), round(m.capital, 2))
        if m.curva and m.curva[-1][0] == punto[0]:
            m.curva[-1] = punto          # un punto por hora, no uno por símbolo
        else:
            m.curva.append(punto)

    if avisos and m.riesgo.parado and not estado.get("parado"):
        avisos.avisar("parada", "drawdown", f"El bot se ha PARADO: el capital ficticio cayó más de un "
                                            f"{m.riesgo.drawdown_max_pct:.0f}% desde su máximo. No abrirá nada "
                                            "hasta que lo revisemos juntos.")
    os.makedirs(os.path.dirname(ruta_estado), exist_ok=True)
    with open(ruta_estado, "w", encoding="utf-8") as f:
        json.dump({**m.exportar(), "inicio": inicio}, f, ensure_ascii=False, default=str)
    panel = backtest.estado_panel(m, series, "live", f"papel desde {inicio[:16].replace('T', ' ')}")
    panel["desk"] = mesa
    # comprar y mantener desde que arrancó el papel, no desde el principio de las velas bajadas
    t0 = datetime.fromisoformat(inicio)
    desde = {k: [v for v in vs if v.t >= t0] for k, vs in series.items()}
    bh = backtest._comprar_y_mantener({k: v for k, v in desde.items() if len(v) > 1})
    panel["skeptic_saved"]["buy_hold_pct"] = bh
    panel["exam"] = backtest.examen(m.estadisticas(), bh)
    with open(os.path.join(RAIZ, "salida", f"estado_papel_{mesa}.json"), "w", encoding="utf-8") as f:
        json.dump(panel, f, ensure_ascii=False, default=str)
    print(f"{ahora:%H:%M} · {len(eventos)} velas nuevas · capital ficticio {m.capital:,.2f} · "
          f"{len(m.abiertas)} abiertas")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mesa", choices=["cripto", "mercados"], default="cripto")
    ap.add_argument("--una-vez", action="store_true")
    ap.add_argument("--cada", type=int, default=300, help="segundos entre pasadas")
    ap.add_argument("--telegram", default=os.path.join(RAIZ, "telegram.json"))
    ap.add_argument("--prueba-telegram", action="store_true")
    ap.add_argument("--sin-mando", action="store_true",
                    help="no leer comandos de Telegram (solo una copia del bot puede leerlos a la vez)")
    ap.add_argument("--puerto-panel", type=int, default=8000,
                    help="puerto del panel abierto con `dashboard_server.py --red` (0 = no dar enlace)")
    a = ap.parse_args(argv)

    import aprender
    auto = aprender.cargar_auto(a.mesa)          # lo que aceptó el autoajuste semanal (si hay)
    cfg = {**backtest.CONFIG_DEFECTO, **mesas.AJUSTES[a.mesa], **auto, "modo": "paper", "caminos_ultima": 3000}
    if auto:
        print(f"Ajustes automáticos aceptados: {auto}")
    seguridad.comprobar_modo(cfg["modo"])
    seguridad.comprobar_config(cfg)
    grupo = Grupo(a.telegram)
    if a.prueba_telegram:
        for bot, nombre in NOMBRES.items():
            grupo.enviar(bot, f"{nombre} listo. Modo papel: dinero ficticio, sin órdenes reales.")
        return 0

    ruta = os.path.join(RAIZ, "salida", f"papel_{a.mesa}.json")
    import dashboard_server
    url_panel = f"http://{dashboard_server.ip_local()}:{a.puerto_panel}/?fuente=papel" if a.puerto_panel else ""
    def analista(texto: str) -> str:
        estado = {}
        if os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as f:
                estado = json.load(f)
        return analisis.analizar(texto, cfg, lambda s: bajar_velas(s, cfg["timeframe"]), estado)

    # /panel, /analizar, /cartera, /parametros, /avisos, /vincular desde Telegram
    mando = Mando(grupo, cfg, ruta, url_panel=url_panel, analista=analista)
    if a.sin_mando:
        mando.activo = False
    from consejo.avisos import Avisador
    avisos = Avisador(grupo, f"papel {a.mesa}")
    if grupo.activo("risk") and not a.una_vez:
        grupo.enviar("risk", f"🏛️ Consejo {a.mesa} en marcha (papel)." +
                     (" Escribe /panel para ver los botones." if mando.activo else ""))
        avisos.version()
        if auto:
            avisos.avisar("cambio", json.dumps(auto, sort_keys=True), f"Uso los ajustes aceptados por el autoajuste: {auto}")
    while True:
        try:
            pasada(a.mesa, cfg, grupo, ruta, avisos)
        except Exception as e:  # noqa: BLE001 - un fallo no tumba el bot: avisa y lo reintenta
            print(f"Error en la pasada: {e!r}")
            avisos.error(e, "la pasada del Consejo")
        if a.una_vez:
            return 0
        mando.esperar(a.cada)


if __name__ == "__main__":
    sys.exit(main())
