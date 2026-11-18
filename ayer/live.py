"""MODO EN VIVO con dinero ficticio (paper trading). Los 6 bots hablan en tu grupo de Telegram.

Uso:
  python live.py --test     # cada bot saluda en el grupo (para comprobar los tokens)
  python live.py            # se queda vigilando el mercado
  python live.py --once     # una sola pasada (para lanzarlo con cron)

El dinero real NO está activado: primero el backtest y unas semanas en papel.
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

import yaml

from council.broker import PaperBroker
from council.council import Council
from council import dashboard as dash_mod
from council.data import fetch_fear_greed, fetch_recent
from council.indicators import add_indicators
from council.private_chat import PrivateChat
from council.telegram import NAMES, TelegramGroup

STATE = "state.json"
SEEN = "state_seen.yaml"
DASH_STATE = "state_dash.json"
DASH_FILE = "dashboard.json"


def load_cfg(path):
    with open(path if os.path.exists(path) else "config.example.yaml") as f:
        return yaml.safe_load(f)


def fmt_event(e, equity):
    if e["type"] == "open":
        p = e["pos"]
        return (f"📥 <b>Entrada {p['symbol']} {p['dir'].upper()}</b> a {p['entry']:.2f} "
                f"({p['kind']}) · SL {p['sl']:.2f} · TP {p['tp']:.2f}")
    if e["type"] == "close":
        t = e["trade"]
        icon = "🟢" if t["pnl"] > 0 else "🔴"
        return (f"{icon} <b>Cerrado {t['symbol']}</b> por {t['reason']}: {t['pnl']:+.2f} ({t['R']:+.2f}R)\n"
                f"Capital ficticio: {equity:,.2f}")
    return f"⌛ Orden de espera en {e['symbol']} cancelada: {e['why']}"


def cycle(cfg, council, broker, tg, seen, fg, dash, pc=None):
    errors = 0
    tf = cfg["timeframe"]
    for sym in cfg["symbols"]:
        try:
            df = fetch_recent(sym, tf, 1000)  # máximo de Binance: más historia para el estudio
        except Exception as e:
            print(f"[{sym}] error de datos: {e}")
            errors += 1
            continue
        last = str(df.index[-1])
        if seen.get(sym) == last:
            continue
        # procesar las velas nuevas para SL / TP / órdenes pendientes
        new = df if sym not in seen else df[df.index.astype(str) > seen[sym]]
        for ts, r in new.iterrows():
            broker.new_day_check(ts)
            for e in broker.on_bar(sym, ts, r.open, r.high, r.low, r.close):
                txt = fmt_event(e, broker.equity)
                tg.send("risk", txt)
                if pc and e["type"] in ("open", "close"):
                    pc.send(txt)
                dash_mod.log_messages(dash["bot_log"], ts, [("risk", txt)])
        dash["prices"][sym] = float(df["close"].iat[-1])
        first_time = sym not in seen
        seen[sym] = last
        if first_time:
            continue  # al arrancar no se opera la vela vieja

        if dash.get("stopped"):
            continue  # interruptor activado: no se buscan operaciones nuevas
        dfi = add_indicators(df)
        res = council.deliberate(sym, tf, dfi, live=True, fear_greed=fg, loss_streak=broker.loss_streak())
        if res is None:
            continue
        tg.conversation(res["messages"])
        dash_mod.log_messages(dash["bot_log"], df.index[-1], res["messages"])
        dash["last_decision"] = dash_mod.decision_payload(sym, df.index[-1], res)
        v, s = res["verdict"], res["setup"]
        dash["decisions"].append(v.decision)
        if pc and v.decision in ("COMPRAR", "VENDER", "ESPERAR"):
            pc.send(f"🤨 <b>{v.decision}</b> en {sym} · entrada {(v.wait_price or s.entry):.4g} · "
                    f"SL {s.sl:.4g} · TP {s.tp:.4g}\nMira el debate completo en el grupo.")
        if v.decision in ("COMPRAR", "VENDER", "ESPERAR"):
            entry = v.wait_price if v.decision == "ESPERAR" else s.entry
            rd = council.risk.evaluate(s, broker.equity, broker.peak, broker.day_start,
                                       broker.open_list(), sym, entry_price=entry)
            rtxt = council.risk.message(rd, broker.equity)
            tg.send("risk", rtxt)
            dash_mod.log_messages(dash["bot_log"], df.index[-1], [("risk", rtxt)])
            if rd.approved:
                meta = {"decision": v.decision, "score": v.score}
                if v.decision == "ESPERAR":
                    broker.place_limit(sym, s, v.wait_price, rd.qty, rd.risk_amount, meta)
                else:
                    broker.queue_market(sym, s, rd.qty, rd.risk_amount, meta)
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=60, help="segundos entre comprobaciones")
    a = ap.parse_args()

    cfg = load_cfg(a.config)
    if cfg.get("mode", "paper") != "paper":
        raise SystemExit("Solo existe el modo 'paper'. El dinero real se conecta después de validar el backtest.")
    tg = TelegramGroup(cfg.get("telegram", {}))

    if a.test:
        for bot, name in NAMES.items():
            tg.send(bot, f"{name} conectado. Listo para el consejo.")
        return

    acc = cfg["account"]
    broker = PaperBroker(acc["starting_equity"], acc["fee_pct"], acc["slippage_pct"],
                         acc["max_bars_in_trade"], acc["wait_bars"])
    if os.path.exists(STATE):
        broker.load_json(STATE)
    seen = yaml.safe_load(open(SEEN)) if os.path.exists(SEEN) else {}
    seen = seen or {}
    council = Council(cfg)
    dash = {"curve": [], "bot_log": {}, "last_decision": None, "decisions": [], "prices": {}, "stopped": False}
    pc = PrivateChat(cfg)
    fails = 0
    if os.path.exists(DASH_STATE):
        with open(DASH_STATE) as f:
            dash.update(json.load(f))
    fg, fg_time, last_day, last_snap = None, 0, None, None
    if not a.once:
        pc.send("👋 Consejo en marcha. Escribe /panel para ver el panel o /ayuda para los comandos.")
        tg.send("scout", f"Vigilando {', '.join(cfg['symbols'])} en {cfg['timeframe']}. "
                         f"Capital ficticio: {broker.equity:,.2f}")

    while True:
        if time.time() - fg_time > 3600:
            s = fetch_fear_greed(1)
            fg, fg_time = (int(s.iloc[-1]) if len(s) else None), time.time()
        today = datetime.now(timezone.utc).date()
        if last_day and today != last_day:
            t = [x for x in broker.trades if x["closed"][:10] == str(last_day)]
            pnl = sum(x["pnl"] for x in t)
            if last_snap:
                pc.send_panel(last_snap)
            tg.send("risk", f"📊 <b>Resumen {last_day}</b>: {len(t)} trades, {pnl:+.2f}. "
                            f"Capital ficticio {broker.equity:,.2f} "
                            f"({(broker.equity/acc['starting_equity']-1)*100:+.2f}% total)")
        last_day = today

        errs = cycle(cfg, council, broker, tg, seen, fg, dash, pc)
        fails = fails + 1 if errs == len(cfg["symbols"]) else 0
        if fails == 10:
            pc.send("⚠️ Llevo 10 intentos sin poder leer el mercado (¿internet o Binance caído?). "
                    "Las posiciones abiertas no se están vigilando.")
        broker.to_json(STATE)
        now = datetime.now(timezone.utc).isoformat(timespec="minutes")
        eq = broker.mark_to_market(dash["prices"])
        if not dash["curve"] or dash["curve"][-1][0][:13] != now[:13]:   # un punto por hora
            dash["curve"].append([now, eq])
        else:
            dash["curve"][-1] = [now, eq]
        dash["curve"] = dash["curve"][-5000:]
        dash["decisions"] = dash["decisions"][-2000:]
        with open(DASH_STATE, "w") as f:
            json.dump(dash, f, default=str)
        def snapshot():
            return dash_mod.build(broker, cfg, source="live", curve=dash["curve"], bot_log=dash["bot_log"],
                                  last_decision=dash["last_decision"], decisions=dash["decisions"],
                                  prices=dash["prices"], stopped=dash.get("stopped", False))
        last_snap = snapshot()
        dash_mod.write(last_snap, DASH_FILE)
        with open(SEEN, "w") as f:
            yaml.safe_dump(seen, f)
        if a.once:
            pc.handle(pc.poll(), last_snap, dash)
            break
        # mientras espera, atiende tus comandos de Telegram cada 3 segundos
        t_end = time.time() + a.every
        while time.time() < t_end:
            cmds = pc.poll()
            if cmds:
                pc.handle(cmds, last_snap, dash)
                last_snap = snapshot()
                dash_mod.write(last_snap, DASH_FILE)
                with open(DASH_STATE, "w") as f:
                    json.dump(dash, f, default=str)
            time.sleep(3)


if __name__ == "__main__":
    main()
