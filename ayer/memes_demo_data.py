"""Genera una GRABACIÓN INVENTADA de memecoins (data/memes_demo/) para probar el backtest
sin esperar días. Incluye monedas que suben, que se desinflan y que hacen rug.
NO dice nada del mercado real: solo sirve para ver que todo funciona."""
import json
import os
import random
from datetime import datetime, timedelta, timezone

random.seed(3)
OUT = os.path.join("data", "memes_demo")
os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    os.remove(os.path.join(OUT, f))

start = datetime(2026, 11, 1, tzinfo=timezone.utc)
cycles, step = 3 * 24 * 12, 5          # 3 días, un ciclo cada 5 minutos
coins = []
for i in range(260):
    kind = random.choices(["rug", "moon", "fade", "chop"], [0.35, 0.15, 0.3, 0.2])[0]
    coins.append({
        "mint": f"DEMO{i:04d}mint", "symbol": random.choice(["PEPE", "DOGE", "CAT", "FROG", "MOON", "WIF", "BONK",
                                                             "GIGA", "CHAD", "HAMS"]) + str(i),
        "born": random.randint(-200, cycles - 40), "kind": kind, "price": random.uniform(0.0002, 0.01),
        "liq": random.uniform(20_000, 250_000), "supply": 1e9, "dead": False,
        "safety": {"mint_revoked": random.random() > 0.15, "freeze_revoked": random.random() > 0.1,
                   "lp_locked_pct": random.choice([100, 100, 100, 95, 40, 0]),
                   "top10_pct": random.uniform(8, 45), "dev_pct": random.uniform(0, 6), "bundled_pct": None,
                   "rug_score": None, "risks": [], "rugged": False},
        "drift": {"rug": 0.004, "moon": 0.012, "fade": -0.004, "chop": 0.0}[kind],
        "vol": random.uniform(0.02, 0.06), "rug_at": random.randint(30, 400), "hist": [],
    })

for k in range(cycles):
    ts = (start + timedelta(minutes=k * step)).isoformat(timespec="seconds")
    rows = []
    for c in coins:
        age_cycles = k - c["born"]
        if age_cycles < 0 or c["dead"]:
            continue
        if c["kind"] == "rug" and age_cycles >= c["rug_at"]:
            c["dead"] = True
            rows.append({"ts": ts, "gone": c["mint"]})
            continue
        if c["kind"] == "moon" and age_cycles > 150:
            c["drift"] = -0.006
        c["price"] *= max(0.05, 1 + random.gauss(c["drift"], c["vol"]))
        c["liq"] *= 1 + random.gauss(0.001, 0.01)
        c["hist"].append(c["price"])
        h = c["hist"]
        chg = lambda n: (h[-1] / h[-min(n, len(h))] - 1) * 100
        vol_h1 = c["liq"] * random.uniform(0.6, 3.5)
        buys = int(random.uniform(100, 900))
        ratio = 1.8 if c["drift"] > 0 else 0.8
        mcap = c["price"] * c["supply"]
        rows.append({"ts": ts, "s": c["safety"], "c": {
            "mint": c["mint"], "symbol": c["symbol"], "name": c["symbol"], "pair": c["mint"] + "pair",
            "dex": "raydium", "url": None, "quote": "SOL", "price": c["price"], "liq": c["liq"], "mcap": mcap,
            "vol_m5": vol_h1 / 12 * random.uniform(0.5, 2), "vol_h1": vol_h1, "vol_h6": vol_h1 * 5, "vol_h24": vol_h1 * 15,
            "buys_m5": buys // 12, "sells_m5": int(buys / 12 / ratio), "buys_h1": buys, "sells_h1": int(buys / ratio),
            "chg_m5": chg(2), "chg_h1": chg(12), "chg_h6": chg(72), "chg_h24": chg(288),
            "created_ms": None, "age_min": (age_cycles + 40) * step, "socials": random.choice([0, 1, 2, 3]),
            "boosts": 0}})
    with open(os.path.join(OUT, ts[:10] + ".jsonl"), "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
print(f"Grabación inventada creada en {OUT}/ ({cycles} ciclos, {len(coins)} monedas).")
print("Ahora: python memes_backtest.py --dir data/memes_demo --grid")
