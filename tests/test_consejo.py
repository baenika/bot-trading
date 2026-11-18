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
        assert o["reason"] in ("stop loss", "take profit", "tiempo máximo")
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
    d = json.loads(salida.read_text())
    assert d["desk"] == "mercados" and d["mode"] == "paper"
