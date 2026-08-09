"""Conexión y escrituras a TimescaleDB."""

import json
from contextlib import contextmanager

import psycopg

from .config import settings


@contextmanager
def get_conn():
    with psycopg.connect(settings.dsn) as conn:
        yield conn


def upsert_leader(conn, platform: str, platform_uid: str, display_name: str | None,
                  instrument_type: str | None) -> int:
    """Inserta o recupera un líder. Devuelve leader_id."""
    row = conn.execute(
        """
        INSERT INTO leaders (platform, platform_uid, display_name, instrument_type)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (platform, platform_uid)
        DO UPDATE SET display_name = COALESCE(EXCLUDED.display_name, leaders.display_name)
        RETURNING leader_id
        """,
        (platform, platform_uid, display_name, instrument_type),
    ).fetchone()
    return row[0]


def insert_cohort(conn, platform: str, criteria: dict, composition_hash: str,
                  members: list[dict]) -> str:
    """Escribe cohort_snapshot + cohort_member. Solo puede ocurrir una vez:
    los triggers append-only impiden corregirlo después."""
    snapshot_id = conn.execute(
        """
        INSERT INTO cohort_snapshot (platform, criteria, member_count, composition_hash)
        VALUES (%s, %s, %s, %s)
        RETURNING snapshot_id
        """,
        (platform, json.dumps(criteria), len(members), composition_hash),
    ).fetchone()[0]

    for m in members:
        conn.execute(
            """
            INSERT INTO cohort_member (snapshot_id, leader_id, rank_at_freeze, metrics_at_freeze)
            VALUES (%s, %s, %s, %s)
            """,
            (snapshot_id, m["leader_id"], m["rank"], json.dumps(m["metrics"])),
        )
    return str(snapshot_id)


def insert_leader_trade(conn, trade: dict) -> None:
    """Inserta un trade observado. ON CONFLICT DO NOTHING: el mismo trade
    visto en dos polls consecutivos no es un dato nuevo."""
    conn.execute(
        """
        INSERT INTO leader_trade
            (ts, leader_id, trade_uid, symbol, side, entry_px, exit_px,
             closed_at, notional_usd, leverage)
        VALUES (%(ts)s, %(leader_id)s, %(trade_uid)s, %(symbol)s, %(side)s,
                %(entry_px)s, %(exit_px)s, %(closed_at)s, %(notional_usd)s, %(leverage)s)
        ON CONFLICT (leader_id, trade_uid, ts) DO NOTHING
        """,
        trade,
    )
