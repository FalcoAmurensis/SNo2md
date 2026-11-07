"""Small public data and error types shared by CLI and future applications."""
from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Optional


MARKDOWN_SCHEMA = 4
GENERATOR_VERSION = 'md-12-image-review-output'


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
    image_storage: str = 'external'
    document_folders: bool = True
    review_images: bool = True
    repeat_distance: int = 8

    def __post_init__(self):
        if not math.isfinite(self.width_scale) or self.width_scale <= 0:
            raise ValueError('width_scale must be finite and positive')
        if self.image_storage not in {'external', 'embedded'}:
            raise ValueError('image_storage must be external or embedded')
        if type(self.document_folders) is not bool:
            raise ValueError('document_folders must be a boolean')
        if type(self.review_images) is not bool:
            raise ValueError('review_images must be a boolean')
        if type(self.repeat_distance) is not int or not 0 <= self.repeat_distance <= 32:
            raise ValueError('repeat_distance must be an integer from 0 to 32')


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
    chunk_index: Optional[Path] = None
    image_assets: dict = field(default_factory=dict)
    image_review_report: Optional[dict] = None
