"""Safe paths, deterministic identities and atomic storage."""
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class GateError(Exception):
    def __init__(self, code):
        if not re.fullmatch(r'[a-z_]+', code):
            code = 'operation_failed'
        self.code = code
        super().__init__(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def moment(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError
        return dt
    except (ValueError, TypeError, AttributeError):
        raise GateError('invalid_timestamp') from None


def path_from(root, value, *, write=False):
    expanded = os.path.expandvars(str(value))
    if '$' in expanded or re.search(r'%[^%]+%', expanded):
        raise GateError('unresolved_path_variable')
    p = Path(expanded)
    p = (p if p.is_absolute() else Path(root) / p).resolve()
    if write and os.name == 'nt' and p.drive.upper() != 'D:':
        raise GateError('output_requires_d_drive')
    return p


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (ValueError, OSError):
        raise GateError('invalid_local_json') from None


def atomic_json(path, value):
    path = path_from('.', path, write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path_from('.', os.environ.get('GB_TEMP_DIR', str(path.parent)), write=True)
    temp.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='gb-', suffix='.json', dir=temp)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class FileLock:
    """Exclusive process lock; crashes leave a lock requiring human inspection."""
    def __init__(self, path):
        self.path = path_from('.', path, write=True)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise GateError('workspace_locked') from None
        os.write(self.fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *args):
        os.close(self.fd)
        self.path.unlink()
