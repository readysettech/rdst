#!/usr/bin/env python3
"""
Workload script for testing MySQL real-time Top command.

Creates 9 concurrent query instances (3 query types × 3 instances each) using SLEEP(2).
This tests query parameterization by using different LIMIT values and genre filters.
"""

import mysql.connector
import threading
import time
import sys
import os

def run_query(query_type: int, instance_num: int):
    """Execute a slow query repeatedly."""
    # MySQL connection from environment variables
    conn = mysql.connector.connect(
        host=os.environ['DB_HOST'],
        port=int(os.environ['DB_PORT']),
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        database=os.environ['DB_NAME']
    )
    cursor = conn.cursor()

    # Define 3 different query patterns with different parameters
    queries = [
        "SELECT primaryTitle, SLEEP(2), startYear FROM title_basics WHERE genres LIKE '%Drama%' LIMIT 5",
        "SELECT primaryTitle, SLEEP(2), startYear FROM title_basics WHERE genres LIKE '%Comedy%' LIMIT 10",
        "SELECT primaryTitle, SLEEP(2), startYear FROM title_basics WHERE genres LIKE '%Action%' LIMIT 15"
    ]

    query = queries[query_type]

    print(f"Starting query type {query_type}, instance {instance_num}")

    try:
        # Run the query continuously (each iteration takes ~2 seconds due to SLEEP)
        while True:
            cursor.execute(query)
            cursor.fetchall()  # Consume results
    except KeyboardInterrupt:
        pass
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    # Log environment variables for debugging
    print("=== MySQL Workload Script Starting ===", file=sys.stderr)
    print(f"DB_HOST: {os.environ.get('DB_HOST', 'NOT SET')}", file=sys.stderr)
    print(f"DB_PORT: {os.environ.get('DB_PORT', 'NOT SET')}", file=sys.stderr)
    print(f"DB_USER: {os.environ.get('DB_USER', 'NOT SET')}", file=sys.stderr)
    print(f"DB_NAME: {os.environ.get('DB_NAME', 'NOT SET')}", file=sys.stderr)
    print(f"DB_PASSWORD: {'SET' if os.environ.get('DB_PASSWORD') else 'NOT SET'}", file=sys.stderr)
    sys.stderr.flush()

    threads = []

    # Create 3 instances of each query type (9 threads total)
    for query_type in range(3):
        for instance in range(3):
            thread = threading.Thread(
                target=run_query,
                args=(query_type, instance),
                daemon=True
            )
            thread.start()
            threads.append(thread)
            time.sleep(0.1)  # Small delay to stagger starts

    print(f"Started {len(threads)} query instances", file=sys.stderr)
    print("Press Ctrl+C to stop...", file=sys.stderr)
    sys.stderr.flush()

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping workload...", file=sys.stderr)
        sys.exit(0)
