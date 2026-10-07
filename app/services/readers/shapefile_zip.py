"""Reader for a zipped ESRI Shapefile (.shp + .dbf [+ .prj, .cpg, .shx]).

Uses `pyshp` (pure Python) so there is no GDAL install step. The zip is
untrusted input, so extraction is deliberately restrictive:
  * only the handful of shapefile extensions are ever written to disk,
  * member names are discarded (written as layer.<ext>) -> no zip-slip,
  * decompressed bytes are counted while copying -> no zip bombs.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

import shapefile  # pyshp
from pyproj import CRS
from pyproj.exceptions import CRSError

from app.config import MAX_FEATURES, MAX_UNCOMPRESSED_BYTES

from .base import FileReadError, ParsedDataset, ParsedFeature

_EXTS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _extract(zip_path: Path, dest: Path) -> None:
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise FileReadError("Upload is not a valid zip archive") from exc

    with zf:
        groups: dict[str, dict[str, zipfile.ZipInfo]] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            p = PurePosixPath(info.filename.replace("\\", "/"))
            if "__MACOSX" in p.parts or p.name.startswith("."):
                continue
            if p.suffix.lower() not in _EXTS:
                continue
            group = groups.setdefault(str(p.with_suffix("")).lower(), {})
            if p.suffix.lower() in group:
                raise FileReadError(f"Duplicate {p.suffix.lower()} file in archive")
            group[p.suffix.lower()] = info

        candidates = {stem: g for stem, g in groups.items() if ".shp" in g}
        if not candidates:
            raise FileReadError("Zip does not contain a .shp file")
        if len(candidates) > 1:
            raise FileReadError(
                "Zip contains multiple shapefiles; upload one shapefile per zip"
            )
        members = next(iter(candidates.values()))
        if ".dbf" not in members:
            raise FileReadError("Shapefile is missing its .dbf attribute table")

        written = 0
        for ext, info in members.items():
            with zf.open(info) as src, open(dest / f"layer{ext}", "wb") as out:
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_UNCOMPRESSED_BYTES:
                        raise FileReadError("Archive expands beyond the allowed size")
                    out.write(chunk)


def read_shapefile_zip(path: Path) -> ParsedDataset:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        _extract(path, tmp_dir)

        crs = None
        prj = tmp_dir / "layer.prj"
        if prj.exists():
            try:
                crs = CRS.from_wkt(prj.read_text(errors="replace"))
            except CRSError as exc:
                raise FileReadError(f"Could not parse .prj projection file: {exc}") from exc

        encoding = "utf-8"
        cpg = tmp_dir / "layer.cpg"
        if cpg.exists():
            encoding = cpg.read_text(errors="ignore").strip() or encoding

        try:
            reader = shapefile.Reader(
                str(tmp_dir / "layer"), encoding=encoding, encodingErrors="replace"
            )
        except Exception as exc:  # pyshp raises several exception types
            raise FileReadError(f"Could not open shapefile: {exc}") from exc

        features: list[ParsedFeature] = []
        with reader:
            try:
                for i, sr in enumerate(reader.iterShapeRecords()):
                    if i >= MAX_FEATURES:
                        raise FileReadError(f"File has more than {MAX_FEATURES} features")
                    geometry, error = None, None
                    if sr.shape.shapeType == shapefile.NULL:
                        error = "Null shape (feature has no geometry)"
                    else:
                        try:
                            geometry = _json_safe(sr.shape.__geo_interface__)
                        except Exception as exc:
                            error = f"Could not convert geometry: {exc}"
                    props = _json_safe(sr.record.as_dict())
                    features.append(ParsedFeature(i, geometry, props, error))
            except FileReadError:
                raise
            except Exception as exc:
                raise FileReadError(f"Shapefile is corrupt: {exc}") from exc

        if not features:
            raise FileReadError("Shapefile contains no features")
        return ParsedDataset(crs=crs, features=features)
