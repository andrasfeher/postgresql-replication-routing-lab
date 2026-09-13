#!/usr/bin/env python3

"""
Patroni Switchover Data-Loss Test
=================================

Purpose
-------
This program continuously writes individually committed transactions to
PostgreSQL while a Patroni switchover is performed.

It records both:

    1. What the client observed.
    2. The PostgreSQL WAL position associated with each transaction.

After the switchover, the verifier compares the client-side observations
with what actually exists on the new PostgreSQL primary.


Main question
-------------
The most important question is:

    "Did PostgreSQL acknowledge a COMMIT to the application and then lose
     that transaction during the Patroni switchover?"

This is different from simply detecting connection errors.

During a switchover, a client connection can disappear at almost exactly
the same moment PostgreSQL commits a transaction. Therefore:

    connection error != transaction definitely rolled back


Transaction classifications
---------------------------

COMMIT_ACK

    conn.commit() returned successfully.

    PostgreSQL therefore told the application that the transaction
    committed.

    If this row is missing after switchover, that is potentially genuine
    application-visible data loss.


UNKNOWN

    An exception occurred before or during COMMIT.

    PostgreSQL may have:

        - rolled back the transaction

    OR:

        - committed the transaction but lost the client connection before
          the acknowledgement reached Python.

    The verification phase determines which case actually occurred.


WAL LSN tracking
----------------

For each INSERT, the script captures:

    pg_current_wal_insert_lsn()

immediately after the INSERT and before COMMIT.

Example:

    0/3035A48

This tells us the WAL insertion position reached while processing the
transaction.

IMPORTANT:

This is NOT necessarily the LSN of the transaction's COMMIT WAL record.

The sequence is approximately:

    INSERT
      |
      +--> WAL generated
      |
      +--> pg_current_wal_insert_lsn()
      |
    COMMIT
      |
      +--> COMMIT WAL record generated
      |
      +--> WAL flushed according to synchronous_commit

Therefore the captured transaction_wal_lsn should be considered a useful
transaction WAL marker rather than the exact commit-record LSN.

After switchover, the verifier also captures the WAL position of the node
being queried:

    primary:
        pg_current_wal_lsn()

    standby:
        pg_last_wal_replay_lsn()

This allows us to correlate transactions with the WAL position reached by
the promoted server.


Requirements
------------

Python 3
psycopg 3

Install:

    python3 -m pip install "psycopg[binary]"


Typical usage
-------------

Writer:

    python3 patroni_loss_test.py \
        --dsn "host=localhost port=5432 dbname=postgres user=postgres password=secret" \
        --count 100000 \
        --delay 0.01

Perform the Patroni switchover while that is running.

Then verify using the run ID printed by the writer:

    python3 patroni_loss_test.py \
        --mode verify \
        --dsn "host=localhost port=5432 dbname=postgres user=postgres password=secret" \
        --log patroni_switchover_test.csv \
        --run-id <UUID>
"""

import argparse
import csv
import sys
import time
import uuid

from datetime import datetime, timezone

import psycopg


# ===========================================================================
# Utility functions
# ===========================================================================

def utc_now():
    """
    Return the current UTC timestamp in ISO-8601 format.

    Example:

        2026-09-13T16:15:27.123456+00:00

    Using UTC makes timestamps easier to compare between machines.
    """

    return datetime.now(timezone.utc).isoformat()


def connect(conninfo, autocommit=False):
    """
    Establish a PostgreSQL connection.

    Connection failures are retried indefinitely.

    This is intentional for a Patroni switchover test because there will
    normally be a short interval where the write endpoint is unavailable.

    For example:

        HAProxy
           |
           X old primary disappears
           |
           X no writable backend temporarily
           |
           + new primary becomes available

    We want the test writer to survive that interruption rather than exit.

    Parameters
    ----------
    conninfo:
        psycopg connection string.

    autocommit:
        False by default because the test needs explicit control over
        COMMIT boundaries.

    Returns
    -------
    psycopg.Connection
    """

    while True:

        try:
            return psycopg.connect(
                conninfo,
                autocommit=autocommit
            )

        except Exception as exc:

            print(
                f"[{utc_now()}] Connection failed: {exc}",
                file=sys.stderr
            )

            # Avoid reconnecting in an extremely tight loop.
            time.sleep(1)


