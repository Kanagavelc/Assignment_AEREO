"""Orchestrates: read file -> pick CRS -> measure every feature -> persist."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pyproj import CRS
from sqlalchemy.orm import Session

from app.models import Feature, UploadedFile

from .crs import crs_label
from .measurements import MeasurementEngine, UnsupportedCRSError, summarise
from .readers import FileReadError, read_dataset

log = logging.getLogger(__name__)


class ProcessingError(Exception):
    """A problem with the file's content that the uploader can fix."""


def process_file(
    db: Session, record: UploadedFile, path: Path, assume_crs: Optional[CRS] = None
) -> UploadedFile:
    record.status = "PROCESSING"
    db.commit()
    try:
        dataset = read_dataset(path, record.file_type)

        crs = dataset.crs or assume_crs
        if crs is None:
            raise ProcessingError(
                "The file does not declare a CRS (missing .prj). Re-upload with the "
                "'assume_crs' form field, e.g. assume_crs=EPSG:4326."
            )
        label = crs_label(crs)
        engine = MeasurementEngine(crs)

        rows, measurements = [], []
        for pf in dataset.features:
            if pf.error and pf.geometry is None:
                m = {
                    "status": "UNSUPPORTED" if "no geometry" in pf.error.lower()
                    or "null shape" in pf.error.lower() else "ERROR",
                    "message": pf.error,
                }
            else:
                m = engine.measure(pf.geometry)
            measurements.append(m)
            rows.append(
                {
                    "file_id": record.id,
                    "feature_index": pf.index,
                    "geometry_type": pf.geometry.get("type") if pf.geometry else None,
                    "geometry": pf.geometry,
                    "crs": label,
                    "properties": pf.properties,
                    "measurement": m,
                }
            )

        db.bulk_insert_mappings(Feature, rows)
        record.crs = label
        record.feature_count = len(rows)
        record.summary = summarise(measurements)
        record.status = "COMPLETED"
        record.error = None
        db.commit()
    except (FileReadError, ProcessingError, UnsupportedCRSError) as exc:
        db.rollback()
        record.status, record.error = "FAILED", str(exc)
        db.commit()
    except Exception:
        log.exception("Unexpected failure processing file %s", record.id)
        db.rollback()
        record.status = "FAILED"
        record.error = "Unexpected error while processing the file"
        db.commit()
    return record
