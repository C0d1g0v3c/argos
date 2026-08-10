"""Corre el detector de manipulación sobre los trades del cohorte y escribe
detection_rule + surveillance_alert.

    python surveillance/run.py            # sobre el último cohorte congelado
    python surveillance/run.py --min-trades 30

Cada líder con trades suficientes recibe un análisis; si el score de una regla
supera su umbral, se escribe una surveillance_alert (triage_state='nuevo').
Etiquetar esas alertas a mano es el dataset supervisado de la tesis.

ALCANCE: instrumento de detección de riesgo, no señal de inversión (ver detector.py).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest"))

from argos_ingest import db  # noqa: E402
import detector  # noqa: E402

RULES = [
    ("ARG-001", "Benford sobre notional", "benford", "medium"),
    ("ARG-002", "Exceso de tamaños redondos", "roundedness", "medium"),
    ("ARG-003", "Ráfagas temporales de trades", "volume_spike", "low"),
    ("ARG-004", "Ida-y-vuelta (wash trading)", "graph_cycle", "high"),
]
ALERT_THRESHOLD = 0.30  # una regla alerta si su score la supera


def ensure_rules(conn) -> None:
    for rule_id, name, technique, severity in RULES:
        conn.execute(
            """
            INSERT INTO detection_rule (rule_id, name, technique, severity, version, enabled)
            VALUES (%s, %s, %s, %s, 1, true)
            ON CONFLICT (rule_id) DO UPDATE SET name = EXCLUDED.name
            """,
            (rule_id, name, technique, severity),
        )


def load_trades(conn, leader_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT symbol, side, entry_px, leverage, ts
        FROM leader_trade WHERE leader_id = %s ORDER BY ts
        """,
        (leader_id,),
    ).fetchall()
    trades = []
    for symbol, side, entry_px, leverage, ts in rows:
        # size aproximado por notional: sin sizeX en USD usamos leverage como proxy de tamaño relativo
        trades.append({
            "symbol": symbol,
            "side": side,
            "size": float(leverage or 1),
            "price": float(entry_px),
            "ts_s": ts.timestamp(),
        })
    return trades


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-trades", type=int, default=30)
    args = parser.parse_args()

    with db.get_conn() as conn:
        ensure_rules(conn)
        snap = conn.execute(
            "SELECT snapshot_id FROM cohort_snapshot ORDER BY frozen_at DESC LIMIT 1"
        ).fetchone()
        if snap is None:
            print("No hay cohorte congelado.", file=sys.stderr)
            return 1

        members = conn.execute(
            "SELECT leader_id FROM cohort_member WHERE snapshot_id = %s", (snap[0],)
        ).fetchall()

        analyzed = alerts = 0
        for (leader_id,) in members:
            trades = load_trades(conn, leader_id)
            if len(trades) < args.min_trades:
                continue
            report = detector.analyze(trades)
            analyzed += 1
            for rule in report.rules:
                if rule.score <= ALERT_THRESHOLD:
                    continue
                conn.execute(
                    """
                    INSERT INTO surveillance_alert (leader_id, rule_id, score, evidence)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (leader_id, rule.rule_id, rule.score, json.dumps({
                        "verdict": report.verdict,
                        "combined_score": round(report.score, 4),
                        **rule.evidence,
                    })),
                )
                alerts += 1
        conn.commit()

    print(f"Analizados {analyzed} líderes con >= {args.min_trades} trades; {alerts} alertas escritas.")
    if analyzed == 0:
        print("Ningún líder del cohorte tiene trades suficientes todavía. Falta ingesta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
