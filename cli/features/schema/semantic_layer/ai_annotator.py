"""
AI-Assisted Schema Annotation

Uses LLM to generate descriptions for tables, columns, and enum values.
Supports both bulk annotation and on-demand suggestions within the wizard.
"""

import json
from typing import Optional, Dict, List, Any
from shared.llm_manager import LLMManager
from features.schema.semantic_models import (
    TableAnnotation,
    ColumnAnnotation,
    Terminology
)

# Very wide tables are annotated in column chunks of this size so each
# response fits comfortably in the output-token budget.
COLUMNS_PER_CALL = 40


class AIAnnotator:
    """
    Generates semantic layer annotations using LLM.

    Provides methods for annotating:
    - Table descriptions
    - Column descriptions
    - Enum value mappings
    - Business terminology

    Uses structured prompts with database context including:
    - Schema metadata (table/column names, types)
    - Sample data rows
    - Detected enum values
    - Web search results for known schemas
    """

    def __init__(self, llm_manager: Optional[LLMManager] = None,
                 provider: Optional[str] = None,
                 model: Optional[str] = None):
        """
        Initialize AI annotator.

        Args:
            llm_manager: Optional LLM manager instance (creates default if None)
            provider: LLM provider override ('claude', 'openai', etc.)
            model: Specific model override
        """
        self.llm = llm_manager or LLMManager()
        self.provider = provider
        self.model = model
        self._cache = {}  # Cache suggestions to avoid re-querying

    def annotate_table(self,
                       table_name: str,
                       table: TableAnnotation,
                       sample_data: Optional[List[Dict]] = None,
                       schema_context: Optional[str] = None,
                       only_columns: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Annotate a whole table in one LLM call.

        Sends the table's columns with their profile stats (null fraction,
        distinct count, top values) and enum values in a single prompt, and
        parses a structured response covering the table description, business
        context, and every column. Tables wider than COLUMNS_PER_CALL are
        split into column chunks, one call per chunk.

        Args:
            table_name: Name of the table
            table: TableAnnotation carrying columns and profile stats
            sample_data: Optional list of sample rows
            schema_context: Optional context about the schema
            only_columns: Optional subset of column names to request; other
                columns are left out of the prompt entirely. An empty subset
                still makes one call for the table-level fields.

        Returns:
            {"description": str, "business_context": str,
             "columns": {name: {"description": str, "enum_mappings": dict}}}

        Raises:
            ValueError: When a response cannot be parsed as the expected JSON.
        """
        col_items = list(table.columns.items())
        if only_columns is not None:
            requested = set(only_columns)
            col_items = [(name, col) for name, col in col_items if name in requested]

        sample_text = ""
        if sample_data:
            sample_text = "\n\nSample rows:\n" + json.dumps(
                sample_data[:3], indent=2, default=str
            )

        if col_items:
            chunks = [
                col_items[start:start + COLUMNS_PER_CALL]
                for start in range(0, len(col_items), COLUMNS_PER_CALL)
            ]
        else:
            # No columns to annotate; one call still fills the table fields.
            chunks = [[]]

        merged: Dict[str, Any] = {"description": "", "business_context": "", "columns": {}}
        for chunk in chunks:
            prompt = self._build_table_prompt(
                table_name, table, chunk, sample_text, schema_context
            )
            response = self.llm.query(
                system_message="You are an expert database analyst helping document schema semantics.",
                user_query=prompt,
                max_tokens=4096,
                temperature=0.2,
                provider=self.provider,
                model=self.model
            )

            result = self._parse_json_response(response['text'])
            if not isinstance(result, dict) or not isinstance(result.get('columns'), dict):
                raise ValueError(
                    f"Unparseable annotation response for table '{table_name}'"
                )

            if not merged["description"]:
                merged["description"] = str(result.get("description") or "")
            if not merged["business_context"]:
                merged["business_context"] = str(result.get("business_context") or "")

            chunk_names = {name for name, _col in chunk}
            for name, data in result["columns"].items():
                if name not in chunk_names or not isinstance(data, dict):
                    continue
                mappings = data.get("enum_mappings")
                merged["columns"][name] = {
                    "description": str(data.get("description") or ""),
                    "enum_mappings": mappings if isinstance(mappings, dict) else {},
                }

        return merged

    def _build_table_prompt(self,
                            table_name: str,
                            table: TableAnnotation,
                            columns: List[tuple],
                            sample_text: str,
                            schema_context: Optional[str]) -> str:
        """Build the single-call prompt for one table (or one column chunk)."""
        col_lines = "\n".join(
            self._column_stats_line(name, col) for name, col in columns
        )

        context_prefix = ""
        if schema_context:
            context_prefix = f"Schema context: {schema_context}\n\n"

        return f"""{context_prefix}Analyze this database table and annotate it.

