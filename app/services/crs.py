"""CRS helpers: labelling and choosing a projected CRS to measure in."""
from __future__ import annotations

from functools import lru_cache

from pyproj import CRS, Transformer

WGS84 = CRS.from_epsg(4326)


def crs_label(crs: CRS) -> str:
    """Short human-readable identifier, e.g. 'EPSG:4326'."""
    epsg = crs.to_epsg(min_confidence=70)
    return f"EPSG:{epsg}" if epsg else (crs.name or "UNKNOWN")


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone for a point (UPS at the poles, where UTM ends)."""
    if lat >= 84:
        return 32661  # WGS 84 / UPS North
    if lat <= -80:
        return 32761  # WGS 84 / UPS South
    zone = min(60, max(1, int((lon + 180) // 6) + 1))
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=256)
def utm_transformer(epsg: int) -> Transformer:
    """WGS84 lon/lat -> UTM/UPS (conformal: good for distances)."""
    return Transformer.from_crs(WGS84, CRS.from_epsg(epsg), always_xy=True)


@lru_cache(maxsize=1024)
def laea_transformer(lat0: float, lon0: float) -> Transformer:
    """WGS84 lon/lat -> Lambert Azimuthal Equal-Area centred on (lat0, lon0).

    Equal-area means polygon areas are preserved exactly, which UTM does not
    guarantee. The centre is rounded by the caller so the cache stays small.
    """
    target = CRS.from_proj4(
        f"+proj=laea +lat_0={lat0} +lon_0={lon0} +x_0=0 +y_0=0 "
        "+datum=WGS84 +units=m +no_defs"
    )
    return Transformer.from_crs(WGS84, target, always_xy=True)
