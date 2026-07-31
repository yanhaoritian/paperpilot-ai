import os
import sys
from pathlib import Path

# Allow `pytest` from backend/ with `app` package importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Configure isolation before pytest imports any test module that may import
# app.db. Module-local environment changes are too late once the engine exists.
if os.getenv("POSTGRES_GATE_TEST") != "1":
    TEST_DB = ROOT / "_pytest_tmp.db"
    if TEST_DB.exists():
        TEST_DB.unlink()
    os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"

os.environ["JWT_SECRET"] = "test-secret-at-least-24-chars-xx"
os.environ["JWT_REQUIRE_STRONG"] = "0"
os.environ["OPENAI_API_KEY"] = ""
os.environ["EMBEDDING_PROVIDER"] = "local"
os.environ["AUTH_EXPOSE_CODE"] = "1"
os.environ["INDEX_RECOVER_ON_STARTUP"] = "0"
os.environ["INDEX_EXTERNAL_WORKER"] = "0"
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["SMTP_FROM"] = ""
os.environ["PDF_STORAGE_DIR"] = str(ROOT / "tests" / "_test_pdfs")
