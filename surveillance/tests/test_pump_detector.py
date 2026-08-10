"""El detector de pump debe: (1) NO marcar una serie estable/orgánica, y
(2) marcar una serie con spike de volumen + precio parabólico + reversión.

El test (1) es el falsification-check del propio detector: si marca ruido
estable como artificial, está mal calibrado (fue el error de la versión
tick-level, que marcaba hasta BTC)."""

import random

from pump_detector import (
    MIN_BASELINE,
    RECENT,
    VERDICT_ARTIFICIAL,
    VERDICT_ORGANIC,
    analyze_klines,
)

random.seed(7)


def _organic(n=200):
    """Random walk suave con volumen estacionario."""
    kl, price, t = [], 100.0, 1_700_000_000
    for _ in range(n):
        o = price
        price *= (1 + random.gauss(0, 0.01))
        c = price
        vol = random.uniform(800, 1200)
        kl.append({"ts_s": t, "open": o, "high": max(o, c) * 1.002, "low": min(o, c) * 0.998,
                   "close": c, "volume": vol, "trades": 500, "taker_buy_base": vol / 2})
        t += 3600
    return kl


def _pumped(n=200):
    """Base estable y luego un pump: volumen 20x, precio parabólico, y dump."""
    kl = _organic(n - RECENT)
    price = kl[-1]["close"]
    t = kl[-1]["ts_s"] + 3600
    # run-up parabólico con volumen enorme
    for i in range(RECENT - 2):
        o = price
        price *= 1.25
        kl.append({"ts_s": t, "open": o, "high": price * 1.05, "low": o,
                   "close": price, "volume": 20000, "trades": 9000, "taker_buy_base": 18000})
        t += 3600
    # dump: revierte
    for _ in range(2):
        o = price
        price *= 0.80
        kl.append({"ts_s": t, "open": o, "high": o, "low": price,
                   "close": price, "volume": 15000, "trades": 8000, "taker_buy_base": 3000})
        t += 3600
    return kl


def test_organico_no_dispara():
    rep = analyze_klines("TESTUSDT", _organic())
    assert rep.verdict == VERDICT_ORGANIC, rep.as_dict()
    assert rep.score < 0.28


def test_pump_se_detecta():
    rep = analyze_klines("PUMPUSDT", _pumped())
    assert rep.verdict == VERDICT_ARTIFICIAL, rep.as_dict()
    assert rep.score >= 0.55


def test_serie_corta_no_dispara():
    rep = analyze_klines("XUSDT", _organic(MIN_BASELINE))  # muy corta
    assert rep.verdict == VERDICT_ORGANIC
