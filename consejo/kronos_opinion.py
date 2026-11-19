"""Kronos como segunda opinión de la máquina del tiempo (reel 10). OPCIONAL.

Kronos (shiyu-coder/Kronos, Universidad Tsinghua) es un modelo entrenado con velas de muchos
mercados que dibuja futuros posibles del precio. Aquí se usa igual que la máquina del tiempo:
se le piden N futuros y se cuenta en cuántos toca antes el objetivo que el stop.

Igual que la máquina del tiempo, NO vota en el Skeptic hasta que la calibración demuestre que
acierta (error de menos de 7 puntos con 200 casos o más). Ningún modelo adivina el mercado:
se mide, no se le cree.

Necesita PyTorch y el repo de Kronos clonado en tu ordenador. El resto del bot funciona sin él.
"""

from __future__ import annotations

import os
import sys

_PREDICTOR = None


def cargar(ruta_repo: str, modelo: str = "NeoQuasar/Kronos-small",
           tokenizer: str = "NeoQuasar/Kronos-Tokenizer-base", dispositivo: str = "cpu"):
    """Carga Kronos una sola vez. Devuelve None si no está instalado."""
    global _PREDICTOR
    if _PREDICTOR is not None:
        return _PREDICTOR
    if not ruta_repo or not os.path.isdir(ruta_repo):
        return None
    sys.path.insert(0, ruta_repo)
    try:
        from model import Kronos, KronosPredictor, KronosTokenizer  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"Kronos no disponible: {e}")
        return None
    tok = KronosTokenizer.from_pretrained(tokenizer)
    mod = Kronos.from_pretrained(modelo)
    _PREDICTOR = KronosPredictor(mod, tok, device=dispositivo, max_context=512)
    return _PREDICTOR


def futuros(predictor, velas: list, paso, horizonte: int = 48, caminos: int = 10) -> list[list[tuple]]:
    """Pide a Kronos `caminos` futuros de `horizonte` velas. Cada vela: (máximo, mínimo, cierre)."""
    import pandas as pd  # solo hace falta con Kronos
    hist = velas[-400:]
    df = pd.DataFrame({"open": [v.o for v in hist], "high": [v.h for v in hist], "low": [v.l for v in hist],
                       "close": [v.c for v in hist], "volume": [v.v for v in hist],
                       "amount": [v.v * v.c for v in hist]})
    x_t = pd.Series(pd.to_datetime([v.t for v in hist]))
    y_t = pd.Series(pd.to_datetime([hist[-1].t + paso * (k + 1) for k in range(horizonte)]))
    out = []
    for _ in range(caminos):
        p = predictor.predict(df=df, x_timestamp=x_t, y_timestamp=y_t, pred_len=horizonte,
                              T=1.0, top_p=0.9, sample_count=1, verbose=False)
        out.append(list(zip(p["high"].tolist(), p["low"].tolist(), p["close"].tolist())))
    return out


def probabilidad(caminos: list[list[tuple]], stop: float, objetivo: float, dir_: str) -> dict:
    """En cuántos futuros toca antes el objetivo que el stop (si toca los dos en la misma vela, cuenta el stop)."""
    tp = sl = 0
    for camino in caminos:
        for h, l, _ in camino:
            if (dir_ == "long" and l <= stop) or (dir_ == "short" and h >= stop):
                sl += 1
                break
            if (dir_ == "long" and h >= objetivo) or (dir_ == "short" and l <= objetivo):
                tp += 1
                break
    n = len(caminos) or 1
    return {"paths": len(caminos), "p_tp": 100 * tp / n, "p_sl": 100 * sl / n,
            "p_none": 100 * (n - tp - sl) / n, "fuente": "Kronos"}
