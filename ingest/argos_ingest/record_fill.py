"""CLI de registro de fills del experimento de calibración.

El usuario ejecuta la orden EN LA PLATAFORMA (nunca este código) y luego
registra aquí lo observado. Escribe real_fill + tax_event en el momento;
reconstruirlo en abril cuesta mucho más.

Uso interactivo:
    python -m argos_ingest.record_fill

Uso directo:
    python -m argos_ingest.record_fill --symbol BTCUSDT --side long \
        --qty 0.0001 --my-px 61234.5 --leader bluntz --leader-px 61230.0 \
        --leader-ts "2026-08-22T14:03:11Z" --fee-mxn 1.20 --fx-dof 18.65
"""

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal

from . import db

STOP_MAX_FILLS = 20
STOP_MAX_DAYS = 30
STOP_MAX_LOSS_MXN = Decimal("200")


def _prompt(label: str, default: str | None = None, required: bool = True) -> str | None:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        val = input(f"  {label}{suffix}: ").strip()
        if not val and default is not None:
            return default
        if val or not required:
            return val or None
        print("    (requerido)")


def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _resolve_leader(conn, name_or_mark: str) -> tuple[int | None, str]:
    row = conn.execute(
        """
        SELECT leader_id, display_name FROM leaders
        WHERE platform_uid = %s OR display_name ILIKE %s
        """,
        (name_or_mark, name_or_mark),
    ).fetchone()
    if row is None:
        return None, name_or_mark
    return row[0], row[1] or name_or_mark


def _stop_criteria_status(conn) -> tuple[bool, list[str]]:
    """Evalúa los criterios de paro escritos ANTES de empezar. True = parar."""
    lines, stop = [], False

    n_fills, first_ts = conn.execute(
        "SELECT count(*), min(my_ts) FROM real_fill"
    ).fetchone()
    lines.append(f"Fills registrados: {n_fills}/{STOP_MAX_FILLS}")
    if n_fills >= STOP_MAX_FILLS:
        stop = True

    if first_ts is not None:
        days = (datetime.now(timezone.utc) - first_ts).days
        lines.append(f"Días transcurridos: {days}/{STOP_MAX_DAYS}")
        if days >= STOP_MAX_DAYS:
            stop = True

    realized = conn.execute(
        "SELECT COALESCE(sum(gain_mxn), 0) FROM tax_event WHERE event_type = 'venta'"
    ).fetchone()[0]
    lines.append(f"P&L realizado (ventas): ${realized:,.2f} MXN (límite de pérdida: -${STOP_MAX_LOSS_MXN})")
    if realized <= -STOP_MAX_LOSS_MXN:
        stop = True

    return stop, lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol")
    parser.add_argument("--side", choices=["long", "short"])
    parser.add_argument("--qty")
    parser.add_argument("--my-px", dest="my_px")
    parser.add_argument("--my-ts", dest="my_ts", help="ISO-8601; default: ahora")
    parser.add_argument("--leader", help="nickname o leaderMark del cohorte")
    parser.add_argument("--leader-px", dest="leader_px")
    parser.add_argument("--leader-ts", dest="leader_ts", help="ISO-8601, apertura del líder")
    parser.add_argument("--fee-mxn", dest="fee_mxn", default=None)
    parser.add_argument("--fx-dof", dest="fx_dof",
                        help="TC del DOF de la fecha (dof.gob.mx / Banxico FIX)")
    parser.add_argument("--notes", default=None)
    parser.add_argument("--tax-type", choices=["compra", "venta", "swap", "fee"],
                        help="Registrar también el tax_event de este fill")
    parser.add_argument("--value-mxn", dest="value_mxn",
                        help="Valor de la operación en MXN (para tax_event)")
    parser.add_argument("--cost-basis-mxn", dest="cost_basis_mxn", default=None)
    args = parser.parse_args()

    print("— Registro de fill (experimento de calibración) —")
    symbol = args.symbol or _prompt("Símbolo (ej. BTCUSDT)")
    side = args.side or _prompt("Side (long/short)", default="long")
    qty = args.qty or _prompt("Cantidad (qty)")
    my_px = args.my_px or _prompt("Tu precio de fill")
    my_ts_raw = args.my_ts or _prompt("Tu timestamp (ISO)", default=datetime.now(timezone.utc).isoformat())
    leader = args.leader or _prompt("Líder (nickname)", required=False)
    leader_px = args.leader_px or _prompt("Precio del líder", required=False)
    leader_ts_raw = args.leader_ts or _prompt("Timestamp del líder (ISO)", required=False)
    fee_mxn = args.fee_mxn if args.fee_mxn is not None else _prompt("Fee en MXN", default="0")
    fx_dof = args.fx_dof or _prompt("TC del DOF (MXN/USD)", required=False)
    notes = args.notes if args.notes is not None else _prompt("Notas", required=False)

    with db.get_conn() as conn:
        leader_id = None
        if leader:
            leader_id, resolved = _resolve_leader(conn, leader)
            if leader_id is None:
                print(f"  AVISO: '{leader}' no está en leaders; el fill se guarda sin leader_id")
            else:
                print(f"  Líder: {resolved} (id {leader_id})")

        fill_id = conn.execute(
            """
            INSERT INTO real_fill
                (leader_id, symbol, side, leader_ts, my_ts, leader_px, my_px,
                 qty, fee_mxn, fx_dof, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING fill_id
            """,
            (
                leader_id, symbol, side,
                _parse_ts(leader_ts_raw) if leader_ts_raw else None,
                _parse_ts(my_ts_raw),
                Decimal(leader_px) if leader_px else None,
                Decimal(my_px), Decimal(qty), Decimal(fee_mxn),
                Decimal(fx_dof) if fx_dof else None,
                notes,
            ),
        ).fetchone()[0]

        if args.tax_type:
            value_mxn = args.value_mxn or _prompt("Valor en MXN (tax_event)")
            if not fx_dof:
                fx_dof = _prompt("TC del DOF (obligatorio para tax_event)")
            conn.execute(
                """
                INSERT INTO tax_event
                    (ts, event_type, regime, symbol_out, qty_out, fx_dof,
                     value_mxn, cost_basis_mxn, fiscal_year, source_fill_id)
                VALUES (%s, %s, 'derivados', %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    _parse_ts(my_ts_raw), args.tax_type, symbol, Decimal(qty),
                    Decimal(fx_dof), Decimal(value_mxn),
                    Decimal(args.cost_basis_mxn) if args.cost_basis_mxn else None,
                    _parse_ts(my_ts_raw).year, fill_id,
                ),
            )

        conn.commit()

        print(f"\nFill #{fill_id} registrado.")
        derived = conn.execute(
            "SELECT copy_latency_s, slippage_bp_adverse FROM fill_slippage WHERE fill_id = %s",
            (fill_id,),
        ).fetchone()
        if derived and derived[0] is not None:
            print(f"  Latencia de copia: {derived[0]:.1f} s")
            print(f"  Slippage adverso: {derived[1]:.2f} bp")
        else:
            print("  (sin datos del líder: latencia/slippage no derivables para este fill)")

        stop, lines = _stop_criteria_status(conn)
        print("\n— Criterios de paro —")
        for line in lines:
            print(f"  {line}")
        if stop:
            print("\n*** CRITERIO DE PARO ALCANZADO: se cierra todo, se exportan los datos, se apaga. ***")
            print("*** No hay 'una más para completar la muestra'. ***")

    return 0


if __name__ == "__main__":
    sys.exit(main())
