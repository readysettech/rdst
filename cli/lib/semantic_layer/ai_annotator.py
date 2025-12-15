"""
AI-Assisted Schema Annotation

Uses LLM to generate descriptions for tables, columns, and enum values.
Supports both bulk annotation and on-demand suggestions within the wizard.
"""

import json
from typing import Optional, Dict, List, Any
from ..llm_manager.llm_manager import LLMManager
from ..data_structures.semantic_layer import (
    SemanticLayer,
    TableAnnotation,
    ColumnAnnotation,
    Terminology
)


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

    def annotate_layer_bulk(self,
                           layer: SemanticLayer,
                           table_names: Optional[List[str]] = None,
                           sample_data_fn: Optional[callable] = None) -> None:
        """
        Bulk annotate multiple tables in the semantic layer.

        Modifies the layer in-place with AI-generated suggestions.

        Args:
            layer: SemanticLayer to annotate
            table_names: Optional list of specific tables (annotates all if None)
            sample_data_fn: Optional function(table_name) -> List[Dict] to get samples
        """
        tables_to_annotate = table_names or list(layer.tables.keys())

        for table_name in tables_to_annotate:
            if table_name not in layer.tables:
                continue

            table = layer.tables[table_name]

            # Skip if already has description
            if table.description:
                continue

            # Get sample data if function provided
            sample_data = None
            if sample_data_fn:
                try:
                    sample_data = sample_data_fn(table_name)
                except Exception:
                    pass

            # Generate table description
            description = self.generate_table_description(
                table_name=table_name,
                columns=table.columns,
                row_estimate=table.row_estimate or "unknown",
                sample_data=sample_data,
                schema_context=f"{layer.target} database"
            )

            table.description = description

            # Generate column descriptions
            for col_name, col in table.columns.items():
                if not col.description:
                    col_desc = self.generate_column_description(
                        table_name=table_name,
                        column_name=col_name,
                        data_type=col.data_type,
                        sample_values=None,  # Could extract from sample_data
                        table_context=description
                    )
                    col.description = col_desc

            # Generate enum mappings
            for col_name, col in table.columns.items():
                if col.enum_values and all(v.startswith("TODO:") for v in col.enum_values.values()):
                    mappings = self.generate_enum_mappings(
                        table_name=table_name,
                        column_name=col_name,
                        enum_values=list(col.enum_values.keys()),
                        schema_context=f"{layer.target} database"
                    )
                    if mappings:
                        col.enum_values = mappings

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
