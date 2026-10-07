"""Check all working files, index blobs and every reachable Git commit."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from groupbuy.common import GateError, atomic_json

SECRET_PATTERNS = {
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'credential_assignment': re.compile(r'(?i)(?:authorization|cookie|access[_-]?token|password)\s*[=:]\s*[\"\x27]?(?:Bearer\s+)?[A-Za-z0-9_/+=.-]{16,}'),
    'service_secret': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b'),
    'personal_email': re.compile(r'(?i)\b[A-Z0-9._%+-]+@(?!example\.invalid\b)[A-Z0-9.-]+\.[A-Z]{2,}\b'),
    'phone': re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'),
    'personal_directory': re.compile(r'(?i)[A-Z]:[\\/](?:Users[\\/][^\s/\\]+|xwechat_files[\\/])'),
    'legacy_terms': re.compile('医' + '院|院' + '区|hos' + 'pital|cam' + 'pus|CN' + 'TEAM-', re.I),
}
FORBIDDEN_SUFFIXES = {'.har', '.pem', '.pfx', '.p12', '.key', '.reqable', '.log', '.jsonl', '.zip', '.xlsx', '.png'}


def phone_scan_text(text):
    """Do not interpret numeric runs inside structured SHA-256 values as phones."""
    try:
        value = json.loads(text)
    except ValueError:
        return text
    hash_fields = {'task_hash', 'result_digest', 'manifest_sha256', 'allowlist_sha256',
                   'source_sha256', 'source_hash', 'filter_frame_hash', 'final_frame_hash', 'list_hash',
                   'sha256', 'evidence_sha256'}
    artifact_names = {'result.json', 'compact.json', 'shops.csv', 'products.csv'}

    def scrub(item, parent=None):
        if isinstance(item, dict):
            return {k: ('[integrity-hash]' if isinstance(v, str) and re.fullmatch(r'[a-f0-9]{64}', v)
                        and (k in hash_fields or parent == 'files' and k in artifact_names)
                        else scrub(v, k)) for k, v in item.items()}
        if isinstance(item, list):
            return [scrub(v, parent) for v in item]
        return item
    return json.dumps(scrub(value), ensure_ascii=False)


def git(*args):
    p = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True)
    if p.returncode:
        raise GateError('git_scan_failed')
    return p.stdout


def local_projected_har(path, data):
    """Allow only the receiver's public-field snapshot in local runtime, never Git."""
    if not path.startswith('runtime/') or '/capture/' not in path or not path.endswith('/input.har'):
        return False
    try:
        from groupbuy.adapters.dianping import DianpingAdapter
        from groupbuy.local_input import params
        doc = json.loads(data)
        if set(doc) != {'log', '_groupbuy_projected'} or doc['_groupbuy_projected'] is not True:
            return False
        log = doc['log']
        if set(log) != {'version', 'creator', 'entries'} or log['version'] != '1.2':
            return False
        if not isinstance(log['entries'], list) or not log['entries']:
            return False
        for entry in log['entries']:
            query = params(entry['request'])
            keyword = query.get('keyword', query.get('searchkeyword', query.get('query')))
            if not keyword or DianpingAdapter().capture_entry({'search_term': keyword}, entry) != entry:
                return False
        return True
    except (KeyError, TypeError, ValueError, GateError):
        return False


def findings_for(path, data, published, allowed):
    findings = []
    if published and path not in allowed:
        findings.append('outside_publication_allowlist')
    local_status_log = not published and path.startswith('runtime/') and Path(path).suffix.lower() in {'.jsonl', '.log'}
    projected = not published and local_projected_har(path, data) if Path(path).suffix.lower() == '.har' else False
    if Path(path).suffix.lower() in FORBIDDEN_SUFFIXES and not local_status_log and not projected:
        findings.append('forbidden_file_type')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError:
        return findings + ['unreviewed_binary']
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(phone_scan_text(text) if label == 'phone' else text):
            findings.append(label)
    return findings


def scan():
    allowed = set(json.loads((ROOT / 'publication-files.json').read_text(encoding='utf-8')))
    issues, counts = [], {'working_files': 0, 'staged_files': 0, 'history_commits': 0, 'history_blobs': 0}
    excluded = 0
    for path in ROOT.rglob('*'):
        if '.git' in path.relative_to(ROOT).parts or not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        # Local dependency installations are outside the publication boundary.
        if relative.startswith(('.venv/', '__pycache__/')) or '/__pycache__/' in relative:
            excluded += 1
            continue
        counts['working_files'] += 1
        published = relative in allowed
        if not published:
            excluded += 1
        labels = findings_for(relative, path.read_bytes(), False, allowed)
        if labels:
            issues.append({'surface': 'working', 'path': relative, 'codes': labels})
    missing = sorted(p for p in allowed if not (ROOT / p).is_file())
    if missing:
        issues.append({'surface': 'working', 'code': 'missing_allowlisted_files', 'count': len(missing)})
    if not (ROOT / '.git').exists():
        issues.append({'surface': 'git', 'code': 'repository_not_initialized'})
    else:
        for line in git('ls-files', '--stage', '-z').split(b'\0'):
            if not line:
                continue
            meta, filename = line.split(b'\t', 1)
            mode, blob, stage = meta.split()
            path = filename.decode('utf-8')
            labels = findings_for(path, git('cat-file', 'blob', blob.decode()), True, allowed)
            if mode not in {b'100644', b'100755'} or stage != b'0':
                labels.append('unsupported_index_mode')
            counts['staged_files'] += 1
            if labels:
                issues.append({'surface': 'index', 'path': path, 'codes': labels})
        commits = git('rev-list', '--all').decode().splitlines()
        counts['history_commits'] = len(commits)
        seen = set()
        for commit in commits:
            # Commit messages, including author email/name, are intentionally kept out of reports.
            commit_data = git('cat-file', 'commit', commit)
            labels = findings_for('commit', commit_data, False, allowed)
            if labels:
                issues.append({'surface': 'history_metadata', 'commit': commit, 'codes': labels})
            for line in git('ls-tree', '-r', '-z', commit).split(b'\0'):
                if not line:
                    continue
                meta, filename = line.split(b'\t', 1)
                mode, kind, blob = meta.split()
                path = filename.decode('utf-8')
                key = (path, blob)
                if key in seen:
                    continue
                seen.add(key)
                labels = findings_for(path, git('cat-file', 'blob', blob.decode()), True, allowed)
                if mode not in {b'100644', b'100755'} or kind != b'blob':
                    labels.append('unsupported_history_mode')
                counts['history_blobs'] += 1
                if labels:
                    issues.append({'surface': 'history', 'path': path, 'commit': commit, 'codes': labels})
    # Reports list paths/codes only, never matched credential text or source content.
    return {'status': 'PASS' if not issues else 'FAIL', 'counts': counts,
            'excluded_working_files': excluded, 'issues': issues,
            'allowlist_sha256': hashlib.sha256((ROOT / 'publication-files.json').read_bytes()).hexdigest()}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--report')
    args = p.parse_args()
    try:
        result = scan()
        if args.report:
            atomic_json(Path(args.report).resolve(), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result['status'] == 'PASS' else 1)
    except GateError as error:
        print(json.dumps({'status': 'FAIL', 'code': error.code}))
        raise SystemExit(1)
