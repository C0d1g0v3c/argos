"""Ingesta de trades públicos de mercado por símbolo.

A diferencia del leaderboard de copy trading (bloqueado por WAF, mayormente
opaco), la API pública de mercado del exchange expone cada trade ejecutado:
público, abundante, reproducible. Es la fuente correcta para detectar inflado
artificial de una moneda — el wash trading y los pumps se estudian a nivel de
símbolo, no de copiadores.

Cada trade se normaliza al shape que consume surveillance/detector.py:
    {symbol, side ('long'/'short'), size, price, ts_s (epoch segundos)}
"""

from __future__ import annotations

import httpx

_UA = {"User-Agent": "argos-research/0.1 (medicion academica; contacto en el repo)"}


def fetch_binance_trades(symbol: str, limit: int = 1000) -> list[dict]:
    """Trades recientes de Binance spot. limit <= 1000.

    side: en la API 'isBuyerMaker=true' significa que el comprador era maker,
    es decir el trade lo cruzó un vendedor agresivo -> 'short' (venta taker).
    """
    url = "https://api.binance.com/api/v3/trades"
    resp = httpx.get(url, params={"symbol": symbol.upper(), "limit": min(limit, 1000)},
                     headers=_UA, timeout=30)
    resp.raise_for_status()
    out = []
    for t in resp.json():
        out.append({
            "symbol": symbol.upper(),
            "side": "short" if t["isBuyerMaker"] else "long",
            "size": float(t["qty"]),
            "price": float(t["price"]),
            "ts_s": t["time"] / 1000.0,
        })
    return out


def fetch_bybit_trades(symbol: str, limit: int = 60, category: str = "spot") -> list[dict]:
    """Trades recientes de Bybit (API pública de mercado, sin WAF). limit <= 60."""
    url = "https://api.bybit.com/v5/market/recent-trade"
    resp = httpx.get(url, params={"category": category, "symbol": symbol.upper(), "limit": min(limit, 60)},
                     headers=_UA, timeout=30)
    resp.raise_for_status()
    out = []
    for t in resp.json()["result"]["list"]:
        out.append({
            "symbol": symbol.upper(),
            "side": "long" if t["side"] == "Buy" else "short",
            "size": float(t["size"]),
            "price": float(t["price"]),
            "ts_s": int(t["time"]) / 1000.0,
        })
    return out


def fetch_trades(symbol: str, source: str = "binance", limit: int = 1000) -> list[dict]:
    if source == "binance":
        return fetch_binance_trades(symbol, limit)
    if source == "bybit":
        return fetch_bybit_trades(symbol, limit)
    raise ValueError(f"Fuente no soportada: {source}")


def fetch_binance_klines(symbol: str, interval: str = "1h", limit: int = 336) -> list[dict]:
    """Velas OHLCV de Binance. Default: 336 velas de 1h = 14 días.

    La detección de inflado artificial (pump) se hace sobre la serie de velas,
    no sobre ticks: un pump es una ANOMALÍA de volumen/precio contra la línea
    base histórica de la propia moneda. Por eso BTC, estable, no dispara.
    """
    url = "https://api.binance.com/api/v3/klines"
    resp = httpx.get(url, params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
                     headers=_UA, timeout=30)
    resp.raise_for_status()
    out = []
    for k in resp.json():
        out.append({
            "ts_s": k[0] / 1000.0,
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]),
            "trades": int(k[8]),
            "taker_buy_base": float(k[9]),
        })
    return out
