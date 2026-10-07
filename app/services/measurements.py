"""Measurement of individual geometries.

Flow for one feature
    source CRS --(1)--> WGS84 lon/lat --(2)--> local projected CRS --(3)--> metres

 (1) Gives a lon/lat centroid, which is used to choose the projection, no matter
     whether the file was geographic or already projected.
 (2) Polygons -> local Lambert Azimuthal Equal-Area (area-preserving).
     Lines    -> UTM zone of the centroid (conformal, accurate lengths).
 (3) shapely computes planar area / length in metres.

As a sanity check each result also carries a geodesic value computed directly on
the WGS84 ellipsoid. Degrees are never used for area or distance.
"""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

from .crs import WGS84, crs_label, laea_transformer, utm_epsg, utm_transformer

GEOD = Geod(ellps="WGS84")
AREA_TYPES = {"Polygon", "MultiPolygon"}
LINE_TYPES = {"LineString", "MultiLineString"}
NO_MEASURE_TYPES = {"Point", "MultiPoint"}


class UnsupportedCRSError(ValueError):
    pass


def _result(status: str, message: Optional[str] = None, **values: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status}
    out.update(values)
    if message:
        out["message"] = message
    return out


def _polygonal_part(geom):
    """Repair an invalid polygon and keep only its areal pieces."""
    fixed = shapely.make_valid(geom)
    if fixed.geom_type in AREA_TYPES:
        return fixed
    parts = [g for g in getattr(fixed, "geoms", []) if g.geom_type in AREA_TYPES]
    if not parts:
        raise ValueError("geometry has no area after repair")
    return shapely.union_all(parts)


def _geodesic_area(wgs_geom) -> float:
    total = 0.0
    for poly in getattr(wgs_geom, "geoms", [wgs_geom]):
        shell = abs(GEOD.polygon_area_perimeter(*poly.exterior.xy)[0])
        holes = sum(abs(GEOD.polygon_area_perimeter(*r.xy)[0]) for r in poly.interiors)
        total += shell - holes
    return total


class MeasurementEngine:
    def __init__(self, source_crs: CRS):
        if not (source_crs.is_geographic or source_crs.is_projected):
            raise UnsupportedCRSError(
                f"CRS '{crs_label(source_crs)}' is neither geographic nor projected"
            )
        self.source_crs = source_crs
        self._to_wgs84 = Transformer.from_crs(source_crs, WGS84, always_xy=True)

    def measure(self, geometry: Optional[dict[str, Any]]) -> dict[str, Any]:
        if geometry is None:
            return _result("UNSUPPORTED", "Feature has no geometry")
        gtype = geometry.get("type")
        if gtype in NO_MEASURE_TYPES:
            return _result("NOT_REQUIRED", "No measurement is defined for points")
        if gtype not in AREA_TYPES | LINE_TYPES:
            return _result("UNSUPPORTED", f"Measurement not supported for {gtype}")

        try:
            geom = shapely.force_2d(shape(geometry))
            if geom.is_empty:
                return _result("ERROR", "Geometry is empty")

            warnings: list[str] = []
            if gtype in AREA_TYPES and not geom.is_valid:
                geom = _polygonal_part(geom)
                warnings.append("Invalid polygon was repaired before measuring")

            wgs = shp_transform(self._to_wgs84.transform, geom)
            if not np.isfinite(shapely.get_coordinates(wgs)).all():
                return _result("ERROR", "Coordinates cannot be transformed (outside CRS domain)")

            c = wgs.centroid
            if gtype in AREA_TYPES:
                out = self._area(wgs, c.x, c.y)
            else:
                out = self._length(wgs, c.x, c.y)
            if warnings:
                out["warnings"] = warnings
            return out
        except Exception as exc:  # one bad feature must never fail the whole file
            return _result("ERROR", f"Could not measure geometry: {exc}")

    def _area(self, wgs, lon: float, lat: float) -> dict[str, Any]:
        lat0, lon0 = round(lat, 1), round(lon, 1)
        projected = shp_transform(laea_transformer(lat0, lon0).transform, wgs)
        area = projected.area
        out = _result(
            "OK",
            area_sq_m=round(area, 4),
            area_hectares=round(area / 10_000, 6),
            area_sq_km=round(area / 1_000_000, 8),
            method="projected",
            projected_crs=f"Lambert Azimuthal Equal-Area (lat_0={lat0}, lon_0={lon0})",
        )
        try:
            out["geodesic_area_sq_m"] = round(_geodesic_area(wgs), 4)
        except Exception:
            pass
        return out

    def _length(self, wgs, lon: float, lat: float) -> dict[str, Any]:
        epsg = utm_epsg(lon, lat)
        projected = shp_transform(utm_transformer(epsg).transform, wgs)
        length = projected.length
        out = _result(
            "OK",
            length_m=round(length, 4),
            length_km=round(length / 1000, 6),
            method="projected",
            projected_crs=f"EPSG:{epsg}",
        )
        try:
            out["geodesic_length_m"] = round(GEOD.geometry_length(wgs), 4)
        except Exception:
            pass
        return out


def summarise(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-feature results into file-level totals."""
    counts = {"OK": 0, "NOT_REQUIRED": 0, "UNSUPPORTED": 0, "ERROR": 0}
    area = length = 0.0
    for m in measurements:
        counts[m["status"]] = counts.get(m["status"], 0) + 1
        area += m.get("area_sq_m", 0.0) or 0.0
        length += m.get("length_m", 0.0) or 0.0
    return {
        "features": len(measurements),
        "measured": counts["OK"],
        "not_required": counts["NOT_REQUIRED"],
        "unsupported": counts["UNSUPPORTED"],
        "errors": counts["ERROR"],
        "total_area_sq_m": round(area, 4),
        "total_length_m": round(length, 4),
    }
