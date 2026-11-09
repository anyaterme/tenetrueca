"""Freeze the local migration artifacts; never generates/replaces a migration."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def migrations():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(ROOT.glob('*/migrations/*.py'))}


if __name__ == '__main__':
    print(json.dumps(migrations(), indent=2, sort_keys=True))
