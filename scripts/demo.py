"""Start the isolated synthetic demo without loading .env or an API key."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
for key in list(os.environ):
    if key.startswith(('APP_', 'LLM_')):
        del os.environ[key]
os.environ.update(APP_MODE='test', APP_DATA_MODE='synthetic', APP_DB_PATH='runtime/demo.sqlite')
from backend.server import main

if __name__ == '__main__':
    if sys.version_info < (3, 12):
        raise SystemExit('Python 3.12+ required; tested with 3.12.14')
    main()
