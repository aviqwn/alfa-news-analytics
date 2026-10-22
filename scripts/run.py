"""Single entrypoint, no shell sourcing of .env, no third party dependencies."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip().replace("_", "").isalnum():
            raise SystemExit("Invalid .env line; expected KEY=value")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


if __name__ == "__main__":
    if sys.version_info < (3, 12):
        raise SystemExit("Python 3.12+ required; tested with 3.12.14")
    load_env(ROOT / ".env")
    from backend.server import main
    if len(sys.argv) == 1:
        sys.argv.extend(["--host", os.getenv("APP_HOST", "127.0.0.1"), "--port", os.getenv("APP_PORT", "8000")])
    main()
