"""Schema-owned semantic-layer package."""

from __future__ import annotations

from .ai_annotator import AIAnnotator
from .annotate_wizard import AnnotateWizard
from .data_profiler import DataProfiler
from .guided_annotator import GuidedAnnotator, _parse_row_estimate
from .introspector import SchemaIntrospector
from .manager import SemanticLayerManager


def create_ai_annotator(*args, **kwargs):
    return AIAnnotator(*args, **kwargs)


def create_data_profiler(*args, **kwargs):
    return DataProfiler(*args, **kwargs)


def create_guided_annotator(*args, **kwargs):
    return GuidedAnnotator(*args, **kwargs)


def create_semantic_layer_manager(*args, **kwargs):
    return SemanticLayerManager(*args, **kwargs)


def parse_row_estimate(value):
    return _parse_row_estimate(value)


__all__ = [
    "AIAnnotator",
    "AnnotateWizard",
    "DataProfiler",
    "GuidedAnnotator",
    "SchemaIntrospector",
    "SemanticLayerManager",
    "create_ai_annotator",
    "create_data_profiler",
    "create_guided_annotator",
    "create_semantic_layer_manager",
    "parse_row_estimate",
]
