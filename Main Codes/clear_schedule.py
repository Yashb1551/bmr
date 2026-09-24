"""Bulk-clear the production schedule: delete batches and their bookings.

Clears what is on the books - batches, their equipment allocations and their
ECR cleaning logs, plus any order left with no batches. Master data is never
touched: equipment, products, recipes, ECR templates, users and the audit log
all stay exactly as they are.

Deleting each batch through scheduler.delete_batch() rather than with a bulk
SQL DELETE is the point of this script. That path writes an AuditLog row per
batch naming who removed what, which a raw DELETE silently skips - and an
unexplained gap in a production schedule is exactly the thing an audit trail
exists to account for.

    # PowerShell, from the project root
    $env:DATABASE_URL = "postgresql://...pooler.supabase.com:5432/postgres"
    python "Main Codes/clear_schedule.py"                    # preview only
    python "Main Codes/clear_schedule.py" --yes --actor you  # actually delete

Nothing is deleted without --yes. With no DATABASE_URL set this runs against
the local SQLite file, so check the target line it prints before confirming.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from batch_planner import config, scheduler  # noqa: E402
from batch_planner.db import SessionLocal  # noqa: E402
from batch_planner.models import (  # noqa: E402
    Allocation, Batch, CleaningStep, Order, product_display_names,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true",
                        help="Actually delete. Without this the script only previews.")
    parser.add_argument("--actor", default="bulk-clear",
                        help="Name recorded in the audit log (default: bulk-clear)")
    parser.add_argument("--status", action="append", metavar="STATUS",
                        help="Only delete batches with this status (repeatable). "
                             "Default: every batch.")
    args = parser.parse_args()

    print(f"Target : {config.describe_db_target()}")

    with SessionLocal() as session:
        query = session.query(Batch).join(Order, Batch.order_id == Order.id)
        if args.status:
            query = query.filter(Batch.status.in_(args.status))
        batches = query.all()
        labels = product_display_names(session)

        rows = []
        for b in batches:
            allocs = sorted(b.allocations, key=lambda a: a.op_start)
            rows.append((
                b.id,
                labels.get(b.product_code, b.product_code),
                b.status,
                allocs[0].op_start if allocs else None,
                len(allocs),
            ))
        rows.sort(key=lambda r: (r[3] is None, r[3]))

        if not rows:
            print("\nNothing to delete - no batches are scheduled.")
            return 0

        print(f"\n{len(rows)} batch(es) would be deleted:\n")
        print(f"  {'id':>5}  {'product':<44} {'status':<10} {'starts':<16} ops")
        for bid, product, status, start, ops in rows:
            when = start.strftime("%d/%m/%Y %H:%M") if start else "(not booked)"
            print(f"  {bid:>5}  {product:<44} {status:<10} {when:<16} {ops}")

        orders = {b.order_id for b in batches}
        print(f"\n  allocations    {session.query(Allocation).count()}")
        print(f"  cleaning_steps {session.query(CleaningStep).count()}")
        print(f"  orders touched {len(orders)}")

        if not args.yes:
            print("\nPreview only - nothing deleted. Re-run with --yes to delete.")
            return 0

        print(f"\nDeleting as '{args.actor}' ...")
        deleted = 0
        for bid, product, *_ in rows:
            try:
                scheduler.delete_batch(session, bid, actor=args.actor)
            except scheduler.SchedulingError as exc:
                print(f"  batch {bid}: SKIPPED ({exc})")
            else:
                deleted += 1

        print(f"\nDeleted {deleted} batch(es).")
        print("Remaining:")
        for name, model in (("orders", Order), ("batches", Batch),
                            ("allocations", Allocation), ("cleaning_steps", CleaningStep)):
            print(f"  {name:<15} {session.query(model).count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
