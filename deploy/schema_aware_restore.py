#!/usr/bin/env python3
"""
Schema-Aware PostgreSQL Data Restoration

Handles schema evolution during MIRA migrations by:
1. Creating a temp database to extract data from the backup dump
2. Finding common columns between backup and current schema
3. Copying only matching columns, respecting FK dependencies
4. Detecting conflicts for config tables (account_tiers, internal_llm)

Exit codes:
  0 - Success
  1 - Error
  2 - Conflict detected (re-run with --prefer-backup or --prefer-schema)
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import psycopg2
from psycopg2.extras import Json


def json_serializer(obj):
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        return obj.hex()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

# Config tables that require conflict detection (contain application configuration, not user data)
CONFIG_TABLES = {'account_tiers', 'internal_llm'}


def get_fk_ordered_tables(cursor, tables: set[str]) -> list[str]:
    """
    Compute table insertion order based on FK dependencies.
    Parents must be inserted before children.
    """
    # Get FK relationships using pg_constraint (more reliable than information_schema)
    cursor.execute("""
        SELECT
            conrelid::regclass::text AS child_table,
            confrelid::regclass::text AS parent_table
        FROM pg_constraint
        WHERE contype = 'f'
          AND conrelid::regclass::text != confrelid::regclass::text
    """)

    # Build dependency graph
    dependencies = defaultdict(set)
    for child, parent in cursor.fetchall():
        if child in tables and parent in tables:
            dependencies[child].add(parent)

    # Topological sort (Kahn's algorithm)
    # Tables with no dependencies can go first
    in_degree = {t: 0 for t in tables}
    for child, parents in dependencies.items():
        in_degree[child] = len(parents)

    # Start with tables that have no parents
    queue = [t for t in tables if in_degree[t] == 0]
    result = []

    while queue:
        # Sort for deterministic order
        queue.sort()
        table = queue.pop(0)
        result.append(table)

        # Reduce in-degree for tables that depend on this one
        for child, parents in dependencies.items():
            if table in parents:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

    # Any remaining tables have circular dependencies - add them anyway with warning
    remaining = [t for t in tables if t not in result]
    if remaining:
        print(f"Warning: Circular FK dependencies detected for: {remaining}", file=sys.stderr)
        result.extend(sorted(remaining))

    return result


def get_table_primary_key(cursor, table: str) -> list[str]:
    """Query PostgreSQL for the primary key column(s) of a table."""
    cursor.execute("""
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = %s::regclass
          AND i.indisprimary
        ORDER BY array_position(i.indkey, a.attnum)
    """, (table,))
    return [row[0] for row in cursor.fetchall()]


def save_orphaned_data(orphaned_data: dict, backup_dir: str, loud: bool):
    """Save orphaned column data to a JSON file in the backup directory."""
    if not orphaned_data:
        return

    # Determine output path
    if backup_dir and os.path.isdir(backup_dir):
        output_path = os.path.join(backup_dir, 'orphaned_columns.json')
    else:
        output_path = '/tmp/mira_orphaned_columns.json'

    with open(output_path, 'w') as f:
        json.dump(orphaned_data, f, indent=2, default=json_serializer)

    if loud:
        total_cols = sum(len(table_data.get('orphaned_columns', [])) for table_data in orphaned_data.values())
        print(f"\n  Orphaned columns saved to: {output_path}")
        print(f"  ({total_cols} columns across {len(orphaned_data)} tables)")


def get_psql_env(db_password: str) -> tuple[list[str], dict]:
    """Get psql command prefix and environment."""
    env = dict(subprocess.os.environ)
    cmd = ['psql', '-U', 'mira_admin', '-h', 'localhost']
    if db_password:
        env['PGPASSWORD'] = db_password
    return cmd, env


def run_pg_restore(db_password: str, database: str, backup_file: str) -> tuple[bool, str]:
    """Run pg_restore to restore dump into database."""
    env = dict(subprocess.os.environ)
    if db_password:
        env['PGPASSWORD'] = db_password

    cmd = [
        'pg_restore',
        '-U', 'mira_admin',
        '-h', 'localhost',
        '-d', database,
        backup_file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return result.returncode == 0, result.stderr


def create_temp_database(db_password: str) -> bool:
    """Create temporary database for restore."""
    # First drop if exists (separate command to avoid transaction block)
    drop_temp_database(db_password)

    cmd, env = get_psql_env(db_password)
    cmd.extend(['-d', 'postgres', '-c', 'CREATE DATABASE mira_restore_temp;'])
    result = subprocess.run(cmd, capture_output=True, env=env)
    return result.returncode == 0


def drop_temp_database(db_password: str):
    """Drop temporary database."""
    cmd, env = get_psql_env(db_password)
    cmd.extend(['-d', 'postgres', '-c', 'DROP DATABASE IF EXISTS mira_restore_temp;'])
    subprocess.run(cmd, capture_output=True, env=env)


def enable_vector_extension(os_type: str, db_password: str):
    """Enable vector extension in temp database (requires superuser on Linux)."""
    if os_type == 'linux':
        # Vector extension requires superuser, use sudo
        cmd = ['sudo', '-u', 'postgres', 'psql', '-d', 'mira_restore_temp',
               '-c', 'CREATE EXTENSION IF NOT EXISTS vector;']
        subprocess.run(cmd, capture_output=True)
    else:
        # On macOS, try with mira_admin (may work if granted)
        cmd, env = get_psql_env(db_password)
        cmd.extend(['-d', 'mira_restore_temp', '-c', 'CREATE EXTENSION IF NOT EXISTS vector;'])
        subprocess.run(cmd, capture_output=True, env=env)


def get_connection(database: str, db_password: str):
    """Get a psycopg2 connection to the specified database."""
    # Use mira_admin on both platforms - has necessary permissions and password from Vault
    return psycopg2.connect(
        dbname=database,
        user='mira_admin',
        host='localhost',
        password=db_password or ''
    )


def get_table_columns(cursor) -> dict[str, set[str]]:
    """Get all tables and their columns from the database."""
    cursor.execute("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name IN (
              SELECT table_name FROM information_schema.tables
              WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
          )
    """)

    tables = defaultdict(set)
    for table, col in cursor.fetchall():
        tables[table].add(col)
    return dict(tables)


