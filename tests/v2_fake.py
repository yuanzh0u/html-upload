"""In-memory V2 contract server; never uses network or real credentials."""
import base64
import binascii
import copy
import datetime as dt
import hashlib
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit


class FakeDocs:
    def __init__(self, *, key='k' * 24, user='甲', origin='http://127.0.0.1:9',
                 api_version='2.0', friends=('乙',), groups=('测试组',), daily_limit=100, retention_days=30):
        self.key, self.user, self.origin, self.api_version = key, user, origin, api_version
        self.friends, self.groups = friends, groups
        self.daily_limit, self.retention_days = daily_limit, retention_days
        self.calls, self.docs, self.fail_next, self.fail_reads = [], {}, [], []
        self.echo_token = self.broken_cursor = False
        self._idem, self._used, self._day, self._sequence = {}, 0, dt.datetime.now(dt.timezone.utc).date(), 0

    @staticmethod
    def upload_raw(**fields):
        html = b'<!doctype html><title>test</title><p>test</p>'
        value = dict(title='测试文档', summary='测试摘要', tags=[], source_info={},
                     html_base64=base64.b64encode(html).decode(), sha256=hashlib.sha256(html).hexdigest())
        value.update(fields)
        return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()

    def _error(self, status, code, token, headers=None):
        return status, {'error': code, 'message': '测试错误' + (token if self.echo_token else ''),
                        'request_id': 'fake-request'}, headers or {}

    def _stamp(self):
        self._sequence += 1
        return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=self._sequence)).isoformat()

    def _expiry(self):
        return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=self.retention_days)).isoformat()

    def _new(self, data, size):
        ident, stamp = secrets.token_hex(16), self._stamp()
        doc = dict(id=ident, url=self.origin + '/d/' + ident, title=data['title'], summary=data['summary'],
                   tags=data.get('tags', []), source_info=data.get('source_info', {}), size_bytes=size,
                   uploaded_at=stamp, content_updated_at=stamp, favorite=False, expires_at=self._expiry())
        self.docs[ident] = doc
        return doc

    def seed(self, n, *, title='文档', tags=()):
        for i in range(n):
            self._new(dict(title=f'{title}{i:04d}', summary='测试摘要', tags=list(tags)), 1)

    def _share(self, share):
        report = {'shared': [], 'failed': []}
        for plural, kind, allowed, reason in [('friends', 'friend', self.friends, 'not_friend'),
                                              ('groups', 'group', self.groups, 'group_not_found')]:
            for name in dict.fromkeys(n.strip() for n in share.get(plural, [])):
                item = {'type': kind, 'name': name}
                if name in allowed:
                    report['shared'].append(item)
                else:
                    report['failed'].append(dict(item, reason=reason))
        return report

    def transport(self, method, url, token, body=None, idem=None):
        parts = urlsplit(url)
        path, query = parts.path, parse_qs(parts.query)
        self.calls.append(dict(method=method, path=path, query=query, idem=idem, body=body))
        if token != self.key:
            return self._error(401, 'unauthorized', token)
        failures = self.fail_reads if method == 'GET' else self.fail_next if method in {'POST', 'PUT', 'PATCH', 'DELETE'} else []
        fault = failures.pop(0) if failures else None
        if fault == 'crash':
            raise RuntimeError('boom ' + token)
        if fault == 'network':
            raise OSError('network ' + (token if self.echo_token else ''))
        if isinstance(fault, tuple):
            return self._error(fault[0], fault[1], token, fault[2] if len(fault) > 2 else None)
        result = self._handle(method, path, query, token, body, idem)
        if fault == 'lost':
            raise OSError('lost ' + (token if self.echo_token else ''))
        return copy.deepcopy(result)

    def _handle(self, method, path, query, token, body, idem):
        error = lambda status=400, code='invalid_request', headers=None: self._error(status, code, token, headers)
        match = re.fullmatch(r'/api/v2/documents/([A-Za-z0-9_-]+)(/(content|favorite))?', path)
        root = path == '/api/v2/documents'
        valid = ((path in {'/api/v2/me', '/api/v2/tags'} and method == 'GET') or
                 (root and method in {'POST', 'GET'}) or
                 (match and ((not match[2] and method in {'GET', 'PATCH', 'DELETE'}) or
                             (match[3] == 'content' and method == 'PUT') or
                             (match[3] == 'favorite' and method in {'PUT', 'DELETE'}))))
        if not valid:
            return error(404, 'not_found')
        if body is not None and (method in {'GET', 'DELETE'} or (match and match[3] == 'favorite')):
            return error()
        today = dt.datetime.now(dt.timezone.utc).date()
        if today != self._day:
            self._day, self._used = today, 0
        if path == '/api/v2/me':
            return 200, dict(user_name=self.user, api_version=self.api_version, daily_upload_limit=self.daily_limit,
                             daily_upload_used=self._used, retention_days=self.retention_days), {}
        if path == '/api/v2/tags':
            counts = {}
            for doc in self.docs.values():
                for tag in doc['tags']:
                    counts[tag] = counts.get(tag, 0) + 1
            return 200, {'items': [{'tag': tag, 'count': counts[tag]} for tag in sorted(counts)]}, {}
        if root and method == 'GET':
            return self._list(query, error)
        writing_content = (root and method == 'POST') or (match and match[3] == 'content')
        if writing_content:
            if not isinstance(idem, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', idem):
                return error()
            previous = self._idem.get((self.user, idem))
            if previous and time.time() - previous[0] < 86400:
                _, old_body, response = previous
                if old_body != body:
                    return error(409, 'idempotency_conflict')
                if response[1]['id'] not in self.docs:
                    return error(404, 'not_found')
                return 200, copy.deepcopy(response[1]), copy.deepcopy(response[2])
        if match:
            doc = self.docs.get(match[1])
            if doc is None:
                return error(404, 'not_found')
            if method == 'GET':
                return 200, doc, {}
            if match[3] == 'favorite':
                doc.update(favorite=method == 'PUT', expires_at=None if method == 'PUT' else self._expiry())
                return 200, doc, {}
            if method == 'DELETE':
                del self.docs[match[1]]
                return 204, None, {}
        if body is None:
            return error()
        if len(body) > 2_861_740:
            return error(413, 'file_too_large')
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            return error()
        if not isinstance(data, dict):
            return error()
        allowed = {'title', 'summary', 'tags'}
        if writing_content:
            allowed = {'summary', 'html_base64', 'sha256', 'share_with'}
            if root:
                allowed |= {'title', 'tags', 'source_info'}
        if data.keys() - allowed or not data:
            return error()
        if writing_content or 'summary' in data:
            summary = data.get('summary')
            if summary is None or isinstance(summary, str) and not summary.strip():
                return error(400, 'summary_required')
            if not isinstance(summary, str):
                return error()
            if len(summary) > 1000:
                return error(400, 'summary_too_long')
        if root and 'title' not in data:
            return error()
        if 'title' in data and (not isinstance(data['title'], str) or not 1 <= len(data['title'].strip()) <= 200):
            return error()
        if 'tags' in data:
            tags = data['tags']
            if not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(t, str) or not 1 <= len(t.strip()) <= 40 for t in tags):
                return error()
            data['tags'] = list(dict.fromkeys(t.strip() for t in tags))
        if 'source_info' in data and (not isinstance(data['source_info'], dict) or len(json.dumps(data['source_info'], ensure_ascii=False).encode()) > 4096):
            return error()
        share = data.get('share_with', {})
        if not isinstance(share, dict) or share.keys() - {'friends', 'groups'}:
            return error()
        for names in share.values():
            if not isinstance(names, list) or len(names) > 50 or any(not isinstance(n, str) or not n.strip() for n in names):
                return error()
        if writing_content:
            try:
                html = base64.b64decode(data['html_base64'], validate=True)
            except (KeyError, ValueError, TypeError, binascii.Error):
                return error()
            if len(html) > 2_097_152:
                return error(413, 'file_too_large')
            try:
                html.decode('utf-8')
            except UnicodeError:
                return error(415, 'not_utf8')
            if data.get('sha256') != hashlib.sha256(html).hexdigest():
                return error()
            if self._used >= self.daily_limit:
                return error(429, 'daily_limit_reached', {'Retry-After': '57600'})
            if root:
                doc = self._new(data, len(html))
            else:
                doc.update(summary=data['summary'], size_bytes=len(html), content_updated_at=self._stamp())
            self._used += 1
            result = (201 if root else 200), dict(copy.deepcopy(doc), share_report=self._share(share)), {}
            self._idem[(self.user, idem)] = (time.time(), body, copy.deepcopy(result))
            return result
        doc.update(data)
        return 200, doc, {}

    def _list(self, query, error):
        try:
            limit = int(query.get('limit', ['50'])[0])
            cursor = query.get('cursor', ['0'])[0]
            offset = limit if cursor == 'fixed' else int(cursor)
            if not 1 <= limit <= 200 or offset < 0:
                return error()
            items = list(self.docs.values())
            for name, compare in [('uploaded_from', lambda a, b: a >= b), ('uploaded_to', lambda a, b: a < b)]:
                if name in query:
                    bound = dt.datetime.fromisoformat(query[name][0].replace('Z', '+00:00'))
                    if bound.tzinfo is None:
                        return error()
                    items = [d for d in items if compare(dt.datetime.fromisoformat(d['uploaded_at']), bound)]
            q = query.get('q', [''])[0].casefold()
            items = [d for d in items if (q in d['title'].casefold() or q in d['summary'].casefold()) and
                     all(t in d['tags'] for t in query.get('tag', []))]
            sort = query.get('sort', ['uploaded_desc'])[0]
            if sort not in {'uploaded_desc', 'uploaded_asc', 'title'}:
                return error()
            items.sort(key=lambda d: d['title'] if sort == 'title' else d['uploaded_at'], reverse=sort == 'uploaded_desc')
            next_cursor = ('fixed' if self.broken_cursor else str(offset + limit)) if offset + limit < len(items) else None
            return 200, {'items': items[offset:offset + limit], 'next_cursor': next_cursor}, {}
        except (ValueError, TypeError, OverflowError):
            return error()
