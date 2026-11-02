#!/usr/bin/env python3
"""Read-only HTML, metadata and external-reference inspection; no network."""
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
from normalize import normalize

MAX_HTML_BYTES = 10 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
RFC3339 = re.compile(r'\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)')


def utc_time(value):
    if not isinstance(value, str) or not RFC3339.fullmatch(value):
        raise ValueError('生成时间需要带时区的 RFC3339')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00').replace('z', '+00:00'))
    converted = parsed.astimezone(timezone.utc)
    if not 1970 <= parsed.year <= 2100 or not 1970 <= converted.year <= 2100:
        raise ValueError('生成时间超出现行契约 1970–2100')
    fraction = RFC3339.fullmatch(value).group(1) or ''
    return converted.strftime('%Y-%m-%dT%H:%M:%S') + fraction + 'Z'


def same_instant(a, b):
    # Retain all source fractional digits, including sub-microsecond precision.
    def key(value):
        value = utc_time(value)
        whole, _, fraction = value[:-1].partition('.')
        return whole, fraction.rstrip('0')
    return key(a) == key(b)


def valid_title(value):
    return (isinstance(value, str) and 1 <= len(value) <= 200 and value == value.strip()
            and not any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value))


def read_html(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.suffix.lower() != '.html':
        raise ValueError('需要非符号链接的本地普通 .html 文件')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('源文件在打开前发生变化')
        data = stream.read(MAX_HTML_BYTES + 1)
    return data


def external_notice(found):
    template = (ROOT / 'templates/confirm-prompt.txt').read_text()
    marker = '有命中时逐字使用：\n' if found else '无命中时逐字使用：\n'
    return template.split(marker, 1)[1].split('\n\n', 1)[0].strip()


class Inspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.templates = 0
        self.script = False
        self.style = False
        self.title = None
        self.titles = []
        self.meta = {'plans:completed': [], 'plans:worker': []}
        self.charsets = []
        self.urls = []

    def url(self, value):
        if any(unicodedata.category(c) in ('Cc', 'Cf') for c in value):
            return
        value = value.strip().strip('"\'').strip()
        if value.lower().startswith(('http:', 'https:', '//')) and value not in self.urls:
            self.urls.append(value)

    def css(self, text):
        for m in re.finditer(r'''url\(\s*("[^"]*"|'[^']*'|[^)]*)\s*\)|@import\s+("[^"]*"|'[^']*')''', text, re.I):
            self.url(m.group(1) or m.group(2))

    def handle_starttag(self, tag, attrs):
        if tag == 'template':
            self.templates += 1
            return
        if self.templates or self.script:
            return
        for key, value in attrs:
            if value is None:
                continue
            if key in ('src', 'href', 'xlink:href', 'poster', 'data', 'action', 'formaction'):
                self.url(value)
            elif key == 'srcset':
                for candidate in value.split(','):
                    parts = candidate.strip().split()
                    if parts:
                        self.url(parts[0])
            elif key == 'style':
                self.css(value)
        if tag == 'script':
            self.script = True
        elif tag == 'style':
            self.style = True
        elif tag == 'title':
            self.title = []
        elif tag == 'meta':
            self.charsets.extend(value or '' for key, value in attrs if key == 'charset')
            # Browsers keep the first occurrence of duplicate HTML attributes.
            charset_attrs = dict(reversed(attrs))
            if (charset_attrs.get('http-equiv') or '').strip().lower() == 'content-type':
                for m in re.finditer(r'''(?:^|;)\s*charset\s*=\s*(?:"([^"]*)"|'([^']*)'|([^;\s]*))''', charset_attrs.get('content') or '', re.I):
                    self.charsets.append(next(value for value in m.groups() if value is not None))
            attrs = dict(attrs)
            name = (attrs.get('name') or '').lower()
            if name in self.meta:
                self.meta[name].append(attrs.get('content') if attrs.get('content') is not None else '')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == 'template' and self.templates:
            self.templates -= 1
            return
        if self.templates:
            return
        if tag == 'script':
            self.script = False
        elif self.script:
            return
        elif tag == 'style':
            self.style = False
        elif tag == 'title' and self.title is not None:
            self.titles.append(''.join(self.title))
            self.title = None

    def handle_data(self, data):
        if self.templates or self.script:
            return
        if self.title is not None:
            self.title.append(data)
        if self.style:
            self.css(data)


def inspect_html(data, *, title=None, generated_at=None, model_name=None, model_id=None):
    if not 1 <= len(data) <= MAX_HTML_BYTES:
        raise ValueError('解码后 HTML 上限 10 MiB，且文件不能为空')
    try:
        text = data.decode('utf-8', errors='strict')
    except UnicodeError:
        raise ValueError('HTML 不是严格 UTF-8') from None
    lower = data.lower()
    if (data.startswith(b'\xef\xbb\xbf') or b'\0' in data
            or not lower.lstrip(b' \t\r\n').startswith((b'<!doctype html', b'<html')) or b'</html>' not in lower):
        raise ValueError('HTML 字节条件不合规：BOM、NUL、起始或结束标记')
    parser = Inspector()
    parser.feed(text)
    parser.close()
    if any(value.strip().lower() not in ('utf-8', 'utf8') for value in parser.charsets):
        raise ValueError('HTML charset 声明与严格 UTF-8 字节要求冲突')
    issues = []
    def issue(field, reason, values):
        issues.append(dict(field=field, kind=field, reason=reason, values=values))
    titles = list(dict.fromkeys(parser.titles))
    chosen_title = title if title is not None else (titles[0] if len(titles) == 1 else None)
    if len(titles) > 1 or (title is not None and titles and any(x != title for x in titles)):
        issue('title', '标题冲突，请选定标题', titles + ([title] if title is not None else []))
    if not valid_title(chosen_title):
        issue('title', '缺少合法标题，不能用文件名代替', titles)
    times = list(dict.fromkeys(parser.meta['plans:completed']))
    workers = list(dict.fromkeys(parser.meta['plans:worker']))
    if len(times) > 1:
        issue('generated_at', '多条生成时间 content 不完全相同', times)
    raw_time = generated_at if generated_at is not None else (times[0] if len(times) == 1 else None)
    converted = None
    try:
        converted = utc_time(raw_time)
        for meta in times:
            if not same_instant(meta, raw_time):
                issue('generated_at', '参数与 meta 的 UTC 时刻冲突', [raw_time, meta, converted, utc_time(meta)])
    except (ValueError, TypeError, OverflowError):
        issue('generated_at', '时间缺失、缺时区或无法解析；请提供时间和时区', times + ([generated_at] if generated_at else []))
    worker = workers[0] if len(workers) == 1 else None
    if len(workers) > 1:
        issue('model', '多条 worker content 不完全相同', workers)
    if worker is not None:
        try:
            _, worker_key = normalize(worker)
            if model_name is not None and normalize(model_name)[1] != worker_key:
                issue('model', '参数与 worker 的规范化键冲突', [model_name, worker])
        except ValueError:
            issue('model', 'worker 无法规范化', workers)
    if worker is None and model_name is None and model_id is None:
        issue('model', '缺模型；不能猜测模型', [])
    external = dict(urls=parser.urls[:20], total=len(parser.urls), notice=external_notice(bool(parser.urls)))
    return dict(status='needs_clarification' if issues else 'ready', issues=issues,
                title=chosen_title, title_source='explicit' if title is not None else 'html_title',
                generated_at=converted, generated_at_raw=raw_time,
                time_source='html_meta' if times else 'explicit', worker=worker,
                model_name=model_name if model_name is not None else worker,
                model_id=model_id, model_source='html_meta' if worker is not None else 'explicit',
                external_links=external)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html')
    parser.add_argument('--title')
    parser.add_argument('--generated-at')
    parser.add_argument('--model-name')
    parser.add_argument('--model-id')
    args = parser.parse_args()
    try:
        values = vars(args).copy()
        path = values.pop('html')
        result = inspect_html(read_html(path), **values)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 3 if result['issues'] else 0
    except (ValueError, OSError):
        print(json.dumps(dict(status='invalid_html', message='本地 HTML 无法读取或不满足字节条件'), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