def get_table_data(cursor, table: str, columns: list[str]) -> list[dict]:
    """Get all rows from a table as list of dicts."""
    cols_str = ', '.join(f'"{c}"' for c in columns)
    cursor.execute(f'SELECT {cols_str} FROM "{table}"')
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def adapt_value(val):
    """Convert Python types to psycopg2-compatible types."""
    if isinstance(val, dict):
        return Json(val)
    elif isinstance(val, list):
        # Check if list contains dicts (JSONB array)
        if val and isinstance(val[0], dict):
            return Json(val)
    return val


def adapt_row(row: tuple) -> tuple:
    """Adapt all values in a row for psycopg2."""
    return tuple(adapt_value(v) for v in row)


def detect_conflicts(backup_data: list[dict], current_data: list[dict], pk_col: str) -> list[dict]:
    """
    Detect conflicts between backup and current data.
    Returns list of conflict dicts with 'pk', 'column', 'backup_value', 'current_value'.
    """
    conflicts = []

    # Index current data by primary key
    current_by_pk = {row[pk_col]: row for row in current_data}

    for backup_row in backup_data:
        pk = backup_row[pk_col]
        if pk in current_by_pk:
            current_row = current_by_pk[pk]
            # Compare common columns
            common_cols = set(backup_row.keys()) & set(current_row.keys())
            for col in common_cols:
                if col == pk_col:
                    continue
                if backup_row[col] != current_row[col]:
                    conflicts.append({
                        'pk': pk,
                        'column': col,
                        'backup_value': backup_row[col],
                        'current_value': current_row[col],
                    })

    return conflicts


