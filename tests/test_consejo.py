import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from consejo import backtest, datos, maquina_tiempo, seguridad  # noqa: E402
from consejo.agentes import charts, scout  # noqa: E402
from consejo.agentes.risk import GestorRiesgo  # noqa: E402
from consejo.indicadores import Contexto  # noqa: E402


# --- seguridad: nada de dinero real ----------------------------------------------

@pytest.mark.parametrize("modo", ["live", "real", "LIVE", "", "produccion"])
def test_modos_reales_bloqueados(modo):
    with pytest.raises(seguridad.ModoNoPermitido):
        seguridad.comprobar_modo(modo)


@pytest.mark.parametrize("modo", ["paper", "backtest", "Paper"])
def test_modos_simulados_permitidos(modo):
    assert seguridad.comprobar_modo(modo) in seguridad.MODOS_PERMITIDOS


def test_config_con_clave_bloqueada():
    with pytest.raises(seguridad.ModoNoPermitido):
        seguridad.comprobar_config({"modo": "paper", "binance_api_key": "x"})


def test_motor_no_arranca_en_real():
    series = {"BTC": datos.sinteticas("BTC", 300, semilla=1)}
    with pytest.raises(seguridad.ModoNoPermitido):
        backtest.Motor(series, {"modo": "live"})


def test_no_hay_codigo_de_brokers():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prohibido = ("ccxt", "alpaca", "binance.client", "solana.keypair", "place_order", "create_order")
    for carpeta, _, archivos in os.walk(os.path.join(raiz, "consejo")):
        for a in archivos:
            if a.endswith(".py"):
                texto = open(os.path.join(carpeta, a), encoding="utf-8").read().lower()
                for p in prohibido:
                    assert p not in texto, f"{a} contiene '{p}'"


# --- datos -------------------------------------------------------------------------

def test_sinteticas_coherentes():
    vs = datos.sinteticas("X", 500, semilla=3)
    assert len(vs) == 500
    for v in vs:
        assert v.l <= min(v.o, v.c) <= max(v.o, v.c) <= v.h and v.v > 0
    assert all(b.t - a.t == timedelta(hours=1) for a, b in zip(vs, vs[1:]))


def test_csv_formato_binance(tmp_path):
    p = tmp_path / "BTCUSDT-1h.csv"
    p.write_text("1704070800000,42283.58,42554.57,42261.02,42475.23,1271.68,1704074399999,0,0,0,0,0\n"
                 "1704067200000,42314.00,42370.00,42200.00,42283.58,900.1,1704070799999,0,0,0,0,0\n")
    vs = datos.cargar_csv(str(p))
    assert [v.c for v in vs] == [42283.58, 42475.23]        # ordenadas por fecha
    assert vs[0].t == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_csv_con_cabecera(tmp_path):
    p = tmp_path / "eth.csv"
    p.write_text("date,open,high,low,close,volume\n2024-01-01T00:00:00Z,1,2,0.5,1.5,10\n")
    v = datos.cargar_csv(str(p))[0]
    assert (v.o, v.h, v.l, v.c, v.v) == (1, 2, 0.5, 1.5, 10)


# --- agentes -----------------------------------------------------------------------

def _serie_con_ruptura():
    vs = datos.sinteticas("X", 300, semilla=5)
    t, ultimo = vs[-1].t, vs[-1].c
    o = ultimo
    c = o * 1.06
    vs.append(datos.Vela(t + timedelta(hours=1), o, c * 1.002, o * 0.999, c, vs[-1].v * 40))
    return vs


def test_scout_detecta_ruptura():
    vs = _serie_con_ruptura()
    ctx = Contexto("X", vs)
    a = scout.escanear(ctx, len(vs) - 1, backtest.CONFIG_DEFECTO)
    assert a is not None and a.dir == "long" and a.fuerza > 50


def test_plan_long_coherente():
    vs = _serie_con_ruptura()
    ctx = Contexto("X", vs)
    a = scout.escanear(ctx, len(vs) - 1, backtest.CONFIG_DEFECTO)
    p = charts.planificar(ctx, a, backtest.CONFIG_DEFECTO)
    assert p.stop < p.precio_espera < p.entrada < p.objetivo
    assert p.rr == pytest.approx(2.0)


