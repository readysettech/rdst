"""
Guided Schema Annotator

Two-phase LLM-assisted annotation:
1. Analyze: profile each table, send schema + data profile to LLM,
   receive draft annotations + targeted questions (one LLM call per table).
2. Interview: present drafts for review, ask the LLM's questions,
   incorporate answers into the semantic layer.

Uses LLMManager.query() directly — no Agent SDK, no tool-use loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from ..data_structures.semantic_layer import (
    ColumnAnnotation,
    SemanticLayer,
    Terminology,
)
from ..llm_manager.llm_manager import LLMManager
from .data_profiler import DataProfiler, TableProfile


# ── LLM analysis result DTOs ─────────────────────────────────────────

@dataclass
class ColumnDraft:
    """LLM draft for a single column."""

    name: str
    description: str = ""
    confidence: str = "medium"
    unit: str = ""
    nullable_meaning: str = ""
    is_pii: bool = False
    enum_mappings: dict[str, str] = field(default_factory=dict)


@dataclass
class Question:
    """A targeted question the LLM wants to ask the user."""

    target: str  # column name (or "table" for table-level)
    question: str
    context: str = ""  # data shown alongside
    options: list[str] = field(default_factory=list)
    default: str = ""


@dataclass
class TableAnalysis:
    """Full LLM analysis for one table."""

    table_name: str
    table_description: str = ""
    business_context: str = ""
    column_drafts: dict[str, ColumnDraft] = field(default_factory=dict)
    questions: list[Question] = field(default_factory=list)
    terminology: list[dict] = field(default_factory=list)


# ── System / user prompt templates ───────────────────────────────────

SYSTEM_MESSAGE = """\
You are a database documentation specialist. Analyze schemas and data
to produce semantic annotations. Be aggressive about inferring meaning
from column names, types, sample values, and distributions. Only ask
questions when data is genuinely ambiguous — cryptic abbreviations,
opaque numeric codes, or business-specific domain terms."""

USER_PROMPT_TEMPLATE = """\
Analyze this table and produce annotations.

Table: {table_name}
Row estimate: {row_estimate}
{fk_section}
Columns:
{columns_section}

{sample_rows_section}

Return a JSON object with this exact structure:
{{
  "table_description": "1-2 sentence description of what this table contains",
  "business_context": "When/why rows are created or updated",
  "columns": {{
    "<column_name>": {{
      "description": "1-sentence description",
      "confidence": "high|medium|low",
      "unit": "",
      "nullable_meaning": "",
      "is_pii": false,
      "enum_mappings": {{}}
    }}
  }},
  "questions": [
    {{
      "target": "<column_name>",
      "question": "What does X mean?",
      "context": "Observed values: ...",
      "options": ["option1", "option2"],
      "default": "option1"
    }}
  ],
  "terminology": [
    {{
      "term": "active user",
      "definition": "A user with status = 'A'",
      "sql_pattern": "status = 'A'"
    }}
  ]
}}

