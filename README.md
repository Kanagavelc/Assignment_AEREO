# Geospatial File Measurement API

A FastAPI service that accepts a **KML** file or a **zipped Shapefile**, extracts every
feature (ID, geometry type, geometry, CRS, properties) and returns **area** (polygons)
and **length** (lines), always computed in a metric projected CRS - never in degrees.

## Setup

Requires Python 3.10+. No GDAL install needed (pure `pip` dependencies).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload          # http://localhost:8000
```

- Interactive docs: http://localhost:8000/docs
- Tests: `pytest -q`
- Docker: `docker build -t geo-api . && docker run -p 8000:8000 -v geo-data:/data geo-api`

Configuration (environment variables, all optional):

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./geo.db` | Any SQLAlchemy URL |
| `MAX_UPLOAD_BYTES` | 50 MB | Max upload size (HTTP 413 above) |
| `MAX_UNCOMPRESSED_BYTES` | 200 MB | Zip-bomb guard for shapefile archives |
| `MAX_FEATURES` | 100000 | Max features per file |

A ready-made test file is in `sample_data/sample.kml`.

## API

### `POST /api/files/` - upload and process
Multipart form: `file` (`.kml` or `.zip` containing one shapefile), optional `assume_crs`
(e.g. `EPSG:4326`, used only if a shapefile has no `.prj`).

```bash
curl -F "file=@sample_data/sample.kml" http://localhost:8000/api/files/
curl -F "file=@parcels.zip" -F "assume_crs=EPSG:4326" http://localhost:8000/api/files/
```
```json
{ "id": "272e0c72b43b4bedab00003a2a1187b9", "filename": "sample.kml", "file_type": "kml",
  "feature_count": 5, "crs": "EPSG:4326", "status": "COMPLETED", "error": null,
  "created_at": "2026-10-07T05:01:23.437846Z" }
```

| Code | When |
|---|---|
| 201 | Processed (`status: COMPLETED`) |
| 400 | Empty file, or invalid `assume_crs` |
| 413 / 415 | Too large / not `.kml` or `.zip` |
| 422 | Content problem (corrupt zip, bad XML, no `.prj`, ...). Body: `{"id", "status": "FAILED", "detail"}`; the record stays queryable |

### `GET /api/files/{id}/` - file information
Same shape as the upload response. `404` if unknown. `status` is `PENDING | PROCESSING | COMPLETED | FAILED`.

### `GET /api/files/{id}/measurements/?limit=100&offset=0`
```json
{
  "file_id": "272e...", "crs": "EPSG:4326", "total": 5, "limit": 100, "offset": 0,
  "summary": { "features": 5, "measured": 2, "not_required": 1, "unsupported": 2,
               "errors": 0, "total_area_sq_m": 1200289.838, "total_length_m": 3100.8359 },
  "results": [
    { "index": 0, "geometry_type": "Polygon", "crs": "EPSG:4326",
      "properties": { "name": "Plot A (polygon)", "owner": "Demo" },
      "measurement": { "status": "OK", "area_sq_m": 1200289.838, "area_hectares": 120.028984,
                       "area_sq_km": 1.20028984, "method": "projected",
                       "projected_crs": "Lambert Azimuthal Equal-Area (lat_0=13.0, lon_0=77.6)",
                       "geodesic_area_sq_m": 1200289.8425 } },
    { "index": 1, "geometry_type": "LineString", "crs": "EPSG:4326",
      "properties": { "name": "Road (line)" },
      "measurement": { "status": "OK", "length_m": 3100.8359, "length_km": 3.100836,
                       "method": "projected", "projected_crs": "EPSG:32643",
                       "geodesic_length_m": 3099.0252 } },
    { "index": 2, "geometry_type": "Point", "crs": "EPSG:4326",
      "properties": { "name": "Gate (point)" },
      "measurement": { "status": "NOT_REQUIRED", "message": "No measurement is defined for points" } },
    { "index": 3, "geometry_type": "GeometryCollection", "...": "...",
      "measurement": { "status": "UNSUPPORTED", "message": "Measurement not supported for GeometryCollection" } }
  ]
}
```
Per-feature `measurement.status`: `OK`, `NOT_REQUIRED` (points), `UNSUPPORTED` (e.g. mixed collections, no geometry), `ERROR` (e.g. empty or unmeasurable geometry). One bad feature never fails the file. Returns `409` if the file is not `COMPLETED`.

### `GET /api/files/{id}/features/?limit=100&offset=0` (extra)
Same pagination; each item adds the full GeoJSON-style `geometry`.

## Architecture

```
app/
  main.py            FastAPI app, router wiring, table creation
  api/files.py       HTTP layer: validation, upload streaming, pagination
  models.py          SQLAlchemy: UploadedFile 1---* Feature
  schemas.py         Pydantic response models
  services/
    readers/         one module per format -> common ParsedDataset
      kml.py           stdlib XML parser (defusedxml)
      shapefile_zip.py safe unzip + pyshp
    crs.py           CRS labels, UTM/LAEA selection (cached transformers)
    measurements.py  MeasurementEngine: geometry -> area/length
    processing.py    orchestration + status transitions
tests/               pytest + TestClient (in-memory SQLite)
```

