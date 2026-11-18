"""Estructuras que se pasan los bots entre sí."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScoutReport:
    alert: bool
    direction: str = ""          # "long" o "short"
    strength: float = 0.0        # 0-100
    reasons: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


@dataclass
class NewsReport:
    available: bool
    alignment: str = "sin_datos"  # confirma | contradice | sin_catalizador | sin_datos
    sentiment: float = 0.0        # -1 a 1
    explanation: str = ""
    headlines: list = field(default_factory=list)


@dataclass
class Setup:
    direction: str
    entry: float
    sl: float
    tp: float
    rr: float
    risk_pct: float               # distancia al SL en % del precio
    resistance: Optional[float] = None
    rr_to_level: Optional[float] = None
    pullback_entry: Optional[float] = None
    notes: list = field(default_factory=list)


@dataclass
class SentimentReport:
    temperature: float            # 0 frío - 100 FOMO/pánico
    label: str
    recommendation: str           # ok | esperar_retroceso | bloquear
    flags: list = field(default_factory=list)
    study: dict = field(default_factory=dict)   # estudio histórico de casos parecidos
    fear_greed: Optional[int] = None
    sim: dict = field(default_factory=dict)       # máquina del tiempo (solo informativo)


@dataclass
class Verdict:
    decision: str                 # COMPRAR | VENDER | ESPERAR | PASAR
    score: float
    pros: list = field(default_factory=list)
    cons: list = field(default_factory=list)
    vetoes: list = field(default_factory=list)
    wait_price: Optional[float] = None


@dataclass
class RiskDecision:
    approved: bool
    qty: float = 0.0
    notional: float = 0.0
    risk_amount: float = 0.0
    reasons: list = field(default_factory=list)