Guidelines:
- For EVERY column, provide a description even if confidence is low.
- For enum columns, populate enum_mappings with value → human meaning.
- Mark PII columns (email, phone, SSN, name, address, IP).
- Only add a question when you truly cannot infer the answer.
- Prefer fewer, more impactful questions (max 5 per table).
- For nullable columns with >50% nulls, explain what NULL means.
- Derive terminology entries from enum values when useful."""


# ── Main orchestrator ────────────────────────────────────────────────

class GuidedAnnotator:
    """Two-phase guided annotation: analyze → interview."""

    def __init__(
        self,
        llm_manager: Optional[LLMManager] = None,
        console=None,
        manager=None,
    ):
        self.llm = llm_manager or LLMManager()
        if console is None:
            from lib.ui import get_console
            console = get_console()
        self.console = console
        self._manager = manager

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        layer: SemanticLayer,
        target_config: dict,
        table_name: Optional[str] = None,
        auto_accept: bool = False,
    ) -> SemanticLayer:
        """Run guided annotation for all (or one) table(s).

        Args:
            layer: Existing semantic layer (with schema from introspection).
            target_config: Database connection config dict.
            table_name: Optional single table to annotate.
            auto_accept: If True, skip interactive review — accept all drafts and question defaults.

        Returns:
            The same layer, mutated in place with annotations.
        """
        from lib.ui import StyleTokens, Rule

        tables = (
            {table_name: layer.tables[table_name]}
            if table_name and table_name in layer.tables
            else dict(layer.tables)
        )

        if not tables:
            self.console.print(f"[{StyleTokens.WARNING}]No tables found in semantic layer.[/{StyleTokens.WARNING}]")
            return layer

        # Phase 0: Profile
        self.console.print(f"\n[{StyleTokens.ACCENT}]Profiling {len(tables)} table(s)...[/{StyleTokens.ACCENT}]")
        profiler = DataProfiler(target_config)
        profiles: dict[str, TableProfile] = {}
        for tname, tann in tables.items():
            try:
                row_est_num = _parse_row_estimate(tann.row_estimate)
                p = profiler.profile_table(
                    tname,
                    columns=tann.columns,
                    row_estimate=row_est_num,
                    row_estimate_str=tann.row_estimate or "0",
                    relationships=tann.relationships,
                )
                profiles[tname] = p
                self.console.print(
                    f"  [{StyleTokens.SUCCESS}]✓[/{StyleTokens.SUCCESS}] {tname} "
                    f"({tann.row_estimate or '?'} rows, {len(tann.columns)} columns)"
                )
            except Exception as e:
                self.console.print(
                    f"  [{StyleTokens.WARNING}]⚠ {tname}: profiling failed ({e})[/{StyleTokens.WARNING}]"
                )

        if not profiles:
            self.console.print(f"[{StyleTokens.ERROR}]No tables could be profiled.[/{StyleTokens.ERROR}]")
            return layer

        # Phase 1: LLM analysis
        self.console.print(f"\n[{StyleTokens.ACCENT}]Analyzing schema with AI...[/{StyleTokens.ACCENT}]")
        analyses: dict[str, TableAnalysis] = {}
        for tname, profile in profiles.items():
            try:
                analysis = self._analyze_table(tname, profile, tables[tname])
                analyses[tname] = analysis
                q_count = len(analysis.questions)
                auto_count = sum(
                    1 for d in analysis.column_drafts.values()
                    if d.confidence == "high"
                )
                self.console.print(
                    f"  [{StyleTokens.SUCCESS}]✓[/{StyleTokens.SUCCESS}] {tname} — "
                    f"{auto_count} auto-annotated, {q_count} question(s)"
                )
            except Exception as e:
                self.console.print(
                    f"  [{StyleTokens.WARNING}]⚠ {tname}: LLM analysis failed ({e})[/{StyleTokens.WARNING}]"
                )

        if not analyses:
            self.console.print(f"[{StyleTokens.ERROR}]No tables could be analyzed.[/{StyleTokens.ERROR}]")
            return layer

        # Phase 2: Interactive review
        if self._manager is None:
            from ..semantic_layer.manager import SemanticLayerManager
            self._manager = SemanticLayerManager()

        for tname, analysis in analyses.items():
            self.console.print("")
            self.console.print(Rule(f" Table: {tname} "))
            accepted = self._review_table(layer, tname, analysis, profiles.get(tname), auto_accept=auto_accept)
            if accepted:
                # Save after each table for fault tolerance
                self._manager.save(layer)
                self.console.print(f"  [{StyleTokens.SUCCESS}]Saved {tname}.[/{StyleTokens.SUCCESS}]")
            else:
                self.console.print(f"  [{StyleTokens.MUTED}]Skipped {tname}.[/{StyleTokens.MUTED}]")

        return layer

    # ------------------------------------------------------------------
    # Phase 1: LLM analysis
    # ------------------------------------------------------------------

    def _analyze_table(
        self,
        table_name: str,
        profile: TableProfile,
        table_ann,
    ) -> TableAnalysis:
        """Send schema + data profile to LLM, get structured analysis back."""

        prompt = self._build_prompt(table_name, profile, table_ann)

        response = self.llm.query(
            system_message=SYSTEM_MESSAGE,
            user_query=prompt,
            max_tokens=4096,
            temperature=0.2,
        )

        return self._parse_analysis(table_name, response["text"])

    def _build_prompt(self, table_name: str, profile: TableProfile, table_ann) -> str:
        """Build the user prompt for a single table analysis."""

        # Foreign keys section
        fk_section = ""
        if profile.foreign_keys:
            fk_section = "Foreign keys:\n" + "\n".join(f"  - {fk}" for fk in profile.foreign_keys)

        # Columns section — rich per-column stats
        col_lines = []
        for col_name, col_ann in table_ann.columns.items():
            cp = profile.columns.get(col_name)
            line = f"  {col_name} ({col_ann.data_type})"

            details = []
            if cp:
                details.append(f"null: {cp.null_fraction:.0%}")
                details.append(f"distinct: {cp.distinct_count}")

                if cp.top_values:
                    top_str = ", ".join(
                        f"{k} ({v})" for k, v in list(cp.top_values.items())[:8]
                    )
                    details.append(f"top values: {top_str}")

                if cp.sample_values:
                    details.append(f"samples: {', '.join(cp.sample_values[:5])}")

                if cp.detected_pattern:
                    details.append(f"pattern: {cp.detected_pattern}")

            if col_ann.enum_values:
                ev_str = ", ".join(list(col_ann.enum_values.keys())[:10])
                details.append(f"enum values: [{ev_str}]")

            if details:
                line += " — " + "; ".join(details)

            col_lines.append(line)

        columns_section = "\n".join(col_lines)

        # Sample rows
        sample_rows_section = ""
        if profile.sample_rows:
            sample_rows_section = "Sample rows:\n" + json.dumps(
                profile.sample_rows[:3], indent=2, default=str
            )

        return USER_PROMPT_TEMPLATE.format(
            table_name=table_name,
            row_estimate=profile.row_estimate_str,
            fk_section=fk_section,
            columns_section=columns_section,
            sample_rows_section=sample_rows_section,
        )

    def _parse_analysis(self, table_name: str, text: str) -> TableAnalysis:
        """Parse LLM JSON response into a TableAnalysis."""
        data = _parse_json(text)
        if not data:
            return TableAnalysis(table_name=table_name)

        analysis = TableAnalysis(
            table_name=table_name,
            table_description=data.get("table_description", ""),
            business_context=data.get("business_context", ""),
        )

        for col_name, col_data in data.get("columns", {}).items():
            if not isinstance(col_data, dict):
                continue
            analysis.column_drafts[col_name] = ColumnDraft(
                name=col_name,
                description=col_data.get("description", ""),
                confidence=col_data.get("confidence", "medium"),
                unit=col_data.get("unit", ""),
                nullable_meaning=col_data.get("nullable_meaning", ""),
                is_pii=col_data.get("is_pii", False),
                enum_mappings=col_data.get("enum_mappings", {}),
            )

        for q_data in data.get("questions", []):
            if not isinstance(q_data, dict):
                continue
            analysis.questions.append(Question(
                target=q_data.get("target", ""),
                question=q_data.get("question", ""),
                context=q_data.get("context", ""),
                options=q_data.get("options", []),
                default=q_data.get("default", ""),
            ))

        analysis.terminology = data.get("terminology", [])

        return analysis

    # ------------------------------------------------------------------
    # Phase 2: Interactive review
    # ------------------------------------------------------------------

    def _review_table(
        self,
        layer: SemanticLayer,
        table_name: str,
        analysis: TableAnalysis,
        profile: Optional[TableProfile],
        auto_accept: bool = False,
    ) -> bool:
        """Interactive review of LLM analysis for one table. Returns True if accepted."""
        from lib.ui import StyleTokens, Prompt

        table = layer.tables.get(table_name)
        if not table:
            return False

        # -- Table description --
        if analysis.table_description:
            self.console.print(f"  [{StyleTokens.ACCENT}]Description:[/{StyleTokens.ACCENT}] {analysis.table_description}")
            if analysis.business_context:
                self.console.print(f"  [{StyleTokens.MUTED}]Context:[/{StyleTokens.MUTED}] {analysis.business_context}")

            if auto_accept:
                choice = "accept"
            else:
                choice = Prompt.ask(
                    "  [accept/edit/skip]",
                    choices=["accept", "edit", "skip"],
                    default="accept",
                )
            if choice == "accept":
                table.description = analysis.table_description
                table.business_context = analysis.business_context
            elif choice == "edit":
                table.description = Prompt.ask("  Description", default=analysis.table_description)
                table.business_context = Prompt.ask("  Business context", default=analysis.business_context)
            elif choice == "skip":
                return False

        # -- Auto-annotated columns (high confidence) --
        high_conf = {
            n: d for n, d in analysis.column_drafts.items()
            if d.confidence == "high"
        }
        if high_conf:
            self.console.print(f"\n  [{StyleTokens.ACCENT}]Auto-annotated (high confidence):[/{StyleTokens.ACCENT}]")
            for col_name, draft in high_conf.items():
                pii_tag = f" [{StyleTokens.WARNING}][PII][/{StyleTokens.WARNING}]" if draft.is_pii else ""
                self.console.print(
                    f"    [{StyleTokens.SUCCESS}]✓[/{StyleTokens.SUCCESS}] {col_name} "
                    f"({table.columns[col_name].data_type if col_name in table.columns else '?'}) "
                    f"— {draft.description}{pii_tag}"
                )
                self._apply_draft(table, col_name, draft)

        # -- Medium/low confidence (show but let user override) --
        other_conf = {
            n: d for n, d in analysis.column_drafts.items()
            if d.confidence != "high"
        }
        if other_conf:
            self.console.print(f"\n  [{StyleTokens.MUTED}]Inferred (medium/low confidence):[/{StyleTokens.MUTED}]")
            for col_name, draft in other_conf.items():
                self.console.print(
                    f"    [{StyleTokens.MUTED}]~[/{StyleTokens.MUTED}] {col_name} — {draft.description}"
                )
                self._apply_draft(table, col_name, draft)

        # -- Ask questions --
        if analysis.questions:
            self.console.print(f"\n  [{StyleTokens.ACCENT}]Questions:[/{StyleTokens.ACCENT}]")
            for i, q in enumerate(analysis.questions, 1):
                self.console.print(f"\n  {i}. {q.question}")
                if q.context:
                    self.console.print(f"     [{StyleTokens.MUTED}]{q.context}[/{StyleTokens.MUTED}]")

                if auto_accept:
                    answer = q.default or (q.options[0] if q.options else "")
                elif q.options:
                    # Multiple choice
                    for j, opt in enumerate(q.options, 1):
                        default_marker = " (default)" if opt == q.default else ""
                        self.console.print(f"     [{j}] {opt}{default_marker}")

                    answer = Prompt.ask(
                        f"     [{StyleTokens.ACCENT}]Answer[/{StyleTokens.ACCENT}]",
                        default=q.default or (q.options[0] if q.options else ""),
                    )
                else:
                    # Free text
                    answer = Prompt.ask(
                        f"     [{StyleTokens.ACCENT}]Answer[/{StyleTokens.ACCENT}]",
                        default=q.default,
                    )

                self._apply_answer(table, q, answer, analysis)

        # -- Terminology --
        for term_data in analysis.terminology:
            if isinstance(term_data, dict) and term_data.get("term"):
                layer.add_terminology(
                    term=term_data["term"],
                    definition=term_data.get("definition", ""),
                    sql_pattern=term_data.get("sql_pattern", ""),
                    tables_used=[table_name],
                )

        return True

    def _apply_draft(self, table, col_name: str, draft: ColumnDraft):
        """Apply a column draft to the semantic layer table annotation."""
        if col_name not in table.columns:
            return

        col = table.columns[col_name]
        if not col.description or col.description.startswith("TODO:"):
            col.description = draft.description
        if draft.unit and not col.unit:
            col.unit = draft.unit
        if draft.nullable_meaning and not col.nullable_meaning:
            col.nullable_meaning = draft.nullable_meaning
        if draft.is_pii:
            col.is_pii = True
        if draft.enum_mappings:
            # Replace TODO placeholders with LLM mappings
            for val, meaning in draft.enum_mappings.items():
                if val in col.enum_values and col.enum_values[val].startswith("TODO:"):
                    col.enum_values[val] = meaning
                elif val not in col.enum_values:
                    col.enum_values[val] = meaning

    def _apply_answer(self, table, question: Question, answer: str, analysis: TableAnalysis):
        """Apply a user's answer to the relevant column annotation."""
        col_name = question.target
        if col_name not in table.columns:
            return

        col = table.columns[col_name]

        # If the question was about enum values, try to parse answer as mappings
        if question.options and col.enum_values:
            # User picked an option — try to interpret as confirmation or replacement
            # If the answer matches an option, use the LLM's draft
            draft = analysis.column_drafts.get(col_name)
            if draft and draft.enum_mappings:
                for val, meaning in draft.enum_mappings.items():
                    if val in col.enum_values and col.enum_values[val].startswith("TODO:"):
                        col.enum_values[val] = meaning

        # If it's a nullable meaning question, store it
        if "null" in question.question.lower() and "mean" in question.question.lower():
            col.nullable_meaning = answer
            return

        # General: store as description supplement or enum mapping
        if "=" in answer and col.enum_values:
            # Parse "1=Free, 2=Pro, 3=Enterprise" style answers
            for pair in answer.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    col.enum_values[k.strip()] = v.strip()
        elif not col.description or col.description.startswith("TODO:"):
            col.description = answer


# ── Helpers ──────────────────────────────────────────────────────────

def _parse_json(text: str) -> Optional[dict]:
    """Parse JSON from LLM response, handling markdown code blocks."""
    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Markdown code block
    for marker in ("```json", "```"):
        if marker in text:
            start = text.find(marker) + len(marker)
            end = text.find("```", start)
            if end > start:
                try:
                    return json.loads(text[start:end].strip())
                except json.JSONDecodeError:
                    pass

    # Fallback: find outermost { ... }
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    return None


def _parse_row_estimate(estimate_str: str) -> int:
    """Convert human-readable row estimate back to integer.

    '1.2M' → 1_200_000, '50K' → 50_000, '1234' → 1234
    """
    if not estimate_str:
        return 0
    s = estimate_str.strip().upper()
    try:
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        elif s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        else:
            return int(float(s))
    except (ValueError, TypeError):
        return 0
