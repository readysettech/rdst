from __future__ import annotations

import json
import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404 - subprocess required for Docker/database operations
from typing import Dict, Any, List, Set


def check_tables_have_data(
    container_name: str = None,
    target_config: Dict[str, Any] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Check if tables in the test database have any data.

    Args:
        container_name: Docker container name
        target_config: Database target configuration
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing whether tables have data
    """
    try:
        # Parse target config if it's a JSON string
        if isinstance(target_config, str):
            target_config = json.loads(target_config)

        if not target_config:
            return {
                "success": False,
                "error": "No target configuration provided"
            }

        engine = target_config.get('engine', 'postgresql')
        user = target_config.get('user', 'postgres')
        database = target_config.get('database', 'testdb')

        if engine == 'postgresql':
            # First get list of tables
            get_tables_query = """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_type = 'BASE TABLE';
            """

            cmd = [
                'docker', 'exec', container_name,
                'psql',
                '-U', user,
                '-d', database,
                '-t',
                '-A',
                '-c', get_tables_query
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to get table list: {result.stderr}"
                }

            tables = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]

            if not tables:
                # No tables, no data
                return {
                    "success": True,
                    "has_data": False,
                    "total_rows": 0,
                    "table_counts": {},
                    "skip_generation": False
                }

            # Check row counts for each table
            total_rows = 0
            table_counts = {}

            for table in tables:
                count_query = f"SELECT COUNT(*) FROM {table};"
                cmd = [
                    'docker', 'exec', container_name,
                    'psql',
                    '-U', user,
                    '-d', database,
                    '-t',
                    '-A',
                    '-c', count_query
                ]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                if result.returncode == 0:
                    try:
                        row_count = int(result.stdout.strip())
                        table_counts[table] = row_count
                        total_rows += row_count
                    except (ValueError, IndexError):
                        table_counts[table] = 0

            has_data = total_rows > 0

            return {
                "success": True,
                "has_data": has_data,
                "total_rows": total_rows,
                "table_counts": table_counts,
                "skip_generation": has_data
            }

        elif engine == 'mysql':
            # Get table list and row counts
            cmd = [
                'docker', 'exec', container_name,
                'mysql',
                '-u', user,
                '-p' + target_config.get('password', 'testpassword'),
                database,
                '-N',
                '-e',
                "SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema = DATABASE();"
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to check table data: {result.stderr}"
                }

            # Parse results
            total_rows = 0
            table_counts = {}
            lines = result.stdout.strip().split('\n')

            for line in lines:
                if not line.strip():
                    continue
                parts = line.split('\t')
                if len(parts) >= 2:
                    table_name = parts[0].strip()
                    try:
                        row_count = int(parts[1].strip())
                        table_counts[table_name] = row_count
                        total_rows += row_count
                    except (ValueError, IndexError):
                        continue

            has_data = total_rows > 0

            return {
                "success": True,
                "has_data": has_data,
                "total_rows": total_rows,
                "table_counts": table_counts,
                "skip_generation": has_data
            }

        else:
            return {
                "success": False,
                "error": f"Unsupported database engine: {engine}"
            }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Table data check timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to check table data: {str(e)}"
        }


def get_test_database_schema(
    container_name: str = None,
    target_config: Dict[str, Any] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Get complete schema from test database for LLM-based data generation.

    Args:
        container_name: Docker container name
        target_config: Database target configuration
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing schema information
    """
    try:
        # Parse target config if it's a JSON string
        if isinstance(target_config, str):
            target_config = json.loads(target_config)

        if not target_config:
            return {
                "success": False,
                "error": "No target configuration provided"
            }

        engine = target_config.get('engine', 'postgresql')
        user = target_config.get('user', 'postgres')
        database = target_config.get('database', 'testdb')

        if engine == 'postgresql':
            # Get full schema with CREATE TABLE statements
            sql_query = """
                SELECT
                    table_name,
                    string_agg(
                        column_name || ' ' || data_type ||
                        CASE WHEN character_maximum_length IS NOT NULL
                            THEN '(' || character_maximum_length || ')'
                            ELSE ''
                        END ||
                        CASE WHEN is_nullable = 'NO' THEN ' NOT NULL' ELSE '' END,
                        ', '
                        ORDER BY ordinal_position
                    ) as columns
                FROM information_schema.columns
                WHERE table_schema = 'public'
                GROUP BY table_name
                ORDER BY table_name;
            """

            cmd = [
                'docker', 'exec', container_name,
                'psql',
                '-U', user,
                '-d', database,
                '-t',  # tuples only
                '-A',  # unaligned output
                '-F', '|',  # field separator
                '-c', sql_query
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to get schema: {result.stderr}"
                }

            # Parse the output
            schema_parts = []
            lines = result.stdout.strip().split('\n')

            for line in lines:
                if not line.strip():
                    continue

                parts = line.split('|')
                if len(parts) >= 2:
                    table_name = parts[0].strip()
                    columns = parts[1].strip()

                    schema_parts.append(f"CREATE TABLE {table_name} ({columns});")

            if not schema_parts:
                return {
                    "success": False,
                    "error": "No tables found in schema"
                }

            schema_text = "\n\n".join(schema_parts)

            return {
                "success": True,
                "schema_info": schema_text,
                "table_count": len(schema_parts)
            }

        elif engine == 'mysql':
            # Get SHOW CREATE TABLE for all tables
            # First get table list
            cmd = [
                'docker', 'exec', container_name,
                'mysql',
                '-u', user,
                '-p' + target_config.get('password', 'testpassword'),
                database,
                '-N',  # skip column names
                '-e', 'SHOW TABLES;'
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to get tables: {result.stderr}"
                }

            tables = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]

            if not tables:
                return {
                    "success": False,
                    "error": "No tables found in schema"
                }

            # Get CREATE TABLE for each table
            schema_parts = []
            for table in tables:
                cmd = [
                    'docker', 'exec', container_name,
                    'mysql',
                    '-u', user,
                    '-p' + target_config.get('password', 'testpassword'),
                    database,
                    '-N',
                    '-e', f'SHOW CREATE TABLE {table};'
                ]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if result.returncode == 0:
                    # Output format: table_name\tCREATE TABLE ...
                    parts = result.stdout.strip().split('\t', 1)
                    if len(parts) >= 2:
                        schema_parts.append(parts[1] + ';')

            schema_text = "\n\n".join(schema_parts)

            return {
                "success": True,
                "schema_info": schema_text,
                "table_count": len(schema_parts)
            }

        else:
            return {
                "success": False,
                "error": f"Unsupported database engine: {engine}"
            }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Schema query timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to get schema: {str(e)}"
        }


