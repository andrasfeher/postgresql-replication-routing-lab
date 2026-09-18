#!/usr/bin/env python3

"""
PostgreSQL / PgBouncer / HAProxy / Patroni workload tester.

Designed for testing a path such as:

    Python clients
         |
         v
      PgBouncer
         |
         v
       HAProxy
         |
         v
      Patroni
         |
         v
    PostgreSQL nodes

Features
--------
* Configurable number of concurrent client threads.
* Configurable READ/WRITE ratio.
* Fixed operation count, duration, or continuous execution.
* Persistent or per-operation client connections.
* PostgreSQL backend PID tracking.
* PostgreSQL server address/port tracking.
* Primary/replica detection with pg_is_in_recovery().
* transaction_read_only tracking.
* WAL LSN tracking.
* Automatic reconnect after connection failures.
* UUID assigned before every write attempt.
* Post-test reconciliation of attempted writes.
* Detection of:
    - acknowledged and present writes
    - failed and absent writes
    - ambiguous writes (client error but row exists)
    - potentially lost writes (acknowledged but missing)
* Latency and throughput statistics.
* Primary/server transition reporting.

Dependency
----------

    pip install "psycopg[binary]"

Example
-------

    export PGPASSWORD='postgres'

    python3 cluster_test.py \
        --host localhost \
        --port 5432 \
        --database postgres \
        --user postgres \
        --threads 20 \
        --write-percent 50 \
        --duration 60
"""

import argparse
import os
import random
import statistics
import threading
import time
import uuid

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import psycopg


# ----------------------------------------------------------------------
# Shared state
# ----------------------------------------------------------------------

stats_lock = threading.Lock()
print_lock = threading.Lock()
stop_event = threading.Event()

latencies = []
errors = []
backend_pids = set()
servers_seen = set()

read_count = 0
write_count = 0

# operation_id -> WriteResult
write_results = {}

# Used to detect routing/primary changes.
last_server_by_thread = {}


# ----------------------------------------------------------------------
# Write result
# ----------------------------------------------------------------------

@dataclass
class WriteResult:
    operation_id: str
    thread_id: int
    sequence_no: int

    acknowledged: bool = False
    error: str | None = None

    server_addr: str | None = None
    server_port: int | None = None
    backend_pid: int | None = None
    wal_lsn: str | None = None


# ----------------------------------------------------------------------
# Command-line arguments
# ----------------------------------------------------------------------

def parse_args():

    parser = argparse.ArgumentParser(
        description=(
            "Concurrent PostgreSQL/PgBouncer/HAProxy/Patroni "
            "read/write workload tester"
        )
    )

    # --------------------------------------------------------------
    # PostgreSQL connection
    # --------------------------------------------------------------

    parser.add_argument(
        "--host",
        default="127.0.0.1",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=5432,
    )

    parser.add_argument(
        "--database",
        default="postgres",
    )

    parser.add_argument(
        "--user",
        default="postgres",
    )

    parser.add_argument(
        "--password",
        default=os.environ.get("PGPASSWORD"),
        help="Defaults to PGPASSWORD",
    )

    # --------------------------------------------------------------
    # Workload
    # --------------------------------------------------------------

    parser.add_argument(
        "--threads",
        type=int,
        default=10,
        help="Number of concurrent clients",
    )

    parser.add_argument(
        "--operations",
        type=int,
        default=100,
        help="Operations per thread",
    )

    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Run for N seconds instead of operation count",
    )

    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run until Ctrl+C",
    )

    parser.add_argument(
        "--write-percent",
        type=int,
        default=20,
        help="Percentage of operations that are writes",
    )

    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=0,
        help="Optional pg_sleep inside each transaction",
    )

    # --------------------------------------------------------------
    # Connection behavior
    # --------------------------------------------------------------

    parser.add_argument(
        "--connection-mode",
        choices=[
            "persistent",
            "per-operation",
        ],
        default="persistent",
        help=(
            "persistent = one client connection per thread; "
            "per-operation = reconnect for every operation"
        ),
    )

    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=1.0,
        help="Seconds before reconnecting after failure",
    )

    # --------------------------------------------------------------
    # Output
    # --------------------------------------------------------------

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every operation",
    )

    return parser.parse_args()


# ----------------------------------------------------------------------
# Connection
# ----------------------------------------------------------------------

def connection_string(args):

    parts = [
        f"host={args.host}",
        f"port={args.port}",
        f"dbname={args.database}",
        f"user={args.user}",
        "connect_timeout=5",
        "application_name=cluster_test",
    ]

    if args.password:
        parts.append(f"password={args.password}")

    return " ".join(parts)


