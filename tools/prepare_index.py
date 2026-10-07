"""Stage only the reviewed allowlist; no push, commit or broad git-add."""
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
files = json.loads((root / 'publication-files.json').read_text(encoding='utf-8'))
subprocess.run(['git', '-C', str(root), 'add', '--', *files], check=True)
subprocess.run(['git', '-C', str(root), 'diff', '--cached', '--check'], check=True)
