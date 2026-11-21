"""M06b registry: existing M04/M05 tools plus GTFS stop coverage."""

from __future__ import annotations

from .accessibility import analyze_gtfs_stop_coverage
from .accessibility_contracts import (
    AnalyzeGtfsStopCoverageInput,
    AnalyzeGtfsStopCoverageOutput,
)
from .contracts import ToolLimits, ToolSpec
from .population_registry import build_population_tool_registry
from .quality import (
    DEFAULT_MAX_INPUT_BYTES,
    DEFAULT_MAX_INPUT_FEATURES,
    DEFAULT_MAX_OUTPUT_BYTES,
)
from .registry import AnalysisToolRegistry, ToolRegistration


def build_accessibility_tool_registry(
    *,
    max_input_features: int = DEFAULT_MAX_INPUT_FEATURES,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> AnalysisToolRegistry:
    base = build_population_tool_registry(
        max_input_features=max_input_features,
        max_input_bytes=max_input_bytes,
        max_output_bytes=max_output_bytes,
    )
    registry = AnalysisToolRegistry()
    for spec in base.describe():
        registry.register(base.get(spec.name))
    registry.register(ToolRegistration(
        spec=ToolSpec(
            name="analyze_gtfs_stop_coverage",
            description=(
                "Count service-date GTFS stop departures and route coverage, "
                "buffer active stops, and aggregate unique POIs and fully "
                "contained population grids across ordered time windows"
            ),
            input_contract="AnalyzeGtfsStopCoverageInput.v1",
            output_contract="AnalyzeGtfsStopCoverageOutput.v1",
            supported_dataset_kinds=["network", "vector"],
            produces_dataset=True,
            mutates_input=False,
            deterministic=True,
            limits=ToolLimits(
                max_input_features=max_input_features,
                max_input_bytes=max_input_bytes,
                max_output_bytes=max_output_bytes,
            ),
        ),
        input_model=AnalyzeGtfsStopCoverageInput,
        output_model=AnalyzeGtfsStopCoverageOutput,
        handler=analyze_gtfs_stop_coverage,
    ))
    return registry


ACCESSIBILITY_TOOL_REGISTRY = build_accessibility_tool_registry()
