"""M12 scenario layer (ADR-0021): restricted command grammars, typed scenario plans,
bounded validated execution and evidence-backed scenario reports."""
from .bindings import check_binding, crs_plausible, reconfirm_source_crs
from .contracts import (
    MAX_PLAN_STEPS, MAX_TOOL_CALLS, CatalogueEntry, CrsEvidence, CrsReconfirmation, FrontendResult,
    ScenarioCatalogue, ScenarioClarification, ScenarioError, ScenarioIntent, ScenarioOutcome, ScenarioPlan,
    ScenarioStep, StepRecord,
)
from .executor import execute_scenario_plan
from .gate import PLANNABLE_TOOLS, validate_plan
from .registry import SCENARIO_TOOL_REGISTRY, build_scenario_tool_registry

__all__ = [
    "MAX_PLAN_STEPS", "MAX_TOOL_CALLS", "PLANNABLE_TOOLS", "SCENARIO_TOOL_REGISTRY", "CatalogueEntry", "CrsEvidence",
    "CrsReconfirmation", "FrontendResult", "ScenarioCatalogue", "ScenarioClarification", "ScenarioError",
    "ScenarioIntent", "ScenarioOutcome", "ScenarioPlan", "ScenarioStep", "StepRecord", "build_scenario_tool_registry",
    "check_binding", "crs_plausible", "execute_scenario_plan", "reconfirm_source_crs", "validate_plan",
]
