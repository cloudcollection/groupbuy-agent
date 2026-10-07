"""Business projection is the primary boundary; free text is sanitized as well."""
import json
import re

PRIVATE_KEYS = {'cookie', 'setcookie', 'authorization', 'token', 'accesstoken',
                'refreshtoken', 'password', 'openid', 'unionid', 'userid', 'uid',
                'deviceid', 'sessionid', 'phone', 'mobile', 'phoneno', 'mylat',
                'mylng', 'userlatitude', 'userlongitude', 'payinfo', 'orderid',
                'redemptioncode', 'voucherpassword', 'certificate', 'headers'}
PATTERNS = [
    re.compile(r'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b'),
    re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'),
    re.compile(r'(?<!\d)\d{17}[\dXx](?!\w)'),
    re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b'),
    re.compile(r'(?i)(?:bearer\s+)[A-Za-z0-9._~+/=-]+'),
    re.compile(r'(?i)(?:cookie|token|authorization|password|openid|unionid|核销码|验证码)\s*[:=：]\s*[^\s,;，；]+'),
    re.compile(r'(?i)[A-Z]:[\\/](?:Users|Desktop|xwechat_files)[^\s"<>]*'),
    re.compile(r'https?://[^\s<>"\u4e00-\u9fff]+'),
]


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()
                if re.sub('[^a-z0-9]', '', str(k).lower()) not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        # Embedded JSON is sanitized rather than treated as an opaque string.
        if value.strip().startswith(('{', '[')):
            try:
                return json.dumps(clean(json.loads(value)), ensure_ascii=False)
            except ValueError:
                pass
        for pattern in PATTERNS:
            value = pattern.sub('[REDACTED]', value)
        return value
    return value


def safe(value):
    return clean(value) == value


def display(value):
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    return clean(str(value).strip()) or None
