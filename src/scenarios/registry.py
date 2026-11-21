"""SCENARIO_TOOL_REGISTRY: the 12 M06b accessibility tools (M04 10 + M05 1 + M06b 1).

Later M12 sub-stages add only ADR-approved scenario tools here (ADR-0022, ADR-0023);
the existing registries and the M07 agent whitelist are never modified.
"""
from __future__ import annotations

from src.tools.accessibility_registry import build_accessibility_tool_registry
from src.tools.registry import AnalysisToolRegistry


def build_scenario_tool_registry() -> AnalysisToolRegistry:
    base = build_accessibility_tool_registry()
    registry = AnalysisToolRegistry()
    for spec in base.describe():
        registry.register(base.get(spec.name))
    return registry


SCENARIO_TOOL_REGISTRY = build_scenario_tool_registry()
