"""Minimal, namespace-agnostic KML reader built on the standard library.

Why not GDAL/fiona? The KML driver is not present in every GDAL build, and a
take-home service should install with plain `pip`. KML is simple enough that a
small parser is safer than a heavy native dependency. `defusedxml` protects
against XML entity-expansion attacks.

Notes
- KML is always WGS84 lon/lat (EPSG:4326) by specification.
- Altitude is dropped; area/length are planar-on-the-ellipsoid quantities.
- Placemarks without a usable geometry are kept (geometry=None) so feature
  indices stay stable and the caller can report them.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from pyproj import CRS

from app.config import MAX_FEATURES

from .base import FileReadError, ParsedDataset, ParsedFeature

WGS84 = CRS.from_epsg(4326)
_GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}
_WS_AROUND_COMMA = re.compile(r"\s*,\s*")


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el: Element, name: str) -> Optional[Element]:
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _children(el: Element, name: str) -> list[Element]:
    return [c for c in el if _local(c.tag) == name]


def _coordinates(el: Element) -> list[list[float]]:
    node = _child(el, "coordinates")
    if node is None or not (node.text or "").strip():
        raise ValueError(f"<{_local(el.tag)}> has no coordinates")
    coords: list[list[float]] = []
    for token in _WS_AROUND_COMMA.sub(",", node.text.strip()).split():
        parts = token.split(",")
        if len(parts) < 2:
            raise ValueError(f"malformed coordinate tuple {token!r}")
        lon, lat = float(parts[0]), float(parts[1])
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError(f"coordinate out of range: {lon}, {lat}")
        coords.append([lon, lat])
    return coords


def _ring(container: Element, label: str) -> list[list[float]]:
    ring = _child(container, "LinearRing")
    if ring is None:
        raise ValueError(f"<{label}> has no LinearRing")
    return _coordinates(ring)


def _geometry(el: Element) -> dict[str, Any]:
    kind = _local(el.tag)
    if kind == "Point":
        coords = _coordinates(el)
        if len(coords) != 1:
            raise ValueError("Point must have exactly one coordinate")
        return {"type": "Point", "coordinates": coords[0]}
    if kind in ("LineString", "LinearRing"):
        coords = _coordinates(el)
        if len(coords) < 2:
            raise ValueError("LineString needs at least two coordinates")
        return {"type": "LineString", "coordinates": coords}
    if kind == "Polygon":
        outer = _child(el, "outerBoundaryIs")
        if outer is None:
            raise ValueError("Polygon has no outerBoundaryIs")
        rings = [_ring(outer, "outerBoundaryIs")]
        for inner in _children(el, "innerBoundaryIs"):
            rings.append(_ring(inner, "innerBoundaryIs"))
        return {"type": "Polygon", "coordinates": rings}
    if kind == "MultiGeometry":
        parts = [_geometry(c) for c in el if _local(c.tag) in _GEOMETRY_TAGS]
        if not parts:
            raise ValueError("empty MultiGeometry")
        kinds = {p["type"] for p in parts}
        for single, multi in (
            ("Point", "MultiPoint"),
            ("LineString", "MultiLineString"),
            ("Polygon", "MultiPolygon"),
        ):
            if kinds == {single}:
                return {"type": multi, "coordinates": [p["coordinates"] for p in parts]}
        return {"type": "GeometryCollection", "geometries": parts}
    raise ValueError(f"unsupported KML geometry <{kind}>")


def _text(el: Optional[Element]) -> Optional[str]:
    return (el.text or "").strip() if el is not None else None


def _properties(placemark: Element) -> dict[str, Any]:
    props: dict[str, Any] = {}
    if placemark.get("id"):
        props["kml_id"] = placemark.get("id")
    for tag in ("name", "description"):
        node = _child(placemark, tag)
        if node is not None:
            props[tag] = _text(node)
    ext = _child(placemark, "ExtendedData")
    if ext is not None:
        for node in ext.iter():
            name = node.get("name")
            if not name:
                continue
            if _local(node.tag) == "Data":
                props[name] = _text(_child(node, "value"))
            elif _local(node.tag) == "SimpleData":
                props[name] = _text(node)
    return props


def read_kml(path: Path) -> ParsedDataset:
    try:
        root = ET.parse(str(path)).getroot()
    except (ParseError, DefusedXmlException) as exc:
        raise FileReadError(f"Invalid KML/XML: {exc}") from exc

    features: list[ParsedFeature] = []
    for placemark in root.iter():
        if _local(placemark.tag) != "Placemark":
            continue
        if len(features) >= MAX_FEATURES:
            raise FileReadError(f"File has more than {MAX_FEATURES} features")

        geom_el = next((c for c in placemark if _local(c.tag) in _GEOMETRY_TAGS), None)
        geometry, error = None, None
        if geom_el is None:
            error = "Placemark has no geometry"
        else:
            try:
                geometry = _geometry(geom_el)
            except ValueError as exc:
                error = f"Could not parse geometry: {exc}"
        features.append(
            ParsedFeature(len(features), geometry, _properties(placemark), error)
        )

    if not features:
        raise FileReadError("KML contains no Placemark features")
    return ParsedDataset(crs=WGS84, features=features)