def test_maquina_tiempo_suma_100():
    rend = [0.001 * ((i % 7) - 3) for i in range(500)]
    s = maquina_tiempo.simular(rend, 100, 98, 104, "long", caminos=2000, semilla=1)
    assert s["p_tp"] + s["p_sl"] + s["p_none"] == pytest.approx(100, abs=0.2)
    assert s["breakeven"] == pytest.approx(33.3, abs=0.1)


def test_riesgo_tamano_y_limites():
    g = GestorRiesgo({"risk_per_trade_pct": 1, "max_open_positions": 2, "daily_loss_limit_pct": 3})
    plan = charts.Plan("long", 100, 95, 110, 98.75, 2, 2, None)
    r = g.aprobar(10000, 10000, 0, plan, False)
    assert r["ok"] and r["arriesgo"] == pytest.approx(100)
    assert not g.aprobar(10000, 10000, 2, plan, False)["ok"]          # demasiadas posiciones
    assert not g.aprobar(9600, 10000, 0, plan, False)["ok"]           # pérdida diaria > 3 %
    g.revisar_cuenta(8000, 10000)
    assert g.parado and not g.aprobar(8000, 8000, 0, plan, False)["ok"]


# --- backtest completo ------------------------------------------------------------

def test_backtest_produce_json_del_panel():
    series = {"BTCUSDT": datos.sinteticas("BTCUSDT", 1500, 30000, semilla=1),
              "ETHUSDT": datos.sinteticas("ETHUSDT", 1500, 2000, semilla=2)}
    d = backtest.ejecutar(series, {"caminos_backtest": 200, "caminos_ultima": 200})
    for k in ("updated", "source", "mode", "symbols", "equity", "curve", "trades", "stats", "limits",
              "bots", "decisions", "skeptic_saved", "compare_stats", "daily_pnl"):
        assert k in d
    assert d["mode"] == "paper"
    json.dumps(d, default=str)
    for o in d["trades"]:
        assert o["reason"] in ("stop loss", "take profit", "tiempo máximo", "corte de drawdown")
    # nunca arriesga más de lo permitido: ninguna pérdida mucho mayor que 1R
    assert all(o["R"] > -1.6 for o in d["trades"])


def test_sin_cortos_por_defecto():
    series = {"BTCUSDT": datos.sinteticas("BTCUSDT", 2000, 30000, semilla=7)}
    d = backtest.ejecutar(series, {"caminos_backtest": 100, "caminos_ultima": 100})
    assert all(o["dir"] == "long" for o in d["trades"])


# --- mesa de mercados ---------------------------------------------------------

def test_nombres_de_archivo_ida_y_vuelta():
    from consejo import mesas
    for s in list(mesas.MERCADOS) + list(mesas.CRIPTO):
        nombre = mesas.archivo(s)
        assert "^" not in nombre and "=" not in nombre
        assert mesas.simbolo_de_archivo("datos/" + nombre) == s


def test_yahoo_a_filas_salta_huecos():
    import descargar_datos
    js = {"chart": {"result": [{"timestamp": [1704067200, 1704070800, 1704074400],
                                "indicators": {"quote": [{"open": [1, None, 3], "high": [2, None, 4],
                                                          "low": [0.5, None, 2.5], "close": [1.5, None, 3.5],
                                                          "volume": [10, None, None]}]}}]}}
    filas = descargar_datos.yahoo_a_filas(js)
    assert len(filas) == 2 and filas[1][5] == 0
    assert filas[0][0].startswith("2024-01-01T00:00")


def test_run_backtest_mesa_mercados(tmp_path):
    import run_backtest
    salida = tmp_path / "m.json"
    assert run_backtest.main(["--mesa", "mercados", "--sinteticos", "--velas", "800",
                              "--salida", str(salida)]) == 0
    d = json.loads(salida.read_text(encoding="utf-8"))
    assert d["desk"] == "mercados" and d["mode"] == "paper"


