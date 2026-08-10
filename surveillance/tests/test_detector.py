"""El detector debe separar una serie orgánica de una inflada artificialmente.

Estos tests son también el mini falsification-check del módulo: si el detector
marca ruido orgánico como artificial, está mal calibrado.
"""

import random

import pytest

from detector import (
    VERDICT_ARTIFICIAL,
    VERDICT_ORGANIC,
    analyze,
    benford_score,
)

random.seed(42)


def _organic(n=300):
    """Trades orgánicos: tamaños log-normales dispersos, precios con deriva,
    tiempos espaciados (segundos a minutos), sin ida-y-vuelta sistemática."""
    trades, t = [], 1_700_000_000.0
    price = 60000.0
    for _ in range(n):
        t += random.expovariate(1 / 45)          # ~45 s promedio entre trades
        price *= math.exp(random.gauss(0, 0.002))
        size = math.exp(random.gauss(0, 1)) * 0.05   # log-normal, disperso
        trades.append({
            "symbol": "BTCUSDT",
            "side": random.choice(["long", "short"]),
            "size": round(size, 6),
            "price": round(price, 2),
            "ts_s": t,
        })
    return trades


def _artificial(n=300):
    """Volumen inflado: tamaños redondos repetidos, ráfagas sub-segundo,
    ida-y-vuelta del mismo tamaño y precio (wash trading)."""
    trades, t = [], 1_700_000_000.0
    price = 60000.0
    for _ in range(n // 2):
        t += random.uniform(0.05, 0.4)            # ráfagas sub-segundo
        size = random.choice([100.0, 200.0, 500.0, 1000.0])  # redondos
        px = round(price, 1)
        trades.append({"symbol": "BTCUSDT", "side": "long", "size": size, "price": px, "ts_s": t})
        t += random.uniform(0.05, 0.4)
        # ida y vuelta: mismo tamaño y precio, sentido opuesto
        trades.append({"symbol": "BTCUSDT", "side": "short", "size": size, "price": px, "ts_s": t})
    return trades


import math  # noqa: E402  (usado en los generadores de arriba)


def test_organico_se_clasifica_organico():
    rep = analyze(_organic())
    assert rep.verdict == VERDICT_ORGANIC, rep.as_dict()
    assert rep.score < 0.30


def test_artificial_se_clasifica_artificial():
    rep = analyze(_artificial())
    assert rep.verdict == VERDICT_ARTIFICIAL, rep.as_dict()
    assert rep.score >= 0.60


def test_wash_cycle_detecta_ida_vuelta():
    rep = analyze(_artificial())
    wash = next(r for r in rep.rules if r.rule_id == "ARG-004")
    assert wash.score > 0.5


def test_benford_muestra_chica_no_dispara():
    r = benford_score([123.0, 456.0])  # n < 30
    assert r.score == 0.0
