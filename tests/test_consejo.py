import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

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
    monkeypatch.setattr(papel.NoticiasEnVivo, "_hn", lambda self, s: None)
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


def test_fomo_no_persigue_espera_retroceso():
    from consejo.agentes import skeptic
    vs = _serie_con_ruptura()
    ctx = Contexto("X", vs)
    a = scout.escanear(ctx, len(vs) - 1, backtest.CONFIG_DEFECTO)
    p = charts.planificar(ctx, a, backtest.CONFIG_DEFECTO)
    cfg = {**backtest.CONFIG_DEFECTO, "rr_minimo": 0, "puntos_para_entrar": -10, "puntos_para_esperar": -20}
    normal = skeptic.decidir(ctx, a, p, {}, {"n": 0}, {}, {}, cfg)
    fomo = skeptic.decidir(ctx, a, p, {"fomo": True}, {"n": 0}, {}, {}, cfg)
    assert normal["decision"] == "COMPRAR" and fomo["decision"] == "ESPERAR"


def test_pausa_por_racha_no_es_eterna():
    series = {"BTCUSDT": datos.sinteticas("BTCUSDT", 600, 30000, semilla=2)}
    m = backtest.Motor(series, {"caminos_backtest": 50, "caminos_ultima": 50, "pausa_horas": 24})
    vs = series["BTCUSDT"]
    m.racha, m.t_pausa = 3, vs[100].t
    for i in range(101, 600):
        m.paso("BTCUSDT", i)
        if m.racha == 0:
            break
    # tras 24 h y en cuanto llega una alerta, la racha se reinicia
    assert m.racha == 0 or all(scout.escanear(m.ctx["BTCUSDT"], k, m.cfg) is None for k in range(125, 600))


# --- estrategia de las 9:30 (reel 13) ------------------------------------------------

def test_apertura_ny_horario_verano():
    from datetime import date
    from consejo.estrategias import orb
    assert orb.apertura_ny_utc(date(2026, 7, 1)).hour == 13
    assert orb.apertura_ny_utc(date(2026, 1, 15)).hour == 14
    assert orb.apertura_ny_utc(date(2026, 11, 2)).hour == 14     # el 1 de noviembre de 2026 es domingo


def _dia_orb(precios):
    """precios: lista de (o, h, l, c) de velas de 5 min desde las 9:30 NY de un miércoles de enero."""
    from datetime import date
    from consejo.estrategias import orb
    t0 = orb.apertura_ny_utc(date(2026, 1, 14))
    return [datos.Vela(t0 + timedelta(minutes=5 * k), o, h, l, c, 1000) for k, (o, h, l, c) in enumerate(precios)]


def test_orb_order_block_y_objetivo():
    from datetime import date
    from consejo.estrategias import orb
    p = [(100, 101, 99.5, 100.5), (100.5, 100.8, 99.8, 100.0), (100, 100.9, 99.6, 99.9),  # rango 99.5-101
         (99.9, 100.6, 99.7, 99.8),      # vela bajista: order block (máx 100.6, mín 99.7)
         (99.8, 101.6, 99.8, 101.4),     # cierra por encima de 101: ruptura
         (101.4, 101.5, 100.5, 100.7),   # vuelve y toca 100.6: entra
         (100.7, 102.6, 100.7, 102.5)]   # objetivo = 100.6 + 2*0.9 = 102.4
    p += [(102.5, 102.6, 102.4, 102.5)] * 70
    op, que = orb.operar_dia("SPY", _dia_orb(p), date(2026, 1, 14), {**orb.CONFIG_ORB, "comision_pct": 0})
    assert que == "operación" and op.motivo == "objetivo"
    assert op.entrada == pytest.approx(100.6) and op.stop == pytest.approx(99.7)
    assert op.R == pytest.approx(2.0)