def test_corte_de_drawdown_cierra_todo():
    series = {"BTCUSDT": datos.sinteticas("BTCUSDT", 3000, 30000, semilla=4),
              "ETHUSDT": datos.sinteticas("ETHUSDT", 3000, 2000, semilla=5)}
    m = backtest.Motor(series, {"max_drawdown_pct": 2.0, "caminos_backtest": 50, "caminos_ultima": 50},
                       con_skeptic=False).correr()
    if m.riesgo.parado:
        assert not m.abiertas
        assert m.estadisticas()["max_dd_pct"] > -4.0


# --- noticias reales (sin mirar el futuro) ------------------------------------------

def _archivo(titulares, cubre=True):
    from consejo.agentes import news
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return news.Archivo.desde_json({
        "simbolo": "X",
        "titulares": [{"t": t.isoformat(), "titulo": x} for t, x in titulares],
        "dias": {},
        "cubre": [[t0.isoformat(), (t0 + timedelta(days=400)).isoformat()]] if cubre else []})


def test_news_no_mira_el_futuro():
    vs = _serie_con_ruptura()
    ctx = Contexto("X", vs)
    a = scout.escanear(ctx, len(vs) - 1, backtest.CONFIG_DEFECTO)
    cierre = a.t + timedelta(hours=1)
    from consejo.agentes import news
    arch = _archivo([(cierre - timedelta(hours=2), "Bitcoin surges after ETF approval"),
                     (cierre + timedelta(minutes=1), "Exchange hack: funds stolen")])
    r = news.opinar(a, ctx, arch, cierre)
    assert r["titulares"] == ["Bitcoin surges after ETF approval"]
    assert r["veto"] is None and r["pro"]
    assert "etf" in r["etiquetas"]


def test_news_veta_noticia_grave():
    vs = _serie_con_ruptura()
    ctx = Contexto("X", vs)
    a = scout.escanear(ctx, len(vs) - 1, backtest.CONFIG_DEFECTO)
    cierre = a.t + timedelta(hours=1)
    from consejo.agentes import news
    r = news.opinar(a, ctx, _archivo([(cierre - timedelta(hours=3), "Major exchange hack drains wallets")]), cierre)
    assert r["veto"]


def test_news_sin_cobertura_no_opina():
    vs = _serie_con_ruptura()
    ctx = Contexto("X", vs)
    a = scout.escanear(ctx, len(vs) - 1, backtest.CONFIG_DEFECTO)
    from consejo.agentes import news
    r = news.opinar(a, ctx, _archivo([], cubre=False), a.t + timedelta(hours=1))
    assert r["veto"] is None and r["pro"] is None and r["contra"] is None
    assert news.opinar(a, ctx, None, None)["contra"] is None


def test_news_moving_3_por_ciento():
    vs = _serie_con_ruptura()           # última vela: +6 %
    from consejo.agentes import news
    assert abs(news.cambio_vs_ayer(Contexto("X", vs), len(vs) - 1)) > 0
    a = scout.escanear(Contexto("X", vs), len(vs) - 1, backtest.CONFIG_DEFECTO)
    assert news.opinar(a, Contexto("X", vs))["moving"] == (abs(news.cambio_vs_ayer(Contexto("X", vs), len(vs) - 1)) >= 3)


def test_skeptic_ignora_maquina_sin_calibrar():
    from consejo.agentes import skeptic
    vs = _serie_con_ruptura()
    ctx = Contexto("X", vs)
    a = scout.escanear(ctx, len(vs) - 1, backtest.CONFIG_DEFECTO)
    p = charts.planificar(ctx, a, backtest.CONFIG_DEFECTO)
    sim = {"paths": 1000, "p_tp": 90.0, "p_sl": 10.0, "breakeven": 33.3}
    args = (ctx, a, p, {}, {"n": 0}, sim, {}, backtest.CONFIG_DEFECTO)
    sin = skeptic.decidir(*args)
    con = skeptic.decidir(*args, calibrada=True)
    assert not any("máquina" in x for x in sin["pros"])
    assert con["score"] == sin["score"] + 1


