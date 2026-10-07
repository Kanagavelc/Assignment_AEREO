"""Format-agnostic structures every reader produces.

Readers know about file formats; nothing downstream (CRS handling, measurement,
persistence) knows or cares which format the data came from.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from pyproj import CRS


class FileReadError(Exception):
    """The file is corrupt, incomplete, or otherwise unreadable."""


@dataclass
class ParsedFeature:
    index: int
    geometry: Optional[dict[str, Any]]  # GeoJSON-style geometry, source CRS
    properties: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None  # per-feature parse problem (feature is kept)


@dataclass
class ParsedDataset:
    crs: Optional[CRS]  # None => the file did not declare one
    features: list[ParsedFeature]
