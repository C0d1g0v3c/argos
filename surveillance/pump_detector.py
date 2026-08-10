"""Detección de inflado artificial (pump) sobre velas OHLCV.

CÓMO se distingue natural de artificial, y por qué contra la línea base propia:
un pump es una ANOMALÍA de la moneda contra su propio comportamiento normal,
no un valor absoluto. Por eso BTC (líquido y estable) sale orgánico y una
moneda que acaba de spikear sale marcada — sin falsos positivos en los mercados
grandes, que fue el error de la versión tick-level.

Señales (todas relativas a la ventana base de la propia serie):
  P-VOL   Volumen anómalo   — z-score del volumen reciente vs baseline
  P-ACC   Aceleración precio — retorno reciente vs volatilidad histórica
  P-DIV   Divergencia vol-precio — volumen que sube sin que el precio lo siga
                                    (churn/wash), o precio que salta con volumen
                                    concentrado en muy pocas velas
  P-REV   Reversión (pump & dump) — el spike ya se está revirtiendo

ALCANCE: instrumento de detección de riesgo, no señal de inversión. Un score
alto significa "esta subida tiene firma artificial: desconfiar", nunca "comprar".
Un score bajo NO significa "buena inversión". Nada aquí es asesoría financiera.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _mean_std(xs: list[float]) -> tuple[float, float]:
    if len(xs) < 2:
        return (xs[0] if xs else 0.0), 0.0
    m = statistics.fmean(xs)
    sd = statistics.pstdev(xs)
    return m, sd


@dataclass
class Signal:
    code: str
    score: float
    detail: dict = field(default_factory=dict)


# baseline = todas menos las últimas RECENT velas; recent = ventana del "evento"
RECENT = 6            # velas recientes que forman el posible pump
MIN_BASELINE = 24     # mínimo de velas base para tener estadística


def _volume_anomaly(klines: list[dict]) -> Signal:
    vols = [k["volume"] for k in klines]
    base, recent = vols[:-RECENT], vols[-RECENT:]
    m, sd = _mean_std(base)
    if sd == 0:
        return Signal("P-VOL", 0.0, {"reason": "sin varianza base"})
    peak_z = max((v - m) / sd for v in recent)
    # z de 3 empieza a marcar; z de 10+ es fuerte
    score = _clamp01((peak_z - 3) / (10 - 3))
    return Signal("P-VOL", score, {"peak_z": round(peak_z, 2)})


def _price_acceleration(klines: list[dict]) -> Signal:
    closes = [k["close"] for k in klines]
    base = closes[:-RECENT]
    rets = [(b / a - 1) for a, b in zip(base, base[1:]) if a]
    _, sd = _mean_std(rets)
    if sd == 0:
        return Signal("P-ACC", 0.0, {"reason": "sin volatilidad base"})
    recent_ret = closes[-1] / closes[-RECENT] - 1 if closes[-RECENT] else 0
    # retorno reciente expresado en desviaciones de la volatilidad por vela
    z = abs(recent_ret) / (sd * (RECENT ** 0.5))
    score = _clamp01((z - 2) / (8 - 2))
    return Signal("P-ACC", score, {"recent_return_pct": round(recent_ret * 100, 2), "z": round(z, 2)})


def _vol_price_divergence(klines: list[dict]) -> Signal:
    """Volumen anómalo sin movimiento de precio proporcional = churn/wash.
    Se activa solo si hay spike de volumen; mide cuánto NO lo acompaña el precio."""
    vols = [k["volume"] for k in klines]
    closes = [k["close"] for k in klines]
    base_v, _ = _mean_std(vols[:-RECENT])
    recent_v = statistics.fmean(vols[-RECENT:])
    if base_v == 0:
        return Signal("P-DIV", 0.0, {"reason": "sin base"})
    vol_mult = recent_v / base_v
    price_move = abs(closes[-1] / closes[-RECENT] - 1) if closes[-RECENT] else 0
    if vol_mult < 3:
        return Signal("P-DIV", 0.0, {"vol_mult": round(vol_mult, 2), "reason": "sin spike de volumen"})
    # con volumen 5x+ esperaríamos movimiento de precio; si es <2% es sospechoso
    expected = 0.02 * (vol_mult / 3)
    shortfall = _clamp01(1 - price_move / expected) if expected else 0
    return Signal("P-DIV", shortfall, {"vol_mult": round(vol_mult, 2), "price_move_pct": round(price_move * 100, 2)})


def _reversal(klines: list[dict]) -> Signal:
    """Pump & dump: el precio spikeó y ya está cayendo desde el máximo reciente."""
    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    recent_high = max(highs[-RECENT:])
    last = closes[-1]
    base = closes[-RECENT]
    run_up = recent_high / base - 1 if base else 0
    if run_up < 0.05:
        return Signal("P-REV", 0.0, {"reason": "sin run-up relevante"})
    give_back = (recent_high - last) / (recent_high - base) if recent_high > base else 0
    score = _clamp01(give_back) if run_up >= 0.05 else 0.0
    return Signal("P-REV", score, {"run_up_pct": round(run_up * 100, 2), "give_back_frac": round(give_back, 2)})


WEIGHTS = {"P-VOL": 0.35, "P-ACC": 0.25, "P-DIV": 0.25, "P-REV": 0.15}

VERDICT_ORGANIC = "organico"
VERDICT_WATCH = "vigilar"
VERDICT_ARTIFICIAL = "artificial"


@dataclass
class PumpReport:
    symbol: str
    score: float
    verdict: str
    signals: list[Signal]
    n_klines: int

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": round(self.score, 4),
            "verdict": self.verdict,
            "n_klines": self.n_klines,
            "signals": {s.code: {"score": round(s.score, 4), **s.detail} for s in self.signals},
        }


def analyze_klines(symbol: str, klines: list[dict]) -> PumpReport:
    """Devuelve score de inflado artificial [0,1] y veredicto. Relativo a la
    propia historia de la moneda: un mercado estable y líquido sale orgánico."""
    if len(klines) < MIN_BASELINE + RECENT:
        return PumpReport(symbol, 0.0, VERDICT_ORGANIC, [], len(klines))
    signals = [
        _volume_anomaly(klines),
        _price_acceleration(klines),
        _vol_price_divergence(klines),
        _reversal(klines),
    ]
    score = sum(WEIGHTS[s.code] * s.score for s in signals)
    if score >= 0.55:
        verdict = VERDICT_ARTIFICIAL
    elif score >= 0.28:
        verdict = VERDICT_WATCH
    else:
        verdict = VERDICT_ORGANIC
    return PumpReport(symbol, score, verdict, signals, len(klines))
