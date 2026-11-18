"""Corre el backtest del Consejo y guarda el resultado para el panel.

Uso:
    python run_backtest.py                      # mesa cripto: usa datos/ si hay CSV, si no datos sintéticos
    python run_backtest.py --mesa mercados      # S&P 500, Nasdaq, oro, petróleo, EUR/USD, IBEX...
    python run_backtest.py --csv datos/BTCUSDT-1h.csv --csv datos/ETHUSDT-1h.csv

Primero baja los datos reales con:  python descargar_datos.py todo
Todo es simulado. Este programa no se conecta a ningún broker ni exchange.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from consejo import backtest, datos, mesas, seguridad

RAIZ = os.path.dirname(os.path.abspath(__file__))


def _csv_de_mesa(mesa: str, intervalo: str) -> list[str]:
    lista = mesas.CRIPTO if mesa == "cripto" else mesas.MERCADOS
    rutas = [os.path.join(RAIZ, "datos", mesas.archivo(s, intervalo)) for s in lista]
    return [r for r in rutas if os.path.exists(r)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mesa", choices=["cripto", "mercados"], default="cripto")
    ap.add_argument("--csv", action="append", default=[], help="CSV de velas; el nombre del archivo da el símbolo")
    ap.add_argument("--intervalo", default="1h")
    ap.add_argument("--config", help="JSON con la configuración (ver config.example.json)")
    ap.add_argument("--salida", help="por defecto salida/estado_<mesa>.json")
    ap.add_argument("--velas", type=int, default=6000, help="velas sintéticas por símbolo")
    ap.add_argument("--sinteticos", action="store_true", help="ignorar datos/ y usar datos inventados")
    a = ap.parse_args(argv)

    cfg = dict(mesas.AJUSTES[a.mesa], timeframe=a.intervalo)
    if a.config:
        with open(a.config, encoding="utf-8") as f:
            cfg.update(json.load(f))
    seguridad.comprobar_config({**backtest.CONFIG_DEFECTO, **cfg})

    rutas = a.csv or ([] if a.sinteticos else _csv_de_mesa(a.mesa, a.intervalo))
    if rutas:
        series = {mesas.simbolo_de_archivo(r): datos.cargar_csv(r) for r in rutas}
        series = {s: v for s, v in series.items() if len(v) > 250}
        ts = [v for vs in series.values() for v in (vs[0], vs[-1])]
        periodo = f"{min(v.t for v in ts):%Y-%m-%d} → {max(v.t for v in ts):%Y-%m-%d}" if ts else "vacío"
    else:
        print("Sin CSV en datos/: uso datos SINTÉTICOS (solo para probar el código).")
        if a.mesa == "cripto":
            series = {"BTCUSDT": datos.sinteticas("BTCUSDT", a.velas, 30000, semilla=1),
                      "ETHUSDT": datos.sinteticas("ETHUSDT", a.velas, 2000, semilla=2)}
        else:
            series = {s: datos.sinteticas(s, a.velas, 100 + 50 * k, semilla=10 + k)
                      for k, s in enumerate(["SPY", "QQQ", "GLD"])}
        periodo = "sintético"
    if not series:
        print("No hay datos suficientes para correr el backtest.")
        return 1

    estado = backtest.ejecutar(series, cfg, fuente="backtest", periodo=periodo)
    estado["desk"] = a.mesa
    salida = a.salida or os.path.join(RAIZ, "salida", f"estado_{a.mesa}.json")
    os.makedirs(os.path.dirname(salida) or ".", exist_ok=True)
    with open(salida, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1, default=str)

    st, sc = estado["stats"], estado["compare_stats"]
    print(f"Mesa {a.mesa} · periodo: {periodo} · símbolos: {', '.join(series)}")
    print(f"Con Skeptic : {st['trades']} operaciones · {st['return_pct']:+.1f}% · drawdown {st['max_dd_pct']:.1f}% "
          f"· acierto {st['win_rate']:.0f}% · profit factor {st['profit_factor']:.2f}")
    print(f"Sin Skeptic : {sc['trades']} operaciones · {sc['rentabilidad_%']:+.1f}% · drawdown "
          f"{sc['max_drawdown_%']:.1f}% · acierto {sc['acierto_%']:.0f}%")
    print(f"Comprar y mantener (media): {estado['skeptic_saved']['buy_hold_pct']:+.1f}%")
    print(f"Guardado en {salida}. Ábrelo en el panel con: python dashboard_server.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
