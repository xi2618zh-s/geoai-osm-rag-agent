"""Allow-listed runtime for strict DatasetRef analysis tools."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from src.datasets import DatasetRef, DatasetRegistry, DatasetRegistryError

from .contracts import AnalysisToolContract, ToolSpec


class AnalysisToolError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


ToolHandler = Callable[[AnalysisToolContract, DatasetRegistry], AnalysisToolContract]


@dataclass(frozen=True)
class ToolRegistration:
    spec: ToolSpec
    input_model: type[AnalysisToolContract]
    output_model: type[AnalysisToolContract]
    handler: ToolHandler


class AnalysisToolRegistry:
    """Validates contracts and resource limits around deterministic handlers."""

    def __init__(self):
        self._tools: dict[str, ToolRegistration] = {}

    def register(self, registration: ToolRegistration) -> None:
        name = registration.spec.name
        if name in self._tools:
            raise ValueError(f"Analysis tool already registered: {name}")
        expected_input = f"{registration.input_model.__name__}.v1"
        expected_output = f"{registration.output_model.__name__}.v1"
        if registration.spec.input_contract != expected_input:
            raise ValueError(f"Tool input contract must be {expected_input}")
        if registration.spec.output_contract != expected_output:
            raise ValueError(f"Tool output contract must be {expected_output}")
        self._tools[name] = registration

    def describe(self) -> list[ToolSpec]:
        return [self._tools[name].spec for name in sorted(self._tools)]

    def get(self, name: str) -> ToolRegistration:
        try:
            return self._tools[name]
        except KeyError as error:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_NOT_REGISTERED",
                f"Analysis tool is not allow-listed: {name}",
            ) from error

    @staticmethod
    def _parse_input(model: type[AnalysisToolContract], payload: Any) -> AnalysisToolContract:
        if isinstance(payload, model):
            return payload
        if isinstance(payload, BaseModel):
            payload = payload.model_dump(mode="json")
        try:
            return model.model_validate_json(json.dumps(payload, ensure_ascii=False))
        except (TypeError, ValueError, ValidationError) as error:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_INPUT_INVALID", f"Invalid analysis tool input: {error}"
            ) from error

    @staticmethod
    def _dataset_refs(contract: AnalysisToolContract) -> list[DatasetRef]:
        refs: list[DatasetRef] = []
        seen: set[str] = set()
        for field_name in contract.__class__.model_fields:
            value = getattr(contract, field_name)
            if isinstance(value, DatasetRef) and value.id not in seen:
                refs.append(value)
                seen.add(value.id)
        if not refs:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_INPUT_INVALID",
                "Tool input must contain at least one DatasetRef",
            )
        return refs

    @staticmethod
    def _current_refs(
        refs: list[DatasetRef], datasets: DatasetRegistry
    ) -> list[DatasetRef]:
        current_refs: list[DatasetRef] = []
        for dataset in refs:
            try:
                current = datasets.get(dataset.id, verify=True)
            except DatasetRegistryError as error:
                raise AnalysisToolError(
                    "ANALYSIS_TOOL_DATASET_ACCESS_FAILED",
                    f"Dataset access failed ({error.code}): {error}",
                ) from error
            if current != dataset:
                raise AnalysisToolError(
                    "ANALYSIS_TOOL_DATASET_REF_STALE",
                    f"Input DatasetRef does not match current registry metadata: {dataset.id}",
                )
            current_refs.append(current)
        return current_refs

    @staticmethod
    def _release_output_safely(
        candidate: object,
        input_ids: set[str],
        datasets: DatasetRegistry,
    ) -> None:
        if not isinstance(candidate, DatasetRef) or candidate.id in input_ids:
            return
        try:
            current = datasets.get(candidate.id, verify=False)
            if current.lifecycle == "temporary":
                datasets.release(candidate.id)
        except DatasetRegistryError:
            pass

    def invoke(
        self,
        name: str,
        payload: Any,
        *,
        datasets: DatasetRegistry,
    ) -> AnalysisToolContract:
        registration = self.get(name)
        contract = self._parse_input(registration.input_model, payload)
        supplied_refs = self._dataset_refs(contract)
        input_refs = self._current_refs(supplied_refs, datasets)
        input_ids = {item.id for item in input_refs}
        spec = registration.spec
        for dataset in input_refs:
            if dataset.kind not in spec.supported_dataset_kinds:
                raise AnalysisToolError(
                    "ANALYSIS_TOOL_DATASET_KIND_UNSUPPORTED",
                    f"{name} does not support dataset kind: {dataset.kind}",
                )
        total_features = sum(item.row_count for item in input_refs)
        total_bytes = sum(item.size_bytes for item in input_refs)
        if (
            total_features > spec.limits.max_input_features
            or total_bytes > spec.limits.max_input_bytes
        ):
            raise AnalysisToolError(
                "ANALYSIS_TOOL_LIMIT_EXCEEDED",
                f"{name} cumulative input exceeds declared feature or byte limit",
            )
        try:
            result = registration.handler(contract, datasets)
        except AnalysisToolError:
            raise
        except DatasetRegistryError as error:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_DATASET_ACCESS_FAILED",
                f"Dataset access failed ({error.code}): {error}",
            ) from error
        except Exception as error:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_EXECUTION_FAILED", f"{name} failed: {error}"
            ) from error
        candidate_output = getattr(result, "dataset", None)
        for dataset in input_refs:
            try:
                current_after_execution = datasets.get(dataset.id, verify=True)
            except DatasetRegistryError as error:
                self._release_output_safely(candidate_output, input_ids, datasets)
                raise AnalysisToolError(
                    "ANALYSIS_TOOL_INPUT_MUTATED",
                    f"{name} changed or invalidated input {dataset.id} ({error.code})",
                ) from error
            if current_after_execution != dataset:
                self._release_output_safely(candidate_output, input_ids, datasets)
                raise AnalysisToolError(
                    "ANALYSIS_TOOL_INPUT_MUTATED",
                    f"{name} changed input dataset metadata: {dataset.id}",
                )
        try:
            validated = (
                result
                if isinstance(result, registration.output_model)
                else registration.output_model.model_validate(result)
            )
            reported_dataset = getattr(validated, "dataset", None)
            if not isinstance(reported_dataset, DatasetRef):
                raise ValueError("output must contain a DatasetRef")
            if spec.produces_dataset:
                if reported_dataset.id in input_ids:
                    raise ValueError("derived output must use a new dataset ID")
                try:
                    current_output = datasets.get(reported_dataset.id, verify=True)
                except DatasetRegistryError as error:
                    raise ValueError(
                        f"derived output is not registered or valid ({error.code})"
                    ) from error
                if current_output != reported_dataset:
                    raise ValueError(
                        "derived output DatasetRef does not match registry metadata"
                    )
                lineage_parent_ids = {
                    parent_id
                    for item in reported_dataset.lineage
                    for parent_id in item.parent_ids
                }
                if not input_ids.issubset(lineage_parent_ids):
                    raise ValueError(
                        "derived output lineage is missing direct input parent IDs"
                    )
            elif reported_dataset != input_refs[0]:
                raise ValueError("read-only output DatasetRef must match the primary input")
            serialized = validated.model_dump_json().encode("utf-8")
        except (TypeError, ValueError, ValidationError) as error:
            self._release_output_safely(candidate_output, input_ids, datasets)
            raise AnalysisToolError(
                "ANALYSIS_TOOL_OUTPUT_INVALID", f"Invalid {name} output: {error}"
            ) from error
        if (
            len(serialized) > spec.limits.max_output_bytes
            or (
                spec.produces_dataset
                and reported_dataset.size_bytes > spec.limits.max_output_bytes
            )
        ):
            self._release_output_safely(reported_dataset, input_ids, datasets)
            raise AnalysisToolError(
                "ANALYSIS_TOOL_OUTPUT_LIMIT_EXCEEDED",
                f"{name} output exceeds declared byte limit",
            )
        return validated