def test_orb_stop_primero_y_sin_ruptura():
    from datetime import date
    from consejo.estrategias import orb
    base = [(100, 101, 99.5, 100.5), (100.5, 100.8, 99.8, 100.0), (100, 100.9, 99.6, 99.9),
            (99.9, 100.6, 99.7, 99.8), (99.8, 101.6, 99.8, 101.4),
            (101.4, 101.5, 99.0, 99.2)]          # toca la entrada y el stop en la misma vela
    op, _ = orb.operar_dia("SPY", _dia_orb(base + [(99.2, 99.3, 99.1, 99.2)] * 70), date(2026, 1, 14),
                           {**orb.CONFIG_ORB, "comision_pct": 0, "deslizamiento_pct": 0})
    assert op.motivo == "stop" and op.R == pytest.approx(-1.0)
    plano = [(100, 101, 99.5, 100.5)] * 78
    assert orb.operar_dia("SPY", _dia_orb(plano), date(2026, 1, 14), orb.CONFIG_ORB)[0] is None


def test_kronos_probabilidad_sin_modelo():
    from consejo import kronos_opinion as k
    caminos = [[(105, 99, 104)], [(101, 94, 95)], [(110, 94, 100)], [(101, 99, 100)]]
    r = k.probabilidad(caminos, stop=95, objetivo=104, dir_="long")
    assert (r["p_tp"], r["p_sl"], r["p_none"]) == (25.0, 50.0, 25.0)
    assert k.cargar("") is None


# --- señales de un canal de Telegram ---------------------------------------------------------

def _min(t, o, h, l, c, spread=0.0):
    import senales as S
    return S.Minuto(t, (o, h, l, c), (o + spread, h + spread, l + spread, c + spread))


def test_senales_lee_formatos_del_canal():
    import senales as S
    s = S.leer_senal({"id": 1, "date_unixtime": "1700000000",
                      "text": "SELL XAUUSD AHORA 94 - 96\n\n SL 5204\n\nTP1  5189\nTP2  5182\nTP3  5177"})
    assert (s.dir, s.entrada, s.zona, s.sl, s.tps) == ("short", 5194, (5194, 5196), 5204, [5189, 5182, 5177])
    b = S.leer_senal({"id": 2, "date_unixtime": "1700000000", "edited": "x",
                      "text": [{"type": "bold", "text": "BUY BTCUSD ( 70950 - 70400 )"}, "\n\nSL : 69800\n\nTP1 : 71400"
                               "\nTP2 : 71850\nTP3 : 75000\nTP3 HIT ✅"]})
    assert b.simbolo == "BTCUSD" and b.dir == "long" and b.editada and b.canal_tp == 3
    assert S.leer_senal({"id": 3, "date_unixtime": "0", "text": "BUY XAUUSD 5190 SL 5195 TP1 5200"}) is None


def test_senales_export_asigna_avisos_y_simula_sin_mirar_atras():
    import senales as S
    t0 = datetime(2026, 3, 2, 15, 5, 30, tzinfo=timezone.utc)
    s = S.Senal(1, t0, "XAUUSD", "long", 5193.5, (5193.5, 5190), 5187, [5198, 5205, 5230], "")
    m = lambda k, *v: _min(t0.replace(second=0) + timedelta(minutes=k), *v, spread=0.3)  # noqa: E731
    ms = [m(0, 5180, 5181, 5179, 5180),          # el minuto del mensaje: no cuenta (sería mirar atrás)
          m(1, 5194, 5195, 5192, 5193),          # entra (ask baja a 5192,3)
          m(2, 5193, 5199, 5193, 5198),          # TP1
          m(3, 5198, 5198, 5192, 5193),          # vuelve a la entrada: en "tercios" sale en BE
          m(4, 5193, 5193, 5186, 5187)]          # SL original
    r = S.simular(s, ms, validez_min=60)
    assert r["estado"] == "entró" and r["entrada_real"] == 5193.5
    assert r["TP1"]["salida"] == "TP1" and r["TP1"]["R"] > 0
    assert r["TP2"]["salida"] == "SL" and abs(r["TP2"]["R"] + 1) < 1e-9
    assert r["tercios"]["salida"] == "BE" and r["tercios"]["R"] > 0
    assert r["tp_real"] == 1 and r["sl_real"]
    s.canal_tp = 3
    s.res = r
    assert "TP3" in S.veredicto_canal(s)


