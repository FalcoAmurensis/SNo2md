"""Small public data and error types shared by CLI and future applications."""
from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Optional


MARKDOWN_SCHEMA = 1
GENERATOR_VERSION = 'md-1-pymupdf4llm-1.28.2'


class DecodeError(ValueError):
    """Input could not be decoded without losing note content."""


class Unsupported(DecodeError):
    """A currently unsupported source feature was encountered."""


class RenderError(ValueError):
    """PDF composition or text extraction failed."""


class OutputError(OSError):
    """Output could not be published; inspect the message for recovery details."""


@dataclass(frozen=True)
class ConversionOptions:
    width_scale: float = 0.5

    def __post_init__(self):
        if not math.isfinite(self.width_scale) or self.width_scale <= 0:
            raise ValueError('width_scale must be finite and positive')


@dataclass
class Note:
    source: Path
    title: str
    pages: list
    files: dict
    media: dict
    metadata: dict


@dataclass
class ConversionResult:
    pdf: Path
    counts: dict
    markdown: Optional[Path] = None
    warnings: list = field(default_factory=list)