def connect(args):

    return psycopg.connect(
        connection_string(args),
    )


# ----------------------------------------------------------------------
# Initialization
# ----------------------------------------------------------------------

def initialize_database(args):

    with connect(args) as conn:

        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS pgbouncer_test (
                    operation_id UUID PRIMARY KEY,
                    thread_id    INTEGER NOT NULL,
                    sequence_no  BIGINT NOT NULL,
                    value        BIGINT NOT NULL,
                    created_at   TIMESTAMPTZ NOT NULL
                                 DEFAULT clock_timestamp()
                )
            """)

        conn.commit()

    print("Test table ready.")


# ----------------------------------------------------------------------
# PostgreSQL routing information
# ----------------------------------------------------------------------

def get_backend_info(cur):

    cur.execute("""
        SELECT
            pg_backend_pid(),

            COALESCE(
                inet_server_addr()::text,
                'local'
            ),

            inet_server_port(),

            pg_is_in_recovery(),

            current_setting(
                'transaction_read_only'
            )::boolean,

            CASE
                WHEN pg_is_in_recovery()
                    THEN pg_last_wal_replay_lsn()::text
                ELSE pg_current_wal_lsn()::text
            END
    """)

    return cur.fetchone()


# ----------------------------------------------------------------------
# Statistics
# ----------------------------------------------------------------------

def record_success(
    operation,
    latency,
    backend_pid,
    server_addr,
    server_port,
):

    global read_count
    global write_count

    with stats_lock:

        latencies.append(latency)

        backend_pids.add(
            backend_pid
        )

        servers_seen.add(
            (server_addr, server_port)
        )

        if operation == "READ":
            read_count += 1
        else:
            write_count += 1


def record_error(thread_id, error):

    with stats_lock:

        errors.append(
            (
                thread_id,
                str(error),
            )
        )


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------

def print_operation(
    thread_id,
    operation,
    backend_pid,
    server_addr,
    server_port,
    in_recovery,
    read_only,
    wal_lsn,
    latency,
):

    role = (
        "REPLICA"
        if in_recovery
        else "PRIMARY"
    )

    with print_lock:

        print(
            f"T{thread_id:02d} "
            f"{operation:<5} "
            f"{role:<7} "
            f"server={server_addr}:{server_port} "
            f"backend={backend_pid:<6} "
            f"readonly={str(read_only):<5} "
            f"LSN={wal_lsn:<14} "
            f"{latency * 1000:8.2f} ms"
        )


def report_server_change(
    thread_id,
    server_addr,
    server_port,
    in_recovery,
):

    role = (
        "REPLICA"
        if in_recovery
        else "PRIMARY"
    )

    current = (
        server_addr,
        server_port,
        role,
    )

    with stats_lock:

        previous = last_server_by_thread.get(
            thread_id
        )

        last_server_by_thread[
            thread_id
        ] = current

    if previous is not None and previous != current:

        with print_lock:

            print()
            print(
                "*** ROUTING CHANGE ***"
            )

            print(
                f"Thread {thread_id}: "
                f"{previous} -> {current}"
            )

            print()


# ----------------------------------------------------------------------
# READ
# ----------------------------------------------------------------------

def perform_read(
    conn,
    args,
    thread_id,
):

    start = time.perf_counter()

    with conn.cursor() as cur:

        (
            backend_pid,
            server_addr,
            server_port,
            in_recovery,
            read_only,
            wal_lsn,
        ) = get_backend_info(cur)

        cur.execute("""
            SELECT
                count(*),
                max(sequence_no)
            FROM pgbouncer_test
        """)

        cur.fetchone()

        if args.sleep_ms > 0:

            cur.execute(
                "SELECT pg_sleep(%s)",
                (
                    args.sleep_ms / 1000.0,
                ),
            )

        conn.commit()

    latency = (
        time.perf_counter()
        - start
    )

    record_success(
        "READ",
        latency,
        backend_pid,
        server_addr,
        server_port,
    )

    report_server_change(
        thread_id,
        server_addr,
        server_port,
        in_recovery,
    )

    if args.verbose:

        print_operation(
            thread_id,
            "READ",
            backend_pid,
            server_addr,
            server_port,
            in_recovery,
            read_only,
            wal_lsn,
            latency,
        )


# ----------------------------------------------------------------------
# WRITE
# ----------------------------------------------------------------------

def perform_write(
    conn,
    args,
    thread_id,
    sequence_no,
):

    # IMPORTANT:
    #
    # Generate the UUID BEFORE sending anything to PostgreSQL.
    #
    # This lets us determine later whether a transaction actually
    # committed even if the client lost the connection before
    # receiving COMMIT acknowledgement.

    operation_id = str(
        uuid.uuid4()
    )

    result = WriteResult(
        operation_id=operation_id,
        thread_id=thread_id,
        sequence_no=sequence_no,
    )

    with stats_lock:

        write_results[
            operation_id
        ] = result

    start = time.perf_counter()

    try:

        with conn.cursor() as cur:

            (
                backend_pid,
                server_addr,
                server_port,
                in_recovery,
                read_only,
                wal_lsn_before,
            ) = get_backend_info(cur)

            result.backend_pid = backend_pid
            result.server_addr = server_addr
            result.server_port = server_port

            cur.execute(
                """
                INSERT INTO pgbouncer_test (
                    operation_id,
                    thread_id,
                    sequence_no,
                    value
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    operation_id,
                    thread_id,
                    sequence_no,
                    random.randint(
                        1,
                        1_000_000,
                    ),
                ),
            )

            if args.sleep_ms > 0:

                cur.execute(
                    "SELECT pg_sleep(%s)",
                    (
                        args.sleep_ms
                        / 1000.0,
                    ),
                )

            # Capture WAL position after INSERT but before COMMIT.
            cur.execute("""
                SELECT
                    CASE
                        WHEN pg_is_in_recovery()
                            THEN pg_last_wal_replay_lsn()::text
                        ELSE pg_current_wal_lsn()::text
                    END
            """)

            result.wal_lsn = (
                cur.fetchone()[0]
            )

        # The interesting failure window is here:
        #
        # PostgreSQL may commit successfully but the client connection
        # may disappear before psycopg receives confirmation.

        conn.commit()

        result.acknowledged = True

        latency = (
            time.perf_counter()
            - start
        )

        record_success(
            "WRITE",
            latency,
            backend_pid,
            server_addr,
            server_port,
        )

        report_server_change(
            thread_id,
            server_addr,
            server_port,
            in_recovery,
        )

        if args.verbose:

            print_operation(
                thread_id,
                "WRITE",
                backend_pid,
                server_addr,
                server_port,
                in_recovery,
                read_only,
                result.wal_lsn,
                latency,
            )

        return True

    except Exception as exc:

        result.error = str(exc)

        try:
            conn.rollback()
        except Exception:
            pass

        record_error(
            thread_id,
            exc,
        )

        with print_lock:

            print(
                f"WRITE ERROR "
                f"thread={thread_id} "
                f"operation={operation_id} "
                f"{exc}"
            )

        return False


