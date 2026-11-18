"""BACKTEST con dinero ficticio.

Pasa el consejo de 6 bots vela a vela por el historial y simula los trades. Lo ejecuta DOS veces:
  1) con Skeptic + Sentimental (el sistema de verdad)
  2) sin ellos (todo lo que ve el Scout se opera)
para que veas con números cuánto dinero salva el Skeptic.

Uso:
  python backtest.py                                   # usa config.yaml
  python backtest.py --start 2024-01-01 --end 2025-12-31
  python backtest.py --symbols BTCUSDT --timeframe 4h
  python backtest.py --synthetic                       # datos inventados, solo para probar que funciona
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import yaml

from council import dashboard as dash_mod
from council.broker import PaperBroker
from council.council import Council
from council.data import fetch_fear_greed, fetch_klines, synthetic_klines
from council.indicators import add_indicators

BARS_PER_YEAR = {"15m": 35040, "30m": 17520, "1h": 8760, "2h": 4380, "4h": 2190, "1d": 365}
WARMUP = 250


def load_cfg(path):
    if not os.path.exists(path):
        path = "config.example.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def hypothetical_R(df, i, setup, max_bars, fee_pct):
    """¿Qué habría pasado si se hubiera entrado igualmente? (para medir los trades rechazados)."""
    if i + 1 >= len(df):
        return np.nan, "sin datos"
    long = setup.direction == "long"
    entry = df["open"].iat[i + 1]
    risk = abs(entry - setup.sl)
    if risk <= 0:
        return np.nan, "inválido"
    end = min(len(df), i + 1 + max_bars)
    for j in range(i + 1, end):
        h, l = df["high"].iat[j], df["low"].iat[j]
        if (long and l <= setup.sl) or (not long and h >= setup.sl):
            px, why = setup.sl, "SL"
            break
        if (long and h >= setup.tp) or (not long and l <= setup.tp):
            px, why = setup.tp, "TP"
            break
    else:
        px, why = df["close"].iat[end - 1], "tiempo"
    r = ((px - entry) if long else (entry - px)) / risk
    r -= 2 * fee_pct / 100 * entry / risk
    return r, why


def run(cfg, data: dict, fng: pd.Series, skeptic_enabled: bool, tf: str, quiet=False):
    acc = cfg["account"]
    council = Council(cfg, skeptic_enabled=skeptic_enabled)
    broker = PaperBroker(acc["starting_equity"], acc["fee_pct"], acc["slippage_pct"],
                         acc["max_bars_in_trade"], acc["wait_bars"])
    index = sorted(set().union(*[d.index for d in data.values()]))
    pos_of = {s: {t: k for k, t in enumerate(d.index)} for s, d in data.items()}
    decisions, curve, last_px = [], [], {}
    bot_log, last = {}, None
    total = len(index)

    for n, ts in enumerate(index):
        broker.new_day_check(ts)
        for sym, df in data.items():
            i = pos_of[sym].get(ts)
            if i is None:
                continue
            o, h, l, c = (df[k].iat[i] for k in ("open", "high", "low", "close"))
            broker.on_bar(sym, ts, o, h, l, c)
            last_px[sym] = c
            if i < WARMUP:
                continue
            fg = None
            if len(fng):
                prev = fng[fng.index < ts.normalize()]
                fg = int(prev.iloc[-1]) if len(prev) else None
            res = council.deliberate(sym, tf, df.iloc[: i + 1], live=False, fear_greed=fg,
                                     loss_streak=broker.loss_streak())
            if res is None:
                continue
            v, s = res["verdict"], res["setup"]
            dash_mod.log_messages(bot_log, ts, res["messages"])
            last = dash_mod.decision_payload(sym, ts, res)
            row = {"time": ts, "symbol": sym, "dir": s.direction, "decision": v.decision,
                   "score": v.score, "scout_strength": res["scout"].strength,
                   "temperature": res["senti"].temperature, "senti": res["senti"].recommendation,
                   "sim_p_tp": res["senti"].sim.get("p_tp"),
                   "vetoes": " | ".join(v.vetoes), "entry": s.entry, "sl": s.sl, "tp": s.tp,
                   "rr_to_level": s.rr_to_level, "executed": False, "risk_note": ""}
            if v.decision in ("COMPRAR", "VENDER", "ESPERAR"):
                entry_px = v.wait_price if v.decision == "ESPERAR" else s.entry
                rd = council.risk.evaluate(s, broker.equity, broker.peak, broker.day_start,
                                           broker.open_list(), sym, entry_price=entry_px)
                row["risk_note"] = " | ".join(rd.reasons)
                dash_mod.log_messages(bot_log, ts, [("risk", council.risk.message(rd, broker.equity))])
                if rd.approved:
                    meta = {"decision": v.decision, "score": v.score}
                    if v.decision == "ESPERAR":
                        broker.place_limit(sym, s, v.wait_price, rd.qty, rd.risk_amount, meta)
                    else:
                        broker.queue_market(sym, s, rd.qty, rd.risk_amount, meta)
                    row["executed"] = True
            r, why = hypothetical_R(df, i, s, acc["max_bars_in_trade"], acc["fee_pct"])
            row["hypo_R"], row["hypo_exit"] = r, why
            decisions.append(row)
        curve.append((ts, broker.mark_to_market(last_px)))
        if not quiet and n % 2000 == 0:
            print(f"  {n}/{total} velas…", file=sys.stderr)

    eq = pd.Series(dict(curve)).sort_index()
    broker.dash_extra = {"bot_log": bot_log, "last_decision": last, "prices": last_px}
    return broker, pd.DataFrame(decisions), eq


def metrics(broker, eq, start_equity, tf):
    t = pd.DataFrame(broker.trades)
    out = {"capital_final": eq.iloc[-1] if len(eq) else start_equity,
           "rentabilidad_%": (eq.iloc[-1] / start_equity - 1) * 100 if len(eq) else 0,
           "max_drawdown_%": ((eq / eq.cummax()) - 1).min() * 100 if len(eq) else 0,
           "trades": len(t)}
    rets = eq.pct_change().dropna()
    out["sharpe"] = (rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR.get(tf, 8760))) if rets.std() > 0 else 0
    if len(t):
        wins, losses = t[t.pnl > 0], t[t.pnl <= 0]
        out["acierto_%"] = len(wins) / len(t) * 100
        out["R_medio"] = t.R.mean()
        out["profit_factor"] = wins.pnl.sum() / abs(losses.pnl.sum()) if len(losses) and losses.pnl.sum() else np.inf
        out["mejor_trade"] = t.pnl.max()
        out["peor_trade"] = t.pnl.min()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--timeframe")
    ap.add_argument("--synthetic", action="store_true", help="datos inventados para probar el código")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()

    cfg = load_cfg(a.config)
    tf = a.timeframe or cfg.get("timeframe", "1h")
    symbols = a.symbols or cfg.get("symbols", ["BTCUSDT"])
    os.makedirs(a.out, exist_ok=True)

    print("Cargando datos…")
    if a.synthetic:
        data = {s: add_indicators(synthetic_klines(seed=7 + k, start_price=30000 / (k + 1)))
                for k, s in enumerate(symbols)}
        fng = pd.Series(dtype=float)
        tf = "1h"
    else:
        data = {s: add_indicators(fetch_klines(s, tf, a.start, a.end)) for s in symbols}
        fng = fetch_fear_greed(0)
    start_eq = cfg["account"]["starting_equity"]

    print("Backtest CON Skeptic…")
    b1, dec1, eq1 = run(cfg, data, fng, True, tf)
    print("Backtest SIN Skeptic (comparación)…")
    b2, _, eq2 = run(cfg, data, fng, False, tf, quiet=True)

    m1, m2 = metrics(b1, eq1, start_eq, tf), metrics(b2, eq2, start_eq, tf)
    bh = np.mean([(d["close"].iat[-1] / d["close"].iat[WARMUP] - 1) * 100 for d in data.values()])

    pd.DataFrame(b1.trades).to_csv(f"{a.out}/trades_con_skeptic.csv", index=False)
    pd.DataFrame(b2.trades).to_csv(f"{a.out}/trades_sin_skeptic.csv", index=False)
    dec1.to_csv(f"{a.out}/decisiones_skeptic.csv", index=False)

    # qué hizo el Skeptic con lo que rechazó
    rej = dec1[(dec1.decision == "PASAR") & ~dec1.vetoes.fillna("").str.contains("cortos")] if len(dec1) else dec1
    rej_valid = rej.dropna(subset=["hypo_R"])
    saved = rej_valid[rej_valid.hypo_R < 0]
    counts = dec1.decision.value_counts().to_dict() if len(dec1) else {}

    ex = b1.dash_extra
    snap = dash_mod.build(b1, {**cfg, "symbols": symbols, "timeframe": tf}, source="backtest",
                          curve=list(eq1.items()), compare_curve=list(eq2.items()),
                          bot_log=ex["bot_log"], last_decision=ex["last_decision"],
                          decisions=list(dec1.decision) if len(dec1) else [], prices=ex["prices"],
                          skeptic_saved={"rejected": int(len(rej_valid)), "would_lose": int(len(saved)),
                                         "R_avoided": float(saved.hypo_R.sum()) if len(saved) else 0.0,
                                         "R_all_rejected": float(rej_valid.hypo_R.sum()) if len(rej_valid) else 0.0,
                                         "buy_hold_pct": float(bh)})
    snap["period"] = "sintético" if a.synthetic else f"{a.start} → {a.end}"
    snap["compare_stats"] = {k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                             for k, v in metrics(b2, eq2, start_eq, tf).items()}
    dash_mod.write(snap, f"{a.out}/dashboard.json")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(eq1.index, eq1.values, label="Con Skeptic", color="#1f6feb", lw=1.6)
    ax.plot(eq2.index, eq2.values, label="Sin Skeptic", color="#d1242f", lw=1.0, alpha=0.8)
    ax.axhline(start_eq, color="grey", lw=0.8, ls="--")
    ax.set_title(f"Capital ficticio — {', '.join(symbols)} {tf}" + (" (DATOS SINTÉTICOS)" if a.synthetic else ""))
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(f"{a.out}/equity.png", dpi=130)

    def fmt(m):
        keys = ["capital_final", "rentabilidad_%", "max_drawdown_%", "trades", "acierto_%", "R_medio",
                "profit_factor", "sharpe"]
        return {k: (round(m[k], 2) if isinstance(m.get(k), (int, float, np.floating)) else m.get(k, "-"))
                for k in keys}

    f1, f2 = fmt(m1), fmt(m2)
    lines = [f"# Informe de backtest — {', '.join(symbols)} {tf}",
             f"Periodo: {'sintético' if a.synthetic else f'{a.start} → {a.end}'} · Capital inicial: {start_eq}",
             "", "| Métrica | Con Skeptic | Sin Skeptic |", "|---|---|---|"]
    lines += [f"| {k} | {f1[k]} | {f2[k]} |" for k in f1]
    lines += ["", f"Comprar y mantener (media de los activos): {bh:+.2f}%", "",
              "## Qué decidió el Skeptic", f"Alertas del Scout: {len(dec1)} → {counts}",
              f"Trades rechazados que habrían PERDIDO: {len(saved)} de {len(rej_valid)} "
              f"({(len(saved)/len(rej_valid)*100 if len(rej_valid) else 0):.0f}%), "
              f"R total evitado: {saved.hypo_R.sum():+.1f}R",
              f"R que habrían dado TODOS los rechazados: {rej_valid.hypo_R.sum():+.1f}R "
              "(si es positivo, el Skeptic es demasiado estricto)",
              "", "## Cómo leerlo",
              "- Busca: rentabilidad > comprar y mantener, profit factor > 1,3, drawdown < 20%, y al menos 100 trades.",
              "- Pocos trades = resultado poco fiable. Prueba más activos o más años.",
              "- Las noticias NO entran en el backtest (no hay histórico gratis): en vivo el filtro será algo distinto."]
    # ¿acierta la máquina del tiempo? (calibración)
    cal = dec1.dropna(subset=["sim_p_tp"]) if len(dec1) and "sim_p_tp" in dec1 else dec1.iloc[0:0]
    cal = cal[cal.hypo_exit.isin(["TP", "SL", "tiempo"])] if len(cal) else cal
    if len(cal) >= 30:
        cal = cal.assign(real=(cal.hypo_exit == "TP") * 100,
                         tramo=pd.cut(cal.sim_p_tp, [0, 25, 35, 45, 55, 100], labels=["<25%", "25-35%", "35-45%", "45-55%", ">55%"]))
        tb = cal.groupby("tramo", observed=True).agg(casos=("real", "size"), predicho=("sim_p_tp", "mean"),
                                                     real=("real", "mean")).round(1)
        err = (tb.predicho - tb.real).abs().mul(tb.casos).sum() / tb.casos.sum()
        lines += ["", "## 🕰️ ¿Acierta la máquina del tiempo?",
                  "Compara la probabilidad que dio con lo que pasó de verdad (todas las alertas, se operaran o no).",
                  tb.reset_index().to_markdown(index=False),
                  f"Error medio: {err:.1f} puntos. " + ("Bastante fiable." if err < 7 else
                  "Todavía no es fiable: que siga siendo solo informativa.")]
    if a.synthetic:
        lines.insert(1, "**⚠️ DATOS SINTÉTICOS: este informe solo prueba que el código funciona. No dice nada del mercado.**")
    report = "\n".join(lines)
    with open(f"{a.out}/informe.md", "w") as f:
        f.write(report)
    print("\n" + report)
    print(f"\nArchivos en {a.out}/: informe.md, equity.png, trades_*.csv, decisiones_skeptic.csv")


if __name__ == "__main__":
    main()
