from pathlib import Path

from .base import FileReadError, ParsedDataset, ParsedFeature
from .kml import read_kml
from .shapefile_zip import read_shapefile_zip

__all__ = ["FileReadError", "ParsedDataset", "ParsedFeature", "read_dataset"]


def read_dataset(path: Path, file_type: str) -> ParsedDataset:
    """Dispatch to the right reader. Every reader returns the same ParsedDataset."""
    if file_type == "kml":
        return read_kml(path)
    if file_type == "shapefile":
        return read_shapefile_zip(path)
    raise FileReadError(f"Unsupported file type: {file_type}")
