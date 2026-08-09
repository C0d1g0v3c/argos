"""Congela el cohorte. SE CORRE UNA SOLA VEZ.

Escribe cohort_snapshot + cohort_member con hash sha256 de la composición.
Los triggers append-only del esquema impiden modificarlo después: si este
script se corre mal, no hay corrección — hay un cohorte nuevo y el reloj
se reinicia.

Uso:
    python -m argos_ingest.freeze_cohort --platform bybit --top 50
    python -m argos_ingest.freeze_cohort --from-json data/leaderboard_raw_X.json
    python -m argos_ingest.freeze_cohort --dry-run   # ver qué se congelaría
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import db
from .platforms import LeaderEntry, get_platform


def composition_hash(uids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(uids)).encode()).hexdigest()


def load_from_json(path: Path, top_n: int) -> tuple[list[LeaderEntry], dict]:
    """Carga una captura cruda del leaderboard (ver data/) tomada desde el
    navegador. El archivo mismo es la evidencia de qué se vio y cuándo."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    details = raw["body"]["result"]["leaderDetails"][:top_n]
    entries = [
        LeaderEntry(
            platform_uid=item["leaderMark"],
            display_name=item.get("nickName"),
            rank=i + 1,
            metrics=item,
        )
        for i, item in enumerate(details)
    ]
    provenance = {
        "source_file": path.name,
        "endpoint": raw.get("endpoint"),
        "params": raw.get("params"),
        "fetched_at": raw.get("fetched_at"),
    }
    return entries, provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", default=None)
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--from-json", type=Path, default=None,
                        help="Congelar desde una captura JSON en vez de scrapear")
    parser.add_argument("--dry-run", action="store_true",
                        help="Muestra el cohorte sin escribir nada")
    args = parser.parse_args()

    provenance: dict = {}
    if args.from_json:
        platform_name = args.platform or "bybit"
        print(f"Cargando captura {args.from_json}…")
        entries, provenance = load_from_json(args.from_json, args.top)
    else:
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
        **provenance,
    }

    with db.get_conn() as conn:
        existing = conn.execute("SELECT count(*) FROM cohort_snapshot").fetchone()[0]
        if existing:
            print("ERROR: ya existe un cohort_snapshot. El cohorte se congela UNA vez.", file=sys.stderr)
            return 1

        # Copy trading de Bybit opera sobre perpetuos USDT exclusivamente
        instrument_type = "perp" if platform_name == "bybit" else None
        members = []
        for e in entries:
            leader_id = db.upsert_leader(conn, platform_name, e.platform_uid, e.display_name, instrument_type)
            members.append({"leader_id": leader_id, "rank": e.rank, "metrics": e.metrics})

        snapshot_id = db.insert_cohort(conn, platform_name, criteria, chash, members)
        conn.commit()

    print(f"COHORTE CONGELADO. snapshot_id={snapshot_id}")
    print("Siguiente paso: registrar instrument_type por líder y arrancar la ingesta continua.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
