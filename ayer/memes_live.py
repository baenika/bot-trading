"""MESA MEMECOINS en vivo, con DINERO FICTICIO.

Hace dos cosas a la vez:
  1. Opera en papel con datos reales de Solana (DexScreener + RugCheck).
  2. GRABA todo lo que ve en data/memes/ (incluidas las monedas que luego hacen rug).
     Con esa grabación, memes_backtest.py prueba estrategias distintas sin trampas.

Uso:
  python memes_live.py            # se queda funcionando
  python memes_live.py --once     # una sola pasada
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

import yaml

from council.dashboard import log_messages, write
from council.private_chat import PrivateChat
from council.telegram import TelegramGroup
from memes.engine import MemeEngine
from memes.sources import DexScreener, RugCheck

STATE = "state_memes.json"
DASH = "dashboard_memes.json"
REC_DIR = os.path.join("data", "memes")


def load_cfg(path):
    with open(path if os.path.exists(path) else "config.example.yaml") as f:
        return yaml.safe_load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=None)
    a = ap.parse_args()
    cfg = load_cfg(a.config)
    mc = cfg.get("memes", {})
    every = a.every or mc.get("every_seconds", 60)

    # Telegram: los mismos 6 bots, en el grupo de memes (o en el mismo grupo si no pones otro)
    tcfg = dict(cfg.get("telegram", {}))
    tcfg["chat_id"] = mc.get("group_chat_id") or tcfg.get("chat_id")
    tg = TelegramGroup(tcfg)
    pc = PrivateChat({"telegram": {"private_bot_token": mc.get("private_bot_token", ""),
                                   "private_chat_id": cfg.get("telegram", {}).get("private_chat_id", "")}})

    eng = MemeEngine(mc.get("params", {}), mc.get("starting_equity", 1000))
    extra = {"bot_log": {}, "watch": {}, "safety": {}, "debated": []}
    if os.path.exists(STATE):
        with open(STATE) as f:
            st = json.load(f)
        eng.broker.load_json(STATE + ".broker")
        eng.kills.update(st.get("kills", {}))
        eng.decisions.update(st.get("decisions", {}))
        eng.curve = st.get("curve", [])
        eng.stopped = st.get("stopped", False)
        eng.last_decision = st.get("last_decision")
        extra.update({k: st.get(k, extra[k]) for k in extra})

    ds, rc = DexScreener(), RugCheck()
    rc.cache.update(extra["safety"])
    os.makedirs(REC_DIR, exist_ok=True)
    pc.send("🪙 Mesa Memecoins en marcha (papel). /panel para ver cómo va.")
    last_summary = time.time()

    def get_safety(m):
        s = rc.safety(m)
        extra["safety"][m] = s
        return s

    while True:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new = ds.latest_mints(mc.get("scan_limit", 60))
        # lista de vigilancia: todo lo que pasó el Scout en las últimas 72 h (para grabar su evolución)
        cutoff = time.time() - 72 * 3600
        extra["watch"] = {m: t for m, t in extra["watch"].items() if t > cutoff}
        mints = list(dict.fromkeys(new + list(eng.broker.positions) + list(extra["watch"])))[:600]
        pairs = ds.pairs_for(mints)
        for c in pairs:
            if eng.desk.scout(c):
                extra["watch"].setdefault(c["mint"], time.time())

        events, debates = eng.step(ts, pairs, get_safety)

        # grabación para el backtest
        got = {c["mint"] for c in pairs}
        with open(os.path.join(REC_DIR, ts[:10] + ".jsonl"), "a") as f:
            for c in pairs:
                f.write(json.dumps({"ts": ts, "c": c, "s": rc.cache.get(c["mint"])}) + "\n")
            for m in set(mints) - got:
                f.write(json.dumps({"ts": ts, "gone": m}) + "\n")

        # Telegram: solo se debate lo que pasa los filtros on-chain (el resto va al resumen)
        for rv, size in debates:
            # al grupo solo van las compras y los casi-compras (una vez por moneda), para no saturar
            close_call = rv.score >= eng.p["min_score"] - 10 and rv.c["mint"] not in extra["debated"]
            if rv.decision != "COMPRAR" and not close_call:
                continue
            extra["debated"] = (extra["debated"] + [rv.c["mint"]])[-2000:]
            msgs = eng.desk.messages(rv, size)
            tg.conversation(msgs)
            log_messages(extra["bot_log"], ts, msgs)
            if rv.decision == "COMPRAR":
                pc.send(f"🪙 <b>COMPRAR</b> ${rv.c.get('symbol')} · puntuación {rv.score:.0f} · "
                        f"{(size or 0):,.2f} $ (papel)\n{rv.c.get('url') or ''}")
        for kind, x in events:
            if kind == "close":
                txt = (f"{'🟢' if x['pnl'] > 0 else '🔴'} <b>Cerrado {x['symbol']}</b> por {x['reason']}: "
                       f"{x['pnl']:+.2f} $ ({x['pnl_pct']:+.0f}%)\nCapital ficticio: {eng.broker.equity():,.2f} $")
                tg.send("risk", "🪙 " + txt)
                pc.send("🪙 " + txt)
                log_messages(extra["bot_log"], ts, [("risk", txt)])
        if time.time() - last_summary > 3600 and eng.kills:
            top = "\n".join(f"• {k}: {v}" for k, v in eng.kills.most_common(6))
            tg.send("news", f"🪙 <b>Descartes de la última hora</b> (acumulado)\n{top}")
            last_summary = time.time()

        snap = eng.snapshot("live", "Solana · memecoins", extra["bot_log"])
        write(snap, DASH)
        eng.broker.to_json(STATE + ".broker")
        with open(STATE, "w") as f:
            json.dump({"kills": eng.kills, "decisions": eng.decisions, "curve": eng.curve[-5000:],
                       "stopped": eng.stopped, "last_decision": eng.last_decision, **extra}, f, default=str)

        state = {"stopped": eng.stopped}
        pc.handle(pc.poll(), snap, state)
        eng.stopped = state["stopped"]
        if a.once:
            break
        t_end = time.time() + every
        while time.time() < t_end:
            cmds = pc.poll()
            if cmds:
                pc.handle(cmds, eng.snapshot("live", "Solana · memecoins", extra["bot_log"]), state)
                eng.stopped = state["stopped"]
            time.sleep(3)


if __name__ == "__main__":
    main()
