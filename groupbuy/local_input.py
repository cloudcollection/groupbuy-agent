"""Read exported files only. No network client, cache discovery or request replay."""
import base64
import gzip
import hashlib
import json
import zlib
from urllib.parse import parse_qsl, urlsplit
from .common import GateError, load_json, moment

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_BODY_BYTES = 16 * 1024 * 1024
REQUEST_KEYS = {'keyword', 'query', 'searchkeyword', 'start', 'startindex',
                'category', 'rangetext', 'cityname'}


def decode(raw, depth=0):
    if depth > 5:
        raise GateError('encoding_depth_exceeded')
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    if len(raw) > MAX_BODY_BYTES:
        raise GateError('response_too_large')
    if raw.startswith(b'\x1f\x8b'):
        # Stream through a bounded read; never expand an unbounded archive.
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:
            return decode(f.read(MAX_BODY_BYTES + 1), depth + 1)
    try:
        value = json.loads(raw.decode('utf-8-sig'))
        return decode(value, depth + 1) if isinstance(value, str) else value
    except (UnicodeError, ValueError):
        for decoder in (lambda b: zlib.decompressobj().decompress(b, MAX_BODY_BYTES + 1),
                        lambda b: base64.b64decode(b, validate=True)):
            try:
                return decode(decoder(raw), depth + 1)
            except (ValueError, zlib.error, GateError):
                pass
        raise GateError('invalid_response_encoding') from None


def params(request):
    pairs = parse_qsl(urlsplit(request.get('url', '')).query)
    post = request.get('postData', {})
    pairs += [(p.get('name'), p.get('value')) for p in post.get('params', [])]
    try:
        value = json.loads(post.get('text', '{}'))
        if isinstance(value, dict):
            pairs += list(value.items())
    except ValueError:
        pairs += parse_qsl(post.get('text', ''))
    for k, v in list(pairs):
        if k in {'data', 'params', 'param'} and isinstance(v, str):
            try:
                value = json.loads(v)
                if isinstance(value, dict):
                    pairs += list(value.items())
            except ValueError:
                pass
    return {str(k).lower(): v for k, v in pairs if str(k).lower() in REQUEST_KEYS
            and isinstance(v, (str, int, float)) and not isinstance(v, bool)}


def read_responses(path, kind, start, end):
    stat = path.stat()
    if stat.st_size > MAX_FILE_BYTES:
        raise GateError('input_too_large')
    raw = path.read_bytes()
    current = path.stat()
    if current.st_size != stat.st_size or current.st_mtime_ns != stat.st_mtime_ns:
        raise GateError('input_changed_during_read')
    source_hash = hashlib.sha256(raw).hexdigest()
    try:
        doc = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        raise GateError('invalid_local_json') from None
    after, before = moment(start), moment(end)
    if after > before:
        raise GateError('invalid_capture_window')
    if kind == 'har':
        entries = doc.get('log', {}).get('entries', [])
        for i, entry in enumerate(entries):
            ts = moment(entry.get('startedDateTime'))
            if not after <= ts <= before:
                continue
            request = entry.get('request', {})
            url = urlsplit(request.get('url', ''))
            host = (url.hostname or '').lower()
            if not any(host == h or host.endswith('.' + h) for h in ('dianping.com', 'meituan.com')):
                continue
            if 'search' not in url.path.lower():
                continue
            response = entry.get('response', {})
            if response.get('status') != 200:
                raise GateError('capture_http_failure')
            content = response.get('content', {})
            body = content.get('text', '')
            if content.get('encoding') == 'base64':
                try:
                    body = base64.b64decode(body, validate=True)
                except ValueError:
                    raise GateError('invalid_response_encoding') from None
            yield {'captured_at': entry['startedDateTime'], 'request': params(request),
                   'response': decode(body), 'source_hash': source_hash, 'entry_index': i}
    else:
        # Documented envelope for JSON exports/cache conversions, with metadata.
        # Bare response JSON cannot prove capture window or keyword and is refused.
        if doc.get('schema') != 'groupbuy.local-responses.v1':
            raise GateError('missing_response_metadata')
        for i, entry in enumerate(doc.get('entries', [])):
            if after <= moment(entry.get('captured_at')) <= before:
                yield {'captured_at': entry['captured_at'],
                       'request': {str(k).lower(): v for k, v in entry.get('request', {}).items()
                                   if str(k).lower() in REQUEST_KEYS},
                       'response': entry.get('response'), 'round_id': entry.get('round_id'),
                       'source_hash': source_hash, 'entry_index': i}
