"""Optional macOS TLS setup; copies Mozilla CA bundle, never disables verification."""
import hashlib
from pathlib import Path
import shutil

try:
    import certifi
except ImportError:
    raise SystemExit('Install optional TLS dependency: python -m pip install -r requirements-tls.txt')

root = Path(__file__).resolve().parents[1]
target = root / 'runtime/cacert.pem'
target.parent.mkdir(exist_ok=True)
shutil.copyfile(certifi.where(), target)
print('CA bundle saved:', target)
print('SHA-256:', hashlib.sha256(target.read_bytes()).hexdigest())
print('Add to .env: SSL_CERT_FILE=runtime/cacert.pem')
