"""Ballenas en vivo (BTC/ETH en Binance y grandes fondos del COT) con su cartera de COPIA en papel.

    py ballenas_vivo.py            # se queda vigilando (Binance cada 20 s, COT cada 6 h)
    py ballenas_vivo.py --una-vez  # una pasada (para probar)

- Avisa por Telegram de las órdenes gigantes y de las señales de compra de ballenas.
- Copia esas compras en papel con los parámetros del backtest (salida/copia_cripto.json y
  salida/copia_mercados.json) y avisa de cada entrada y cierre. /ballenas en Telegram lo resume.
- Graba todo en datos/ballenas/ para poder ajustar los umbrales con datos reales.
No lee comandos de Telegram (eso lo hace papel.py). NUNCA dinero real.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from consejo import backtest, mesas, seguridad
from consejo import ballenas_mercado as bm
from consejo.copia import CarteraCopia
from consejo.indicadores import atr
from consejo.telegram import Grupo

RAIZ = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(RAIZ, "salida")
DATOS = os.path.join(RAIZ, "datos", "ballenas")
RUTA_ESTADO = os.path.join(SALIDA, "estado_ballenas.json")
AVISO_GRANDE_USD = 1_000_000          # una sola orden de más de 1 M$ se avisa siempre
MAX_BARRAS_COT = 120                  # la copia del COT dura como mucho 5 días (velas de 1 h)


def velas(simbolo: str):
    import papel
    return papel.bajar_velas(simbolo, "1h")


def precio_y_atr(simbolo: str):
    vs = velas(simbolo)
    if len(vs) < 20:
        return None, None, vs
    return vs[-1].c, atr(vs)[-1], vs


def actualizar_copia(cartera: CarteraCopia, grupo: Grupo, nombre: str) -> None:
    """Pasa por las velas cerradas nuevas de cada posición copiada (stop, objetivo, tiempo)."""
    for s, p in list(cartera.d["abiertas"].items()):
        try:
            vs = velas(s)
        except Exception as e:  # noqa: BLE001
            print(f"{s}: sin velas para la copia ({e})", flush=True)
            continue
        visto = p.get("ultima_vela") or p["opened"]
        for v in vs:
            if v.t.isoformat() <= str(visto):
                continue
            p["ultima_vela"] = v.t.isoformat()
            op = cartera.actualizar(s, v.h, v.l, v.c, v.t.isoformat())
            if op:
                grupo.enviar("risk", f"🐋 {nombre} · {'🟢' if op['pnl'] > 0 else '🔴'} Cerrada la copia de {s} por "
                                     f"{op['reason']}: {op['pnl']:+.2f} $ ({op['R']:+.2f}R) · capital ficticio "
                                     f"{cartera.d['capital']:,.2f} $")
                break
    cartera.guardar()


def copiar(cartera: CarteraCopia, grupo: Grupo, nombre: str, simbolo: str, motivo: str, max_barras=None) -> None:
    try:
        precio, a, vs = precio_y_atr(simbolo)
    except Exception as e:  # noqa: BLE001
        print(f"{simbolo}: no puedo copiar ({e})", flush=True)
        return
    if not precio:
        return
    p = cartera.abrir(simbolo, precio, a, vs[-1].t.isoformat(), motivo, max_barras=max_barras)
    if p:
        p["ultima_vela"] = vs[-1].t.isoformat()
        grupo.enviar("risk", f"🐋 {nombre} · 📥 Copio en papel: compra {simbolo} a {p['entry']:.6g} · stop "
                             f"{p['stop']:.6g} · objetivo {p['tp']:.6g} · {p['usd']:,.0f} $ ficticios\nPor qué: {motivo}")
    cartera.guardar()


def pasada_binance(flujo: bm.FlujoBinance, cripto: CarteraCopia, grupo: Grupo, estado: dict) -> None:
    flujo.leer()
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for g in flujo.grandes:
        bm.guardar_linea(os.path.join(DATOS, f"binance_{hoy}.jsonl"), g)
        if g["usd"] >= AVISO_GRANDE_USD:
            grupo.enviar("scout", f"🐋 Orden gigante en {g['symbol']}: {g['lado']} de {g['usd'] / 1e6:.2f} M$ a "
                                  f"{g['precio']:,.2f} ({datetime.fromtimestamp(g['t'], tz=timezone.utc):%H:%M:%S} UTC)")
    flujo.grandes = []
    for s, h, r in flujo.horas_cerradas():
        fila = {"symbol": s, "hora": h, **{k: round(v, 2) for k, v in r.items()}}
        bm.guardar_linea(os.path.join(DATOS, f"flujo_{hoy}.jsonl"), fila)
        estado.setdefault("flujo", {})[s] = {**fila, "hora_txt": bm.hora_txt(h)}
        motivo = bm.senal_flujo(s, r)
        if motivo:
            grupo.enviar("scout", f"🐋 SEÑAL DE BALLENAS en {s} ({bm.hora_txt(h)}): {motivo}.")
            copiar(cripto, grupo, "Copia cripto", s, motivo)


def pasada_cot(cartera: CarteraCopia, cripto: CarteraCopia, grupo: Grupo, estado: dict) -> None:
    vistos = estado.setdefault("cot_visto", {})
    nuevos = []
    for codigo in bm.COT:
        try:
            r = bm.cot_resumen(codigo, bm.cot_ultimos(codigo))
        except Exception as e:  # noqa: BLE001
            print(f"COT {codigo}: {e}", flush=True)
            continue
        if not r:
            continue
        estado.setdefault("cot", {})[codigo] = r
        if vistos.get(codigo) == r["fecha"]:
            continue
        vistos[codigo] = r["fecha"]
        nuevos.append(r)
        bm.guardar_linea(os.path.join(DATOS, "cot.jsonl"), r)
        motivo = bm.senal_cot(r)
        if motivo:
            destino = cripto if r["simbolo"] in mesas.CRIPTO else cartera
            copiar(destino, grupo, "Copia COT", r["simbolo"], motivo, max_barras=MAX_BARRAS_COT)
    if nuevos:
        grupo.enviar("scout", "🐋 Informe COT nuevo (qué hacen los grandes fondos):\n" +
                     "\n".join(bm.texto_cot(r) for r in nuevos))


def texto_ballenas(salida: str = SALIDA) -> str:
    """Resumen para /ballenas en Telegram (lee los archivos que escribe este proceso)."""
    cfg = {**backtest.CONFIG_DEFECTO}
    partes = []
    for archivo, titulo in (("copia_cripto.json", "Copia de ballenas BTC/ETH"),
                            ("copia_mercados.json", "Copia de grandes fondos (COT)"),
                            ("copia_memes.json", "Copia de ballenas de memes")):
        ruta = os.path.join(salida, archivo)
        if os.path.exists(ruta):
            if archivo == "copia_memes.json":
                with open(ruta, encoding="utf-8") as f:
                    b = json.load(f)
                partes.append(f"🐋 {titulo}: {b.get('cash', 0) + sum(p.get('usd', 0) for p in (b.get('positions') or {}).values()):,.2f} $ "
                              f"ficticios · {len(b.get('positions') or {})} abiertas · {len(b.get('trades') or [])} cerradas")
            else:
                partes.append(CarteraCopia(ruta, cfg).resumen(titulo))
    try:
        with open(os.path.join(salida, "estado_ballenas.json"), encoding="utf-8") as f:
            e = json.load(f)
    except (OSError, ValueError):
        e = {}
    for s, x in (e.get("flujo") or {}).items():
        partes.append(f"📊 {s} {x.get('hora_txt', '')}: ballenas compran {x['compra'] / 1e6:.2f} M$ · venden "
                      f"{x['venta'] / 1e6:.2f} M$")
    if e.get("cot"):
        partes.append("🏦 Último COT:\n" + "\n".join(bm.texto_cot(r) for r in e["cot"].values()))
    return "\n\n".join(partes) or "Todavía no hay datos de ballenas (arranca py ballenas_vivo.py)."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--una-vez", action="store_true")
    ap.add_argument("--cada", type=int, default=20, help="segundos entre lecturas de Binance")
    ap.add_argument("--telegram", default=os.path.join(RAIZ, "telegram.json"))
    a = ap.parse_args(argv)

    cfg = {**backtest.CONFIG_DEFECTO, "modo": "paper"}
    seguridad.comprobar_modo(cfg["modo"])
    grupo = Grupo(a.telegram)
    from consejo.avisos import Avisador
    avisos = Avisador(grupo, "ballenas")
    cripto = CarteraCopia(os.path.join(SALIDA, "copia_cripto.json"), {**cfg, **mesas.AJUSTES["cripto"]})
    mercados = CarteraCopia(os.path.join(SALIDA, "copia_mercados.json"), {**cfg, **mesas.AJUSTES["mercados"]})
    estado = {}
    if os.path.exists(RUTA_ESTADO):
        with open(RUTA_ESTADO, encoding="utf-8") as f:
            estado = json.load(f)
    flujo = bm.FlujoBinance()
    if not a.una_vez:
        grupo.enviar("scout", "🐋 Vigilancia de ballenas en marcha (BTC/ETH en Binance + grandes fondos COT). "
                              "Copio sus compras en papel. /ballenas para ver cómo van.")
        avisos.version()
    ultimo_cot = ultima_copia = 0.0
    while True:
        try:
            pasada_binance(flujo, cripto, grupo, estado)
            if time.time() - ultima_copia > 300:
                actualizar_copia(cripto, grupo, "Copia cripto")
                actualizar_copia(mercados, grupo, "Copia COT")
                ultima_copia = time.time()
            if time.time() - ultimo_cot > 6 * 3600:
                pasada_cot(mercados, cripto, grupo, estado)
                ultimo_cot = time.time()
            os.makedirs(SALIDA, exist_ok=True)
            with open(RUTA_ESTADO, "w", encoding="utf-8") as f:
                json.dump(estado, f, ensure_ascii=False, indent=1, default=str)
        except Exception as e:  # noqa: BLE001 - un fallo no tumba la vigilancia
            print(f"Error en las ballenas: {e!r}", flush=True)
            avisos.error(e, "la vigilancia de ballenas")
        if a.una_vez:
            return 0
        time.sleep(a.cada)


if __name__ == "__main__":
    sys.exit(main())