# ===========================================================================
# Database initialization
# ===========================================================================

def init_table(conn):
    """
    Create the PostgreSQL table used by the test.

    Each Python transaction inserts exactly one row.

    Columns
    -------

    test_run_id
        UUID identifying one complete test execution.

    seq_id
        Transaction sequence number within this test run.

    client_tx_id
        Globally unique UUID generated by the Python client.

    inserted_at
        Timestamp generated by PostgreSQL itself.

    server_host
        IP address of the PostgreSQL server that handled the transaction.

    server_port
        PostgreSQL server port.

    pg_is_recovery
        False -> PostgreSQL primary
        True  -> PostgreSQL standby

    transaction_wal_lsn
        WAL insertion position observed immediately after the INSERT and
        before COMMIT.

    Primary key
    -----------

    The primary key is:

        (test_run_id, seq_id)

    This allows every test run to start its sequence again from 1.
    """

    with conn.cursor() as cur:

        cur.execute("""
            CREATE TABLE IF NOT EXISTS patroni_switchover_test
            (
                test_run_id         UUID        NOT NULL,
                seq_id              BIGINT      NOT NULL,

                client_tx_id        UUID        NOT NULL UNIQUE,

                inserted_at         TIMESTAMPTZ NOT NULL
                                    DEFAULT clock_timestamp(),

                server_host         TEXT,
                server_port         INTEGER,
                pg_is_recovery      BOOLEAN,

                transaction_wal_lsn PG_LSN,

                PRIMARY KEY
                (
                    test_run_id,
                    seq_id
                )
            );
        """)

        # ------------------------------------------------------------------
        # Upgrade support
        # ------------------------------------------------------------------
        #
        # If an earlier version of the test table already exists without the
        # WAL column, add it.
        #
        # PostgreSQL's ADD COLUMN IF NOT EXISTS makes this safe to run every
        # time.
        #
        cur.execute("""
            ALTER TABLE patroni_switchover_test
            ADD COLUMN IF NOT EXISTS transaction_wal_lsn PG_LSN;
        """)

    conn.commit()


# ===========================================================================
# PostgreSQL server information
# ===========================================================================

def get_server_info(cur):
    """
    Determine which PostgreSQL instance currently handles the connection.

    This is useful when the Python application connects through HAProxy.

    The client might always connect to:

        localhost:5432

    while the real PostgreSQL connection changes from:

        pg-node1

    to:

        pg-node2

    during switchover.

    pg_is_in_recovery():

        False
            Primary / leader.

        True
            Standby / replica.

    Returns
    -------

    (
        server_host,
        server_port,
        pg_is_recovery
    )
    """

    cur.execute("""
        SELECT
            inet_server_addr()::text,
            inet_server_port(),
            pg_is_in_recovery();
    """)

    return cur.fetchone()


# ===========================================================================
# Writer
# ===========================================================================

