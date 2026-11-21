"""SCB population ingestion and fixed reference composition."""

from .contracts import (
    FullyContainedGridPopulationInput,
    FullyContainedGridPopulationOutput,
    PopulationDownloadReceipt,
    ScbPopulationGridRequest,
)
from .reference import run_fully_contained_grid_population
from .scb import (
    HttpResponse,
    PopulationAdapterError,
    PopulationDownload,
    build_scb_population_grid_url,
    download_scb_population_grid,
)

__all__ = [
    "FullyContainedGridPopulationInput",
    "FullyContainedGridPopulationOutput",
    "HttpResponse",
    "PopulationAdapterError",
    "PopulationDownload",
    "PopulationDownloadReceipt",
    "ScbPopulationGridRequest",
    "build_scb_population_grid_url",
    "download_scb_population_grid",
    "run_fully_contained_grid_population",
]
