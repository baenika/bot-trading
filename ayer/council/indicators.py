"""Indicadores técnicos. Todo se calcula solo con datos pasados (sin mirar el futuro)."""
import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Añade columnas de indicadores a un DataFrame OHLCV (open, high, low, close, volume)."""
    df = df.copy()
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["rsi"] = rsi(df["close"], 14)
    df["atr"] = atr(df, 14)
    # z-score del volumen frente a las 50 velas anteriores (sin incluir la actual)
    vol_mean = df["volume"].shift(1).rolling(50).mean()
    vol_std = df["volume"].shift(1).rolling(50).std()
    df["vol_z"] = ((df["volume"] - vol_mean) / vol_std.replace(0, np.nan)).fillna(0)
    # tamaño de la vela en ATRs (ATR de la vela anterior)
    df["range_atr"] = ((df["high"] - df["low"]) / df["atr"].shift(1)).fillna(0)
    df["body_pct"] = (df["close"] - df["open"]) / df["open"] * 100
    # máximos / mínimos de las 20 velas anteriores
    df["hh20"] = df["high"].shift(1).rolling(20).max()
    df["ll20"] = df["low"].shift(1).rolling(20).min()
    # distancia a la EMA20 medida en ATRs (sobre-extensión)
    df["ext_atr"] = (df["close"] - df["ema20"]) / df["atr"]
    # anchura de Bollinger y su percentil en las 120 velas previas (compresión)
    mid = df["close"].rolling(20).mean()
    std = df["close"].rolling(20).std()
    df["bbw"] = (4 * std / mid).fillna(0)
    df["bbw_pct"] = df["bbw"].shift(1).rolling(120).rank(pct=True).fillna(0.5)
    # velas consecutivas del mismo color
    sign = np.sign(df["close"] - df["open"]).fillna(0)
    streak = np.zeros(len(df))
    for i in range(1, len(df)):
        s = sign.iat[i]
        streak[i] = streak[i - 1] + s if s != 0 and np.sign(streak[i - 1]) == s else s
    df["streak"] = streak
    return df
