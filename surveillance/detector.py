"""Detector de manipulación conductual sobre trades de un líder.

Objetivo: distinguir crecimiento ORGÁNICO de INFLADO ARTIFICIALMENTE (pump,
wash trading) a partir de la telemetría de trades. Cada regla es una función
pura y testeable; el detector las combina en un score de manipulación y un
veredicto.

ADVERTENCIA DE ALCANCE — leer antes de usar:
    Este módulo es un instrumento de DETECCIÓN DE RIESGO, no un generador de
    señales de inversión. Un score alto significa "desconfiar / evitar", nunca
    "comprar". Distinguir movimiento natural de artificial es una bandera roja,
    no una recomendación. Nada aquí constituye asesoría financiera.

Reglas implementadas (versionadas en detection_rule):
    ARG-001  Benford      — desviación de la ley de Benford en el notional
    ARG-002  Roundedness  — exceso de tamaños redondos (órdenes fabricadas)
    ARG-003  Burst        — ráfagas temporales incompatibles con flujo orgánico
    ARG-004  WashCycle    — ida-y-vuelta del mismo tamaño (auto-trading)
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

# Frecuencia esperada del primer dígito bajo la ley de Benford
BENFORD = {d: math.log10(1 + 1 / d) for d in range(1, 10)}
# Valor crítico chi-cuadrado, 8 grados de libertad
CHI2_CRIT_05 = 15.507   # alpha 0.05
CHI2_CRIT_01 = 20.090   # alpha 0.01


def _first_digit(x: float) -> int | None:
    x = abs(x)
    if x <= 0 or math.isnan(x) or math.isinf(x):
        return None
    while x < 1:
        x *= 10
    while x >= 10:
        x /= 10
    return int(x)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class RuleResult:
    rule_id: str
    score: float                      # 0 = orgánico, 1 = fuertemente artificial
    evidence: dict = field(default_factory=dict)


def benford_score(notionals: list[float]) -> RuleResult:
    """Chi-cuadrado del primer dígito del notional vs. Benford.
    Volumen fabricado (round-tripping, valores inventados) se desvía."""
    digits = [d for n in notionals if (d := _first_digit(n)) is not None]
    n = len(digits)
    if n < 30:
        return RuleResult("ARG-001", 0.0, {"reason": "muestra insuficiente", "n": n})
    obs = Counter(digits)
    chi2 = sum((obs.get(d, 0) - BENFORD[d] * n) ** 2 / (BENFORD[d] * n) for d in range(1, 10))
    # score: 0 bajo el crítico 0.05, 1 en/encima del crítico 0.01
    score = _clamp01((chi2 - CHI2_CRIT_05) / (CHI2_CRIT_01 - CHI2_CRIT_05))
    return RuleResult("ARG-001", score, {
        "chi2": round(chi2, 3), "n": n,
        "significativo_05": chi2 > CHI2_CRIT_05,
    })


def roundedness_score(sizes: list[float]) -> RuleResult:
    """Fracción de tamaños 'redondos' (muchos ceros al final). El trading
    orgánico produce tamaños dispersos; el wash trading algorítmico repite
    números redondos. Exceso sobre ~15% esperado se penaliza."""
    vals = [s for s in sizes if s and s > 0]
    n = len(vals)
    if n < 20:
        return RuleResult("ARG-002", 0.0, {"reason": "muestra insuficiente", "n": n})

    def is_round(v: float) -> bool:
        # redondo si es múltiplo de una potencia de 10 >= 100
        for p in (1000, 100):
            if abs(v / p - round(v / p)) < 1e-9:
                return True
        return False

    frac = sum(is_round(v) for v in vals) / n
    baseline = 0.15
    score = _clamp01((frac - baseline) / (0.60 - baseline))
    return RuleResult("ARG-002", score, {"frac_redondos": round(frac, 3), "n": n})


def burst_score(timestamps_s: list[float]) -> RuleResult:
    """Detecta ráfagas: muchos trades en ventanas muy cortas, firma de
    inflado de volumen. Mide la fracción de intervalos inter-trade por debajo
    de 1 segundo."""
    ts = sorted(t for t in timestamps_s if t is not None)
    if len(ts) < 20:
        return RuleResult("ARG-003", 0.0, {"reason": "muestra insuficiente", "n": len(ts)})
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    sub_second = sum(1 for g in gaps if g < 1.0) / len(gaps)
    score = _clamp01((sub_second - 0.10) / (0.50 - 0.10))
    return RuleResult("ARG-003", score, {"frac_sub_segundo": round(sub_second, 3), "n": len(ts)})


def wash_cycle_score(trades: list[dict]) -> RuleResult:
    """Ida-y-vuelta: pares de trades del mismo símbolo y tamaño en sentidos
    opuestos dentro de una ventana corta y precio casi igual. Firma clásica de
    wash trading (comprarse a uno mismo para inflar volumen sin exposición)."""
    if len(trades) < 20:
        return RuleResult("ARG-004", 0.0, {"reason": "muestra insuficiente", "n": len(trades)})
    by_symbol: dict[str, list[dict]] = {}
    for t in trades:
        by_symbol.setdefault(t["symbol"], []).append(t)
    pairs = 0
    total = 0
    for rows in by_symbol.values():
        rows = sorted(rows, key=lambda r: r["ts_s"])
        for i, a in enumerate(rows):
            total += 1
            for b in rows[i + 1:]:
                if b["ts_s"] - a["ts_s"] > 60:
                    break
                if (a["side"] != b["side"]
                        and abs(a["size"] - b["size"]) / max(a["size"], 1) < 0.02
                        and abs(a["price"] - b["price"]) / max(a["price"], 1e-9) < 0.001):
                    pairs += 1
                    break
    frac = pairs / max(total, 1)
    score = _clamp01(frac / 0.30)
    return RuleResult("ARG-004", score, {"frac_ida_vuelta": round(frac, 3), "pares": pairs})


# Peso de cada regla en el score agregado (suman 1)
RULE_WEIGHTS = {"ARG-001": 0.30, "ARG-002": 0.20, "ARG-003": 0.20, "ARG-004": 0.30}

VERDICT_ORGANIC = "organico"
VERDICT_SUSPECT = "sospechoso"
VERDICT_ARTIFICIAL = "artificial"


@dataclass
class ManipulationReport:
    score: float
    verdict: str
    rules: list[RuleResult]

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "verdict": self.verdict,
            "rules": {r.rule_id: {"score": round(r.score, 4), **r.evidence} for r in self.rules},
        }


def analyze(trades: list[dict]) -> ManipulationReport:
    """trades: [{symbol, side ('long'/'short'), size, price, ts_s (epoch seg)}].
    Devuelve score de manipulación [0,1] y veredicto natural/artificial.

    El score NO es una señal de compra. Alto = evitar/desconfiar."""
    notionals = [t["size"] * t["price"] for t in trades]
    sizes = [t["size"] for t in trades]
    ts = [t["ts_s"] for t in trades]

    rules = [
        benford_score(notionals),
        roundedness_score(sizes),
        burst_score(ts),
        wash_cycle_score(trades),
    ]
    score = sum(RULE_WEIGHTS[r.rule_id] * r.score for r in rules)
    if score >= 0.60:
        verdict = VERDICT_ARTIFICIAL
    elif score >= 0.30:
        verdict = VERDICT_SUSPECT
    else:
        verdict = VERDICT_ORGANIC
    return ManipulationReport(score=score, verdict=verdict, rules=rules)