Table: {table_name}
Row count: ~{table.row_estimate or "unknown"}
Columns:
{col_lines}{sample_text}

Return a JSON object with this exact structure:
{{
  "description": "1-2 sentence description of what this table contains",
  "business_context": "When/why rows are created or updated",
  "columns": {{
    "<column_name>": {{
      "description": "1 sentence describing what this column represents",
      "enum_mappings": {{"<value>": "human meaning"}}
    }}
  }}
}}

Guidelines:
- Provide a description for every listed column.
- Populate enum_mappings only for columns with listed enum values.
- Focus on business meaning, not technical details."""

    @staticmethod
    def _column_stats_line(col_name: str, col: ColumnAnnotation) -> str:
        """One prompt line per column: name, type, and profile stats."""
        line = f"  {col_name} ({col.data_type or 'unknown'})"
        details = []
        if col.null_fraction is not None:
            details.append(f"null: {col.null_fraction:.0%}")
        if col.distinct_count is not None:
            details.append(f"distinct: {col.distinct_count}")
        if col.top_values:
            top = ", ".join(f"{v} ({c})" for v, c in col.top_values[:8])
            details.append(f"top values: {top}")
        if col.enum_values:
            values = ", ".join(list(col.enum_values)[:10])
            details.append(f"enum values: [{values}]")
        if details:
            line += " -- " + "; ".join(details)
        return line

    def generate_table_description(self,
                                   table_name: str,
                                   columns: Dict[str, ColumnAnnotation],
                                   row_estimate: str,
                                   sample_data: Optional[List[Dict]] = None,
                                   schema_context: Optional[str] = None) -> str:
        """
        Generate a description for a database table.

        Args:
            table_name: Name of the table
            columns: Dictionary of column annotations
            row_estimate: Approximate row count (e.g., "1.2M")
            sample_data: Optional list of sample rows
            schema_context: Optional context about the schema (e.g., "Stack Overflow database")

        Returns:
            Suggested table description
        """
        cache_key = f"table:{table_name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Build column summary
        col_summary = []
        for col_name, col in columns.items():
            col_type = col.data_type
            if col.enum_values:
                col_type = f"enum({len(col.enum_values)} values)"
            col_summary.append(f"  - {col_name}: {col_type}")

        columns_text = "\n".join(col_summary[:15])  # Limit to avoid token bloat
        if len(col_summary) > 15:
            columns_text += f"\n  ... and {len(col_summary) - 15} more columns"

        # Build sample data text
        sample_text = ""
        if sample_data:
            sample_text = "\n\nSample rows:\n" + json.dumps(sample_data[:3], indent=2, default=str)

        # Build context
        context_prefix = ""
        if schema_context:
            context_prefix = f"Schema context: {schema_context}\n\n"

        prompt = f"""{context_prefix}Provide a concise, informative description for this database table.

Table: {table_name}
Row count: ~{row_estimate}
Columns:
{columns_text}{sample_text}

Return a JSON object with this structure:
{{
  "description": "1-2 sentence description of what this table contains",
  "business_context": "When/why this data is created or updated",
  "confidence": "high|medium|low"
}}

Focus on business purpose, not technical details."""

        try:
            response = self.llm.query(
                system_message="You are an expert database analyst helping document schema semantics.",
                user_query=prompt,
                max_tokens=500,
                temperature=0.2,
                provider=self.provider,
                model=self.model
            )

            # Try to parse JSON response
            result = self._parse_json_response(response['text'])
            if result and 'description' in result:
                description = result['description']
                self._cache[cache_key] = description
                return description

            # Fallback to raw text if JSON parsing fails
            text = response['text'].strip()
            self._cache[cache_key] = text
            return text

        except Exception as e:
            return f"Error generating description: {e}"

    def generate_column_description(self,
                                    table_name: str,
                                    column_name: str,
                                    data_type: str,
                                    sample_values: Optional[List[Any]] = None,
                                    table_context: Optional[str] = None) -> str:
        """
        Generate a description for a table column.

        Args:
            table_name: Name of the table
            column_name: Name of the column
            data_type: Column data type
            sample_values: Optional sample values from the column
            table_context: Optional context about the table

        Returns:
            Suggested column description
        """
        cache_key = f"column:{table_name}.{column_name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        samples_text = ""
        if sample_values:
            samples_text = f"\nSample values: {sample_values[:5]}"

        context_text = ""
        if table_context:
            context_text = f"Table context: {table_context}\n"

        prompt = f"""{context_text}Provide a concise description for this database column.