# ----------------------------------------------------------------------
# Operation dispatcher
# ----------------------------------------------------------------------

def perform_operation(
    conn,
    args,
    thread_id,
    sequence_no,
):

    write = (
        random.randint(1, 100)
        <= args.write_percent
    )

    if write:

        return perform_write(
            conn,
            args,
            thread_id,
            sequence_no,
        )

    try:

        perform_read(
            conn,
            args,
            thread_id,
        )

        return True

    except Exception as exc:

        try:
            conn.rollback()
        except Exception:
            pass

        record_error(
            thread_id,
            exc,
        )

        return False


# ----------------------------------------------------------------------
# Workload termination
# ----------------------------------------------------------------------

def should_continue(
    args,
    sequence_no,
    start_time,
):

    if stop_event.is_set():
        return False

    if args.continuous:
        return True

    if args.duration is not None:

        return (
            time.perf_counter()
            - start_time
        ) < args.duration

    return (
        sequence_no
        <= args.operations
    )


# ----------------------------------------------------------------------
# Persistent connection worker
# ----------------------------------------------------------------------

def worker_persistent(
    args,
    thread_id,
    global_start,
):

    sequence_no = 1

    conn = None

    while should_continue(
        args,
        sequence_no,
        global_start,
    ):

        try:

            if conn is None or conn.closed:

                with print_lock:

                    print(
                        f"Thread {thread_id}: connecting..."
                    )

                conn = connect(args)

            success = perform_operation(
                conn,
                args,
                thread_id,
                sequence_no,
            )

            sequence_no += 1

            if not success:

                try:
                    conn.close()
                except Exception:
                    pass

                conn = None

                if should_continue(
                    args,
                    sequence_no,
                    global_start,
                ):

                    time.sleep(
                        args.reconnect_delay
                    )

        except Exception as exc:

            record_error(
                thread_id,
                exc,
            )

            try:

                if conn is not None:
                    conn.close()

            except Exception:
                pass

            conn = None

            time.sleep(
                args.reconnect_delay
            )

    if conn is not None:

        try:
            conn.close()
        except Exception:
            pass


