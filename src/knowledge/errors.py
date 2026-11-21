"""Stable M03 knowledge-pipeline errors."""

from __future__ import annotations


class KnowledgeValidationError(ValueError):
    """Validation failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        super().__init__(message)


class KnowledgeBuildError(RuntimeError):
    """Build failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        super().__init__(message)