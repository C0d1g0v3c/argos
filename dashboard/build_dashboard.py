"""Genera el dashboard contrafactual de Argos a partir del cohorte congelado.

Lee el snapshot y sus miembros de la DB (fuente de verdad inmutable), extrae
las métricas reportadas de metrics_at_freeze y las inyecta en template.html.
El resultado es un HTML autocontenido, sin dependencias externas.

    python dashboard/build_dashboard.py            # -> dashboard/argos_dashboard.html
    python dashboard/build_dashboard.py --open      # y lo abre en el navegador

ADVERTENCIA metodológica (también visible en el dashboard): los ROI, drawdowns
y Sharpe provienen del leaderboard de Bybit, son auto-reportados, tienen sesgo
de supervivencia y NO están auditados. La página es un contrafactual de la
promesa de la plataforma, no una expectativa de rendimiento.
"""

import argparse
import json
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

# Permite ejecutar el script directamente sin instalar el paquete
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest"))

from argos_ingest import db  # noqa: E402

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "template.html"
OUTPUT = ROOT / "argos_dashboard.html"


def _pct(raw: str) -> float:
    """'+72.49%' -> 72.49 ; '0.00%' -> 0.0"""
    return float(raw.replace("%", "").replace(",", "").replace("+", "").strip() or 0)


def _ratio(raw: str) -> float:
    """'0.84 : 1' -> 0.84 ; '+1.17' -> 1.17"""
    raw = raw.strip()
    if ":" in raw:
        raw = raw.split(":")[0]
    return float(raw.replace(",", "").replace("+", "").strip() or 0)


def _num(raw: str) -> float:
    return float(str(raw).replace(",", "").replace("+", "").strip() or 0)


def extract_leaders(rows: list[dict]) -> list[dict]:
    """metricValues del leaderboard de Bybit, en orden:
    [ROI, Drawdown, totalAllFollowProfit, WinRate, profitLossRatio, sharpeRatio]
    """
    out = []
    for rank, m in rows:
        mv = m.get("metricValues", [])
        out.append({
            "rank": rank,
            "name": m.get("nickName") or m.get("leaderMark", "")[:8],
            "roi": _pct(mv[0]) if len(mv) > 0 else 0.0,
            "dd": _pct(mv[1]) if len(mv) > 1 else 0.0,
            "winrate": _pct(mv[3]) if len(mv) > 3 else 0.0,
            "plr": _ratio(mv[4]) if len(mv) > 4 else 0.0,
            "sharpe": _num(mv[5]) if len(mv) > 5 else 0.0,
            "followers": int(_num(m.get("currentFollowerCount", "0"))),
            "level": m.get("leaderLevel", "").replace("COPY_TRADE_LEADER_LEVEL_", "").replace("_TRADER", "").title(),
        })
    return out


def build_payload() -> dict:
    with db.get_conn() as conn:
        snap = conn.execute(
            "SELECT snapshot_id, frozen_at, platform, member_count, composition_hash, criteria "
            "FROM cohort_snapshot ORDER BY frozen_at DESC LIMIT 1"
        ).fetchone()
        if snap is None:
            print("No hay cohorte congelado. Correr freeze_cohort primero.", file=sys.stderr)
            sys.exit(1)
        snapshot_id, frozen_at, platform, member_count, chash, criteria = snap

        members = conn.execute(
            "SELECT rank_at_freeze, metrics_at_freeze FROM cohort_member "
            "WHERE snapshot_id = %s ORDER BY rank_at_freeze",
            (snapshot_id,),
        ).fetchall()
        leaders = extract_leaders(members)

        n_fills, first_ts = conn.execute("SELECT count(*), min(my_ts) FROM real_fill").fetchone()
        realized = conn.execute(
            "SELECT COALESCE(sum(gain_mxn), 0) FROM tax_event WHERE event_type = 'venta'"
        ).fetchone()[0]

    days = (datetime.now(timezone.utc) - first_ts).days if first_ts else None
    crit = criteria if isinstance(criteria, dict) else json.loads(criteria)

    return {
        "snapshot_id": str(snapshot_id),
        "frozen_at": frozen_at.strftime("%Y-%m-%d %H:%M UTC"),
        "platform": platform,
        "hash": chash,
        "source_file": crit.get("source_file", "—"),
        "fetched_at": crit.get("fetched_at", "—"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "leaders": leaders,
        "experiment": {
            "fills": n_fills,
            "days": days,
            "realized_mxn": float(realized),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open", action="store_true", help="Abrir en el navegador al terminar")
    args = parser.parse_args()

    payload = build_payload()
    template = TEMPLATE.read_text(encoding="utf-8")
    html = template.replace("__ARGOS_DATA__", json.dumps(payload, ensure_ascii=False))
    OUTPUT.write_text(html, encoding="utf-8")

    print(f"Dashboard generado: {OUTPUT}")
    print(f"  {len(payload['leaders'])} líderes · cohorte {payload['frozen_at']}")
    if args.open:
        webbrowser.open(OUTPUT.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
