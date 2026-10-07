"""Runtime configuration, overridable through environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'geo.db'}")

# Upload guards (bytes / counts)
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
MAX_UNCOMPRESSED_BYTES = int(os.getenv("MAX_UNCOMPRESSED_BYTES", 200 * 1024 * 1024))
MAX_FEATURES = int(os.getenv("MAX_FEATURES", 100_000))

ALLOWED_EXTENSIONS = {".kml", ".zip"}