def test_calibracion_solo_casos_resueltos():
    c = backtest.Calibracion()
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for k in range(10):
        c.anotar(t0 + timedelta(hours=k), 60.0, k < 6)
    c.avanzar(t0 + timedelta(hours=4))
    assert c.n == 5
    c.avanzar(t0 + timedelta(hours=100))
    assert c.n == 10 and c.error() == pytest.approx(0.0)


def test_run_backtest_sin_datos_no_inventa(tmp_path, monkeypatch):
    import run_backtest
    monkeypatch.setattr(run_backtest, "RAIZ", str(tmp_path))
    assert run_backtest.main(["--mesa", "cripto", "--salida", str(tmp_path / "x.json")]) == 1
    assert not (tmp_path / "x.json").exists()


def test_backtest_con_archivo_de_noticias():
    from consejo.agentes import news
    series = {"BTCUSDT": datos.sinteticas("BTCUSDT", 1500, 30000, semilla=1)}
    vs = series["BTCUSDT"]
    arch = news.Archivo.desde_json({
        "simbolo": "BTCUSDT",
        "titulares": [{"t": v.t.isoformat(), "titulo": "Bitcoin rally continues"} for v in vs[::24]],
        "dias": {}, "cubre": [[vs[0].t.isoformat(), vs[-1].t.isoformat()]]})
    d = backtest.ejecutar(series, {"caminos_backtest": 100, "caminos_ultima": 100}, noticias={"BTCUSDT": arch})
    assert d["news_coverage"] == {"BTCUSDT": len(vs[::24])}
    assert "n" in d["calibration"]


def test_gdelt_a_filas():
    import descargar_noticias as dn
    filas = dn.titulares_a_filas({"articles": [
        {"seendate": "20240115T103000Z", "title": "Gold prices jump", "domain": "x.com", "url": "u"},
        {"seendate": "", "title": "sin fecha"}]})
    assert len(filas) == 1 and filas[0]["t"].startswith("2024-01-15T10:30")
    tono = dn.serie_diaria({"timeline": [{"data": [{"date": "20240115T000000Z", "value": -2},
                                                   {"date": "20240115T120000Z", "value": 0}]}]})
    assert tono == {"2024-01-15": -1.0}
    assert dn._juntar([["b", "c"], ["a", "b"], ["d", "e"]]) == [["a", "c"], ["d", "e"]]


# --- modo papel en vivo ----------------------------------------------------------------

def test_papel_guarda_y_sigue(tmp_path, monkeypatch):
    import descargar_noticias
    import papel
    todas = datos.sinteticas("BTCUSDT", 1600, 30000, semilla=3)
    corte = {"n": 1200}
    monkeypatch.setattr(papel, "RAIZ", str(tmp_path))
    monkeypatch.setattr(papel, "velas_binance", lambda s, tf="1h", n=1000: todas[:corte["n"]] if s == "BTCUSDT" else [])
    monkeypatch.setattr(descargar_noticias, "_pedir", lambda *a, **k: None)
    (tmp_path / "salida").mkdir()
    cfg = {**backtest.CONFIG_DEFECTO, "modo": "paper", "caminos_ultima": 100, "caminos_backtest": 100}
    enviados = []
    grupo = papel.Grupo(None)
    grupo.enviar = lambda bot, txt: enviados.append((bot, txt))
    ruta = str(tmp_path / "salida" / "papel_cripto.json")
    papel.pasada("cripto", cfg, grupo, ruta)              # primera vez: solo empieza a vigilar
    e1 = json.loads(open(ruta, encoding="utf-8").read())
    assert e1["operaciones"] == [] and e1["visto"]["BTCUSDT"] == todas[1199].t.isoformat()
    corte["n"] = 1600
    papel.pasada("cripto", cfg, grupo, ruta)              # 400 velas nuevas
    e2 = json.loads(open(ruta, encoding="utf-8").read())
    assert e2["visto"]["BTCUSDT"] == todas[1599].t.isoformat()
    assert e2["inicio"] == e1["inicio"]
    assert sum(e2["decisiones"].values()) > 0 and enviados
    panel = json.loads((tmp_path / "salida" / "estado_papel_cripto.json").read_text(encoding="utf-8"))
    assert panel["source"] == "live" and panel["mode"] == "paper"


