"""Ballenas de memecoins en Solana: SOLO LECTURA de la blockchain pública, sin claves ni wallet.

Dos vigilancias:

1. Monedas: para cada moneda que vigila la mesa (abiertas en papel y las que pasaron el Scout),
   mira los 10 mayores poseedores y lo compara con la pasada anterior. En memes.py se leen del
   informe público de RugCheck (que los saca de la blockchain), porque el RPC público de Solana
   corta getTokenLargestAccounts con 429 enseguida.
   • 🐋🔴 un gordo vende de golpe (≥ `venta_pct` % del suministro)  → posible salida antes de un rug
   • 🐋🟢 aparece o crece un gordo (≥ `compra_pct` % del suministro) → compra fuerte
   • ⚠️ los 10 mayores (sin contar la pool) tienen ≥ `concentracion_pct` % → riesgo de que la tiren
   Para que cuente como BALLENA de verdad (y no «ha subido algo») el cambio también tiene que valer
   dinero: con el market cap de la moneda se pasa el % a dólares y hace falta mover ≥ `min_mov_usd`
   y tener ≥ `min_ballena_usd` (antes o después). Sin market cap no se avisa: solo se graba.

2. Wallets seguidas (datos/memes/ballenas.json): sus transacciones nuevas (getSignaturesForAddress
   + getTransaction) y qué tokens compraron o vendieron (diferencia de saldos antes/después).
   Se añaden a mano (`py memes.py ballena añadir <dirección> [alias]`) o solas: quien hace una
   compra fuerte en una moneda vigilada pasa a seguirse 7 días.

La fuente es la propia blockchain (no un tuit ni un canal), así que el dato está verificado por
definición. Esto solo INFORMA y graba en datos/memes/ballenas_<fecha>.jsonl: no cambia ninguna
decisión hasta que lo probemos con lo grabado. Solo se permiten métodos de lectura del RPC.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

RPC = "https://api.mainnet-beta.solana.com"
LECTURA = {"getTokenLargestAccounts", "getTokenSupply", "getMultipleAccounts", "getSignaturesForAddress",
           "getTransaction"}
# Dueños que son pools o programas (no son ballenas): autoridades de Raydium, Pump.fun, Meteora, Orca.
NO_BALLENA = {"5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1", "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL",
              "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg", "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",
              "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"}
VACIO = {"wallets": {}, "monedas": {}, "vistas": {}}


class SoloLectura(RuntimeError):
    pass


class Frenado(RuntimeError):
    """El RPC público dijo «demasiadas peticiones» (429): se deja para la pasada siguiente."""


def frenado_si_429(fn):
    """Envuelve una fuente HTTP para que un 429 cuente como Frenado (espera progresiva)."""
    def f(*a, **k):
        try:
            return fn(*a, **k)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise Frenado("la fuente pide ir más despacio (429)") from e
            raise
    return f


def rpc_publico(metodo: str, params: list, url: str = RPC, timeout: int = 20):
    if metodo not in LECTURA:
        raise SoloLectura(f"Método '{metodo}' no permitido: las ballenas solo leen la blockchain.")
    cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params}).encode()
    req = urllib.request.Request(url, data=cuerpo, headers={"Content-Type": "application/json",
                                                            "User-Agent": "bot-trading/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Frenado("el RPC público de Solana pide ir más despacio (429)") from e
        raise
    if "error" in d:
        raise RuntimeError(f"RPC {metodo}: {d['error'].get('message', d['error'])}")
    return d.get("result")


def _corta(x: str) -> str:
    return f"{x[:4]}…{x[-4:]}" if x and len(x) > 10 else str(x)


class Ballenas:
    def __init__(self, ruta: str, rpc=rpc_publico, venta_pct: float = 2.0, compra_pct: float = 2.0,
                 concentracion_pct: float = 50.0, max_monedas: int = 5, max_wallets: int = 10,
                 cada_s: int = 300, dias_auto: float = 7.0, pausa_s: float = 0.0, fuente_poseedores=None,
                 min_mov_usd: float = 10_000, min_ballena_usd: float = 25_000, min_wallet_usd: float = 5_000):
        self.ruta, self.rpc_crudo, self.pausa = ruta, rpc, pausa_s
        # quién tiene cada moneda: por defecto el RPC; en memes.py, RugCheck (el RPC público limita mucho
        # getTokenLargestAccounts). Debe devolver {dueño: % del suministro} sin pools.
        self.fuente_poseedores = fuente_poseedores
        self.espera = cada_s           # sube si el RPC frena (429) y vuelve a cada_s cuando responde
        self.venta, self.compra, self.conc = venta_pct, compra_pct, concentracion_pct
        self.max_monedas, self.max_wallets, self.cada, self.dias_auto = max_monedas, max_wallets, cada_s, dias_auto
        # umbrales en dólares (ver docstring): una ballena mueve y tiene dinero de verdad
        self.min_mov, self.min_ballena, self.min_wallet = min_mov_usd, min_ballena_usd, min_wallet_usd
        self.ultima = 0.0
        try:
            with open(ruta, encoding="utf-8") as f:
                self.d = {**json.loads(json.dumps(VACIO)), **json.load(f)}
        except (OSError, ValueError):
            self.d = json.loads(json.dumps(VACIO))

    def rpc(self, metodo: str, params: list):
        """Una llamada al RPC con pausa entre llamadas, para no pasarse del límite público."""
        if self.pausa:
            time.sleep(self.pausa)
        return self.rpc_crudo(metodo, params)

    def guardar(self) -> None:
        os.makedirs(os.path.dirname(self.ruta) or ".", exist_ok=True)
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False, indent=1)

    def seguir(self, wallet: str, alias: str = "", auto: bool = False, ahora: float | None = None) -> None:
        w = self.d["wallets"].get(wallet) or {"alias": alias or _corta(wallet), "desde": None}
        if alias:
            w["alias"] = alias
        w["hasta"] = (ahora or time.time()) + self.dias_auto * 86400 if auto and not w.get("manual") else None
        w["manual"] = w.get("manual") or not auto
        self.d["wallets"][wallet] = w

    # --- 1. poseedores de cada moneda --------------------------------------------------------

    def poseedores(self, mint: str, excluir: set) -> dict:
        """{dueño: % del suministro} de los 10 mayores, sin pools ni programas."""
        if self.fuente_poseedores:
            if self.pausa:
                time.sleep(self.pausa)
            return {w: p for w, p in self.fuente_poseedores(mint).items() if w not in NO_BALLENA and w not in excluir}
        sup = self.rpc("getTokenSupply", [mint])["value"]
        total = float(sup.get("uiAmount") or 0)
        if total <= 0:
            return {}
        cuentas = self.rpc("getTokenLargestAccounts", [mint])["value"][:10]
        info = self.rpc("getMultipleAccounts", [[c["address"] for c in cuentas], {"encoding": "jsonParsed"}])["value"]
        out: dict[str, float] = {}
        for c, a in zip(cuentas, info):
            dueno = ((((a or {}).get("data") or {}).get("parsed") or {}).get("info") or {}).get("owner")
            if not dueno or dueno in NO_BALLENA or dueno in excluir:
                continue
            out[dueno] = out.get(dueno, 0.0) + 100.0 * float(c.get("uiAmount") or 0) / total
        return out

    def revisar_moneda(self, mint: str, simbolo: str, excluir: set, ahora: float, mcap: float | None = None) -> list[dict]:
        antes = self.d["monedas"].get(mint, {}).get("top", {})
        ahora_top = self.poseedores(mint, excluir)
        ev = []
        usd = (lambda pct: round(pct / 100 * mcap, 0)) if mcap else (lambda pct: None)
        if antes:
            for w in set(antes) | set(ahora_top):
                dif = ahora_top.get(w, 0.0) - antes.get(w, 0.0)
                # si salió del top 10 no sabemos cuánto le queda: solo cuenta como venta si era gordo
                if dif <= -self.venta and (w in ahora_top or antes[w] >= self.venta * 2):
                    tipo = "venta"
                elif dif >= self.compra:
                    tipo = "compra"
                else:
                    continue
                x = {"tipo": tipo, "mint": mint, "symbol": simbolo, "wallet": w,
                     "antes": round(antes.get(w, 0), 2), "despues": round(ahora_top.get(w, 0), 2),
                     "usd_antes": usd(antes.get(w, 0)), "usd_despues": usd(ahora_top.get(w, 0)),
                     "usd_mov": usd(abs(dif)), "mcap": mcap}
                if tipo == "venta":
                    x["fuera_top"] = w not in ahora_top
                if not self.es_ballena(x):
                    x["tipo"] = "movimiento"      # se graba, pero no se avisa ni se copia ni se sigue
                    x["lado"] = tipo
                ev.append(x)
        top10 = sum(ahora_top.values())
        estaba = self.d["monedas"].get(mint, {}).get("concentrada", False)
        if top10 >= self.conc and not estaba:
            ev.append({"tipo": "concentracion", "mint": mint, "symbol": simbolo, "top10": round(top10, 1)})
        self.d["monedas"][mint] = {"symbol": simbolo, "top": {w: round(p, 3) for w, p in ahora_top.items()},
                                   "concentrada": top10 >= self.conc, "t": ahora}
        return ev

    def es_ballena(self, x: dict) -> bool:
        """Ballena = mueve ≥ min_mov_usd y tiene (antes o después) ≥ min_ballena_usd. Sin precio: no."""
        if x.get("usd_mov") is None:
            return False
        return x["usd_mov"] >= self.min_mov and max(x["usd_antes"] or 0, x["usd_despues"] or 0) >= self.min_ballena

    # --- 2. wallets seguidas -----------------------------------------------------------------

    def revisar_wallet(self, wallet: str) -> list[dict]:
        w = self.d["wallets"][wallet]
        firmas = self.rpc("getSignaturesForAddress", [wallet, {"limit": 10}]) or []
        ultima = self.d["vistas"].get(wallet)
        self.d["vistas"][wallet] = firmas[0]["signature"] if firmas else ultima
        if ultima is None:            # primera vez: solo apunta dónde está, no avisa del pasado
            return []
        nuevas = []
        for f in firmas:
            if f["signature"] == ultima:
                break
            if not f.get("err"):
                nuevas.append(f["signature"])
        ev = []
        for sig in reversed(nuevas[:5]):
            tx = self.rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            for mint, dif in cambios_tokens(tx, wallet).items():
                ev.append({"tipo": "wallet_compra" if dif > 0 else "wallet_venta", "wallet": wallet,
                           "alias": w.get("alias"), "mint": mint, "cantidad": dif, "firma": sig})
        return ev

    # --- pasada ------------------------------------------------------------------------------

    def pasada(self, monedas: dict, excluir: set | None = None, ahora: float | None = None,
               forzar: bool = False, mercado: dict | None = None) -> list[dict]:
        """`monedas` = {mint: símbolo} que vigila la mesa (abiertas primero). `mercado` = {mint: {"mcap",
        "price"}} para pasar los movimientos a dólares. Devuelve los eventos."""
        mercado = mercado or {}
        ahora = ahora or time.time()
        if not forzar and ahora - self.ultima < self.espera:
            return []
        self.ultima = ahora
        excluir = excluir or set()
        ev: list[dict] = []
        vivas = set(monedas)
        self.d["monedas"] = {m: x for m, x in self.d["monedas"].items() if m in vivas}
        # por turnos: primero las que nunca se miraron, luego las que llevan más tiempo sin mirarse
        turno = sorted(monedas, key=lambda m: self.d["monedas"].get(m, {}).get("t", 0))[:self.max_monedas]
        frenado = False
        for mint in turno:
            try:
                ev += self.revisar_moneda(mint, monedas[mint], excluir, ahora, (mercado.get(mint) or {}).get("mcap"))
            except Frenado:
                frenado = True
                break
            except Exception as e:  # noqa: BLE001 - una moneda que falla no para las demás
                print(f"Ballenas: no pude leer {monedas[mint]}: {e}", flush=True)
        for x in ev:
            if x["tipo"] == "compra":
                self.seguir(x["wallet"], f"ballena de {x['symbol']}", auto=True, ahora=ahora)
        self.d["wallets"] = {k: w for k, w in self.d["wallets"].items()
                             if w.get("manual") or (w.get("hasta") or 0) > ahora}
        for wallet in ([] if frenado else list(self.d["wallets"])[:self.max_wallets]):
            try:
                for x in self.revisar_wallet(wallet):
                    precio = (mercado.get(x["mint"]) or {}).get("price")
                    x["usd"] = round(abs(x["cantidad"]) * precio, 0) if precio else None
                    # una wallet que sigues a mano se avisa siempre; las automáticas solo si mueven dinero
                    if not self.d["wallets"][wallet].get("manual") and (x["usd"] or 0) < self.min_wallet:
                        x["lado"], x["tipo"] = x["tipo"], "movimiento"
                    ev.append(x)
            except Frenado:
                frenado = True
                break
            except Exception as e:  # noqa: BLE001
                print(f"Ballenas: no pude leer la wallet {_corta(wallet)}: {e}", flush=True)
        if frenado:
            self.espera = min(self.espera * 2, 3600)
            print(f"Ballenas: el RPC público frena (429); vuelvo a mirar en {self.espera // 60:.0f} min", flush=True)
        else:
            self.espera = self.cada
        self.guardar()
        return ev


def cambios_tokens(tx: dict | None, wallet: str) -> dict:
    """{mint: cambio de saldo} de `wallet` en una transacción (pre/postTokenBalances)."""
    meta = (tx or {}).get("meta") or {}
    saldo: dict[str, float] = {}
    for clave, signo in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for b in meta.get(clave) or []:
            if b.get("owner") != wallet:
                continue
            ui = (b.get("uiTokenAmount") or {}).get("uiAmount") or 0
            saldo[b["mint"]] = saldo.get(b["mint"], 0.0) + signo * float(ui)
    return {m: round(v, 6) for m, v in saldo.items() if abs(v) > 1e-9}


def _usd(v) -> str:
    if v is None:
        return "?"
    return f"{v / 1e6:.1f} M$" if v >= 1e6 else f"{v / 1e3:.0f}k $" if v >= 1e3 else f"{v:.0f} $"


def texto(x: dict, simbolos: dict) -> str:
    sim = x.get("symbol") or simbolos.get(x.get("mint"), _corta(x.get("mint", "")))
    if x["tipo"] == "venta":
        fin = " y sale del top 10" if x.get("fuera_top") else ""
        return (f"🐋🔴 {sim}: la ballena {_corta(x['wallet'])} vende {_usd(x.get('usd_mov'))} "
                f"({x['antes']:.1f} % → {x['despues']:.1f} % del suministro){fin}. Antes tenía {_usd(x.get('usd_antes'))}. "
                "Ojo: puede ser una salida antes de un rug.")
    if x["tipo"] == "compra":
        return (f"🐋🟢 {sim}: la ballena {_corta(x['wallet'])} compra {_usd(x.get('usd_mov'))} "
                f"({x['antes']:.1f} % → {x['despues']:.1f} % del suministro) y ya tiene {_usd(x.get('usd_despues'))}. "
                f"Es ballena porque mueve más de 10k $ y tiene más de 25k $ (market cap {_usd(x.get('mcap'))}). "
                "La sigo 7 días.")
    if x["tipo"] == "concentracion":
        return f"⚠️ {sim}: los 10 mayores (sin la pool) tienen el {x['top10']:.0f} % del suministro."
    lado = x.get("lado", x["tipo"])
    verbo = "compra" if lado in ("wallet_compra", "compra") else "vende"
    valor = f" (≈ {_usd(x['usd'])})" if x.get("usd") else ""
    return f"🐋 {x.get('alias') or _corta(x['wallet'])} {verbo} {abs(x.get('cantidad') or 0):,.0f} de {sim}{valor}."
