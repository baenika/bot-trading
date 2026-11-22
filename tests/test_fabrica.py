import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fabrica as cli  # noqa: E402
from consejo import backtest, datos, fabrica, fondeo  # noqa: E402
from consejo.datos import Vela  # noqa: E402


def _velas(precios, t0=datetime(2024, 1, 2, tzinfo=timezone.utc)):
    return [Vela(t0 + timedelta(hours=i), p, p * 1.001, p * 0.999, p, 100.0) for i, p in enumerate(precios)]


def test_hora_ny_verano_e_invierno():
    assert fabrica.hora_ny(datetime(2025, 7, 1, 13, 30, tzinfo=timezone.utc)).hour == 9
    assert fabrica.hora_ny(datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc)).hour == 9


def test_stop_cuenta_antes_que_objetivo_y_riesgo_fijo():
    velas = _velas([100.0] * 300)
    # vela 251: entrada; 252: toca stop y objetivo a la vez -> cuenta el stop
    velas[252] = Vela(velas[252].t, 100, 130, 70, 100, 1)
    D = fabrica.Datos("GEN", velas)
    D._senales[("sobre_ema", (("n", 20),), 1)] = {250}
    e = {"cond": [("sobre_ema", {"n": 20})], "dir": "long", "atr_n": 20, "sl": 1.0, "tp": 1.0, "barras": None,
         "canal": None, "intradia": False}
    P = dict(fabrica.GENERICO, comision_pct=0.0, deslizamiento_pct=0.0)
    ops = fabrica.simular(D, e, P=P)
    assert len(ops) == 1 and ops[0]["reason"] == "stop"
    assert abs(ops[0]["pnl"] + 1000) < 1  # 1 % de 100.000 $


def test_objetivo_tiene_que_pasarse():
    velas = _velas([100.0] * 300)
    D = fabrica.Datos("GEN", velas)
    a = D.ind("atr", 20)[250]
    velas[253] = Vela(velas[253].t, 100, 100 + a * 2, 99.95, 100, 1)  # toca justo el objetivo, no lo pasa
    D = fabrica.Datos("GEN", velas)
    D._senales[("sobre_ema", (("n", 20),), 1)] = {250}
    e = {"cond": [("sobre_ema", {"n": 20})], "dir": "long", "atr_n": 20, "sl": 3.0, "tp": 2.0, "barras": 10,
         "canal": None, "intradia": False}
    P = dict(fabrica.GENERICO, comision_pct=0.0, deslizamiento_pct=0.0)
    assert fabrica.simular(D, e, P=P)[0]["reason"] == "tiempo"


def test_monte_carlo_estrategia_que_siempre_gana():
    ops = [{"pnl": 100.0}] * 50
    assert fabrica.monte_carlo(ops, 200)["pct_ganan"] == 100.0
    mezcla = [{"pnl": 100.0}, {"pnl": -100.0}] * 50
    assert fabrica.monte_carlo(mezcla, 500)["pct_ganan"] < 70


def test_vecina_se_mueve_como_mucho_un_20_por_ciento():
    import random
    e = {"cond": [("cruce_ema", {"rapida": 20, "lenta": 100})], "dir": "long", "atr_n": 100, "sl": 2.0,
         "tp": 3.0, "barras": None, "canal": None, "intradia": False}
    rnd = random.Random(1)
    for _ in range(50):
        v = fabrica.vecina(e, rnd)
        assert 16 <= v["cond"][0][1]["rapida"] <= 24 and 80 <= v["atr_n"] <= 120 and 1.6 <= v["sl"] <= 2.4


def test_estrategias_aleatorias_respetan_las_reglas():
    import random
    rnd = random.Random(3)
    for _ in range(300):
        e = fabrica.aleatoria(rnd, intradia=False)
        assert 1 <= len(e["cond"]) <= 2 and e["sl"] >= 1.0
        assert sum(e[k] is not None for k in ("tp", "barras", "canal")) <= 2
        assert all(n != "rango_apertura" for n, _ in e["cond"])
        assert fabrica.describir(e)


