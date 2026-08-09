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

    TODO: confirmar endpoint y forma de la respuesta contra la web real.
    La página pública del leaderboard usa una API interna JSON; capturar
    la request desde las DevTools del navegador y ajustar aquí.
    """

    BASE_URL = "https://api2.bybit.com"
    LEADERBOARD_PATH = "/fapi/beehive/public/v1/common/dynamic-leader-list"

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

    def fetch_leader_trades(self, platform_uid: str) -> list[dict]:
        """Posiciones/trades visibles de un líder. TODO: endpoint real."""
        raise NotImplementedError("Capturar endpoint de posiciones desde DevTools y implementarlo")


def get_platform(name: str | None = None):
    name = name or settings.platform
    if name == "bybit":
        return BybitCopyTrading()
    raise ValueError(f"Plataforma no soportada todavía: {name}")
