"""Corre el backtest del Consejo y guarda el resultado para el panel.

Uso:
    python run_backtest.py                       # datos sintéticos (solo para probar)
    python run_backtest.py --csv datos/BTCUSDT-1h.csv --csv datos/ETHUSDT-1h.csv

Todo es simulado. Este programa no se conecta a ningún broker ni exchange.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from consejo import backtest, datos, seguridad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", action="append", default=[], help="CSV de velas; el nombre del archivo da el símbolo")
    ap.add_argument("--config", help="JSON con la configuración (ver config.example.json)")
    ap.add_argument("--salida", default="salida/estado_cripto.json")
    ap.add_argument("--velas", type=int, default=6000, help="velas sintéticas por símbolo")
    a = ap.parse_args(argv)

    cfg = {}
    if a.config:
        with open(a.config, encoding="utf-8") as f:
            cfg = json.load(f)
    seguridad.comprobar_config({**backtest.CONFIG_DEFECTO, **cfg})

    if a.csv:
        series = {}
        for ruta in a.csv:
            simbolo = os.path.basename(ruta).split("-")[0].split(".")[0].upper()
            series[simbolo] = datos.cargar_csv(ruta)
        fuente = "backtest"
        ts = [v for vs in series.values() for v in (vs[0], vs[-1]) if vs]
        periodo = f"{min(v.t for v in ts):%Y-%m-%d} → {max(v.t for v in ts):%Y-%m-%d}" if ts else "vacío"
    else:
        series = {"BTCUSDT": datos.sinteticas("BTCUSDT", a.velas, 30000, semilla=1),
                  "ETHUSDT": datos.sinteticas("ETHUSDT", a.velas, 2000, semilla=2)}
        fuente, periodo = "backtest", "sintético"

    estado = backtest.ejecutar(series, cfg, fuente=fuente, periodo=periodo)
    os.makedirs(os.path.dirname(a.salida) or ".", exist_ok=True)
    with open(a.salida, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1, default=str)

    st, sc = estado["stats"], estado["compare_stats"]
    print(f"Periodo: {periodo} · símbolos: {', '.join(series)}")
    print(f"Con Skeptic : {st['trades']} operaciones · {st['return_pct']:+.1f}% · drawdown {st['max_dd_pct']:.1f}% "
          f"· acierto {st['win_rate']:.0f}% · profit factor {st['profit_factor']:.2f}")
    print(f"Sin Skeptic : {sc['trades']} operaciones · {sc['rentabilidad_%']:+.1f}% · drawdown "
          f"{sc['max_drawdown_%']:.1f}% · acierto {sc['acierto_%']:.0f}%")
    print(f"Comprar y mantener: {estado['skeptic_saved']['buy_hold_pct']:+.1f}%")
    print(f"Guardado en {a.salida}. Ábrelo en el panel con: python dashboard_server.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