# ----------------------------------------------------------------------
# Per-operation connection worker
# ----------------------------------------------------------------------

def worker_per_operation(
    args,
    thread_id,
    global_start,
):

    sequence_no = 1

    while should_continue(
        args,
        sequence_no,
        global_start,
    ):

        try:

            with connect(args) as conn:

                perform_operation(
                    conn,
                    args,
                    thread_id,
                    sequence_no,
                )

        except Exception as exc:

            record_error(
                thread_id,
                exc,
            )

        sequence_no += 1


# ----------------------------------------------------------------------
# Percentile
# ----------------------------------------------------------------------

def percentile(
    values,
    percentile_value,
):

    if not values:
        return 0

    ordered = sorted(values)

    index = int(
        (
            percentile_value
            / 100
        )
        * (
            len(ordered)
            - 1
        )
    )

    return ordered[index]


# ----------------------------------------------------------------------
# Reconciliation
# ----------------------------------------------------------------------

def reconcile_writes(args):

    print()
    print("=" * 70)
    print("WRITE RECONCILIATION")
    print("=" * 70)

    attempted = list(
        write_results.values()
    )

    if not attempted:

        print("No writes were attempted.")
        return

    operation_ids = [
        result.operation_id
        for result in attempted
    ]

    present = set()

    try:

        with connect(args) as conn:

            with conn.cursor() as cur:

                # Avoid creating one enormous IN (...) expression.
                #
                # Check UUIDs in manageable batches.

                batch_size = 1000

                for offset in range(
                    0,
                    len(operation_ids),
                    batch_size,
                ):

                    batch = operation_ids[
                        offset:
                        offset + batch_size
                    ]

                    cur.execute(
                        """
                        SELECT
                            operation_id::text
                        FROM pgbouncer_test
                        WHERE operation_id
                              = ANY(%s::uuid[])
                        """,
                        (
                            batch,
                        ),
                    )

                    present.update(
                        row[0]
                        for row in cur.fetchall()
                    )

    except Exception as exc:

        print(
            "Unable to perform reconciliation:"
        )

        print(exc)

        return

    acknowledged_present = []
    error_absent = []
    error_present = []
    acknowledged_absent = []

    for result in attempted:

        exists = (
            result.operation_id
            in present
        )

        if (
            result.acknowledged
            and exists
        ):

            acknowledged_present.append(
                result
            )

        elif (
            not result.acknowledged
            and not exists
        ):

            error_absent.append(
                result
            )

        elif (
            not result.acknowledged
            and exists
        ):

            error_present.append(
                result
            )

        elif (
            result.acknowledged
            and not exists
        ):

            acknowledged_absent.append(
                result
            )

    print(
        f"Attempted writes              : "
        f"{len(attempted)}"
    )

    print(
        f"Acknowledged + present        : "
        f"{len(acknowledged_present)}"
    )

    print(
        f"Error + absent                : "
        f"{len(error_absent)}"
    )

    print(
        f"Error + present (ambiguous)   : "
        f"{len(error_present)}"
    )

    print(
        f"Acknowledged + ABSENT         : "
        f"{len(acknowledged_absent)}"
    )

    # --------------------------------------------------------------
    # Ambiguous transactions
    # --------------------------------------------------------------

    if error_present:

        print()
        print(
            "Ambiguous commits:"
        )

        print(
            "PostgreSQL committed these writes, "
            "but the client received an error."
        )

        for result in error_present[:20]:

            print(
                f"  {result.operation_id} "
                f"thread={result.thread_id} "
                f"seq={result.sequence_no}"
            )

    # --------------------------------------------------------------
    # Potential data loss
    # --------------------------------------------------------------

    if acknowledged_absent:

        print()
        print(
            "!!! POTENTIAL DATA LOSS !!!"
        )

        print(
            "The client received successful COMMIT "
            "acknowledgement but these rows are missing:"
        )

        for result in acknowledged_absent[:20]:

            print(
                f"  {result.operation_id} "
                f"thread={result.thread_id} "
                f"seq={result.sequence_no} "
                f"LSN={result.wal_lsn}"
            )


# ----------------------------------------------------------------------
# Final report
# ----------------------------------------------------------------------

