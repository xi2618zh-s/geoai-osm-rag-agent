"""Fixed, explicitly conservative population-grid reference composition."""

from __future__ import annotations

from src.datasets import DatasetRegistry
from src.tools import (
    AggregateByRegionInput,
    AggregationMetric,
    AnalysisToolError,
    AnalysisToolRegistry,
    BufferFeaturesInput,
    SpatialJoinInput,
)

from .contracts import (
    CONSERVATIVE_POPULATION_WARNING,
    FullyContainedGridPopulationInput,
    FullyContainedGridPopulationOutput,
)


def run_fully_contained_grid_population(
    contract: FullyContainedGridPopulationInput,
    *,
    datasets: DatasetRegistry,
    tools: AnalysisToolRegistry,
) -> FullyContainedGridPopulationOutput:
    if contract.poi_dataset.row_count != 1:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_INPUT_INVALID",
            "The fixed M05 reference case requires exactly one POI feature",
        )
    buffered = tools.invoke(
        "buffer_features",
        BufferFeaturesInput(
            dataset=contract.poi_dataset,
            distance_m=contract.buffer_distance_m,
            quad_segs=contract.quad_segs,
            output_ttl_seconds=contract.output_ttl_seconds,
        ),
        datasets=datasets,
    )
    contained = tools.invoke(
        "spatial_join",
        SpatialJoinInput(
            dataset=buffered.dataset,
            right_dataset=contract.population_dataset,
            predicate="contains",
            output_ttl_seconds=contract.output_ttl_seconds,
        ),
        datasets=datasets,
    )
    intersecting = tools.invoke(
        "spatial_join",
        SpatialJoinInput(
            dataset=buffered.dataset,
            right_dataset=contract.population_dataset,
            predicate="intersects",
            output_ttl_seconds=contract.output_ttl_seconds,
        ),
        datasets=datasets,
    )
    aggregated = tools.invoke(
        "aggregate_by_region",
        AggregateByRegionInput(
            dataset=buffered.dataset,
            values_dataset=contained.dataset,
            region_id_field=contract.poi_id_field,
            values_region_id_field=f"left_{contract.poi_id_field}",
            metrics=[
                AggregationMetric(operation="count"),
                AggregationMetric(operation="sum", field="right_population_total"),
            ],
            output_ttl_seconds=contract.output_ttl_seconds,
        ),
        datasets=datasets,
    )
    contained_records = datasets.read(contained.dataset.id).records
    final_records = datasets.read(aggregated.dataset.id).records
    if len(final_records) != 1:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_OUTPUT_INVALID",
            "The fixed M05 reference case must produce exactly one aggregate row",
        )
    properties = final_records[0]["properties"]
    population_total = properties["agg_sum_right_population_total"]
    if population_total is not None and (
        isinstance(population_total, bool) or not isinstance(population_total, int)
    ):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_OUTPUT_INVALID",
            "The reference population total must be an integer or null",
        )
    grid_ids = [item["properties"]["right_grid_id"] for item in contained_records]
    return FullyContainedGridPopulationOutput(
        dataset=aggregated.dataset,
        fully_contained_grid_ids=grid_ids,
        fully_contained_grid_count=contained.matched_pair_count,
        partially_intersecting_excluded_grid_count=(
            intersecting.matched_pair_count - contained.matched_pair_count
        ),
        fully_contained_grid_population=population_total,
        warning=CONSERVATIVE_POPULATION_WARNING,
    )