def resolve_conflicts_interactive(
    table: str,
    conflicts: list[dict],
    pk_col: str,
    backup_data: list[dict],
    current_data: list[dict]
) -> list[dict]:
    """
    Interactively resolve conflicts, returning the final data to insert.
    Returns list of row dicts with resolved values.
    """
    print(f"\n{'='*60}")
    print(f"CONFIG TABLE CONFLICT: {table}")
    print(f"{'='*60}")

    # Group conflicts by primary key
    by_pk = defaultdict(list)
    for c in conflicts:
        by_pk[c['pk']].append(c)

    # Index data by PK for easy lookup
    backup_by_pk = {row[pk_col]: row for row in backup_data}
    current_by_pk = {row[pk_col]: row for row in current_data}

    # Track resolved rows
    resolved = {}
    use_all_backup = False
    use_all_schema = False

    for pk, pk_conflicts in by_pk.items():
        print(f"\nRow '{pk}' differs:\n")
        print(f"  {'Column':<20} | {'Backup':<30} | {'Schema':<30}")
        print(f"  {'-'*20} | {'-'*30} | {'-'*30}")
        for c in pk_conflicts:
            bv = str(c['backup_value'])[:30]
            cv = str(c['current_value'])[:30]
            print(f"  {c['column']:<20} | {bv:<30} | {cv:<30}")

        if use_all_backup:
            choice = 'b'
            print(f"\n  Using backup (--all)")
        elif use_all_schema:
            choice = 's'
            print(f"\n  Using schema (--all)")
        else:
            print(f"\n  [b]ackup  [s]chema  [B]ackup-all  [S]chema-all  [q]uit")
            choice = input(f"  Choice for '{pk}': ").strip().lower()

        if choice == 'q':
            print("\nMigration aborted by user.")
            sys.exit(1)
        elif choice == 'b':
            resolved[pk] = backup_by_pk[pk]
        elif choice == 's':
            resolved[pk] = current_by_pk[pk]
        elif choice in ('B', 'backup-all'):
            use_all_backup = True
            resolved[pk] = backup_by_pk[pk]
        elif choice in ('S', 'schema-all'):
            use_all_schema = True
            resolved[pk] = current_by_pk[pk]
        else:
            # Default to backup
            print(f"  Invalid choice, using backup")
            resolved[pk] = backup_by_pk[pk]

    # Build final result: resolved rows + backup-only rows + schema-only rows (if not in backup)
    result = []

    # Add all backup rows, using resolved version if available
    for row in backup_data:
        pk = row[pk_col]
        if pk in resolved:
            result.append(resolved[pk])
        else:
            result.append(row)

    # Add schema-only rows (rows in current that aren't in backup)
    backup_pks = {row[pk_col] for row in backup_data}
    for row in current_data:
        if row[pk_col] not in backup_pks:
            result.append(row)

    return result


def print_conflicts(table: str, conflicts: list[dict], pk_col: str):
    """Print conflict report in a readable format (non-interactive mode)."""
    print(f"\n{'='*60}")
    print(f"CONFIG TABLE CONFLICT: {table}")
    print(f"{'='*60}")

    # Group by primary key
    by_pk = defaultdict(list)
    for c in conflicts:
        by_pk[c['pk']].append(c)

    for pk, pk_conflicts in by_pk.items():
        print(f"\nRow '{pk}' differs between backup and current schema:\n")
        print(f"  {'Column':<20} | {'Backup Value':<30} | {'Current Value':<30}")
        print(f"  {'-'*20} | {'-'*30} | {'-'*30}")
        for c in pk_conflicts:
            bv = str(c['backup_value'])[:30]
            cv = str(c['current_value'])[:30]
            print(f"  {c['column']:<20} | {bv:<30} | {cv:<30}")

    print(f"\nResolution options:")
    print(f"  1. Re-run with --prefer-backup to use backup values")
    print(f"  2. Re-run with --prefer-schema to keep current schema values")
    print(f"  3. Manually resolve in database after migration")
    print(f"\nMigration aborted. No user data was modified.")