def generate_test_data_with_llm(
    schema_info: str | Dict[str, Any] = None,
    data_check: Dict[str, Any] = None,
    table_name: str = None,
    row_count: int = 10,
    llm_model: str = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Generate test data using LLM based on schema information.
    Skips generation if tables already have data.

    Args:
        schema_info: Schema information (string or dict)
        data_check: Result from check_tables_have_data
        table_name: Specific table to generate data for
        row_count: Number of rows to generate
        llm_model: LLM model to use
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing generated SQL INSERT statements
    """
    try:
        from ..llm_manager.llm_manager import LLMManager

        if isinstance(data_check, str):
            try:
                data_check = json.loads(data_check)
            except json.JSONDecodeError:
                data_check = None

        # Check if we should skip generation
        if isinstance(data_check, dict) and data_check.get('skip_generation'):
            total_rows = data_check.get('total_rows', 0)
            print(f"✓ Tables already have {total_rows} rows of data, skipping LLM generation")
            return {
                "success": True,
                "insert_statements": [],
                "statement_count": 0,
                "skipped": True,
                "reason": f"Tables already have {total_rows} rows"
            }

        # Parse schema info - handle both string and dict formats
        schema_text = None

        if isinstance(schema_info, dict):
            # If it's already a dict, extract the schema_info field
            schema_text = schema_info.get('schema_info')
        elif isinstance(schema_info, str):
            try:
                # Try to parse as JSON first
                schema_dict = json.loads(schema_info)
                schema_text = schema_dict.get('schema_info', schema_info)
            except (json.JSONDecodeError, AttributeError):
                # It's a plain string, use as-is
                schema_text = schema_info

        if not schema_text:
            return {
                "success": False,
                "error": "No schema information provided",
                "debug_schema_info": str(schema_info)[:200]
            }

        print(f"Generating test data for schema...")

        # Prepare LLM prompt
        system_message = """You are a database test data generator. Generate realistic test data based on the provided schema.
Output ONLY valid SQL INSERT statements, one per line, without any explanation or markdown formatting.
Use realistic values that match the column types and constraints."""

        table_filter = f" for table '{table_name}'" if table_name else ""
        user_query = f"""Generate {row_count} INSERT statements{table_filter} based on this schema:

{schema_text}

Requirements:
- Generate exactly {row_count} INSERT statements
- Use realistic sample data
- Ensure data types match column definitions
- Respect any constraints (PRIMARY KEY, FOREIGN KEY, etc.)
- Output only SQL INSERT statements, no explanations
- One statement per line"""

        # Query LLM
        print(f"Querying LLM to generate test data...")
        llm = LLMManager()

        response = llm.query(
            system_message=system_message,
            user_query=user_query
        )

        if not response.get("text"):
            return {
                "success": False,
                "error": "LLM returned empty response"
            }

        # Extract SQL statements
        sql_text = response["text"].strip()
        print(f"LLM returned {len(sql_text)} characters of response")

        # Clean up any markdown code blocks
        if sql_text.startswith("```"):
            lines = sql_text.split('\n')
            sql_lines = []
            in_code_block = False
            for line in lines:
                if line.startswith("```"):
                    in_code_block = not in_code_block
                    continue
                if in_code_block or not line.startswith("```"):
                    sql_lines.append(line)
            sql_text = '\n'.join(sql_lines).strip()

        # Split into individual statements
        statements = [s.strip() for s in sql_text.split('\n') if s.strip() and s.strip().upper().startswith('INSERT')]

        print(f"✓ Generated {len(statements)} INSERT statements")

        return {
            "success": True,
            "insert_statements": statements,
            "statement_count": len(statements),
            "llm_model": response.get("model"),
            "sql_text": sql_text
        }

    except Exception as e:
        import traceback
        return {
            "success": False,
            "error": f"Failed to generate test data: {str(e)}",
            "traceback": traceback.format_exc()
        }


def load_test_data_to_database(
    insert_statements: List[str] | str = None,
    target_config: Dict[str, Any] = None,
    container_name: str = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Load generated test data into database.

    Args:
        insert_statements: List of INSERT SQL statements or JSON string
        target_config: Database target configuration
        container_name: Docker container name (for docker exec)
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing load results
    """

    try:
        # Parse insert statements - handle both list and dict formats
        statements = None

        if isinstance(insert_statements, list):
            # Already a list
            statements = insert_statements
        elif isinstance(insert_statements, dict):
            # Extract from dict
            statements = insert_statements.get('insert_statements', [])
        elif isinstance(insert_statements, str):
            try:
                # Try to parse as JSON
                data = json.loads(insert_statements)
                if isinstance(data, list):
                    statements = data
                elif isinstance(data, dict):
                    statements = data.get('insert_statements', [])
                else:
                    statements = []
            except json.JSONDecodeError:
                # It's a raw SQL string
                statements = [s.strip() for s in insert_statements.split('\n') if s.strip()]

        if not statements:
            return {
                "success": False,
                "error": "No INSERT statements provided",
                "debug_type": str(type(insert_statements)),
                "debug_value": str(insert_statements)[:200] if insert_statements else "None"
            }

        print(f"Loading {len(statements)} INSERT statements into database...")

        # Parse target config if it's a JSON string
        if isinstance(target_config, str):
            target_config = json.loads(target_config)

        if not target_config:
            return {
                "success": False,
                "error": "No target configuration provided"
            }

        # Combine all statements
        sql_script = '\n'.join(statements) + '\n'

        engine = target_config.get('engine', 'postgresql')

        if engine == 'postgresql':
            # Use docker exec psql
            user = target_config.get('user', 'postgres')
            database = target_config.get('database', 'testdb')

            cmd = [
                'docker', 'exec', '-i', container_name,
                'psql',
                '-U', user,
                '-d', database
            ]

            result = subprocess.run(
                cmd,
                input=sql_script,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to load data: {result.stderr}",
                    "statements_attempted": len(statements)
                }

            return {
                "success": True,
                "rows_inserted": len(statements),
                "output": result.stdout
            }

        elif engine == 'mysql':
            # Use docker exec mysql
            user = target_config.get('user', 'root')
            database = target_config.get('database', 'testdb')
            password = target_config.get('password', 'testpassword')

            cmd = [
                'docker', 'exec', '-i', container_name,
                'mysql',
                '-u', user,
                f'-p{password}',
                database
            ]

            result = subprocess.run(
                cmd,
                input=sql_script,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to load data: {result.stderr}",
                    "statements_attempted": len(statements)
                }

            return {
                "success": True,
                "rows_inserted": len(statements),
                "output": result.stdout
            }

        else:
            return {
                "success": False,
                "error": f"Unsupported database engine: {engine}"
            }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Data load timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to load test data: {str(e)}"
        }
