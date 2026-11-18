"""Descarga de datos: velas (Binance, API pública sin claves), índice Fear & Greed y titulares RSS."""
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd
import requests

BINANCE = "https://api.binance.com/api/v3/klines"
FNG = "https://api.alternative.me/fng/"
UA = {"User-Agent": "consejo-trading/1.0"}

TF_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
         "4h": 14_400_000, "1d": 86_400_000}


def _to_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _klines_to_df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume",
                                     "ct", "qv", "n", "tb", "tq", "x"])
    df = df[["t", "open", "high", "low", "close", "volume", "ct"]].copy()
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["time"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df.set_index("time")


def fetch_klines(symbol: str, interval: str, start: str, end: str, cache_dir: str = "data") -> pd.DataFrame:
    """Velas históricas entre start y end (AAAA-MM-DD). Se guardan en caché CSV."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{symbol}_{interval}_{start}_{end}.csv")
    if os.path.exists(path):
        return pd.read_csv(path, index_col="time", parse_dates=["time"])
    start_ms, end_ms = _to_ms(start), _to_ms(end)
    rows, cur = [], start_ms
    while cur < end_ms:
        r = requests.get(BINANCE, params={"symbol": symbol, "interval": interval, "startTime": cur,
                                          "endTime": end_ms, "limit": 1000}, headers=UA, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows += batch
        cur = batch[-1][0] + TF_MS.get(interval, 3_600_000)
        time.sleep(0.25)
    df = _klines_to_df(rows)[["open", "high", "low", "close", "volume"]]
    df.to_csv(path)
    return df


def fetch_recent(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    """Últimas velas CERRADAS (descarta la vela que aún está en curso)."""
    r = requests.get(BINANCE, params={"symbol": symbol, "interval": interval, "limit": limit},
                     headers=UA, timeout=20)
    r.raise_for_status()
    df = _klines_to_df(r.json())
    now_ms = int(time.time() * 1000)
    df = df[df["ct"] < now_ms]
    return df[["open", "high", "low", "close", "volume"]]


def fetch_fear_greed(limit: int = 0) -> pd.Series:
    """Índice Fear & Greed diario (0 = miedo extremo, 100 = codicia extrema). limit=0 => todo el historial."""
    try:
        r = requests.get(FNG, params={"limit": limit, "format": "json"}, headers=UA, timeout=20)
        r.raise_for_status()
        data = r.json()["data"]
        s = pd.Series({pd.to_datetime(int(d["timestamp"]), unit="s", utc=True).normalize(): int(d["value"])
                       for d in data})
        return s.sort_index()
    except Exception:
        return pd.Series(dtype=float)


def fetch_headlines(feeds, max_age_hours: float = 12) -> list:
    """Titulares recientes de feeds RSS. Devuelve [{title, summary, source, published}]."""
    out, now = [], datetime.now(timezone.utc)
    for url in feeds:
        try:
            r = requests.get(url, headers=UA, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                desc = (item.findtext("description") or "").strip()
                pub = item.findtext("pubDate")
                try:
                    published = parsedate_to_datetime(pub) if pub else now
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                except Exception:
                    published = now
                if (now - published).total_seconds() / 3600 <= max_age_hours:
                    out.append({"title": title, "summary": desc[:300], "source": url.split("/")[2],
                                "published": published})
        except Exception:
            continue
    return sorted(out, key=lambda h: h["published"], reverse=True)


def synthetic_klines(n: int = 6000, seed: int = 7, start_price: float = 30000.0) -> pd.DataFrame:
    """Velas inventadas con tendencias, rupturas y picos de volumen. SOLO para probar el código."""
    rng = np.random.default_rng(seed)
    price, rows = start_price, []
    drift, vol = 0.0, 0.006
    for _ in range(n):
        if rng.random() < 0.01:
            drift = rng.normal(0, 0.0015)
        if rng.random() < 0.01:
            vol = float(np.clip(rng.normal(0.006, 0.002), 0.002, 0.015))
        shock = rng.random() < 0.015
        ret = drift + rng.normal(0, vol) + (rng.normal(0, 0.03) if shock else 0)
        o = price
        c = o * (1 + ret)
        h = max(o, c) * (1 + abs(rng.normal(0, vol / 2)))
        l = min(o, c) * (1 - abs(rng.normal(0, vol / 2)))
        v = abs(rng.normal(1000, 200)) * (rng.uniform(3, 6) if shock else 1)
        rows.append((o, h, l, c, v))
        price = c
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx).rename_axis("time")
