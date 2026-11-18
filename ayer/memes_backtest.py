"""BACKTEST de la Mesa Memecoins sobre lo que ha GRABADO memes_live.py (data/memes/*.jsonl).

Como la grabación incluye las monedas que acabaron en rug, no hay trampa de "solo sobreviven
las buenas". Cuantos más días grabados, más fiable.

Uso:
  python memes_backtest.py                          # parámetros de config.yaml
  python memes_backtest.py --grid                   # compara varias estrategias de golpe
  python memes_backtest.py --min-score 60 --stop 8 --trail-start 20
  python memes_backtest.py --dir data/memes_demo    # grabación inventada (memes_demo_data.py)
"""
import argparse
import glob
import itertools
import json
import os

import pandas as pd
import yaml

from council.dashboard import write
from memes.engine import MemeEngine


def load_batches(d):
    rows = []
    for path in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    batches = {}
    for r in rows:
        batches.setdefault(r["ts"], []).append(r)
    return [batches[k] for k in sorted(batches)]


def run(batches, params, equity):
    eng = MemeEngine(params, equity)
    safety = {}
    for batch in batches:
        ts = batch[0]["ts"]
        pairs = []
        for r in batch:
            if "c" in r:
                pairs.append(r["c"])
                if r.get("s") is not None:
                    safety[r["c"]["mint"]] = r["s"]
        # la seguridad solo se conoce si se grabó en ese momento o antes (sin mirar al futuro)
        eng.step(ts, pairs, lambda m: safety.get(m, {}))
    # cerrar lo que quede abierto al último precio conocido
    for m in list(eng.broker.positions):
        eng.broker._close(m, eng.broker.last_px.get(m, eng.broker.positions[m]["ref"]),
                          eng.broker.positions[m]["liq_now"], batches[-1][0]["ts"], "fin del backtest")
    eng.curve.append([batches[-1][0]["ts"], eng.broker.equity()])
    return eng


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dir", default=os.path.join("data", "memes"))
    ap.add_argument("--min-score", type=float)
    ap.add_argument("--stop", type=float)
    ap.add_argument("--trail-start", type=float)
    ap.add_argument("--trail", type=float)
    ap.add_argument("--max-position", type=float)
    ap.add_argument("--grid", action="store_true", help="prueba varias combinaciones y las compara")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(a.config if os.path.exists(a.config) else "config.example.yaml"))
    mc = cfg.get("memes", {})
    base = dict(mc.get("params", {}))
    for k, v in [("min_score", a.min_score), ("stop_pct", a.stop), ("trail_start_pct", a.trail_start),
                 ("trail_pct", a.trail), ("max_position_pct", a.max_position)]:
        if v is not None:
            base[k] = v
    equity = mc.get("starting_equity", 1000)

    batches = load_batches(a.dir)
    if len(batches) < 10:
        raise SystemExit(f"Muy pocos datos en {a.dir}. Deja memes_live.py grabando unos días "
                         "(o prueba con: python memes_demo_data.py y --dir data/memes_demo).")
    first, last = batches[0][0]["ts"][:16], batches[-1][0]["ts"][:16]
    os.makedirs(a.out, exist_ok=True)
    demo = "demo" in a.dir

    eng = run(batches, base, equity)
    s = eng.stats()
    pd.DataFrame(eng.broker.trades).to_csv(f"{a.out}/memes_trades.csv", index=False)
    snap = eng.snapshot("backtest", "Solana · memecoins", period=f"{first} → {last}")
    write(snap, f"{a.out}/memes_dashboard.json")

    def f(x, d=1):
        return "-" if x is None else round(x, d)

    lines = [f"# Backtest Mesa Memecoins", f"Grabación: {first} → {last} · {len(batches)} ciclos · capital {equity}"]
    if demo:
        lines.insert(1, "**⚠️ GRABACIÓN INVENTADA: solo prueba que el código funciona.**")
    lines += ["", "| Métrica | Valor |", "|---|---|",
              f"| Rentabilidad | {f(s.get('return_pct'))}% |", f"| Operaciones | {s['trades']} |",
              f"| Acierto | {f(s.get('win_rate'))}% |", f"| Profit factor | {f(s.get('profit_factor'), 2)} |",
              f"| Drawdown máx. | {f(s.get('max_dd_pct'))}% |", f"| Rugs sufridos | {s.get('rugs', 0)} |",
              "", "## Descartes por regla", *[f"- {k}: {v}" for k, v in eng.kills.most_common(12)]]

    if a.grid:
        res = []
        for ms, stop, ts_, tr in itertools.product([60, 70, 80], [6, 10], [12, 25], [8, 15]):
            p = {**base, "min_score": ms, "stop_pct": stop, "trail_start_pct": ts_, "trail_pct": tr}
            e = run(batches, p, equity)
            st = e.stats()
            res.append({"puntuación mín.": ms, "stop %": stop, "trailing desde %": ts_, "trailing %": tr,
                        "rentab. %": round(st.get("return_pct", 0), 1), "trades": st["trades"],
                        "acierto %": round(st.get("win_rate", 0) or 0, 0),
                        "PF": round(st["profit_factor"], 2) if st.get("profit_factor") else None,
                        "DD %": round(st.get("max_dd_pct", 0), 1)})
        g = pd.DataFrame(res).sort_values("rentab. %", ascending=False)
        g.to_csv(f"{a.out}/memes_grid.csv", index=False)
        lines += ["", "## Comparación de estrategias (mejores 8)", g.head(8).to_markdown(index=False),
                  "", "Ojo: la mejor del pasado no es la mejor del futuro. Fíate de las que salen bien "
                      "en VARIAS semanas de grabación, no solo en una."]
    report = "\n".join(lines)
    with open(f"{a.out}/memes_informe.md", "w") as fh:
        fh.write(report)
    print(report)
    print(f"\nArchivos en {a.out}/: memes_informe.md, memes_trades.csv, memes_dashboard.json")


if __name__ == "__main__":
    main()