def test_papel_quita_vela_abierta():
    import papel
    vs = datos.sinteticas("X", 5, semilla=1)
    ahora = vs[-1].t + timedelta(minutes=30)
    assert papel.solo_cerradas(vs, timedelta(hours=1), ahora) == vs[:-1]


# --- mesa memecoins (backtest sobre una grabación) ------------------------------------

def _grabacion_de_prueba(carpeta):
    import random
    rnd = random.Random(1)
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    seg = {"mint_revoked": True, "freeze_revoked": True, "lp_locked_pct": 100, "top10_pct": 15,
           "dev_pct": 1, "bundled_pct": None, "rug_score": None, "risks": [], "rugged": False}
    precios = {f"M{k}": 0.001 for k in range(6)}
    with open(os.path.join(carpeta, "2026-11-01.jsonl"), "w", encoding="utf-8") as f:
        for n in range(60):
            ts = (t0 + timedelta(minutes=5 * n)).isoformat()
            for m in list(precios):
                if m == "M0" and n == 30:                 # rug: el par desaparece
                    f.write(json.dumps({"ts": ts, "gone": m}) + "\n")
                    del precios[m]
                    continue
                precios[m] *= 1 + rnd.gauss(0.01, 0.03)
                c = {"mint": m, "symbol": m, "price": precios[m], "liq": 200000, "mcap": precios[m] * 1e9,
                     "vol_m5": 30000, "vol_h1": 300000, "buys_m5": 30, "sells_m5": 15, "buys_h1": 600,
                     "sells_h1": 250, "chg_m5": 2, "chg_h1": 20, "chg_h6": 40, "age_min": 120 + 5 * n,
                     "socials": 2}
                f.write(json.dumps({"ts": ts, "c": c, "s": seg}) + "\n")


def test_memes_backtest_sobre_grabacion(tmp_path, monkeypatch):
    import memes
    _grabacion_de_prueba(tmp_path)
    tandas = memes.cargar_grabacion(str(tmp_path))
    assert len(tandas) == 60
    eng = memes.correr(tandas, {})
    s = eng.stats()
    assert s["trades"] >= 1
    assert all(t["pnl"] > -eng.broker.start for t in eng.broker.trades)
    monkeypatch.setattr(memes, "RAIZ", str(tmp_path))
    assert memes.main(["backtest", "--carpeta", str(tmp_path)]) == 0
    snap = json.loads((tmp_path / "salida" / "estado_memes.json").read_text(encoding="utf-8"))
    assert snap["desk"] == "memes" and snap["mode"] == "paper"


def test_memes_sin_grabacion_no_inventa(tmp_path):
    import memes
    assert memes.main(["backtest", "--carpeta", str(tmp_path)]) == 1


def test_hn_a_filas():
    import descargar_noticias as dn
    filas = dn.hn_a_filas({"hits": [{"title": "Bitcoin ETF approved", "created_at_i": 1704067200,
                                     "url": "https://example.com/a"}, {"title": "", "created_at_i": 1}]})
    assert len(filas) == 1 and filas[0]["t"].startswith("2024-01-01T00:00") and filas[0]["dominio"] == "example.com"


def test_hn_filtra_ruido():
    import descargar_noticias as dn
    js = {"hits": [{"title": "Euro 2024 final", "created_at_i": 1704067200, "points": 50},
                   {"title": "ECB cuts rates again", "created_at_i": 1704067200, "points": 50},
                   {"title": "ECB holds", "created_at_i": 1704067200, "points": 1}]}
    assert [x["titulo"] for x in dn.hn_a_filas(js, ["ecb"], min_puntos=3)] == ["ECB cuts rates again"]