def test_senales_no_entra_y_sl_en_la_vela_de_entrada():
    import senales as S
    t0 = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
    s = S.Senal(1, t0, "BTCUSD", "short", 72700, (72700, 73100), 74100, [72300, 71300, 66200], "")
    lejos = [_min(t0 + timedelta(minutes=k), 72000, 72100, 71900, 72000) for k in range(1, 200)]
    assert S.simular(s, lejos, validez_min=60)["estado"] == "no entró"
    malo = [_min(t0 + timedelta(minutes=1), 72600, 74200, 72200, 74000)]   # entra, toca SL y TP: cuenta SL
    r = S.simular(s, malo, validez_min=60)
    assert r["TP1"]["salida"] == "SL" and r["tp_real"] == 0


def test_senales_dukascopy_y_hora_de_madrid():
    import lzma
    import struct
    import senales as S
    raw = lzma.compress(struct.pack(">5if", 60, 5193500, 5194000, 5190000, 5195000, 1.0))
    v = S.decodificar_bi5(raw, date(2026, 3, 2), 1000)
    assert v[0][0] == datetime(2026, 3, 2, 0, 1, tzinfo=timezone.utc) and v[0][1:] == (5193.5, 5195, 5190, 5194)
    assert S.DIVISORES["XAUUSD"] == 1000
    assert S.madrid_a_utc(date(2026, 2, 25), "16:05").hour == 15
    assert S.madrid_a_utc(date(2026, 7, 1), "16:05").hour == 14


def test_senales_capturas_busca_el_dia_con_velas():
    import senales as S
    s = S.Senal(1, datetime(2026, 1, 1, tzinfo=timezone.utc), "XAUUSD", "short", 5176.5, (5176.5, 5178), 5182.5,
                [5172, 5164, 5157], "")
    objetivo = datetime.now(timezone.utc).date() - timedelta(days=3)

    def minutos(dia):
        t = S.madrid_a_utc(objetivo, "13:47")
        precio = 5177 if dia == t.date() else 4000
        return [_min(t, precio, precio, precio, precio)]
    assert S.buscar_fechas(s, "13:47", 10, minutos_de=minutos) == [S.madrid_a_utc(objetivo, "13:47")]


def test_telegram_un_solo_bot(tmp_path, monkeypatch):
    from consejo import telegram
    import configurar_telegram as ct
    ruta = tmp_path / "telegram.json"
    ruta.write_text(json.dumps({"chat_id": "123", "token": "T"}), encoding="utf-8")
    g = telegram.Grupo(str(ruta), pausa=0)
    enviados = []
    monkeypatch.setattr(telegram.urllib.request, "urlopen",
                        lambda req, timeout: enviados.append(json.loads(req.data)) or __import__("io").BytesIO(b"{}"))
    g.enviar("risk", "hola")
    assert g.activo("news") and enviados[0]["text"].startswith("🛡️ Risk\nhola")
    assert ct.chat_privado({"result": [{"message": {"chat": {"id": 5, "type": "private", "first_name": "D"}}}]}) \
        == ("5", "D")


def test_telegram_limpia_el_token_pegado():
    import configurar_telegram as ct
    tok = "123456789:" + "A" * 35
    assert ct.limpiar(f"  {tok}\r\n") == tok
    assert ct.limpiar("\x16") == ""
    assert ct.limpiar(f"Use this token to access the HTTP API:\n{tok}\nKeep it secure") == tok


