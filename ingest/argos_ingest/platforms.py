"""Adaptadores de plataforma para el leaderboard de copy trading.

Cada adaptador expone la misma interfaz mínima:
  - fetch_leaderboard(top_n) -> list[LeaderEntry]
  - fetch_leader_trades(platform_uid) -> list[dict]

Los endpoints exactos cambian sin aviso y algunos requieren tokens de
sesión del navegador. Verificar contra la plataforma real antes del
freeze; los de aquí son el punto de partida, no una garantía.

Regla del proyecto: rate limiting agresivo desde el día 1. Un cohorte
más chico obtenido limpiamente vale más que uno grande que te banean
a la semana 3.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from .config import settings


@dataclass
class LeaderEntry:
    platform_uid: str
    display_name: str | None
    rank: int
    metrics: dict = field(default_factory=dict)  # lo que muestre el leaderboard, tal cual


class RateLimitedClient:
    """httpx.Client con intervalo mínimo entre requests."""

    def __init__(self, base_url: str, min_interval: float | None = None):
        self._client = httpx.Client(
            base_url=base_url,
            timeout=30,
            headers={"User-Agent": "argos-research/0.1 (medicion academica; contacto en el repo)"},
        )
        self._min_interval = min_interval or settings.min_request_interval
        self._last_request = 0.0

    def get(self, url: str, **kwargs) -> httpx.Response:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()
        resp = self._client.get(url, **kwargs)
        resp.raise_for_status()
        return resp


class BybitCopyTrading:
    """Leaderboard de copy trading de Bybit.

    Endpoint verificado 2026-08-09 (capturado con sesión de navegador):
      GET https://www.bybit.com/x-api/fapi/beehive/public/v1/common/dynamic-leader-list
          ?pageNo=1&pageSize=50&userTag=&dataDuration=DATA_DURATION_NINETY_DAY
          &leaderTag=&code=&leaderLevel=
    Responde retCode=0 con result.leaderDetails[]; el id estable del líder
    es `leaderMark`. totalCount ~7700 líderes.

    LIMITACIÓN: el WAF (Akamai) responde 403 a clientes HTTP directos,
    incluso con UA honesto. La adquisición se hace desde contexto de
    navegador y se guarda como captura JSON en data/; el freeze consume
    esa captura vía `freeze_cohort --from-json`. Este cliente directo se
    conserva por si el WAF cambia de política o aparece API oficial.
    """

    BASE_URL = "https://www.bybit.com"
    LEADERBOARD_PATH = "/x-api/fapi/beehive/public/v1/common/dynamic-leader-list"

    def __init__(self):
        self.http = RateLimitedClient(self.BASE_URL)

    def fetch_leaderboard(self, top_n: int = 50) -> list[LeaderEntry]:
        entries: list[LeaderEntry] = []
        page, page_size = 1, 20
        while len(entries) < top_n:
            resp = self.http.get(
                self.LEADERBOARD_PATH,
                params={"pageNo": page, "pageSize": page_size, "dataDuration": "DATA_DURATION_NINETY_DAY"},
            )
            data = resp.json()
            leaders = data.get("result", {}).get("leaderDetails", [])
            if not leaders:
                break
            for item in leaders:
                entries.append(
                    LeaderEntry(
                        platform_uid=str(item.get("leaderMark") or item.get("leaderId")),
                        display_name=item.get("nickName"),
                        rank=len(entries) + 1,
                        metrics=item,  # snapshot crudo completo: es evidencia, no se filtra
                    )
                )
                if len(entries) >= top_n:
                    break
            page += 1
        return entries

    # Endpoint de trades por líder, verificado 2026-08-09:
    #   GET /x-api/fapi/beehive/public/v1/common/leader-created-record
    #       ?dayCycleType=DAY_CYCLE_TYPE_THIRTY_DAY&pageNo=1&pageSize=N&leaderMark=<enc>
    # result.leaderOrderHistoryDetails[]: orderId, isOpenOrder, symbol, side,
    #   leverageE2, sizeX, entryPriceE8, closedPnlE8, yieldRateE4, createdAtE9 (ns).
    # result.openTradeInfoProtection == 1 → el líder oculta su historial: 0 filas.
    TRADES_PATH = "/x-api/fapi/beehive/public/v1/common/leader-created-record"

    @staticmethod
    def parse_trade_record(item: dict, leader_id: int) -> dict:
        """Mapea un leaderOrderHistoryDetails al shape de la tabla leader_trade."""
        ts_ns = int(item["createdAtE9"])
        return {
            "ts": datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc),
            "leader_id": leader_id,
            "trade_uid": str(item["orderId"]),
            "symbol": item["symbol"],
            "side": "long" if item["side"].lower() in ("buy", "long") else "short",
            "entry_px": Decimal(item["entryPriceE8"]) / Decimal(10**8),
            "exit_px": None,  # el endpoint no expone precio de salida directo
            "closed_at": None if item.get("isOpenOrder") else None,
            "notional_usd": None,  # sizeX está en contratos, no en USD directo
            "leverage": Decimal(item["leverageE2"]) / Decimal(100),
        }

    def fetch_leader_trades(self, platform_uid: str, page_size: int = 50) -> list[dict]:
        """Trades visibles de un líder (dict crudos del endpoint). El mapeo a
        leader_trade lo hace parse_trade_record. Devuelve [] si el líder tiene
        openTradeInfoProtection activo."""
        collected: list[dict] = []
        page = 1
        while True:
            resp = self.http.get(
                self.TRADES_PATH,
                params={
                    "dayCycleType": "DAY_CYCLE_TYPE_THIRTY_DAY",
                    "pageNo": page, "pageSize": page_size,
                    "leaderMark": platform_uid,
                },
            )
            result = resp.json().get("result", {})
            rows = result.get("leaderOrderHistoryDetails", [])
            collected.extend(rows)
            total_pages = int(result.get("totalPageCount", 0))
            if page >= total_pages or not rows:
                break
            page += 1
        return collected


def get_platform(name: str | None = None):
    name = name or settings.platform
    if name == "bybit":
        return BybitCopyTrading()
    raise ValueError(f"Plataforma no soportada todavía: {name}")