Table: {table_name}
Column: {column_name}
Data type: {data_type}{samples_text}

Return a JSON object with:
{{
  "description": "1 sentence describing what this column represents",
  "confidence": "high|medium|low"
}}

Focus on business meaning, not technical type."""

        try:
            response = self.llm.query(
                system_message="You are an expert database analyst.",
                user_query=prompt,
                max_tokens=200,
                temperature=0.2,
                provider=self.provider,
                model=self.model
            )

            result = self._parse_json_response(response['text'])
            if result and 'description' in result:
                description = result['description']
                self._cache[cache_key] = description
                return description

            text = response['text'].strip()
            self._cache[cache_key] = text
            return text

        except Exception as e:
            return f"Error: {e}"

    def generate_enum_mappings(self,
                              table_name: str,
                              column_name: str,
                              enum_values: List[str],
                              schema_context: Optional[str] = None) -> Dict[str, str]:
        """
        Generate meaning descriptions for enum values.

        Args:
            table_name: Name of the table
            column_name: Name of the enum column
            enum_values: List of enum values to describe
            schema_context: Optional schema context (e.g., "Stack Overflow database")

        Returns:
            Dictionary mapping enum values to descriptions
        """
        cache_key = f"enum:{table_name}.{column_name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Build context
        context_prefix = ""
        if schema_context:
            context_prefix = f"Schema: {schema_context}\n"

        values_list = "\n".join([f"  - {v}" for v in enum_values])

        prompt = f"""{context_prefix}Provide concise descriptions for these enum values.

Table: {table_name}
Column: {column_name}
Values:
{values_list}

Return a JSON object mapping each value to its description:
{{
  "{enum_values[0]}": "description",
  ...
}}

Be specific about what each value represents in the business domain."""

        try:
            response = self.llm.query(
                system_message="You are an expert database analyst specializing in data dictionaries.",
                user_query=prompt,
                max_tokens=800,
                temperature=0.2,
                provider=self.provider,
                model=self.model
            )

            result = self._parse_json_response(response['text'])
            if result and isinstance(result, dict):
                # Filter to only include values we asked about
                mappings = {k: v for k, v in result.items() if k in enum_values}
                self._cache[cache_key] = mappings
                return mappings

            # Fallback: return empty dict
            return {}

        except Exception as e:
            return {v: f"Error: {e}" for v in enum_values}

    def generate_terminology(self,
                           table_name: str,
                           column_name: str,
                           enum_mappings: Dict[str, str],
                           schema_context: Optional[str] = None) -> List[Terminology]:
        """
        Generate business terminology definitions based on enum values.

        For example, if status='A' means "Active", create a term "active"
        with SQL pattern "status = 'A'".

        Args:
            table_name: Name of the table
            column_name: Name of the enum column
            enum_mappings: Dictionary of enum value -> description
            schema_context: Optional schema context

        Returns:
            List of Terminology objects
        """
        terms = []

        for value, description in enum_mappings.items():
            # Extract a term name from the description
            # E.g., "Active - can place orders" -> "active"
            term_words = description.lower().split()
            if term_words:
                term_name = term_words[0].strip('- ')

                terms.append(Terminology(
                    term=term_name,
                    definition=description,
                    sql_pattern=f"{column_name} = '{value}'"
                ))

        return terms

    def _parse_json_response(self, text: str) -> Optional[Dict]:
        """
        Parse JSON from LLM response, handling markdown code blocks.

        Args:
            text: Response text from LLM

        Returns:
            Parsed JSON dict or None if parsing fails
        """
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code block
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                try:
                    return json.loads(text[start:end].strip())
                except json.JSONDecodeError:
                    pass
        elif "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            if end > start:
                try:
                    return json.loads(text[start:end].strip())
                except json.JSONDecodeError:
                    pass

        # Try finding JSON object in text
        if "{" in text and "}" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

        return None

    def clear_cache(self):
        """Clear the suggestion cache."""
        self._cache.clear()
