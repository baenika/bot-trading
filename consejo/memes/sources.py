"""Datos de memecoins (solo LECTURA) en Solana (APIs públicas, sin claves):

  • DexScreener: pares nuevos, precio, liquidez, volumen, compras/ventas, capitalización.
  • RugCheck: seguridad del token (permisos de mint/freeze, liquidez bloqueada, mayores poseedores).

Todo se normaliza a diccionarios simples. Si un dato no existe, vale None (nunca se inventa).
"""
import json
import time
import urllib.parse
import urllib.request

DS = "https://api.dexscreener.com"
RC = "https://api.rugcheck.xyz/v1"
UA = {"User-Agent": "bot-trading/0.1", "Accept": "application/json"}


def _get(url, params=None, timeout=15):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read())


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def normalize_pair(p: dict) -> dict:
    """Par de DexScreener → diccionario plano."""
    base = p.get("baseToken") or {}
    tx = p.get("txns") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    created = p.get("pairCreatedAt")
    info = p.get("info") or {}
    return {
        "mint": base.get("address"), "symbol": base.get("symbol"), "name": base.get("name"),
        "pair": p.get("pairAddress"), "dex": p.get("dexId"), "url": p.get("url"),
        "quote": (p.get("quoteToken") or {}).get("symbol"),
        "price": _f(p.get("priceUsd")), "liq": _f((p.get("liquidity") or {}).get("usd")),
        "mcap": _f(p.get("marketCap")) or _f(p.get("fdv")),
        "vol_m5": _f(vol.get("m5")), "vol_h1": _f(vol.get("h1")), "vol_h6": _f(vol.get("h6")),
        "vol_h24": _f(vol.get("h24")),
        "buys_m5": (tx.get("m5") or {}).get("buys"), "sells_m5": (tx.get("m5") or {}).get("sells"),
        "buys_h1": (tx.get("h1") or {}).get("buys"), "sells_h1": (tx.get("h1") or {}).get("sells"),
        "chg_m5": _f(chg.get("m5")), "chg_h1": _f(chg.get("h1")), "chg_h6": _f(chg.get("h6")),
        "chg_h24": _f(chg.get("h24")),
        "created_ms": created,
        "age_min": (time.time() * 1000 - created) / 60000 if created else None,
        "socials": len(info.get("socials") or []) + len(info.get("websites") or []),
        "boosts": ((p.get("boosts") or {}).get("active")) or 0,
    }


class DexScreener:
    def latest_mints(self, limit=60) -> list:
        """Tokens de Solana que acaban de aparecer o de promocionarse (perfiles y boosts)."""
        mints = []
        for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1"):
            try:
                for t in _get(DS + path):
                    if t.get("chainId") == "solana" and t.get("tokenAddress") not in mints:
                        mints.append(t["tokenAddress"])
            except Exception:
                continue
        return mints[:limit]

    def pairs_for(self, mints: list) -> list:
        """Mejor par (más liquidez) de cada token. Hasta 30 tokens por llamada."""
        best = {}
        for i in range(0, len(mints), 30):
            chunk = ",".join(mints[i:i + 30])
            try:
                data = _get(f"{DS}/tokens/v1/solana/{chunk}")
            except Exception:
                continue
            pairs = data if isinstance(data, list) else data.get("pairs") or []
            for p in pairs:
                n = normalize_pair(p)
                if not n["mint"]:
                    continue
                if n["mint"] not in best or (n["liq"] or 0) > (best[n["mint"]]["liq"] or 0):
                    best[n["mint"]] = n
            time.sleep(0.25)
        return list(best.values())


class RugCheck:
    """Seguridad del token. Se guarda en caché: no cambia cada minuto."""

    def __init__(self):
        self.cache = {}

    def safety(self, mint: str) -> dict:
        if mint in self.cache:
            return self.cache[mint]
        out = {"mint_revoked": None, "freeze_revoked": None, "lp_locked_pct": None, "top10_pct": None,
               "dev_pct": None, "bundled_pct": None, "rug_score": None, "risks": [], "rugged": None}
        try:
            d = _get(f"{RC}/tokens/{mint}/report", timeout=20)
        except Exception:
            self.cache[mint] = out
            return out
        tok = d.get("token") or {}
        out["mint_revoked"] = tok.get("mintAuthority") in (None, "") if "mintAuthority" in tok else None
        out["freeze_revoked"] = tok.get("freezeAuthority") in (None, "") if "freezeAuthority" in tok else None
        markets = d.get("markets") or []
        locks = [((m.get("lp") or {}).get("lpLockedPct")) for m in markets]
        locks = [_f(x) for x in locks if _f(x) is not None]
        out["lp_locked_pct"] = max(locks) if locks else None
        holders = d.get("topHolders") or []
        if holders:
            pcts = [_f(h.get("pct")) or 0 for h in holders[:10]]
            out["top10_pct"] = sum(pcts)
        creator = d.get("creator")
        if creator and holders:
            out["dev_pct"] = sum(_f(h.get("pct")) or 0 for h in holders if h.get("owner") == creator)
        out["rug_score"] = _f(d.get("score_normalised") or d.get("score"))
        out["risks"] = [r.get("name") for r in (d.get("risks") or []) if r.get("name")]
        out["rugged"] = bool(d.get("rugged")) if "rugged" in d else None
        self.cache[mint] = out
        return out

    def poseedores(self, mint: str) -> dict:
        """{dueño: % del suministro} de los mayores poseedores, SIN caché (para ver quién vende o compra).
        Quita pools y cuentas conocidas de programas (knownAccounts de RugCheck). Si falla, lanza la excepción."""
        d = _get(f"{RC}/tokens/{mint}/report", timeout=20)
        conocidas = set((d.get("knownAccounts") or {}).keys())
        for m in d.get("markets") or []:
            conocidas.update(x for x in (m.get("pubkey"), m.get("liquidityA"), m.get("liquidityB"),
                                         m.get("liquidityAAccount"), m.get("liquidityBAccount")) if x)
        out = {}
        for h in (d.get("topHolders") or [])[:10]:
            dueno, pct = h.get("owner"), _f(h.get("pct"))
            if not dueno or pct is None or dueno in conocidas or h.get("address") in conocidas:
                continue
            out[dueno] = out.get(dueno, 0.0) + pct
        return out