def test_mando_de_telegram_solo_obedece_a_tu_chat(tmp_path, monkeypatch):
    from consejo import mando, telegram
    ruta = tmp_path / "telegram.json"
    ruta.write_text(json.dumps({"chat_id": "77", "token": "T"}), encoding="utf-8")
    g = telegram.Grupo(str(ruta), pausa=0)
    estado = tmp_path / "papel.json"
    estado.write_text(json.dumps({"capital": 10123.4, "inicio": "2026-11-19T05:37", "operaciones": [],
                                  "abiertas": {"BTCUSDT": {"entry": 62000, "stop": 61000, "tp": 64000}}}))
    m = mando.Mando(g, backtest.CONFIG_DEFECTO, str(estado), str(tmp_path / "no.json"))
    dichos = []
    monkeypatch.setattr(m, "_api", lambda metodo, datos, timeout=10: dichos.append((metodo, datos)) or {})
    assert m.atender({"message": {"chat": {"id": 99}, "text": "/analizar"}}) is None and not dichos
    assert m.atender({"message": {"chat": {"id": 77}, "text": "/analizar"}}) == "analizar"
    m.atender({"message": {"chat": {"id": 77}, "text": "/cartera"}})
    assert "10,123.40" in dichos[-1][1]["text"] and "BTCUSDT" in dichos[-1][1]["text"]
    m.atender({"callback_query": {"id": "c", "data": "/avisos", "message": {"chat": {"id": 77}}}})
    assert g.silencio and dichos[-2][0] == "answerCallbackQuery"
    m.atender({"message": {"chat": {"id": 77}, "text": "/parametros"}})
    assert "Riesgo por operación" in dichos[-1][1]["text"]
    enviados = []
    monkeypatch.setattr(g, "_mandar", lambda tok, txt: enviados.append(txt))
    g.conversacion([("scout", "a"), ("skeptic", "b")])
    assert not enviados                      # en silencio
    g.silencio = False
    g.conversacion([("scout", "a"), ("skeptic", "b")])
    assert len(enviados) == 1 and "Scout" in enviados[0] and "Skeptic" in enviados[0]


def test_operaciones_guardan_ficha_y_grafico_del_panel():
    series = {"BTCUSDT": datos.sinteticas("BTCUSDT", 3000, 30000, semilla=1)}
    cfg = {**backtest.CONFIG_DEFECTO, "caminos_backtest": 200}
    m = backtest.Motor(series, cfg).correr()
    assert m.operaciones and all("score" in o["ficha"] and o["usd"] > 0 for o in m.operaciones)
    g = backtest.grafico(m, series, 500)["BTCUSDT"]
    assert len(g["candles"]) == 500 and all("T" in t["opened"] for t in g["trades"])
    copia = backtest.Motor(series, cfg).restaurar(json.loads(json.dumps(m.exportar(), default=str)))
    assert copia.operaciones[0]["ficha"] == m.operaciones[0]["ficha"]


def test_autoajuste_solo_acepta_lo_permitido_y_mide_tramos(tmp_path, monkeypatch):
    import aprender
    monkeypatch.setattr(aprender, "RAIZ", str(tmp_path))
    (tmp_path / "salida").mkdir()
    (tmp_path / "salida" / "config_auto_cripto.json").write_text(json.dumps(
        {"parametros": {"stop_max_atr": 2.5, "risk_per_trade_pct": 50, "rr_objetivo": 9}}), encoding="utf-8")
    assert aprender.cargar_auto("cripto") == {"stop_max_atr": 2.5}       # nada fuera de lista ni de rango
    ops = [{"pnl": 100, "R": 1, "closed": "2024-01-02"}, {"pnl": -50, "R": -0.5, "closed": "2024-01-03"}]
    st = aprender.stats_ops(ops, 10000)
    assert st["n"] == 2 and abs(st["pf"] - 2) < 1e-9 and abs(st["ret"] - 0.5) < 1e-9 and st["dd"] < 0
    assert not aprender.mejor_que({**st, "n": 5}, {"n": 50, "pf": 1, "ret": 0})   # pocas operaciones: no
    serie = {"BTCUSDT": datos.sinteticas("BTCUSDT", 3000, 30000, semilla=1)}
    corte = serie["BTCUSDT"][2000].t
    cfg = {**backtest.CONFIG_DEFECTO, "caminos_backtest": 100}
    a, b = aprender.evaluar(serie, cfg, {}, None, corte), aprender.evaluar(serie, cfg, {}, corte, None)
    assert a["n"] + b["n"] >= 1
    assert "pocas" in "\n".join(aprender.lecciones([{"R": 1, "ficha": {"score": 2.2, "moving": True}}]))


