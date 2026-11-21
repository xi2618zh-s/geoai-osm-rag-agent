"""Public contracts and registry for DatasetRef-based analysis tools."""

from .contracts import (
    AnalysisToolMetadata,
    GeometryIssue,
    InspectDatasetSchemaInput,
    InspectDatasetSchemaOutput,
    ToolLimits,
    ToolSpec,
    ValidateGeometryInput,
    ValidateGeometryOutput,
)
from .accessibility_contracts import (AnalyzeGtfsStopCoverageInput, AnalyzeGtfsStopCoverageOutput, GtfsTimeWindow)
from .accessibility_registry import ACCESSIBILITY_TOOL_REGISTRY, build_accessibility_tool_registry
from .quality import QUALITY_TOOL_REGISTRY, build_quality_tool_registry
from .population_contracts import (
    LoadPopulationDatasetInput,
    LoadPopulationDatasetOutput,
)
from .population_registry import (
    POPULATION_TOOL_REGISTRY,
    build_population_tool_registry,
)
from .registry import AnalysisToolError, AnalysisToolRegistry, ToolRegistration
from .spatial import ANALYSIS_TOOL_REGISTRY, build_analysis_tool_registry
from .spatial_contracts import (
    AggregateByRegionInput,
    AggregateByRegionOutput,
    AggregationMetric,
    AttributeFilterCondition,
    BufferFeaturesInput,
    BufferFeaturesOutput,
    FeaturesWithinInput,
    FeaturesWithinOutput,
    FilterAttributesInput,
    FilterAttributesOutput,
    NearestFeaturesInput,
    NearestFeaturesOutput,
    RepairGeometryInput,
    RepairGeometryOutput,
    ReprojectDatasetInput,
    ReprojectDatasetOutput,
    SpatialJoinInput,
    SpatialJoinOutput,
)

__all__ = [
    "ACCESSIBILITY_TOOL_REGISTRY",
    "AnalyzeGtfsStopCoverageInput",
    "AnalyzeGtfsStopCoverageOutput",
    "GtfsTimeWindow",
    "build_accessibility_tool_registry",
    "AnalysisToolError",
    "AnalysisToolMetadata",
    "AnalysisToolRegistry",
    "ANALYSIS_TOOL_REGISTRY",
    "AggregateByRegionInput",
    "AggregateByRegionOutput",
    "AggregationMetric",
    "AttributeFilterCondition",
    "BufferFeaturesInput",
    "BufferFeaturesOutput",
    "FeaturesWithinInput",
    "FeaturesWithinOutput",
    "FilterAttributesInput",
    "FilterAttributesOutput",
    "GeometryIssue",
    "InspectDatasetSchemaInput",
    "InspectDatasetSchemaOutput",
    "LoadPopulationDatasetInput",
    "LoadPopulationDatasetOutput",
    "POPULATION_TOOL_REGISTRY",
    "QUALITY_TOOL_REGISTRY",
    "NearestFeaturesInput",
    "NearestFeaturesOutput",
    "RepairGeometryInput",
    "RepairGeometryOutput",
    "ReprojectDatasetInput",
    "ReprojectDatasetOutput",
    "SpatialJoinInput",
    "SpatialJoinOutput",
    "ToolLimits",
    "ToolRegistration",
    "ToolSpec",
    "ValidateGeometryInput",
    "ValidateGeometryOutput",
    "build_analysis_tool_registry",
    "build_population_tool_registry",
    "build_quality_tool_registry",
]
