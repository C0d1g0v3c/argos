"""Job de ingesta continua de leader_trade.

Recorre los miembros del cohorte congelado y registra sus trades visibles
con observed_at = now(). La diferencia observed_at - ts es la latencia de
observación: la métrica central del experimento.

Uso:
    python -m argos_ingest.trades          # una pasada
    python -m argos_ingest.trades --loop   # continuo, cada ARGOS_POLL_INTERVAL s
"""

import argparse
import sys
import time
import traceback

from . import db
from .config import settings
from .platforms import get_platform


def poll_once() -> int:
    """Una pasada por todos los miembros del cohorte. Devuelve trades nuevos."""
    platform = get_platform()
    inserted = 0
    with db.get_conn() as conn:
        members = conn.execute(
            """
            SELECT l.leader_id, l.platform_uid
            FROM cohort_member cm JOIN leaders l USING (leader_id)
            """
        ).fetchall()
        if not members:
            print("No hay cohorte congelado todavía. Correr freeze_cohort primero.", file=sys.stderr)
            return 0

        for leader_id, platform_uid in members:
            try:
                trades = platform.fetch_leader_trades(platform_uid)
            except NotImplementedError:
                print("fetch_leader_trades no está implementado aún para esta plataforma.", file=sys.stderr)
                return 0
            except Exception:
                # Un líder que falla no debe tirar la pasada completa
                traceback.print_exc()
                continue

            for t in trades:
                t["leader_id"] = leader_id
                db.insert_leader_trade(conn, t)
                inserted += 1
        conn.commit()
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()

    while True:
        n = poll_once()
        print(f"Pasada completa: {n} trades registrados")
        if not args.loop:
            return 0
        time.sleep(settings.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
