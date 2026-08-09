"""Congela el cohorte. SE CORRE UNA SOLA VEZ.

Escribe cohort_snapshot + cohort_member con hash sha256 de la composición.
Los triggers append-only del esquema impiden modificarlo después: si este
script se corre mal, no hay corrección — hay un cohorte nuevo y el reloj
se reinicia.

Uso:
    python -m argos_ingest.freeze_cohort --platform bybit --top 50
    python -m argos_ingest.freeze_cohort --dry-run   # ver qué se congelaría
"""

import argparse
import hashlib
import sys

from . import db
from .platforms import get_platform


def composition_hash(uids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(uids)).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", default=None)
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true",
                        help="Muestra el cohorte sin escribir nada")
    args = parser.parse_args()

    platform = get_platform(args.platform)
    platform_name = args.platform or platform.__class__.__name__.lower()

    print(f"Obteniendo top {args.top} de {platform_name}…")
    entries = platform.fetch_leaderboard(args.top)
    if len(entries) < args.top:
        print(f"AVISO: solo se obtuvieron {len(entries)} líderes de {args.top} pedidos")

    uids = [e.platform_uid for e in entries]
    chash = composition_hash(uids)
    print(f"Miembros: {len(entries)}  composition_hash: {chash}")

    if args.dry_run:
        for e in entries:
            print(f"  #{e.rank:>3}  {e.platform_uid}  {e.display_name or ''}")
        print("\n--dry-run: no se escribió nada.")
        return 0

    criteria = {
        "source": "leaderboard",
        "top_n": args.top,
        "ordering": "el orden que mostraba la plataforma al momento del freeze",
    }

    with db.get_conn() as conn:
        existing = conn.execute("SELECT count(*) FROM cohort_snapshot").fetchone()[0]
        if existing:
            print("ERROR: ya existe un cohort_snapshot. El cohorte se congela UNA vez.", file=sys.stderr)
            return 1

        members = []
        for e in entries:
            leader_id = db.upsert_leader(conn, platform_name, e.platform_uid, e.display_name, None)
            members.append({"leader_id": leader_id, "rank": e.rank, "metrics": e.metrics})

        snapshot_id = db.insert_cohort(conn, platform_name, criteria, chash, members)
        conn.commit()

    print(f"COHORTE CONGELADO. snapshot_id={snapshot_id}")
    print("Siguiente paso: registrar instrument_type por líder y arrancar la ingesta continua.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
