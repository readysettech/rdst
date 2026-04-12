"""
Progressive Learning Module for Semantic Layer

Extracts learnable patterns from user conversations to automatically
enrich the semantic layer over time.
"""

import re
from typing import Optional
from dataclasses import dataclass

from .manager import SemanticLayerManager


@dataclass
class LearnedPattern:
    """A pattern learned from user interaction."""
    pattern_type: str  # 'terminology', 'enum', 'column_description'
    confidence: float  # 0.0 to 1.0
    data: dict  # Pattern-specific data


class SemanticLayerLearner:
    """
    Learns from user interactions to enrich the semantic layer.

    Integrates with ask3 engine to capture:
    - Terminology definitions from clarifications
    - Enum value mappings from user explanations
    - Column descriptions from context
    """

    def __init__(self, manager: Optional[SemanticLayerManager] = None):
        """
        Initialize the learner.

        Args:
            manager: SemanticLayerManager instance. Creates default if None.
        """
        self.manager = manager or SemanticLayerManager()

    def learn_from_clarification(self, target: str, question: str,
                                  clarification: str, sql: str) -> list[LearnedPattern]:
        """
        Learn from a user clarification response.

        Args:
            target: Target database name
            question: Original user question
            clarification: User's clarification text
            sql: Generated SQL after clarification

        Returns:
            List of patterns learned
        """
        patterns = []

        # Try to extract terminology definitions
        term_patterns = self._extract_terminology(clarification, sql)
        patterns.extend(term_patterns)

        # Try to extract enum value explanations
        enum_patterns = self._extract_enum_values(clarification, sql)
        patterns.extend(enum_patterns)

        # Apply high-confidence patterns
        for pattern in patterns:
            if pattern.confidence >= 0.7:
                self._apply_pattern(target, pattern)

        return patterns

    def learn_from_correction(self, target: str, question: str,
                              original_sql: str, corrected_sql: str,
                              explanation: str = "") -> list[LearnedPattern]:
        """
        Learn from a user SQL correction.

        Args:
            target: Target database name
            question: Original user question
            original_sql: SQL that was incorrect
            corrected_sql: User's corrected SQL
            explanation: Optional user explanation

        Returns:
            List of patterns learned
        """
        patterns = []

        # Analyze the diff to understand what changed
        diff_patterns = self._analyze_sql_diff(original_sql, corrected_sql, explanation)
        patterns.extend(diff_patterns)

        # Apply high-confidence patterns
        for pattern in patterns:
            if pattern.confidence >= 0.8:  # Higher threshold for corrections
                self._apply_pattern(target, pattern)

        return patterns

    def learn_from_confirmation(self, target: str, question: str,
                                 sql: str) -> list[LearnedPattern]:
        """
        Learn from a successful SQL confirmation.

        When user confirms SQL is correct, we can learn:
        - Term-to-SQL pattern mappings
        - Successful query patterns

        Args:
            target: Target database name
            question: User's natural language question
            sql: SQL that was confirmed correct

        Returns:
            List of patterns learned
        """
        patterns = []

        # Extract potential terminology from question
        term_patterns = self._extract_terms_from_confirmed_sql(question, sql)
        patterns.extend(term_patterns)

        # Apply patterns with lower threshold since user confirmed
        for pattern in patterns:
            if pattern.confidence >= 0.5:
                self._apply_pattern(target, pattern)

        return patterns

    def _extract_terminology(self, text: str, sql: str) -> list[LearnedPattern]:
        """
        Extract terminology definitions from text.

        Looks for patterns like:
        - "X means Y"
        - "X refers to Y"
        - "by X I mean Y"
        - "X is when Y"
        """
        patterns = []

        # Pattern: "term" means/refers to/is description
        definition_patterns = [
            r'"([^"]+)"\s+(?:means|refers to|is)\s+(.+?)(?:\.|$)',
            r"'([^']+)'\s+(?:means|refers to|is)\s+(.+?)(?:\.|$)",
            r'by\s+"([^"]+)"\s+I mean\s+(.+?)(?:\.|$)',
            r"by\s+'([^']+)'\s+I mean\s+(.+?)(?:\.|$)",
            r'(\w+)\s+(?:means|refers to)\s+(.+?)(?:\.|$)',
        ]

        for pattern in definition_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for term, definition in matches:
                # Try to find corresponding SQL pattern
                sql_pattern = self._find_sql_pattern_for_term(term, sql)

                if sql_pattern:
                    patterns.append(LearnedPattern(
                        pattern_type='terminology',
                        confidence=0.8,
                        data={
                            'term': term.lower().strip(),
                            'definition': definition.strip(),
                            'sql_pattern': sql_pattern
                        }
                    ))

        return patterns

    def _extract_enum_values(self, text: str, sql: str) -> list[LearnedPattern]:
        """
        Extract enum value explanations from text.

        Looks for patterns like:
        - "A means Active"
        - "status 'A' is Active"
        - "where A=Active, I=Inactive"
        """
        patterns = []

        # Pattern: value = meaning or value means meaning
        enum_patterns = [
            r"([A-Z])\s*=\s*([A-Za-z]+)",  # A=Active
            r"'([A-Z])'\s+(?:means|is)\s+([A-Za-z]+)",  # 'A' means Active
            r'"([A-Z])"\s+(?:means|is)\s+([A-Za-z]+)',  # "A" means Active
            r"status\s+'([A-Z])'\s+(?:is|means)\s+([A-Za-z]+)",  # status 'A' is Active
        ]

        # Find what column this might relate to
        column_match = re.search(r"(\w+)\s*=\s*'[A-Z]'", sql)
        column_name = column_match.group(1) if column_match else None

        for pattern in enum_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for value, meaning in matches:
                patterns.append(LearnedPattern(
                    pattern_type='enum',
                    confidence=0.7,
                    data={
                        'column': column_name,
                        'value': value.upper(),
                        'meaning': meaning.strip()
                    }
                ))

        return patterns

    def _analyze_sql_diff(self, original: str, corrected: str,
                          explanation: str) -> list[LearnedPattern]:
        """
        Analyze differences between original and corrected SQL.
        """
        patterns = []

        # Look for WHERE clause changes
        original_where = self._extract_where_clause(original)
        corrected_where = self._extract_where_clause(corrected)

        if original_where != corrected_where and explanation:
            # User changed the filter - might be correcting terminology
            patterns.append(LearnedPattern(
                pattern_type='terminology',
                confidence=0.6,
                data={
                    'term': 'user_correction',
                    'definition': explanation,
                    'sql_pattern': corrected_where or ''
                }
            ))

        return patterns

    def _extract_terms_from_confirmed_sql(self, question: str,
                                           sql: str) -> list[LearnedPattern]:
        """
        Extract term-to-SQL mappings from confirmed queries.
        """
        patterns = []

        # Find business terms in question (quoted or special words)
        question_lower = question.lower()

        # Common business terms to look for
        business_terms = [
            'active', 'inactive', 'churned', 'new', 'premium', 'free',
            'trial', 'paid', 'pending', 'completed', 'cancelled', 'failed',
            'recent', 'old', 'top', 'bottom', 'high', 'low'
        ]

        for term in business_terms:
            if term in question_lower:
                # Find corresponding SQL pattern
                sql_pattern = self._find_sql_pattern_for_term(term, sql)
                if sql_pattern:
                    patterns.append(LearnedPattern(
                        pattern_type='terminology',
                        confidence=0.5,  # Lower confidence for inferred patterns
                        data={
                            'term': term,
                            'definition': f'Inferred from confirmed query',
                            'sql_pattern': sql_pattern
                        }
                    ))

        return patterns

    def _find_sql_pattern_for_term(self, term: str, sql: str) -> Optional[str]:
        """
        Find the SQL pattern that likely implements a term.

        Looks for WHERE clause conditions that might match the term.
        """
        term_lower = term.lower()
        sql_lower = sql.lower()

        # Extract WHERE clause
        where_match = re.search(r'where\s+(.+?)(?:group by|order by|limit|$)',
                                sql_lower, re.IGNORECASE | re.DOTALL)
        if not where_match:
            return None

        where_clause = where_match.group(1).strip()

        # Look for conditions that might relate to the term
        # E.g., "active" -> "status = 'A'" or "is_active = true"

        # Check for direct matches
        if term_lower in where_clause:
            # Find the full condition containing this term
            conditions = re.split(r'\s+and\s+|\s+or\s+', where_clause, flags=re.IGNORECASE)
            for cond in conditions:
                if term_lower in cond:
                    return cond.strip()

        # Check for common abbreviation patterns
        first_letter = term_lower[0].upper()
        abbrev_pattern = rf"(\w+)\s*=\s*'{first_letter}'"
        abbrev_match = re.search(abbrev_pattern, where_clause, re.IGNORECASE)
        if abbrev_match:
            return abbrev_match.group(0)

        return None

    def _extract_where_clause(self, sql: str) -> Optional[str]:
        """Extract the WHERE clause from SQL."""
        match = re.search(r'where\s+(.+?)(?:group by|order by|limit|$)',
                         sql, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else None

    def _apply_pattern(self, target: str, pattern: LearnedPattern) -> None:
        """
        Apply a learned pattern to the semantic layer.
        """
        if pattern.pattern_type == 'terminology':
            data = pattern.data
            # Check if this term already exists
            if self.manager.exists(target):
                layer = self.manager.load(target)
                if data['term'] in layer.terminology:
                    # Don't overwrite existing terminology
                    return

            self.manager.learn_terminology(
                target,
                data['term'],
                data['definition'],
                data['sql_pattern']
            )

        elif pattern.pattern_type == 'enum':
            data = pattern.data
            if data.get('column'):
                # Need to find which table this column belongs to
                # For now, skip if we don't have table info
                pass

    def suggest_learning(self, target: str, question: str,
                         sql: str) -> list[dict]:
        """
        Suggest potential learnings without applying them.

        Returns suggestions for user confirmation.

        Args:
            target: Target database name
            question: User question
            sql: Generated SQL

        Returns:
            List of suggestions with 'type', 'description', 'data'
        """
        suggestions = []

        # Check for unmapped terms
        patterns = self._extract_terms_from_confirmed_sql(question, sql)

        for pattern in patterns:
            if pattern.pattern_type == 'terminology':
                # Check if already exists
                if self.manager.exists(target):
                    layer = self.manager.load(target)
                    if pattern.data['term'] in layer.terminology:
                        continue

                suggestions.append({
                    'type': 'terminology',
                    'description': f"Learn that '{pattern.data['term']}' means: {pattern.data['sql_pattern']}",
                    'data': pattern.data
                })

        return suggestions
