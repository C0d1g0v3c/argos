"""API de consulta rápida de inflado artificial — Argos / Cerberos.

Un endpoint por símbolo: ¿esta moneda muestra firmas de crecimiento artificial
(pump / wash) o parece orgánica? Pensado como el scanner que consulta la gente
de trading, pero como INSTRUMENTO DE RIESGO, no de recomendación de compra.

    cd api
    ../ingest/.venv/Scripts/uvicorn app:app --port 8100
    # abrir http://127.0.0.1:8100

ADVERTENCIA (también en la UI): score alto = "desconfiar / evitar", nunca
"comprar". Score bajo NO significa "buena inversión". No es asesoría financiera.
"""

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[0] / "ingest"))
sys.path.insert(0, str(ROOT.parents[0] / "surveillance"))

import httpx  # noqa: E402
from argos_ingest.market import fetch_binance_klines  # noqa: E402
import pump_detector as pd  # noqa: E402

app = FastAPI(title="Argos — Detección de inflado artificial")

_UA = {"User-Agent": "argos-research/0.1"}
_MOVERS_CACHE: dict = {"ts": 0.0, "data": None}


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")


@app.get("/api/analyze")
def analyze(symbol: str = Query(..., min_length=3, max_length=20),
            interval: str = "1h"):
    sym = symbol.upper().strip()
    if not sym.endswith("USDT"):
        sym += "USDT"
    try:
        klines = fetch_binance_klines(sym, interval, 336)
    except httpx.HTTPStatusError:
        raise HTTPException(404, f"Símbolo no encontrado en Binance: {sym}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Error consultando el mercado: {e}")
    if not klines:
        raise HTTPException(404, f"Sin datos para {sym}")
    report = pd.analyze_klines(sym, klines)
    out = report.as_dict()
    # serie de precio/volumen recientes para el sparkline (últimas 72 velas)
    tail = klines[-72:]
    out["series"] = {
        "t": [k["ts_s"] for k in tail],
        "close": [k["close"] for k in tail],
        "volume": [k["volume"] for k in tail],
    }
    out["last_price"] = klines[-1]["close"]
    return JSONResponse(out)


@app.get("/api/scan")
def scan(top: int = 20):
    """Top movers 24h pasados por el detector: el 'radar' de actividad anómala."""
    import time
    if _MOVERS_CACHE["data"] and time.time() - _MOVERS_CACHE["ts"] < 120:
        return JSONResponse(_MOVERS_CACHE["data"])
    tk = httpx.get("https://api.binance.com/api/v3/ticker/24hr", headers=_UA, timeout=30).json()
    usdt = [t for t in tk if t["symbol"].endswith("USDT") and float(t["quoteVolume"]) > 500_000]
    movers = sorted(usdt, key=lambda t: float(t["priceChangePercent"]), reverse=True)[:top]
    results = []
    for t in movers:
        try:
            kl = fetch_binance_klines(t["symbol"], "1h", 336)
            rep = pd.analyze_klines(t["symbol"], kl)
            results.append({
                "symbol": t["symbol"],
                "change24h": round(float(t["priceChangePercent"]), 2),
                "score": rep.score,
                "verdict": rep.verdict,
                "signals": {s.code: round(s.score, 3) for s in rep.signals},
            })
        except Exception:  # noqa: BLE001
            continue
    results.sort(key=lambda r: r["score"], reverse=True)
    payload = {"results": results}
    _MOVERS_CACHE.update(ts=time.time(), data=payload)
    return JSONResponse(payload)
