import io
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest
import shapefile
from fastapi.testclient import TestClient
from pyproj import CRS
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def make_shapefile_zip(shape_kind: str, crs: CRS | None, shapes, records=None) -> bytes:
    """Build an in-memory zipped shapefile. shape_kind: 'poly' | 'line' | 'point'."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "data"
        w = shapefile.Writer(str(base))
        w.field("name", "C")
        w.field("value", "N", decimal=2)
        for i, s in enumerate(shapes):
            if shape_kind == "poly":
                w.poly(s)
            elif shape_kind == "line":
                w.line(s)
            else:
                w.point(*s)
            w.record(f"f{i}", i * 1.5)
        w.close()
        if crs is not None:
            (Path(tmp) / "data.prj").write_text(crs.to_wkt(version="WKT1_ESRI"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for p in Path(tmp).iterdir():
                zf.write(p, p.name)
        return buf.getvalue()
