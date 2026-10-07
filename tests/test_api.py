import io
import zipfile

import pytest
from pyproj import CRS

from tests.conftest import make_shapefile_zip

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>poly</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
77.59,12.97 77.60,12.97 77.60,12.98 77.59,12.98 77.59,12.97
</coordinates></LinearRing></outerBoundaryIs>
<innerBoundaryIs><LinearRing><coordinates>
77.593,12.973 77.597,12.973 77.597,12.977 77.593,12.977 77.593,12.973
</coordinates></LinearRing></innerBoundaryIs></Polygon></Placemark>
<Placemark><name>line</name><LineString><coordinates>77.59,12.97 77.60,12.98</coordinates></LineString></Placemark>
<Placemark><name>pt</name><Point><coordinates>77.59,12.97</coordinates></Point></Placemark>
<Placemark><name>mixed</name><MultiGeometry>
<Point><coordinates>77.59,12.97</coordinates></Point>
<LineString><coordinates>77.59,12.97 77.60,12.98</coordinates></LineString></MultiGeometry></Placemark>
<Placemark><name>empty</name></Placemark>
</Document></kml>"""


def upload(client, name, data, **form):
    return client.post("/api/files/", files={"file": (name, data)}, data=form)


def test_kml_upload_and_measurements(client):
    r = upload(client, "survey.kml", KML)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "COMPLETED"
    assert body["crs"] == "EPSG:4326"
    assert body["feature_count"] == 5

    info = client.get(f"/api/files/{body['id']}/").json()
    assert info["filename"] == "survey.kml"

    m = client.get(f"/api/files/{body['id']}/measurements/").json()
    res = {x["properties"]["name"]: x for x in m["results"]}

    poly = res["poly"]["measurement"]
    assert poly["status"] == "OK"
    # hole subtracted: 0.01 x 0.01 deg box minus 0.004 x 0.004 deg hole (~1.1km x ~1.1km)
    assert 1.0e6 < poly["area_sq_m"] < 1.4e6
    # equal-area projection agrees with ellipsoidal computation
    assert poly["area_sq_m"] == pytest.approx(poly["geodesic_area_sq_m"], rel=1e-3)

    line = res["line"]["measurement"]
    assert line["projected_crs"] == "EPSG:32643"  # UTM 43N covers Bengaluru
    assert line["length_m"] == pytest.approx(line["geodesic_length_m"], rel=1e-3)
    assert 1500 < line["length_m"] < 1800

    assert res["pt"]["measurement"]["status"] == "NOT_REQUIRED"
    assert res["mixed"]["measurement"]["status"] == "UNSUPPORTED"
    assert res["empty"]["measurement"]["status"] == "UNSUPPORTED"
    assert m["summary"]["measured"] == 2
    assert m["summary"]["unsupported"] == 2


def test_features_endpoint_returns_geometry_and_pagination(client):
    fid = upload(client, "a.kml", KML).json()["id"]
    r = client.get(f"/api/files/{fid}/features/", params={"limit": 2, "offset": 1}).json()
    assert r["total"] == 5 and len(r["results"]) == 2
    assert r["results"][0]["index"] == 1
    assert r["results"][0]["geometry"]["type"] == "LineString"
    assert r["results"][0]["crs"] == "EPSG:4326"


def test_shapefile_geographic_crs(client):
    # clockwise outer ring (shapefile convention)
    ring = [[77.59, 12.97], [77.59, 12.98], [77.60, 12.98], [77.60, 12.97], [77.59, 12.97]]
    data = make_shapefile_zip("poly", CRS.from_epsg(4326), [[ring]])
    r = upload(client, "plots.zip", data)
    assert r.status_code == 201, r.text
    m = client.get(f"/api/files/{r.json()['id']}/measurements/").json()
    item = m["results"][0]
    assert item["properties"]["name"] == "f0"
    assert item["measurement"]["area_sq_m"] == pytest.approx(
        item["measurement"]["geodesic_area_sq_m"], rel=1e-3
    )


def test_shapefile_projected_crs_matches_geographic(client):
    """Same ground polygon expressed in UTM 43N must give ~ the same area."""
    from pyproj import Transformer

    t = Transformer.from_crs(4326, 32643, always_xy=True)
    ring_ll = [(77.59, 12.97), (77.59, 12.98), (77.60, 12.98), (77.60, 12.97), (77.59, 12.97)]
    ring_utm = [list(t.transform(x, y)) for x, y in ring_ll]
    data = make_shapefile_zip("poly", CRS.from_epsg(32643), [[ring_utm]])
    r = upload(client, "utm.zip", data)
    assert r.status_code == 201, r.text
    assert r.json()["crs"] == "EPSG:32643"
    m = client.get(f"/api/files/{r.json()['id']}/measurements/").json()["results"][0]["measurement"]
    assert m["area_sq_m"] == pytest.approx(m["geodesic_area_sq_m"], rel=1e-3)


def test_shapefile_line_and_point(client):
    line = make_shapefile_zip("line", CRS.from_epsg(4326), [[[[77.59, 12.97], [77.60, 12.98]]]])
    r = upload(client, "l.zip", line)
    m = client.get(f"/api/files/{r.json()['id']}/measurements/").json()["results"][0]["measurement"]
    assert m["status"] == "OK" and 1500 < m["length_m"] < 1800

    pt = make_shapefile_zip("point", CRS.from_epsg(4326), [(77.59, 12.97)])
    r = upload(client, "p.zip", pt)
    m = client.get(f"/api/files/{r.json()['id']}/measurements/").json()["results"][0]["measurement"]
    assert m["status"] == "NOT_REQUIRED"


def test_shapefile_without_prj_needs_assume_crs(client):
    ring = [[77.59, 12.97], [77.59, 12.98], [77.60, 12.98], [77.60, 12.97], [77.59, 12.97]]
    data = make_shapefile_zip("poly", None, [[ring]])
    r = upload(client, "noprj.zip", data)
    assert r.status_code == 422
    assert "assume_crs" in r.json()["detail"]
    # failed uploads are still inspectable
    info = client.get(f"/api/files/{r.json()['id']}/").json()
    assert info["status"] == "FAILED"
    assert client.get(f"/api/files/{r.json()['id']}/measurements/").status_code == 409

    ok = upload(client, "noprj.zip", data, assume_crs="EPSG:4326")
    assert ok.status_code == 201


def test_invalid_polygon_is_repaired_not_crashing(client):
    bowtie = """<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Polygon><outerBoundaryIs>
    <LinearRing><coordinates>77.59,12.97 77.60,12.98 77.60,12.97 77.59,12.98 77.59,12.97</coordinates>
    </LinearRing></outerBoundaryIs></Polygon></Placemark></kml>"""
    r = upload(client, "bow.kml", bowtie)
    m = client.get(f"/api/files/{r.json()['id']}/measurements/").json()["results"][0]["measurement"]
    assert m["status"] == "OK" and m["warnings"]


def test_bad_inputs(client):
    assert upload(client, "x.txt", b"hello").status_code == 415
    assert upload(client, "empty.kml", b"").status_code == 400
    assert upload(client, "bad.kml", b"<not xml").status_code == 422
    assert upload(client, "bad.zip", b"not a zip").status_code == 422
    assert upload(client, "a.kml", KML, assume_crs="NOPE:1").status_code == 400
    assert client.get("/api/files/doesnotexist/").status_code == 404
    assert client.get("/api/files/doesnotexist/measurements/").status_code == 404


def test_zip_without_shapefile_and_zip_slip(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hi")
    assert upload(client, "n.zip", buf.getvalue()).status_code == 422

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.shp", b"x")
        zf.writestr("../../evil.dbf", b"x")
    r = upload(client, "evil.zip", buf.getvalue())
    assert r.status_code == 422  # unreadable content, and nothing escaped the temp dir
