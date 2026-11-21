"""Strict v1 contracts for the SCB population normalization tool."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from src.datasets import DatasetRef

from .contracts import AnalysisToolContract, AnalysisToolMetadata


class LoadPopulationDatasetInput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    provider: Literal["scb"] = "scb"
    dataset_year: Literal[2025] = 2025
    output_ttl_seconds: int = Field(default=86400, ge=1, le=604800)


class LoadPopulationDatasetOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    provider: Literal["scb"] = "scb"
    layer: Literal["stat:befolkning_1km_2025"] = "stat:befolkning_1km_2025"
    dataset_year: Literal[2025] = 2025
    field_mapping_version: Literal["scb-population-grid-2025-v1"] = (
        "scb-population-grid-2025-v1"
    )
    adapter_version: Literal["1.0"] = "1.0"
    feature_count: int = Field(ge=0)
    reported_count: int = Field(ge=0)
    missing_or_suppressed_count: int = Field(ge=0)
    metadata: AnalysisToolMetadata
