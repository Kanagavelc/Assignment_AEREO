import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pyproj import CRS
from pyproj.exceptions import CRSError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES
from app.database import get_db
from app.models import Feature, UploadedFile
from app.schemas import FeaturePage, FileOut, MeasurementPage
from app.services.processing import process_file

router = APIRouter(prefix="/api/files", tags=["files"])

_FILE_TYPES = {".kml": "kml", ".zip": "shapefile"}


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File '{file_id}' not found")
    return record


def _require_completed(record: UploadedFile) -> None:
    if record.status != "COMPLETED":
        raise HTTPException(
            409,
            f"File status is {record.status}"
            + (f": {record.error}" if record.error else ""),
        )


def _page(db: Session, record: UploadedFile, limit: int, offset: int):
    total = db.scalar(select(func.count()).select_from(Feature).where(Feature.file_id == record.id))
    rows = db.scalars(
        select(Feature)
        .where(Feature.file_id == record.id)
        .order_by(Feature.feature_index)
        .offset(offset)
        .limit(limit)
    ).all()
    return total, rows


@router.post("/", response_model=FileOut, status_code=201)
def upload_file(
    file: UploadFile = File(..., description="A .kml file, or a .zip containing one Shapefile"),
    assume_crs: Optional[str] = Form(
        None, description="CRS to use if the file declares none, e.g. EPSG:4326"
    ),
    db: Session = Depends(get_db),
):
    """Upload a KML / zipped Shapefile; it is parsed and measured synchronously."""
    filename = Path(file.filename or "").name
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Unsupported file type. Upload a .kml or a .zip (Shapefile).")

    assumed = None
    if assume_crs:
        try:
            assumed = CRS.from_user_input(assume_crs)
        except CRSError:
            raise HTTPException(400, f"Invalid assume_crs value: {assume_crs!r}")

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / f"upload{ext}"
        size = 0
        with dest.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"File exceeds {MAX_UPLOAD_BYTES} bytes")
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "Uploaded file is empty")

        record = UploadedFile(filename=filename, file_type=_FILE_TYPES[ext], status="PENDING")
        db.add(record)
        db.commit()
        process_file(db, record, dest, assumed)

    if record.status == "FAILED":
        return JSONResponse(
            status_code=422,
            content={"id": record.id, "status": "FAILED", "detail": record.error},
        )
    return record


@router.get("/{file_id}/", response_model=FileOut)
def get_file(file_id: str, db: Session = Depends(get_db)):
    """Metadata about an uploaded file (id, filename, feature_count, crs, status)."""
    return _get_file_or_404(db, file_id)


@router.get("/{file_id}/features/", response_model=FeaturePage)
def list_features(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Extracted features: index, geometry type, geometry, CRS, properties."""
    record = _get_file_or_404(db, file_id)
    _require_completed(record)
    total, rows = _page(db, record, limit, offset)
    return {
        "file_id": record.id,
        "crs": record.crs,
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [_feature(r, with_geometry=True) for r in rows],
    }


@router.get("/{file_id}/measurements/", response_model=MeasurementPage)
def get_measurements(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Per-feature area/length plus a file-level summary."""
    record = _get_file_or_404(db, file_id)
    _require_completed(record)
    total, rows = _page(db, record, limit, offset)
    return {
        "file_id": record.id,
        "crs": record.crs,
        "summary": record.summary or {},
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [_feature(r, with_geometry=False) for r in rows],
    }


def _feature(row: Feature, with_geometry: bool) -> dict:
    d = {
        "index": row.feature_index,
        "geometry_type": row.geometry_type,
        "crs": row.crs,
        "properties": row.properties,
        "measurement": row.measurement,
    }
    if with_geometry:
        d["geometry"] = row.geometry
    return d