def print_report(elapsed):

    total_operations = (
        read_count
        + write_count
    )

    print()
    print("=" * 70)
    print("WORKLOAD RESULTS")
    print("=" * 70)

    print(
        f"Successful operations : "
        f"{total_operations}"
    )

    print(
        f"Reads                 : "
        f"{read_count}"
    )

    print(
        f"Writes acknowledged   : "
        f"{write_count}"
    )

    print(
        f"Errors                 : "
        f"{len(errors)}"
    )

    print(
        f"Elapsed                : "
        f"{elapsed:.2f} sec"
    )

    if elapsed > 0:

        print(
            f"Throughput             : "
            f"{total_operations / elapsed:.2f} ops/sec"
        )

    # --------------------------------------------------------------
    # Latency
    # --------------------------------------------------------------

    if latencies:

        latency_ms = [
            value * 1000
            for value in latencies
        ]

        print()
        print("Latency")
        print("-" * 70)

        print(
            f"Average : "
            f"{statistics.mean(latency_ms):.2f} ms"
        )

        print(
            f"Median  : "
            f"{statistics.median(latency_ms):.2f} ms"
        )

        print(
            f"P95     : "
            f"{percentile(latency_ms, 95):.2f} ms"
        )

        print(
            f"P99     : "
            f"{percentile(latency_ms, 99):.2f} ms"
        )

        print(
            f"Maximum : "
            f"{max(latency_ms):.2f} ms"
        )

    # --------------------------------------------------------------
    # Backends
    # --------------------------------------------------------------

    print()
    print("PostgreSQL backends")
    print("-" * 70)

    print(
        f"Unique backend PIDs: "
        f"{len(backend_pids)}"
    )

    if backend_pids:

        print(
            ", ".join(
                str(pid)
                for pid in sorted(
                    backend_pids
                )
            )
        )

    # --------------------------------------------------------------
    # Servers
    # --------------------------------------------------------------

    print()
    print("Servers observed")
    print("-" * 70)

    for server in sorted(
        servers_seen
    ):

        print(
            f"{server[0]}:{server[1]}"
        )

    # --------------------------------------------------------------
    # Errors
    # --------------------------------------------------------------

    if errors:

        print()
        print("First errors")
        print("-" * 70)

        for (
            thread_id,
            error,
        ) in errors[:20]:

            print(
                f"Thread {thread_id}: "
                f"{error}"
            )

        if len(errors) > 20:

            print(
                f"... {len(errors) - 20} "
                f"additional errors"
            )


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():

    args = parse_args()

    if not 0 <= args.write_percent <= 100:

        raise ValueError(
            "--write-percent must be "
            "between 0 and 100"
        )

    if args.threads < 1:

        raise ValueError(
            "--threads must be >= 1"
        )

    print()
    print("=" * 70)
    print("PostgreSQL HA / PgBouncer workload test")
    print("=" * 70)

    print(
        f"Target          : "
        f"{args.host}:{args.port}"
    )

    print(
        f"Database        : "
        f"{args.database}"
    )

    print(
        f"User            : "
        f"{args.user}"
    )

    print(
        f"Threads         : "
        f"{args.threads}"
    )

    print(
        f"Write ratio     : "
        f"{args.write_percent}%"
    )

    print(
        f"Connection mode : "
        f"{args.connection_mode}"
    )

    if args.continuous:

        print(
            "Mode            : "
            "continuous"
        )

    elif args.duration is not None:

        print(
            f"Mode            : "
            f"{args.duration} seconds"
        )

    else:

        print(
            f"Mode            : "
            f"{args.operations} operations/thread"
        )

    print()

    initialize_database(args)

    global_start = (
        time.perf_counter()
    )

    if (
        args.connection_mode
        == "persistent"
    ):

        worker = (
            worker_persistent
        )

    else:

        worker = (
            worker_per_operation
        )

    try:

        with ThreadPoolExecutor(
            max_workers=args.threads
        ) as executor:

            futures = [

                executor.submit(
                    worker,
                    args,
                    thread_id,
                    global_start,
                )

                for thread_id
                in range(
                    1,
                    args.threads + 1,
                )
            ]

            for future in as_completed(
                futures
            ):

                future.result()

    except KeyboardInterrupt:

        print()
        print(
            "Ctrl+C received. "
            "Stopping workers..."
        )

        stop_event.set()

    finally:

        stop_event.set()

    elapsed = (
        time.perf_counter()
        - global_start
    )

    print_report(
        elapsed
    )

    reconcile_writes(
        args
    )


if __name__ == "__main__":
    main()