def test_rss_de_cripto_filtra_por_moneda():
    import descargar_noticias as dn
    xml = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel>'
           '<item><title>Bitcoin hits record high</title><pubDate>Thu, 19 Nov 2026 10:00:00 +0000</pubDate>'
           '<link>https://x/1</link></item>'
           '<item><title>Solana outage hits DeFi</title><pubDate>Thu, 19 Nov 2026 11:00:00 GMT</pubDate></item>'
           '<item><title>Stablecoin bill passes</title><pubDate>Thu, 19 Nov 2026 12:00:00 GMT</pubDate></item>'
           '</channel></rss>')
    btc = dn.rss_a_filas(xml, "coindesk.com", dn.RSS_TERMINOS["BTCUSDT"])
    assert [x["titulo"] for x in btc] == ["Bitcoin hits record high"] and btc[0]["t"].startswith("2026-11-19T10:00")
    assert len(dn.rss_a_filas(xml, "c", dn.RSS_TERMINOS["SOLUSDT"])) == 1
    assert dn.rss_a_filas("no es xml", "c") == []


def test_risk_protecciones_de_day_trader_skill():
    from consejo.agentes.charts import Plan
    from consejo.agentes.risk import GestorRiesgo
    r = GestorRiesgo(backtest.CONFIG_DEFECTO)
    plan = Plan("long", 100.0, 99.9, 100.2, 99.97, 2.0, 2.0, None)        # stop muy cerca
    ok = r.aprobar(10000, 10000, 0, plan, False)
    assert ok["ok"] and ok["tope_usd"] <= 3300.01                          # posición máx. 33 %
    assert not r.aprobar(10000, 10000, 0, plan, False, entradas_hoy=3)["ok"]
    assert not r.aprobar(10000, 10000, 0, plan, False, riesgo_hoy=290)["ok"]       # stops de hoy ya suman ~3 %
    assert r.aprobar(10000, 10000, 0, plan, False, riesgo_hoy=200)["arriesgo"] <= 100
    assert not r.aprobar(10000, 10000, 0, plan, False, ya_disparada=True)["ok"]
    assert not r.aprobar(10000, 10000, 2, plan, False, exposicion=9000)["ok"]     # sin el 10 % de efectivo


def test_avisos_no_repiten_y_el_informe_semanal_los_cuenta(tmp_path, monkeypatch):
    from consejo import avisos
    import informe_semanal
    enviados = []

    class G:
        def enviar(self, bot, texto):
            enviados.append(texto)
    ruta = str(tmp_path / "avisos.json")
    a = avisos.Avisador(G(), "papel cripto", ruta)
    ahora = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    assert a.avisar("datos", "BTCUSDT", "No llegan velas", ahora)
    assert not a.avisar("datos", "BTCUSDT", "No llegan velas", ahora + timedelta(hours=1))
    assert a.avisar("datos", "BTCUSDT", "No llegan velas", ahora + timedelta(hours=7))
    a.error(ValueError("x"), "la pasada")
    assert len(enviados) == 3 and "📡" in enviados[0]
    monkeypatch.setattr(informe_semanal, "SALIDA", str(tmp_path))
    monkeypatch.setattr(avisos, "RUTA", ruta)
    (tmp_path / "papel_cripto.json").write_text(json.dumps({
        "capital": 10100, "inicio": "2026-11-19T05:37", "abiertas": {},
        "operaciones": [{"closed": "2026-11-19 10:00:00+00:00", "pnl": 100, "R": 1}]}), encoding="utf-8")
    txt = informe_semanal.informe(datetime(2026, 11, 22, 21, tzinfo=timezone.utc))
    assert "Avisos de la semana: 3" in txt and "1 operaciones cerradas" in txt and "Memes: sin datos" in txt and "Qué toca revisar" in txt


def test_alerta_lleva_historial_de_la_misma_senal():
    series = {"BTCUSDT": datos.sinteticas("BTCUSDT", 3000, 30000, semilla=1)}
    m = backtest.Motor(series, {**backtest.CONFIG_DEFECTO, "caminos_backtest": 100}).correr()
    assert "📜" in m.bots["scout"]["text"]
    assert any(o["ficha"]["historial"]["n"] > 0 for o in m.operaciones)