def write_test(conninfo, count, delay, logfile, run_id):
    """
    Generate individually committed transactions while Patroni switches
    leaders.

    Every row gets its own transaction:

        BEGIN;
        INSERT;
        SELECT pg_current_wal_insert_lsn();
        COMMIT;

    followed by the next transaction.

    This intentionally avoids batching many rows into one transaction.
    """

    # Establish initial connection.
    conn = connect(conninfo)

    # Ensure the test table exists.
    init_table(conn)

    # ----------------------------------------------------------------------
    # Local client log
    # ----------------------------------------------------------------------
    #
    # The CSV log lives outside PostgreSQL.
    #
    # This is critical.
    #
    # If PostgreSQL loses data, we need an independent record showing what
    # the client believed had committed.
    #
    with open(
        logfile,
        "a",
        newline="",
        buffering=1
    ) as f:

        writer = csv.writer(f)

        # Write the header only for a new file.
        if f.tell() == 0:

            writer.writerow([
                "run_id",
                "seq_id",
                "client_tx_id",
                "attempt_time",
                "result",
                "error",
                "server_host",
                "server_port",
                "pg_is_recovery",
                "transaction_wal_lsn",
            ])

        # ------------------------------------------------------------------
        # Main transaction loop
        # ------------------------------------------------------------------

        for seq_id in range(1, count + 1):

            # Generate a unique transaction identifier on the CLIENT.
            #
            # We do this before sending anything to PostgreSQL so that the
            # transaction remains identifiable even if the connection dies.
            client_tx_id = uuid.uuid4()

            attempt_time = utc_now()

            # Server metadata is initialized to empty values because the
            # connection may fail before these values can be collected.
            server_host = ""
            server_port = ""
            pg_is_recovery = ""
            transaction_wal_lsn = ""

            try:

                # Re-establish the connection if the previous switchover
                # event closed it.
                if conn.closed:
                    conn = connect(conninfo)

                with conn.cursor() as cur:

                    # ------------------------------------------------------
                    # Identify the PostgreSQL server
                    # ------------------------------------------------------

                    (
                        server_host,
                        server_port,
                        pg_is_recovery
                    ) = get_server_info(cur)

                    # ------------------------------------------------------
                    # Insert one test row
                    # ------------------------------------------------------

                    cur.execute("""
                        INSERT INTO patroni_switchover_test
                        (
                            test_run_id,
                            seq_id,
                            client_tx_id,
                            server_host,
                            server_port,
                            pg_is_recovery
                        )
                        VALUES
                        (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        );
                    """, (
                        run_id,
                        seq_id,
                        client_tx_id,
                        server_host,
                        server_port,
                        pg_is_recovery,
                    ))

                    # ------------------------------------------------------
                    # Capture WAL insertion position
                    # ------------------------------------------------------
                    #
                    # The INSERT above generated WAL.
                    #
                    # pg_current_wal_insert_lsn() returns the current WAL
                    # insertion position on this PostgreSQL primary.
                    #
                    # Example:
                    #
                    #     0/3035A48
                    #
                    # IMPORTANT:
                    #
                    # We have NOT committed yet.
                    #
                    # Therefore this LSN does NOT represent the COMMIT record
                    # itself.
                    #
                    # It is a useful WAL marker showing how far WAL insertion
                    # had progressed while processing this transaction.
                    #
                    cur.execute("""
                        SELECT pg_current_wal_insert_lsn();
                    """)

                    transaction_wal_lsn = str(
                        cur.fetchone()[0]
                    )

                    # Store that LSN inside the test row as well.
                    #
                    # The row and its WAL marker therefore become part of
                    # the same transaction.
                    cur.execute("""
                        UPDATE patroni_switchover_test
                        SET transaction_wal_lsn = %s
                        WHERE
                            test_run_id = %s
                            AND seq_id = %s;
                    """, (
                        transaction_wal_lsn,
                        run_id,
                        seq_id
                    ))

                # ==========================================================
                # MOST IMPORTANT OPERATION IN THE TEST
                # ==========================================================
                #
                # Python sends COMMIT and waits for PostgreSQL's response.
                #
                # If commit() returns successfully, PostgreSQL has told the
                # application:
                #
                #     "This transaction committed."
                #
                # We therefore record:
                #
                #     COMMIT_ACK
                #
                # If that row later disappears after Patroni promotes a new
                # server, that is the important data-loss condition.
                #
                conn.commit()

                # Record the successful commit independently in the local
                # CSV file.
                writer.writerow([
                    run_id,
                    seq_id,
                    client_tx_id,
                    attempt_time,
                    "COMMIT_ACK",
                    "",
                    server_host,
                    server_port,
                    pg_is_recovery,
                    transaction_wal_lsn,
                ])

                print(
                    f"{seq_id:8d}  "
                    f"COMMIT_ACK  "
                    f"{server_host}:{server_port}  "
                    f"LSN={transaction_wal_lsn}  "
                    f"recovery={pg_is_recovery}"
                )

            except Exception as exc:

                # ==========================================================
                # AMBIGUOUS COMMIT
                # ==========================================================
                #
                # Imagine this:
                #
                #
                # Python                         PostgreSQL
                #   |                               |
                #   | INSERT ---------------------> |
                #   |                               |
                #   | WAL generated                 |
                #   |                               |
                #   | COMMIT ---------------------> |
                #   |                               |
                #   |                         COMMIT succeeds
                #   |                               |
                #   |                         connection dies
                #   |                               X
                #   |
                #   | exception
                #
                #
                # From Python's point of view, we do not know whether COMMIT
                # succeeded.
                #
                # Therefore:
                #
                #     exception != rollback
                #
                # The correct state is:
                #
                #     UNKNOWN
                #
                # The verification phase will determine whether the row
                # actually survived.
                #

                writer.writerow([
                    run_id,
                    seq_id,
                    client_tx_id,
                    attempt_time,
                    "UNKNOWN",
                    repr(exc),
                    server_host,
                    server_port,
                    pg_is_recovery,
                    transaction_wal_lsn,
                ])

                print(
                    f"{seq_id:8d}  "
                    f"UNKNOWN     "
                    f"LSN={transaction_wal_lsn or 'N/A'}  "
                    f"{exc}",
                    file=sys.stderr
                )

                # The transaction may still be open locally from psycopg's
                # point of view, but after this kind of connection failure
                # the safest action is to discard the connection entirely.
                try:
                    conn.close()

                except Exception:
                    pass

                # Reconnect through the SAME HA endpoint.
                #
                # Once HAProxy recognizes the new Patroni leader, this
                # connection should reach the promoted primary.
                conn = connect(conninfo)

            # Optional delay between transactions.
            time.sleep(delay)

    conn.close()

    print()
    print("=" * 70)
    print("Writer finished")
    print("=" * 70)

    print(f"Test run ID: {run_id}")
    print(f"Client log:  {logfile}")