def test_fabrica_entera_y_su_informe():
    v = datos.sinteticas("XAUUSD", n=6000, inicio=1800, semilla=5)
    r = fabrica.correr(fabrica.Datos("XAUUSD", v), n=80, criterios={"min_ops": 10, "min_ret_dd": 0.5,
                                                                       "max_vecinos": 3, "n_vecinos": 10,
                                                                       "vueltas": 100}, progreso=lambda *_: None)
    e = r["embudo"]
    assert e["generadas"] == 82  # 80 + las 2 tortugas
    assert e["generadas"] >= e["distintas"] >= e["ganadoras"] >= e["filtros_basicos"] >= e["monte_carlo"]
    assert e["sin_parecidas"] >= e["vecinos"] >= e["fuera_de_muestra"]
    assert len(r["finalistas"]) == min(10, e["fuera_de_muestra"])
    html = cli.informe_html(r)
    assert "Fábrica de estrategias" in html and "Embudo" in html
    assert "Fábrica" in cli.resumen_texto(r)


def test_fondeo_aprueba_suspende_y_no_cuenta_lo_que_sigue_en_curso():
    t0 = datetime(2025, 1, 2, tzinfo=timezone.utc)
    buenas = [{"closed": t0 + timedelta(days=i), "pnl": 1000.0} for i in range(40)]
    r = fondeo.examen(buenas, 100_000)
    assert r["aprobadas"] >= 1 and not r["fallos"]
    malas = [{"closed": t0 + timedelta(hours=i), "pnl": -1200.0} for i in range(10)]
    r = fondeo.examen(malas, 100_000)
    assert r["aprobadas"] == 0 and r["fallos"].get("pérdida diaria") == 1
    poco = [{"closed": t0 + timedelta(days=i), "pnl": 100.0} for i in range(5)]
    assert fondeo.examen(poco, 100_000)["en_curso"] == 1


def test_examen_del_consejo_incluye_el_reto_fondeado():
    st = {"trades": 3, "profit_factor": 2.0, "max_dd_pct": -1.0, "return_pct": 5.0}
    ops = [{"closed": "2025-01-02 10:00:00+00:00", "pnl": 50.0}] * 3
    filas = backtest.examen(st, 1.0, ops, 10_000)
    assert len(filas) == 5 and "fondeada" in filas[-1]["criterio"]
    assert len(backtest.examen(st, 1.0)) == 4


def test_decodificar_dukascopy_elige_la_escala():
    import lzma
    import struct
    raw = lzma.compress(struct.pack(">5if", 0, 2650_000, 2651_000, 2649_000, 2652_000, 10.0),
                        format=lzma.FORMAT_ALONE)
    f = cli._decodificar(raw, datetime(2025, 1, 1, tzinfo=timezone.utc), (250, 10000))[0]
    assert f[1] == 2650.0 and f[2] == 2652.0 and f[3] == 2649.0 and f[4] == 2651.0


def test_papel_fabrica_congela_estrategias_y_avisa(tmp_path, monkeypatch):
    import json
    import papel_fabrica
    monkeypatch.setattr(papel_fabrica, "SALIDA", str(tmp_path))
    e = {"cond": [["donchian", {"n": 20}]], "dir": "ambas", "atr_n": 20, "sl": 2.0, "tp": None, "barras": 10,
         "canal": None, "intradia": False}
    fin = {"reglas": "x", "estrategia": e, "familia": "ruptura", "is": {"ret_pct": 1}, "oos": {"ret_pct": 1},
           "todo": {"dd_pct": 1}, "fondeo": {"pct_aprobadas": 50}}
    (tmp_path / "fabrica_oro.json").write_text(json.dumps({"finalistas": [fin, fin, fin]}), encoding="utf-8")
    velas = datos.sinteticas("GC=F", n=3000, inicio=2000, semilla=9)
    est = papel_fabrica.estrategias("oro", 2)
    assert len(est) == 2 and (tmp_path / "papel_fabrica_oro_estrategias.json").exists()
    # el papel empieza a mitad de las velas: solo cuentan las operaciones posteriores
    est[0]["inicio"] = est[1]["inicio"] = velas[1500].t.isoformat()
    (tmp_path / "papel_fabrica_oro_estrategias.json").write_text(json.dumps(est), encoding="utf-8")
    avisos = []
    r = papel_fabrica.pasada("oro", 2, avisos.append, velas=velas)
    assert len(r["cuentas"]) == 2 and avisos
    assert all(c["opened"] >= velas[1500].t.isoformat() for c in r["cuentas"][0]["cerradas"])
    n = len(avisos)
    papel_fabrica.pasada("oro", 2, avisos.append, velas=velas)   # misma pasada: nada nuevo que avisar
    assert len(avisos) == n