def migrate_table(
    src_cursor,
    dst_cursor,
    dst_conn,
    table: str,
    src_cols: set[str],
    dst_cols: set[str],
    prefer_backup: bool,
    prefer_schema: bool,
    interactive: bool,
    loud: bool
) -> tuple[bool, int, list[dict] | None, dict | None]:
    """
    Migrate a single table.
    Returns (success, row_count, conflicts_or_none, orphaned_data_or_none).
    """
    common_cols = sorted(src_cols & dst_cols)
    orphaned_cols = src_cols - dst_cols

    if not common_cols:
        if loud:
            print(f"  {table}: no common columns, skipping")
        return True, 0, None, None

    # Get source data for common columns
    cols_str = ', '.join(f'"{c}"' for c in common_cols)
    src_cursor.execute(f'SELECT {cols_str} FROM "{table}"')
    rows = src_cursor.fetchall()

    # Collect orphaned column data if any
    orphaned_data = None
    if orphaned_cols and rows:
        pk_cols = get_table_primary_key(src_cursor, table)
        if pk_cols:
            # Query orphaned columns with primary key
            all_orphan_cols = sorted(orphaned_cols)
            pk_and_orphan = pk_cols + [c for c in all_orphan_cols if c not in pk_cols]
            orphan_cols_str = ', '.join(f'"{c}"' for c in pk_and_orphan)
            src_cursor.execute(f'SELECT {orphan_cols_str} FROM "{table}"')
            orphan_rows = src_cursor.fetchall()

            if orphan_rows:
                orphaned_data = {
                    'orphaned_columns': all_orphan_cols,
                    'primary_key': pk_cols,
                    'rows': [dict(zip(pk_and_orphan, row)) for row in orphan_rows]
                }
                if loud:
                    print(f"  {table}: preserving {len(orphaned_cols)} orphaned columns: {', '.join(sorted(orphaned_cols))}")

    if not rows:
        if loud:
            print(f"  {table}: 0 rows")
        return True, 0, None, orphaned_data

    # For config tables, check for conflicts and use UPSERT
    use_upsert = False
    pk_col = None
    if table in CONFIG_TABLES:
        # Dynamically get primary key column(s)
        pk_cols = get_table_primary_key(src_cursor, table)
        pk_col = pk_cols[0] if pk_cols else None

        if not pk_col:
            if loud:
                print(f"  {table}: no primary key found, skipping conflict detection")
        else:
            # Get current data from destination
            dst_cursor.execute(f'SELECT {cols_str} FROM "{table}"')
            current_rows = dst_cursor.fetchall()

            if current_rows:
                # Convert to dicts for comparison
                backup_data = [dict(zip(common_cols, row)) for row in rows]
                current_data = [dict(zip(common_cols, row)) for row in current_rows]

                conflicts = detect_conflicts(backup_data, current_data, pk_col)

                if conflicts:
                    if prefer_backup:
                        # Use UPSERT to update existing rows with backup values
                        use_upsert = True
                    elif prefer_schema:
                        # Keep current, skip this table
                        if loud:
                            print(f"  {table}: keeping current schema values (--prefer-schema)")
                        return True, 0, None, orphaned_data
                    elif interactive:
                        # Interactive resolution
                        resolved_data = resolve_conflicts_interactive(
                            table, conflicts, pk_col, backup_data, current_data
                        )
                        # Convert resolved dicts back to tuples in column order
                        rows = [tuple(row[c] for c in common_cols) for row in resolved_data]
                        use_upsert = True
                    else:
                        # Non-interactive, no preference - report conflict and abort
                        return False, 0, conflicts, None
                else:
                    # No conflicts - data is identical, INSERT ... ON CONFLICT DO NOTHING is fine
                    pass
            # If no current rows, just insert backup data

    # Adapt rows for JSONB
    rows = [adapt_row(r) for r in rows]

    # Insert data (with UPSERT for config tables when updating)
    placeholders = ', '.join(['%s'] * len(common_cols))
    if use_upsert and table in CONFIG_TABLES and pk_col:
        # Build UPDATE SET clause for non-PK columns
        non_pk_cols = [c for c in common_cols if c != pk_col]
        if non_pk_cols:
            update_set = ', '.join(f'"{c}" = EXCLUDED."{c}"' for c in non_pk_cols)
            insert_sql = f'INSERT INTO "{table}" ({cols_str}) VALUES ({placeholders}) ON CONFLICT ("{pk_col}") DO UPDATE SET {update_set}'
        else:
            # Only PK column, just do nothing on conflict
            insert_sql = f'INSERT INTO "{table}" ({cols_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
    else:
        insert_sql = f'INSERT INTO "{table}" ({cols_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'

    dst_cursor.executemany(insert_sql, rows)
    # Don't commit here - let caller handle transaction for atomicity

    # Check how many rows were actually inserted (for logging skipped duplicates)
    inserted_count = dst_cursor.rowcount if dst_cursor.rowcount >= 0 else len(rows)
    skipped_count = len(rows) - inserted_count if inserted_count < len(rows) else 0

    if loud:
        if skipped_count > 0:
            print(f"  {table}: {inserted_count} rows inserted, {skipped_count} duplicates skipped ({len(common_cols)} cols)")
        else:
            print(f"  {table}: {len(rows)} rows ({len(common_cols)} cols)")

    return True, inserted_count, None, orphaned_data


