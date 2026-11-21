"""M05 composite registry: M04 tools plus SCB population normalization."""

from __future__ import annotations

from .contracts import ToolLimits, ToolSpec
from .population import load_population_dataset
from .population_contracts import (
    LoadPopulationDatasetInput,
    LoadPopulationDatasetOutput,
)
from .quality import (
    DEFAULT_MAX_INPUT_BYTES,
    DEFAULT_MAX_INPUT_FEATURES,
    DEFAULT_MAX_OUTPUT_BYTES,
)
from .registry import AnalysisToolRegistry, ToolRegistration
from .spatial import build_analysis_tool_registry


def build_population_tool_registry(
    *,
    max_input_features: int = DEFAULT_MAX_INPUT_FEATURES,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> AnalysisToolRegistry:
    base = build_analysis_tool_registry(
        max_input_features=max_input_features,
        max_input_bytes=max_input_bytes,
        max_output_bytes=max_output_bytes,
    )
    registry = AnalysisToolRegistry()
    for spec in base.describe():
        registry.register(base.get(spec.name))
    limits = ToolLimits(
        max_input_features=max_input_features,
        max_input_bytes=max_input_bytes,
        max_output_bytes=max_output_bytes,
    )
    registry.register(
        ToolRegistration(
            spec=ToolSpec(
                name="load_population_dataset",
                description=(
                    "Validate and normalize a registered SCB 2025 "
                    "population-grid DatasetRef"
                ),
                input_contract="LoadPopulationDatasetInput.v1",
                output_contract="LoadPopulationDatasetOutput.v1",
                supported_dataset_kinds=["vector"],
                produces_dataset=True,
                mutates_input=False,
                deterministic=True,
                limits=limits,
            ),
            input_model=LoadPopulationDatasetInput,
            output_model=LoadPopulationDatasetOutput,
            handler=load_population_dataset,
        )
    )
    return registry


POPULATION_TOOL_REGISTRY = build_population_tool_registry()