**File-processing flow**
1. Endpoint validates extension and optional `assume_crs`, streams the upload to a temp file (size-capped).
2. A `UploadedFile` row is created (`PENDING` -> `PROCESSING`).
3. The format reader returns a `ParsedDataset` (CRS + features as GeoJSON-style geometry + properties).
4. CRS is resolved (file's own, else `assume_crs`, else the file `FAILED` with an actionable message).
5. Each feature is measured; features are bulk-inserted; a summary is stored; status -> `COMPLETED`.
6. Content errors mark the file `FAILED` with a message (HTTP 422); unexpected errors are logged and reported generically.

**Measurement flow (per feature)**
`source CRS -> WGS84 lon/lat -> local projected CRS -> shapely area/length (metres)`.
Points get `NOT_REQUIRED`; unsupported types get `UNSUPPORTED`; invalid polygons (e.g. self-intersecting) are repaired with `make_valid` and flagged in `warnings`.

**CRS handling**
- KML is always EPSG:4326. Shapefile CRS is parsed from the `.prj`.
- Geographic **and** projected sources are supported; both are first brought to WGS84 only to find the feature's centroid.
- **Polygons** -> Lambert Azimuthal Equal-Area centred on the (rounded) centroid, so area is preserved by construction.
- **Lines** -> the UTM zone of the centroid (UPS near the poles); UTM is conformal, so lengths are accurate.
- Every result also includes a `geodesic_*` value computed directly on the WGS84 ellipsoid, as an independent cross-check (differences are typically < 0.1% for city-scale features).

## Design decisions

| Decision | Why | Alternative considered |
|---|---|---|
| FastAPI | Typed request/response models and free OpenAPI docs; the work is I/O + geometry, not ORM-heavy | Django + DRF: more batteries, more ceremony for 3 endpoints |
| Synchronous processing in the request | Simple and deterministic; status is `COMPLETED` when the POST returns. Fine for files in the 10s of MB | Celery/RQ job queue: right for big files, overkill here (see Future scope) |
| `pyshp` + stdlib KML parser instead of GeoPandas/Fiona/GDAL | `pip install` works anywhere; GDAL's KML driver is not guaranteed in every build; small attack surface | GeoPandas/pyogrio: supports many more formats, much heavier install |
| Equal-area for area, UTM for length | Each is the property-preserving choice for its measurement; UTM alone distorts area by up to ~0.2% | One UTM zone for everything: simpler, slightly less accurate for area |
| Geodesic cross-check in output | Lets a reviewer verify the projected answer without trusting it blindly | Geodesic only: skips the projected-CRS requirement |
| Features stored as rows with JSON geometry/properties | Portable across SQLite/Postgres, simple pagination | PostGIS geometry columns: enables spatial queries, requires Postgres |
| Missing `.prj` => fail unless `assume_crs` given | Silently guessing a CRS gives confidently wrong numbers | Assume 4326: convenient but dangerous |
| Hardened zip extraction | Uploads are untrusted: no path traversal, no zip bombs, only shapefile extensions written | Plain `extractall` |
| Bad features degrade, bad files fail | One malformed placemark should not discard 10,000 good ones | Fail whole file on first error |

## Known limitations
- Altitude / Z values are ignored; measurements are 2D.
- Projection is chosen from the *centroid*, so geometries spanning thousands of km or crossing the antimeridian are less accurate (compare against `geodesic_*`).
- One shapefile per zip; KMZ, GeoJSON and GeoPackage are not accepted.
- No authentication, rate limiting, or file deletion endpoint.
- Tables are created at startup (no migrations); SQLite is the default store.

## Learning
> Rewrite this section in your own words before submitting; it should reflect what *you* actually learned.

While working on this assignment, I learned how to handle geospatial files such as KML and Shapefiles in a backend application. I got a better understanding of how geometries like Point, LineString, and Polygon are represented and processed.

One important thing I learned was that area and length should not be calculated directly using latitude and longitude values. I learned how CRS works and why geometries need to be transformed to a suitable projected CRS before performing measurements.

I also learned how to structure a FastAPI backend, handle file uploads, process files safely, design REST APIs, handle unsupported geometries without breaking the application, and separate file processing, CRS handling, and measurement logic into maintainable components.

## Future Scope

Some improvements I would consider for the next version are:

- Add support for more geospatial formats such as GeoJSON and GeoPackage.
- Improve automatic projected CRS selection for different geographic locations.
- Add asynchronous/background processing for very large files.
- Store uploaded files and processed results using cloud storage such as AWS S3.
- Add authentication and role-based access to the APIs.
- Add automated unit and integration tests with better test coverage.
- Add rate limiting and stronger file validation for production use.
- Add a simple frontend to upload files and visualize the geometries and measurements on a map.
## Future scope
- Background processing (Celery/RQ) with polling or webhooks for lar- PostGIS storage for spatial queries (bbox filter, intersects) and S3 for raw files.
- More formats: KMZ, GeoJSON, GeoPackage (via pyogrio) and multi-layer files.
- Per-feature projection override and 3D (Z-aware) lengths.
- Auth, per-user ownership, file deletion/TTL, rate limiting.
- Alembic migrations, structured logging, metrics, CI (GitHub Actions running `pytest`).
- Export of results as CSV / GeoJSON with measurements embedded.