def main():
    parser = argparse.ArgumentParser(description='Schema-aware PostgreSQL data restoration')
    parser.add_argument('--backup-file', required=True, help='Path to pg_dump backup file')
    parser.add_argument('--backup-dir', help='Directory to save orphaned column data (defaults to backup file dir)')
    parser.add_argument('--os-type', required=True, choices=['linux', 'macos'], help='Operating system')
    parser.add_argument('--db-password', default='', help='Database password')
    parser.add_argument('--prefer-backup', action='store_true', help='Prefer backup values on conflict')
    parser.add_argument('--prefer-schema', action='store_true', help='Prefer schema values on conflict')
    parser.add_argument('--non-interactive', action='store_true', help='Abort on conflict instead of prompting')
    parser.add_argument('--loud', action='store_true', help='Verbose output')
    parser.add_argument('--skip-temp-db-setup', action='store_true',
                        help='Temp database already exists with data (skip create/restore/cleanup)')

    args = parser.parse_args()

    if args.prefer_backup and args.prefer_schema:
        print("Error: Cannot specify both --prefer-backup and --prefer-schema", file=sys.stderr)
        sys.exit(1)

    # Get password from environment variable (preferred) or CLI arg (fallback)
    # Environment variable is more secure - not visible in `ps` output
    db_password = os.environ.get('PGPASSWORD', args.db_password)

    # Interactive mode: prompt for conflict resolution unless --non-interactive or a preference is set
    interactive = not args.non_interactive and not args.prefer_backup and not args.prefer_schema

    # Determine backup directory for orphaned data
    backup_dir = args.backup_dir or os.path.dirname(args.backup_file)

    temp_db_created = False
    src_conn = None
    dst_conn = None
    migration_success = False

    try:
        if args.skip_temp_db_setup:
            # Temp database already set up by caller (e.g., migrate.sh with sudo)
            if args.loud:
                print("Using pre-existing temporary database...")
        else:
            # Step 1: Create temp database
            if args.loud:
                print("Creating temporary database...")
            if not create_temp_database(db_password):
                print("Error: Failed to create temporary database", file=sys.stderr)
                sys.exit(1)
            temp_db_created = True

            # Step 2: Enable vector extension
            enable_vector_extension(args.os_type, db_password)

            # Step 3: Restore dump to temp database
            if args.loud:
                print("Restoring backup to temporary database...")
            success, stderr = run_pg_restore(db_password, 'mira_restore_temp', args.backup_file)
            # pg_restore may return non-zero on warnings, check if tables exist

        # Step 4: Connect to both databases
        src_conn = get_connection('mira_restore_temp', db_password)
        dst_conn = get_connection('mira_service', db_password)
        src_cursor = src_conn.cursor()
        dst_cursor = dst_conn.cursor()

        # Step 5: Get table columns from both databases
        src_tables = get_table_columns(src_cursor)
        dst_tables = get_table_columns(dst_cursor)

        if not src_tables:
            print("Error: No tables found in backup", file=sys.stderr)
            sys.exit(1)

        # Step 6: Find common tables and compute FK-based insertion order dynamically
        common_tables = set(src_tables.keys()) & set(dst_tables.keys())
        ordered_tables = get_fk_ordered_tables(dst_cursor, common_tables)

        if args.loud:
            print(f"\nMigrating {len(ordered_tables)} tables\n")

        # Step 7: Migrate each table within a transaction for atomicity
        # We'll commit only if ALL tables migrate successfully
        total_rows = 0
        all_orphaned_data = {}
        for table in ordered_tables:
            success, rows, conflicts, orphaned = migrate_table(
                src_cursor, dst_cursor, dst_conn, table,
                src_tables[table], dst_tables[table],
                args.prefer_backup, args.prefer_schema, interactive, args.loud
            )

            if not success:
                # Rollback on failure
                dst_conn.rollback()
                if conflicts:
                    pk_cols = get_table_primary_key(src_cursor, table)
                    pk_col = pk_cols[0] if pk_cols else 'id'
                    print_conflicts(table, conflicts, pk_col)
                    sys.exit(2)  # Conflict exit code
                else:
                    print(f"Error: Failed to migrate table {table}", file=sys.stderr)
                    sys.exit(1)

            total_rows += rows
            if orphaned:
                all_orphaned_data[table] = orphaned

        # All tables migrated successfully - commit the transaction
        dst_conn.commit()
        migration_success = True

        # Save orphaned column data if any
        if all_orphaned_data:
            save_orphaned_data(all_orphaned_data, backup_dir, args.loud)

        if args.loud:
            print(f"\nMigration complete: {total_rows} total rows")

    except Exception as e:
        if dst_conn:
            dst_conn.rollback()
        print(f"Error: Migration failed: {e}", file=sys.stderr)
        sys.exit(1)

    finally:
        # Close connections
        if src_conn:
            src_conn.close()
        if dst_conn:
            dst_conn.close()

        # Clean up temp database only if we created it
        if temp_db_created and not args.skip_temp_db_setup:
            if args.loud:
                print("Cleaning up temporary database...")
            drop_temp_database(db_password)


if __name__ == '__main__':
    main()
