#!/usr/bin/env python3
"""
Export all data from DCIM Server database
"""
import sqlite3
import json
import sys
from pathlib import Path
from datetime import datetime

def dict_factory(cursor, row):
    """Convert SQLite rows to dictionaries"""
    fields = [column[0] for column in cursor.description]
    return {key: value for key, value in zip(fields, row)}

def export_sqlite_data(db_path):
    """Export all data from SQLite database"""
    print(f"Opening database: {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = dict_factory
        cursor = conn.cursor()

        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = cursor.fetchall()

        if not tables:
            print("No tables found in database")
            return {}

        print(f"\nFound {len(tables)} tables:")
        for table in tables:
            print(f"  - {table['name']}")

        # Export data from each table
        all_data = {}

        for table in tables:
            table_name = table['name']
            print(f"\n{'='*60}")
            print(f"Table: {table_name}")
            print(f"{'='*60}")

            # Get table schema
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()

            print("\nColumns:")
            for col in columns:
                print(f"  {col['name']}: {col['type']} {'(PK)' if col['pk'] else ''}")

            # Get row count
            cursor.execute(f"SELECT COUNT(*) as count FROM {table_name}")
            count = cursor.fetchone()['count']
            print(f"\nRow count: {count}")

            # Get all data
            cursor.execute(f"SELECT * FROM {table_name}")
            rows = cursor.fetchall()
            all_data[table_name] = rows

            # Display first few rows
            if rows:
                print(f"\nFirst {min(5, len(rows))} rows:")
                for i, row in enumerate(rows[:5], 1):
                    print(f"\n  Row {i}:")
                    for key, value in row.items():
                        if value is not None and len(str(value)) > 100:
                            print(f"    {key}: {str(value)[:100]}... (truncated)")
                        else:
                            print(f"    {key}: {value}")
            else:
                print("\n  (No data)")

        conn.close()

        # Save to JSON file
        output_file = Path(db_path).parent / f"database_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(all_data, f, indent=2, default=str)

        print(f"\n{'='*60}")
        print(f"Data exported to: {output_file}")
        print(f"{'='*60}\n")

        return all_data

    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
        return {}
    except Exception as e:
        print(f"Error: {e}")
        return {}

def export_postgres_data():
    """Export all data from PostgreSQL database"""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            user="postgres",
            password="postgres",
            database="dcim_db"
        )

        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get all tables
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        tables = cursor.fetchall()

        print(f"\nFound {len(tables)} tables in PostgreSQL:")
        for table in tables:
            print(f"  - {table['table_name']}")

        all_data = {}

        for table in tables:
            table_name = table['table_name']
            print(f"\n{'='*60}")
            print(f"Table: {table_name}")
            print(f"{'='*60}")

            # Get row count
            cursor.execute(f"SELECT COUNT(*) as count FROM {table_name}")
            count = cursor.fetchone()['count']
            print(f"Row count: {count}")

            # Get all data
            cursor.execute(f"SELECT * FROM {table_name}")
            rows = cursor.fetchall()
            all_data[table_name] = [dict(row) for row in rows]

            # Display first few rows
            if rows:
                print(f"\nFirst {min(5, len(rows))} rows:")
                for i, row in enumerate(rows[:5], 1):
                    print(f"\n  Row {i}:")
                    for key, value in dict(row).items():
                        if value is not None and len(str(value)) > 100:
                            print(f"    {key}: {str(value)[:100]}... (truncated)")
                        else:
                            print(f"    {key}: {value}")
            else:
                print("\n  (No data)")

        conn.close()

        # Save to JSON file
        output_file = Path("./data") / f"postgres_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        output_file.parent.mkdir(exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(all_data, f, indent=2, default=str)

        print(f"\n{'='*60}")
        print(f"Data exported to: {output_file}")
        print(f"{'='*60}\n")

        return all_data

    except ImportError:
        print("psycopg2 not installed. Install with: pip install psycopg2-binary")
        return None
    except Exception as e:
        print(f"PostgreSQL error: {e}")
        return None

if __name__ == "__main__":
    print("="*60)
    print("DCIM Server Database Export Tool")
    print("="*60)

    # Try PostgreSQL first (as configured)
    print("\nAttempting to connect to PostgreSQL...")
    pg_data = export_postgres_data()

    # If PostgreSQL fails, try SQLite
    if pg_data is None:
        print("\n\nPostgreSQL connection failed. Trying SQLite...")
        db_path = Path(__file__).parent / "data" / "dcim_server.db"

        if not db_path.exists():
            print(f"SQLite database not found at: {db_path}")
            sys.exit(1)

        sqlite_data = export_sqlite_data(str(db_path))

    print("\n" + "="*60)
    print("Export complete!")
    print("="*60)
