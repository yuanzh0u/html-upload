"""V2 HTTP transport and bounded retries."""
import hashlib
import http.client
import json
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from local import KEY_GUIDANCE, Stop

TRANSIENT = frozenset({500, 502, 503, 504})
ADVICE = {
    'invalid_request': '请求格式不合法，请检查参数',
    'summary_required': '请修正摘要', 'summary_too_long': '请修正摘要',
    'file_too_large': '请修正文件', 'not_utf8': '请修正文件',
    'daily_limit_reached': '已达日额度，请次日再试',
    'storage_low': '存储空间不足，请联系管理员',
    'not_found': '文档不存在或无权限',
    'idempotency_conflict': '先确认是否为新内容，确认后才可加 --new',
    'unauthorized': KEY_GUIDANCE,
    'internal_error': '服务端内部错误，请稍后再试',
}


def describe(status, value, headers):
    value = value if isinstance(value, dict) else {}
    code = value.get('error')
    line = ADVICE.get(code, f'服务端返回 HTTP {status}') if isinstance(code, str) else f'服务端返回 HTTP {status}'
    if value.get('request_id'):
        line += f'（请求编号 {value["request_id"]}）'
    if status == 429:
        retry = next((v for k, v in headers.items() if k.lower() == 'retry-after'), '未知')
        line += f'；服务端建议 {retry} 秒后再试；本工具不自动重试'
    if status in TRANSIENT:
        line += '；写操作可能已经生效，请用 get 核对'
    return line


def idempotency_key(raw, nonce=''):
    return hashlib.sha256(b'POST /api/v2/documents\n' + nonce.encode() + b'\n' + raw).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPConnection(http.client.HTTPConnection):
    debuglevel = 0

    def connect(self):
        super().connect()
        self.sock.settimeout(30)


class HTTPSConnection(http.client.HTTPSConnection):
    debuglevel = 0

    def connect(self):
        super().connect()
        self.sock.settimeout(30)


class HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(HTTPConnection, req)


class HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(HTTPSConnection, req, context=ssl.create_default_context())


def http_request(method, url, token, body=None, idem=None):
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if idem is not None:
        headers['Idempotency-Key'] = idem
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), HTTPHandler(), HTTPSHandler())
    try:
        try:
            response = opener.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = response.read(8 * 1024 * 1024 + 1)
            value = None
            if len(data) <= 8 * 1024 * 1024:
                try:
                    value = json.loads(data)
                except (ValueError, UnicodeError, RecursionError):
                    pass
            return response.code, value if isinstance(value, dict) else None, dict(response.headers)
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        raise OSError('网络请求失败') from None


class RemoteUnknown(Exception):
    """A write may have taken effect without a definitive response."""


class Client:
    def __init__(self, origin, token, *, transport=http_request, sleep=time.sleep, rng=random.uniform):
        self.origin, self.token = origin.rstrip('/'), token
        self.transport, self.sleep, self.rng = transport, sleep, rng

    def _pause(self, retry):
        self.sleep(self.rng(0, min(30, 2 ** retry)))

    def me(self):
        value = self.get('/api/v2/me')
        if str(value.get('api_version', '')).split('.')[0] != '2':
            raise Stop('契约主版本不兼容')
        return value

    def get(self, path, query=None):
        url = self.origin + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        for attempt in range(4):
            try:
                status, value, headers = self.transport('GET', url, self.token)
            except OSError:
                status = None
            else:
                if status not in TRANSIENT:
                    if 200 <= status < 300 and isinstance(value, dict):
                        return value
                    raise Stop(describe(status, value, headers))
            if attempt < 3:
                self._pause(attempt + 1)
        raise Stop('服务暂时不可用，请稍后再试')

    def send(self, method, path, body=None):
        raw = None if body is None else json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()
        try:
            return self.transport(method, self.origin + path, self.token, raw)
        except OSError:
            raise RemoteUnknown('写操作可能已经生效，请用 get 核对') from None

    def send_idempotent(self, method, path, raw, idem):
        for attempt in range(6):
            try:
                result = self.transport(method, self.origin + path, self.token, raw, idem)
            except OSError:
                pass
            else:
                if result[0] not in TRANSIENT:
                    return result
            if attempt < 5:
                self._pause(attempt + 1)
        raise RemoteUnknown('写操作结果未知，请用 resume 恢复')