# ===========================================================================
# Verification helpers
# ===========================================================================

def get_current_node_wal_info(cur):
    """
    Return WAL information about the PostgreSQL server currently handling
    the verification connection.

    On a primary:

        pg_current_wal_lsn()

    is available.

    On a standby:

        pg_last_wal_replay_lsn()

    tells us how far WAL replay has progressed.

    We expose both through one effective_wal_lsn value.
    """

    cur.execute("""
        SELECT
            inet_server_addr()::text,
            inet_server_port(),
            pg_is_in_recovery(),

            CASE
                WHEN pg_is_in_recovery()
                    THEN pg_last_wal_replay_lsn()

                ELSE pg_current_wal_lsn()
            END AS effective_wal_lsn;
    """)

    return cur.fetchone()


# ===========================================================================
# Verification
# ===========================================================================

def verify(conninfo, logfile, run_id):
    """
    Compare:

        client-side transaction results

    against:

        rows that exist after Patroni switchover.

    It also reports WAL LSN information.


    Result meanings
    ---------------

    COMMIT_ACK + row exists

        Normal successful transaction.


    COMMIT_ACK + row missing

        This is the dangerous case.

        PostgreSQL acknowledged the commit to the application, but the data
        cannot be found after switchover.


    UNKNOWN + row exists

        The transaction committed, but the acknowledgement was lost.


    UNKNOWN + row missing

        The transaction did not survive or never committed.
    """

    expected = {}

    # ----------------------------------------------------------------------
    # Read independent client-side log
    # ----------------------------------------------------------------------

    with open(logfile, newline="") as f:

        reader = csv.DictReader(f)

        for row in reader:

            # A CSV file may contain many separate test runs.
            #
            # Only load records belonging to the requested run.
            if row["run_id"] == str(run_id):

                expected[int(row["seq_id"])] = row

    if not expected:

        print(
            f"No client records found for run ID {run_id}",
            file=sys.stderr
        )

        return

    # ----------------------------------------------------------------------
    # Connect to PostgreSQL after switchover
    # ----------------------------------------------------------------------

    conn = connect(conninfo)

    with conn.cursor() as cur:

        # --------------------------------------------------------------
        # Identify current PostgreSQL node and its WAL position
        # --------------------------------------------------------------

        (
            verify_server_host,
            verify_server_port,
            verify_is_recovery,
            verify_wal_lsn
        ) = get_current_node_wal_info(cur)

        # --------------------------------------------------------------
        # Read all surviving test rows
        # --------------------------------------------------------------

        cur.execute("""
            SELECT
                seq_id,
                client_tx_id::text,
                inserted_at,
                server_host,
                server_port,
                transaction_wal_lsn::text
            FROM patroni_switchover_test
            WHERE test_run_id = %s
            ORDER BY seq_id;
        """, (run_id,))

        db_rows = {
            row[0]: row
            for row in cur.fetchall()
        }

        # --------------------------------------------------------------
        # Highest transaction WAL marker that survived
        # --------------------------------------------------------------

        cur.execute("""
            SELECT
                max(transaction_wal_lsn)
            FROM patroni_switchover_test
            WHERE test_run_id = %s;
        """, (run_id,))

        highest_surviving_lsn = cur.fetchone()[0]

    conn.close()

    # ======================================================================
    # Classification
    # ======================================================================

    ack_missing = []

    unknown_present = []

    unknown_missing = []

    unexpected = []

    # ----------------------------------------------------------------------
    # Compare every client transaction with PostgreSQL
    # ----------------------------------------------------------------------

    for seq_id, row in expected.items():

        state = row["result"]

        exists = seq_id in db_rows

        if state == "COMMIT_ACK":

            if not exists:

                ack_missing.append({
                    "seq_id": seq_id,
                    "client_tx_id": row["client_tx_id"],
                    "wal_lsn": row["transaction_wal_lsn"],
                })

        elif state == "UNKNOWN":

            if exists:

                unknown_present.append({
                    "seq_id": seq_id,
                    "client_tx_id": row["client_tx_id"],
                    "wal_lsn": row["transaction_wal_lsn"],
                })

            else:

                unknown_missing.append({
                    "seq_id": seq_id,
                    "client_tx_id": row["client_tx_id"],
                    "wal_lsn": row["transaction_wal_lsn"],
                })

    # ----------------------------------------------------------------------
    # Check for DB records not present in the client log
    # ----------------------------------------------------------------------

    for seq_id in db_rows:

        if seq_id not in expected:

            unexpected.append(seq_id)

    # ======================================================================
    # Report
    # ======================================================================

    print()
    print("=" * 78)
    print("Patroni switchover verification")
    print("=" * 78)

    print()
    print("Verification PostgreSQL node")
    print("----------------------------")

    print(
        f"Server:                      "
        f"{verify_server_host}:{verify_server_port}"
    )

    print(
        f"pg_is_in_recovery():         "
        f"{verify_is_recovery}"
    )

    print(
        f"Current/replay WAL LSN:      "
        f"{verify_wal_lsn}"
    )

    print(
        f"Highest surviving test LSN:  "
        f"{highest_surviving_lsn}"
    )

    print()
    print("Test summary")
    print("------------")

    print(
        f"Run ID:                      "
        f"{run_id}"
    )

    print(
        f"Client attempts:             "
        f"{len(expected)}"
    )

    print(
        f"Rows found in PostgreSQL:    "
        f"{len(db_rows)}"
    )

    print()
    print(
        f"ACKed commits missing:       "
        f"{len(ack_missing)}"
    )

    print(
        f"UNKNOWN but present:         "
        f"{len(unknown_present)}"
    )

    print(
        f"UNKNOWN and absent:          "
        f"{len(unknown_missing)}"
    )

    print(
        f"Unexpected DB rows:          "
        f"{len(unexpected)}"
    )

    # ======================================================================
    # Potential data loss
    # ======================================================================

    if ack_missing:

        print()
        print("*" * 78)
        print("POSSIBLE DATA LOSS DETECTED")
        print("*" * 78)

        print()
        print(
            "The following transactions were acknowledged by PostgreSQL"
        )

        print(
            "but cannot be found after the switchover:"
        )

        print()

        for item in ack_missing:

            print(
                f"seq={item['seq_id']:8d}  "
                f"LSN={item['wal_lsn']:15s}  "
                f"tx={item['client_tx_id']}"
            )

    else:

        print()
        print("No acknowledged transaction was lost.")

    # ======================================================================
    # Ambiguous transactions that actually committed
    # ======================================================================

    if unknown_present:

        print()
        print("UNKNOWN transactions that actually survived")
        print("-------------------------------------------")

        for item in unknown_present:

            print(
                f"seq={item['seq_id']:8d}  "
                f"LSN={item['wal_lsn']:15s}  "
                f"tx={item['client_tx_id']}"
            )

    # ======================================================================
    # Ambiguous transactions that are absent
    # ======================================================================

    if unknown_missing:

        print()
        print("UNKNOWN transactions absent after switchover")
        print("--------------------------------------------")

        for item in unknown_missing:

            print(
                f"seq={item['seq_id']:8d}  "
                f"LSN={item['wal_lsn'] or 'N/A':15s}  "
                f"tx={item['client_tx_id']}"
            )


