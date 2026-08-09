"""Semáforo del experimento de calibración: ¿ya es momento de meter los $500?

No es una opinión: son las precondiciones que el propio proyecto escribió,
evaluadas contra el estado real de la base de datos. Responde GO / NO-GO y
dice exactamente qué falta.

    python -m argos_ingest.experiment_gate

Las cinco puertas (todas deben abrir para GO):
  1. Cohorte congelado con hash            [Semana 1]
  2. Trades ingestados para vigilancia     [Semana 1-3]  -> insumo del audit y de las reglas
  3. Falsification audit PASADO sobre real [Semana 2]    -> el escéptico ya corrió y no falsificó
  4. Reglas de detección corridas          [Semana 3]    -> hay scores de vigilancia
  5. Un líder elegible con score bajo       [Semana 3]    -> a quién copiar sin calibrar contra un tramposo
"""

import sys
from dataclasses import dataclass

from . import db

MIN_TRADES_FOR_SURVEILLANCE = 200   # piso para que Benford/roundedness tengan señal
MIN_LABELED_ALERTS = 50             # criterio de salida del plan


@dataclass
class Gate:
    name: str
    ok: bool
    detail: str


def evaluate() -> list[Gate]:
    gates: list[Gate] = []
    with db.get_conn() as conn:
        # 1. Cohorte
        snap = conn.execute(
            "SELECT composition_hash, member_count FROM cohort_snapshot ORDER BY frozen_at DESC LIMIT 1"
        ).fetchone()
        gates.append(Gate(
            "Cohorte congelado con hash",
            snap is not None,
            f"hash {snap[0][:16]}…, {snap[1]} miembros" if snap else "no hay cohort_snapshot",
        ))

        # 2. Trades para vigilancia
        n_trades = conn.execute("SELECT count(*) FROM leader_trade").fetchone()[0]
        gates.append(Gate(
            "Trades ingestados (insumo de vigilancia)",
            n_trades >= MIN_TRADES_FOR_SURVEILLANCE,
            f"{n_trades} trades (piso {MIN_TRADES_FOR_SURVEILLANCE})",
        ))

        # 3. Falsification audit pasado
        audit = conn.execute(
            "SELECT verdict, ts FROM falsification_run WHERE environment='real' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        gates.append(Gate(
            "Falsification audit corrido y PASADO",
            audit is not None and audit[0] == "pasa",
            f"último veredicto sobre real: {audit[0]} ({audit[1]:%Y-%m-%d})" if audit else "el audit no ha corrido sobre entorno real",
        ))

        # 4. Reglas de detección corridas → hay alertas con score
        n_alerts = conn.execute("SELECT count(*) FROM surveillance_alert").fetchone()[0]
        n_labeled = conn.execute(
            "SELECT count(*) FROM surveillance_alert WHERE triage_state != 'nuevo'"
        ).fetchone()[0]
        gates.append(Gate(
            "Vigilancia corrida (hay scores)",
            n_alerts > 0,
            f"{n_alerts} alertas, {n_labeled} etiquetadas (meta del plan: {MIN_LABELED_ALERTS})",
        ))

        # 5. Un líder elegible: en el cohorte, con al menos una alerta de score BAJO
        #    y sin alertas confirmadas de manipulación.
        elegible = conn.execute(
            """
            SELECT l.leader_id, l.display_name,
                   COALESCE(min(sa.score), 999) AS min_score,
                   COALESCE(sum((sa.triage_state='confirmado')::int), 0) AS confirmadas
            FROM cohort_member cm
            JOIN leaders l USING (leader_id)
            LEFT JOIN surveillance_alert sa USING (leader_id)
            GROUP BY l.leader_id, l.display_name
            HAVING COALESCE(sum((sa.triage_state='confirmado')::int), 0) = 0
            ORDER BY min_score ASC
            LIMIT 1
            """
        ).fetchone()
        # Solo cuenta como elegible si ya hay vigilancia corrida (si no, todos son "limpios" por vacío)
        elegible_ok = bool(elegible) and n_alerts > 0
        gates.append(Gate(
            "Líder elegible con score de vigilancia bajo",
            elegible_ok,
            f"candidato: {elegible[1]} (score {elegible[2]})" if elegible_ok
            else "sin vigilancia corrida no se puede elegir un líder limpio con fundamento",
        ))

    return gates


def main() -> int:
    gates = evaluate()
    print("\n  SEMÁFORO DEL EXPERIMENTO DE CALIBRACIÓN — ¿meter los $500?\n")
    for g in gates:
        mark = "✅" if g.ok else "⛔"
        print(f"  {mark}  {g.name}")
        print(f"       {g.detail}")
    go = all(g.ok for g in gates)
    print()
    if go:
        print("  >>> GO. Las cinco precondiciones se cumplen. El experimento medirá lo que fue pensado para medir.")
    else:
        faltan = [g.name for g in gates if not g.ok]
        print(f"  >>> NO-GO. Faltan {len(faltan)}: " + "; ".join(faltan) + ".")
        print("      Meter capital ahora produce un número que no fundamenta el documento.")
    print()
    return 0 if go else 1


if __name__ == "__main__":
    sys.exit(main())
