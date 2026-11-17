"""Read-only local validation and private credential access for V2."""
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
from fields import MAX_HTML, MAX_SOURCE_INFO, FieldError, clean_title

PRODUCTION = 'https://docs.zh0uyuan.com'
KEY_CONTENT = '要发送的内容里出现了你的 API Key，已停止。请先删掉再试。'
KEY_GUIDANCE = '登录后打开 https://docs.zh0uyuan.com/keys（「API Key」页面）生成或重新生成 Key，明文只显示一次；不要把 Key 贴进聊天，也不要写进带明文的命令或 shell history'


class Stop(Exception):
    def __init__(self, message, code=2, status='failed'):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def origin_from(env):
    origin = env.get('DOCS_ORIGIN', PRODUCTION)
    if origin != PRODUCTION and (not isinstance(origin,str) or re.fullmatch(r'https?://(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?',origin) is None):
        raise Stop('DOCS_ORIGIN 只允许固定生产站点或本地回环测试 origin')
    if ':' in origin.rsplit('/',1)[-1]:
        try:
            if not 1 <= int(origin.rsplit(':',1)[1]) <= 65535: raise ValueError()
        except ValueError: raise Stop('测试端口非法') from None
    return origin


def secure_directory(path, create=False):
    try:
        if create and not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700)
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o700 or info.st_uid!=os.geteuid()):
            raise Stop('本地私有目录类型、属主或权限不符')
    except OSError:
        raise Stop('本地私有目录或文件无法安全访问') from None


def private_read(path, limit):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600 or info.st_uid!=os.geteuid():
            raise Stop('私有文件类型、属主或权限不符')
        fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            actual = os.fstat(stream.fileno())
            if (actual.st_dev,actual.st_ino)!=(info.st_dev,info.st_ino): raise Stop('文件打开时变化')
            data = stream.read(limit)
        return data
    except OSError:
        raise Stop('本地私有目录或文件无法安全访问') from None


def credential(env, home):
    if 'DOCS_API_KEY' in env:
        token, source = env['DOCS_API_KEY'], '环境变量'
    else:
        path = home/'.config/docs/api-key'
        try:
            secure_directory(path.parent)
            raw = private_read(path, 515)
            if len(raw)>514:
                raise Stop('凭证来源是文件，该来源无效；不回退。\n'+KEY_GUIDANCE)
            token, source = raw.decode('ascii'), '文件'
        except (OSError,UnicodeError,Stop):
            raise Stop('凭证文件缺失或权限、属主、类型不符。本轮停止。\n'+KEY_GUIDANCE) from None
    if isinstance(token,str): token = token.rstrip('\r\n')
    if (not isinstance(token,str) or len(token)<16 or len('Bearer ')+len(token)>512
            or any(ord(c)<33 or ord(c)>126 for c in token)):
        raise Stop('凭证来源是'+source+'，该来源无效；不回退。\n'+KEY_GUIDANCE)
    return token


def read_html(path, *, forbidden=None):
    try:
        path = Path(path)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.suffix.lower() != '.html':
            raise Stop('需要非符号链接的本地普通 .html 文件')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise Stop('源文件在打开前发生变化')
            data = bytearray()
            needle = forbidden.encode() if forbidden else None
            tail = b''
            while chunk := stream.read(65536):
                if needle:
                    if needle in tail + chunk:
                        raise Stop(KEY_CONTENT)
                    tail = (tail + chunk)[-(len(needle) - 1):]
                if len(data) <= MAX_HTML:
                    data.extend(chunk[:MAX_HTML + 1 - len(data)])
                # Without a credential screen there is no reason to scan excess bytes.
                if not needle and len(data) > MAX_HTML:
                    break
        if len(data) > MAX_HTML:
            raise Stop('HTML 超过 2 MiB')
        return bytes(data)
    except OSError:
        raise Stop('本地 HTML 无法安全读取') from None


class Inspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.templates = 0
        self.script = False
        self.title = None
        self.titles = []
        self.meta = {}
        self.charsets = []

    def handle_starttag(self, tag, attrs):
        if self.script:
            return
        if tag == 'template':
            self.templates += 1
            return
        if self.templates:
            return
        if tag == 'script':
            self.script = True
        elif tag == 'title':
            self.title = []
        elif tag == 'meta':
            self.charsets.extend(value or '' for key, value in attrs if key == 'charset')
            attrs = dict(reversed(attrs))
            if (attrs.get('http-equiv') or '').strip().lower() == 'content-type':
                for match in re.finditer(r"(?:^|;)\s*charset\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^;\s]*))", attrs.get('content') or '', re.I):
                    self.charsets.append(next(value for value in match.groups() if value is not None))
            name = (attrs.get('name') or '').lower()
            if name in {'hx:title', 'hx:generator', 'hx:generated-at', 'hx:channel', 'hx:task-id'}:
                self.meta.setdefault(name, attrs.get('content') or '')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.script:
            if tag == 'script':
                self.script = False
            return
        if tag == 'template' and self.templates:
            self.templates -= 1
            return
        if self.templates:
            return
        if tag == 'title' and self.title is not None:
            self.titles.append(''.join(self.title))
            self.title = None

    def handle_data(self, data):
        if not self.templates and not self.script and self.title is not None:
            self.title.append(data)


def inspect_html(data):
    if not 1 <= len(data) <= MAX_HTML:
        raise Stop('解码后 HTML 上限 2 MiB，且文件不能为空')
    try:
        text = data.decode('utf-8', errors='strict')
    except UnicodeError:
        raise Stop('HTML 不是严格 UTF-8') from None
    lower = data.lower()
    if (data.startswith(b'\xef\xbb\xbf') or b'\0' in data
            or not lower.lstrip(b' \t\r\n').startswith((b'<!doctype html', b'<html')) or b'</html>' not in lower):
        raise Stop('HTML 字节条件不合规：BOM、NUL、起始或结束标记')
    parser = Inspector()
    parser.feed(text)
    parser.close()
    if any(value.strip().lower() not in ('utf-8', 'utf8') for value in parser.charsets):
        raise Stop('HTML charset 声明与严格 UTF-8 字节要求冲突')
    return {'titles': list(dict.fromkeys(parser.titles)), 'meta': parser.meta}


def pick_title(explicit, inspected):
    if explicit is not None:
        value = explicit
    elif len(inspected['titles']) == 1:
        value = inspected['titles'][0]
    else:
        raise Stop('页面标题缺失或有多个标题，请提供 --title')
    try:
        return clean_title(value)
    except FieldError as error:
        raise Stop(str(error) + '；请提供 --title') from None


def build_source_info(inspected, path):
    result = {'client': 'html-upload/2', 'file': path.name}
    for source, target in (('hx:generator', 'generator'), ('hx:generated-at', 'generated_at'),
                           ('hx:channel', 'channel'), ('hx:task-id', 'task_id')):
        if source in inspected['meta']:
            result[target] = inspected['meta'][source]
    for key in reversed(list(result)):
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) <= MAX_SOURCE_INFO:
            break
        if key != 'client':
            del result[key]
    return result