# ===========================================================================
# Command-line arguments
# ===========================================================================

def parse_args():
    """
    Define command-line arguments.

    Program modes:

        write
            Continuously generate transactions.

        verify
            Compare the client-side CSV log with PostgreSQL.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Test PostgreSQL data durability "
            "during Patroni switchover."
        )
    )

    parser.add_argument(
        "--dsn",
        required=True,
        help=(
            "PostgreSQL connection string. Example: "
            '"host=localhost port=5432 '
            'dbname=postgres user=postgres password=secret"'
        ),
    )

    parser.add_argument(
        "--mode",
        choices=[
            "write",
            "verify"
        ],
        default="write",
        help=(
            "write = generate workload; "
            "verify = check results after switchover."
        ),
    )

    parser.add_argument(
        "--count",
        type=int,
        default=100000,
        help=(
            "Number of individually committed transactions "
            "to attempt."
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.01,
        help=(
            "Delay between transaction attempts in seconds."
        ),
    )

    parser.add_argument(
        "--log",
        default="patroni_switchover_test.csv",
        help=(
            "Independent client-side CSV transaction log."
        ),
    )

    parser.add_argument(
        "--run-id",
        help=(
            "UUID identifying the test execution. "
            "Generated automatically in write mode."
        ),
    )

    return parser.parse_args()


# ===========================================================================
# Main program
# ===========================================================================

def main():
    """
    Main application entry point.
    """

    args = parse_args()

    # ----------------------------------------------------------------------
    # WRITE MODE
    # ----------------------------------------------------------------------

    if args.mode == "write":

        # Create a new test UUID unless the caller explicitly supplied one.
        run_id = (
            uuid.UUID(args.run_id)
            if args.run_id
            else uuid.uuid4()
        )

        write_test(
            conninfo=args.dsn,
            count=args.count,
            delay=args.delay,
            logfile=args.log,
            run_id=run_id,
        )

    # ----------------------------------------------------------------------
    # VERIFY MODE
    # ----------------------------------------------------------------------

    elif args.mode == "verify":

        if not args.run_id:

            print(
                "--run-id is required in verify mode",
                file=sys.stderr
            )

            sys.exit(1)

        verify(
            conninfo=args.dsn,
            logfile=args.log,
            run_id=uuid.UUID(args.run_id),
        )


# ===========================================================================
# Standard Python entry-point guard
# ===========================================================================

if __name__ == "__main__":
    main()

