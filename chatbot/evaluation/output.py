"""Optional private JSON exports; the PostgreSQL ledger remains the budget authority."""
import json
import os
from pathlib import Path
import tempfile


def atomic_json(path, payload):
    target = Path(path)
    # Same directory replace + fsync; do not follow a destination symlink.
    if target.is_symlink():
        raise ValueError('output_symlink')
    fd, temporary = tempfile.mkstemp(prefix='.chatbot-eval-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
