"""JSON command entry point for the V2 docs client (Python 3.12+)."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import secrets
import sys
import time

import client
import fields
import local
import store


KEY_CONTENT = local.KEY_CONTENT


def contains_key(value, token):
    if not token:
        return False
    if isinstance(value, bytes):
        return token.encode() in value
    if isinstance(value, str):
        return token in value
    if isinstance(value, dict):
        return any(contains_key(k, token) or contains_key(v, token) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_key(v, token) for v in value)
    return False


def redact(value, token):
    if not token:
        return value
    if isinstance(value, str):
        value = value.replace(token, '[已隐藏]')
    elif isinstance(value, dict):
        value = {redact(k, token): redact(v, token) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        value = [redact(v, token) for v in value]
    # Escaping, numeric scalars, or container syntax can introduce a Key that
    # was absent from the logical strings. Redact a whole value, never JSON text.
    if token in json.dumps(value, ensure_ascii=False):
        return '[已隐藏]'
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise local.Stop('参数错误，请检查命令和参数')


def build_parser():
    parser = Parser(prog='docsctl.py')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('me')
    for name in ('upload', 'replace'):
        p = sub.add_parser(name)
        if name == 'replace':
            p.add_argument('doc_id')
        p.add_argument('html')
        group = p.add_mutually_exclusive_group(required=True)
        group.add_argument('--summary')
        group.add_argument('--summary-file')
        p.add_argument('--share-friend', action='append', default=[])
        p.add_argument('--share-group', action='append', default=[])
        if name == 'upload':
            p.add_argument('--title')
            p.add_argument('--tag', action='append', default=[])
            p.add_argument('--new', action='store_true')
    p = sub.add_parser('list')
    p.add_argument('--query')
    p.add_argument('--tag', action='append', default=[])
    p.add_argument('--from', dest='uploaded_from')
    p.add_argument('--to', dest='uploaded_to')
    p.add_argument('--sort', choices=('uploaded_desc', 'uploaded_asc', 'title'), default='uploaded_desc')
    p.add_argument('--limit', type=int, default=50)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--cursor')
    group.add_argument('--all', action='store_true')
    for name in ('get', 'edit', 'favorite', 'unfavorite', 'delete'):
        p = sub.add_parser(name)
        p.add_argument('doc_id')
        if name == 'delete':
            p.add_argument('--yes', action='store_true')
        if name == 'edit':
            p.add_argument('--title')
            group = p.add_mutually_exclusive_group()
            group.add_argument('--summary')
            group.add_argument('--summary-file')
            group = p.add_mutually_exclusive_group()
            group.add_argument('--tag', action='append')
            group.add_argument('--clear-tags', action='store_true')
    sub.add_parser('tags')
    sub.add_parser('resume').add_argument('client_upload_id')
    return parser


def error_data(status=None, value=None):
    value = value if isinstance(value, dict) else {}
    return dict(error=value.get('error') if isinstance(value.get('error'), str) else 'request_failed',
                http_status=status, request_id=value.get('request_id'))


def trusted(doc, origin, doc_id):
    try:
        ident = fields.clean_doc_id(doc['id'])
        if doc_id is not None and ident != doc_id:
            return False
        if doc['url'] != origin + '/d/' + ident or not isinstance(doc['title'], str):
            return False
        report = doc['share_report']
        for key in ('shared', 'failed'):
            if not isinstance(report[key], list):
                return False
            for item in report[key]:
                if (item['type'] not in ('friend', 'group') or not isinstance(item['name'], str)
                        or (key == 'failed' and item['reason'] not in ('not_friend', 'group_not_found'))):
                    return False
        if doc.get('favorite') is not True:
            datetime.fromisoformat(doc['expires_at'].replace('Z', '+00:00'))
        return True
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def success_report(doc, op, status, *, duplicate_hint=True):
    lines = ['链接：' + doc['url'], '对方需登录且在分享范围内才能打开。']
    replay = op == 'upload' and status == 200
    if replay and duplicate_hint:
        lines.append('24 小时内提交过完全相同的内容和参数，返回的是同一篇；要另起一篇就加 --new。')
    if replay:
        lines.append('以下分享报告来自首次请求；当前分享范围可能已变化，请到网页核对。')
    if op == 'replace':
        lines.append('链接不变；到期时间已从现在重新起算；替换只会新增分享，不会移除已有分享。')
    if doc.get('favorite') is True:
        lines.append('收藏中，不会过期')
    else:
        expiry = datetime.fromisoformat(doc['expires_at'].replace('Z', '+00:00')).astimezone()
        lines.append('到期时间：' + expiry.strftime('%Y-%m-%d %H:%M'))
    shared, failed = doc['share_report']['shared'], doc['share_report']['failed']
    if failed:
        if shared:
            lines.append('已分享：' + '、'.join(x['name'] for x in shared))
        lines.append('以下对象没有分享成功：')
        lines.extend(x['name'] + ('（不是你的好友）' if x['reason'] == 'not_friend' else '（没有这个群组）') for x in failed)
        lines.append('可以到网页的分享管理里补分享。')
    elif shared:
        lines.append('已全部分享成功：' + '、'.join(x['name'] for x in shared))
    elif replay:
        lines.append('首次请求未指定分享对象。')
    elif op == 'replace':
        lines.append('本次未追加分享对象；已有分享保持不变。')
    else:
        lines.append('未指定分享对象，目前只有你自己能看。')
    return '\n'.join(lines)


MAX_LIST_PAGES = 1000


MANAGE = ('list', 'get', 'edit', 'favorite', 'unfavorite', 'delete', 'tags')


def manage_options(args):
    body = {}
    if args.command == 'list':
        if not 1 <= args.limit <= 200:
            raise local.Stop('limit 必须在 1–200 之间')
        for value in (args.uploaded_from, args.uploaded_to):
            if value is not None:
                try:
                    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    if parsed.tzinfo is None or parsed.utcoffset() is None:
                        raise ValueError()
                except ValueError:
                    raise local.Stop('时间必须是带时区的 RFC 3339') from None
        args.tag = fields.clean_tags(args.tag)
    elif args.command != 'tags':
        args.doc_id = fields.clean_doc_id(args.doc_id)
        if args.command == 'edit':
            if args.title is not None:
                body['title'] = fields.clean_title(args.title)
            if args.summary is not None or args.summary_file is not None:
                summary = args.summary if args.summary_file is None else Path(args.summary_file).read_text(encoding='utf-8')
                body['summary'] = fields.clean_summary(summary)
            if args.tag is not None or args.clear_tags:
                body['tags'] = fields.clean_tags(args.tag or [])
            if not body:
                raise local.Stop('edit 至少要修改一项')
    return body


def display_time(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('missing timezone')
    return stamp.astimezone().strftime('%Y-%m-%d %H:%M')


def metadata_valid(doc, origin, ident):
    try:
        return (isinstance(doc, dict) and doc['id'] == ident and
                doc['url'] == origin + '/d/' + ident and isinstance(doc['title'], str) and
                isinstance(doc['summary'], str) and isinstance(doc['tags'], list) and
                all(isinstance(t, str) for t in doc['tags']) and
                isinstance(doc['favorite'], bool) and bool(display_time(doc['uploaded_at'])) and
                ((doc['favorite'] and doc['expires_at'] is None) or
                 (not doc['favorite'] and bool(display_time(doc['expires_at'])))))
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, OSError):
        return False


def content_version_valid(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.tzinfo is not None and stamp.utcoffset() is not None
    except ValueError:
        return False


def replacement_version(api, origin, ident):
    doc = api.get('/api/v2/documents/' + ident)
    if not metadata_valid(doc, origin, ident) or not content_version_valid(doc.get('content_updated_at')):
        raise local.Stop('服务端元数据缺少有效的正文版本；停止替换，请用 get 核对')
    return doc['content_updated_at']


def collect_documents(api, query, *, all_pages):
    items, ids, cursors = [], set(), set()
    pages = 0
    while True:
        page = api.get('/api/v2/documents', query)
        entries, cursor = page.get('items'), page.get('next_cursor')
        reason = None
        if ('next_cursor' not in page or not isinstance(entries, list) or
                not (cursor is None or isinstance(cursor, str))):
            reason = '响应格式无效'
        elif not entries and cursor:
            reason = '空页仍有游标'
        elif cursor and cursor in cursors:
            reason = '游标重复'
        else:
            page_ids = []
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
                    reason = '文档格式无效'
                    break
                page_ids.append(entry['id'])
            if ids.intersection(page_ids) or len(page_ids) != len(set(page_ids)):
                reason = '文档 ID 重复'
        if reason:
            return items, page, '服务端分页异常（' + reason + '），已停止，结果不完整'
        items.extend(entries)
        ids.update(page_ids)
        pages += 1
        if not all_pages or not cursor:
            break
        if pages >= MAX_LIST_PAGES:
            return items, page, '已达到 1000 页安全上限，已停止，结果不完整；请缩小筛选范围后重试。'
        cursors.add(cursor)
        query = [(k, v) for k, v in query if k != 'cursor'] + [('cursor', cursor)]
    return items, page, None


def find_pending_upload(api, meta, raw, origin):
    uploaded_from = datetime.fromtimestamp(meta['prepared_at'] - 48 * 3600, timezone.utc)
    query = [('sort', 'uploaded_desc'), ('limit', '50'), ('q', meta['title']),
             ('uploaded_from', uploaded_from.isoformat().replace('+00:00', 'Z'))]
    items, _, problem = collect_documents(api, query, all_pages=True)
    if problem:
        raise local.Stop(problem)
    size = len(base64.b64decode(json.loads(raw)['html_base64'], validate=True))
    match = None
    # A match is conclusive only after the entire list was read and validated.
    for doc in items:
        try:
            ident = fields.clean_doc_id(doc['id'])
        except fields.FieldError:
            raise local.Stop('服务端清单文档格式无效；查找结果不完整') from None
        if (not metadata_valid(doc, origin, ident) or type(doc.get('size_bytes')) is not int
                or doc['size_bytes'] < 0):
            raise local.Stop('服务端清单文档格式无效；查找结果不完整')
        if match is None and doc['title'] == meta['title'] and doc['size_bytes'] == size:
            match = doc
    return match


def manage(api, args, body, origin):
    command = args.command
    if command == 'list':
        query = [('sort', args.sort), ('limit', str(args.limit))]
        for name, value in (('q', args.query), ('uploaded_from', args.uploaded_from), ('uploaded_to', args.uploaded_to), ('cursor', args.cursor)):
            if value is not None:
                query.append((name, value))
        query.extend(('tag', tag) for tag in args.tag)
        items, page, problem = collect_documents(api, query, all_pages=args.all)
        if problem:
            return 2, 'failed', dict(items=items, count=len(items)), problem
        cursor = page['next_cursor']
        report = '共 ' + str(len(items)) + ' 篇'
        report += ''.join('\n' + str(d.get('title', '')) + '：' + str(d.get('url', '')) for d in items[:50])
        if not args.all and cursor:
            report += '\n还有下一页：加 --cursor ' + cursor + ' 继续，或用 --all 取全部'
        return 0, 'success', dict(items=items, count=len(items)) if args.all else page, report
    if command == 'tags':
        data = api.get('/api/v2/tags')
        items = data.get('items', [])
        report = ('共 ' + str(len(items)) + ' 个标签：' + '、'.join(str(t['tag']) + '（' + str(t['count']) + '）' for t in items)) if items else '还没有用过标签'
        return 0, 'success', data, report
    ident = args.doc_id
    path = '/api/v2/documents/' + ident
    if command == 'get' or (command == 'delete' and not args.yes):
        data = api.get(path)
        if not metadata_valid(data, origin, ident):
            raise local.Stop('服务端元数据格式无效')
        if command == 'delete':
            return 3, 'confirm_required', data, f'将永久删除《{data["title"]}》（{data["url"]}）。删除后所有人立即无法打开，不能恢复。请先向用户确认；用户同意后执行：python3 bin/docsctl.py delete {ident} --yes'
        report = f'标题：{data["title"]}\n链接：{data["url"]}\n摘要：{data["summary"]}\n标签：' + '、'.join(data['tags']) + '\n上传时间：' + display_time(data['uploaded_at'])
        report += '\n收藏中，不会过期' if data['favorite'] else '\n到期时间：' + display_time(data['expires_at'])
        return 0, 'success', data, report
    try:
        method = {'edit': 'PATCH', 'favorite': 'PUT', 'unfavorite': 'DELETE', 'delete': 'DELETE'}[command]
        remote_status, data, headers = api.send(method, path + ('/favorite' if command in ('favorite', 'unfavorite') else ''), body if command == 'edit' else None)
        if remote_status in client.TRANSIENT or not (200 <= remote_status < 300 or 400 <= remote_status < 500 or remote_status == 507):
            raise client.RemoteUnknown()
        if not 200 <= remote_status < 300:
            return 2, 'failed', error_data(remote_status, data), client.describe(remote_status, data, headers)
        if command == 'delete':
            if remote_status != 204:
                raise client.RemoteUnknown()
            return 0, 'success', data, '文档已永久删除'
        if remote_status != 200 or not metadata_valid(data, origin, ident):
            raise client.RemoteUnknown()
        if command == 'favorite':
            if not data['favorite']:
                raise client.RemoteUnknown()
            report = '收藏中，不会过期'
        elif command == 'unfavorite':
            if data['favorite']:
                raise client.RemoteUnknown()
            report = '取消收藏后有 24 小时宽限；新的到期时间：' + display_time(data['expires_at'])
        else:
            report = '已修改：' + '、'.join({'title': '标题', 'summary': '摘要', 'tags': '标签'}[key] for key in body)
            if 'tags' in body:
                report += '\n标签已整体替换为：' + '、'.join(body['tags']) if body['tags'] else '\n标签已清空'
        return 0, 'success', data, report
    except client.RemoteUnknown:
        raise local.Stop(f'结果未知，请用 python3 bin/docsctl.py get {ident} 核对', code=4) from None


def local_record_failure(meta, doc):
    resume = 'python3 bin/docsctl.py resume ' + meta['client_upload_id']
    data = dict(doc, client_upload_id=meta['client_upload_id'], resume_command=resume)
    report = ('文档已经上传成功：' + doc['url'] + '；本地记录没写进去，用 resume 补记'
              + '\n文档 ID：' + doc['id'] + '\n' + resume)
    return 4, 'remote_unknown', data, report


def run(argv=None, *, env=None, out=None, clock=time.time, sleep=time.sleep, rng=random.uniform, transport=None):
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv and argv[0] in ('me', 'upload', 'replace', 'resume', *MANAGE) else None
    # Only credential() may establish a verified secret. Rejected input must
    # never become a pattern that rewrites the output protocol itself.
    token = ''
    meta = None
    last = None
    write_sent = False
    known_success = None
    code, status, data, report = 2, 'failed', error_data(), ''
    try:
        parser = build_parser()
        help_args = argv[:argv.index('--')] if '--' in argv else argv
        if any(value in ('-h', '--help') for value in help_args):
            sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
            help_parser = sub.choices.get(command, parser)
            help_parser.print_help(file=out)
            return 0
        home = Path(env.get('HOME') or Path.home())
        if not home.is_absolute():
            raise local.Stop('家目录必须是绝对路径；请设置有效的 HOME 或修正系统家目录配置')
        token = local.credential(env, home)

        def check_secret(value):
            if contains_key(value, token):
                raise local.Stop(KEY_CONTENT)

        if contains_key(argv, token):
            raise local.Stop(KEY_CONTENT)
        args = parser.parse_args(argv)
        if getattr(args, 'summary_file', None) is not None:
            summary_bytes = Path(args.summary_file).read_bytes()
            if contains_key(summary_bytes, token):
                raise local.Stop(KEY_CONTENT)
            args.summary = summary_bytes.decode('utf-8')
            args.summary_file = None
        if args.command in ('upload', 'replace'):
            html = local.read_html(args.html, forbidden=token)
            if contains_key(html, token):
                raise local.Stop(KEY_CONTENT)
        command = args.command
        origin = local.origin_from(env)
        if command in MANAGE:
            body = manage_options(args)
        if command in ('upload', 'replace'):
            summary = args.summary
            if args.summary_file is not None:
                summary = Path(args.summary_file).read_text(encoding='utf-8')
            summary = fields.clean_summary(summary)
            share = fields.clean_share(args.share_friend, args.share_group)
            if command == 'upload':
                title = fields.clean_title(args.title) if args.title is not None else None
                tags = fields.clean_tags(args.tag)
                doc_id = None
            else:
                doc_id = fields.clean_doc_id(args.doc_id)
                title = '替换文档'
            inspected = local.inspect_html(html)
            body = dict(summary=summary, html_base64=base64.b64encode(html).decode('ascii'),
                        sha256=hashlib.sha256(html).hexdigest())
            if share:
                body['share_with'] = share
            if command == 'upload':
                title = local.pick_title(title, inspected)
                body.update(title=title, source_info=local.build_source_info(inspected, Path(args.html)))
                if tags:
                    body['tags'] = tags
            if contains_key(body, token):
                raise local.Stop(KEY_CONTENT)
            raw = canonical(body)
            if len(raw) > fields.MAX_BODY:
                raise local.Stop('JSON 请求体超过大小上限')
        elif command == 'resume':
            pending_meta, raw = store.load_pending(home, args.client_upload_id, allow_success=True)
            check_secret(pending_meta)
            check_secret(store.canonical(pending_meta))
            if raw is not None:
                archived = json.loads(raw)
                if (contains_key(raw, token) or contains_key(archived, token) or
                        contains_key(base64.b64decode(archived['html_base64']), token)):
                    raise local.Stop(KEY_CONTENT)
            if pending_meta.get('origin') != origin:
                raise local.Stop('补发站点与档案记录不一致，或档案未记录站点；请在原来的环境里补发')
            if 'success' in pending_meta and not store.success_valid(pending_meta):
                raise local.Stop('恢复档案缺失或被改动；停止，请核对本地档案', code=4)
            if ('success' not in pending_meta and pending_meta['op'] == 'replace'
                    and not content_version_valid(pending_meta.get('content_updated_at'))):
                raise local.Stop('替换档案缺少有效的准备时正文版本，无法安全补发；请用 get 核对当前状态，需要时重新执行 replace')
            meta = pending_meta
            if 'success' in meta:
                known_success = meta['success']
                store.finalize(home, meta, clock(), doc=known_success, check=check_secret)
                code, status, data = 0, 'success', known_success
                report = '链接：' + known_success['url'] + '\n对方需登录且在分享范围内才能打开。\n已补记本地成功记录'
            elif meta['op'] != 'upload' and clock() >= meta['prepared_at'] + 86400:
                raise local.Stop('超过 24 小时，服务端已不记得这次请求，不会自动重发；请先用 list 核对是否已经上传成功', code=4)

        if known_success is None:
            def capture(*a, **kw):
                nonlocal last, write_sent
                last = None
                # Inspect the exact wire representation as well as decoded URL
                # values. Never redact or reserialize an idempotent request.
                from urllib.parse import unquote_plus
                check_secret(a[1])
                check_secret(unquote_plus(a[1]))
                if len(a) > 3:
                    check_secret(a[3])
                if len(a) > 4:
                    check_secret(a[4])
                    # /me, retries and backoff may have consumed the remaining
                    # lifetime. Guard every content attempt at the dispatch boundary.
                    if meta is not None and clock() >= meta['prepared_at'] + 86400:
                        raise local.Stop('超过 24 小时，服务端已不记得这次请求，不会自动重发；请先用 list 核对是否已经上传成功', code=4)
                if a[0] != 'GET':
                    write_sent = True
                result = (transport or client.http_request)(*a, **kw)
                result = tuple(redact(v, token) for v in result)
                last = result
                return result

            api = client.Client(origin, token, transport=capture, sleep=sleep, rng=rng)

            def preflight(read):
                try:
                    return read()
                except local.Stop as error:
                    if command == 'resume' and meta is not None:
                        raise local.Stop(error.message + '；先前写操作结果仍未知', code=4) from None
                    raise

            me = preflight(api.me)
            if command == 'me':
                data = me
                report = f'今日已用 {me["daily_upload_used"]}/{me["daily_upload_limit"]} 次'
                code, status = 0, 'success'
            elif command in MANAGE:
                code, status, data, report = manage(api, args, body, origin)
            else:
                if command != 'resume':
                    idem = (client.idempotency_key(raw, secrets.token_hex(16) if args.new else '')
                            if command == 'upload' else secrets.token_hex(16))
                    version = replacement_version(api, origin, doc_id) if command == 'replace' else None
                    meta = store.prepare(home, command, doc_id, raw, body['sha256'], idem, clock(),
                                         title=title, origin=origin, content_updated_at=version, check=check_secret)
                op, doc_id = meta['op'], meta['doc_id']
                if command == 'resume' and op == 'replace':
                    current_version = preflight(lambda: replacement_version(api, origin, doc_id))
                    if current_version != meta['content_updated_at']:
                        store.finalize(home, meta, clock(), error='superseded', check=check_secret)
                        raise local.Stop('这篇文档在准备这次替换之后被改动过（可能正是这次替换已经生效，也可能是之后的另一次替换）。'
                                         '为避免覆盖较新的内容，不再补发；请用 get 查看当前状态，需要时重新执行 replace')
                found = (preflight(lambda: find_pending_upload(api, meta, raw, origin))
                         if command == 'resume' and op == 'upload' else None)
                path = '/api/v2/documents' if op == 'upload' else '/api/v2/documents/' + doc_id + '/content'
                try:
                    if found is not None:
                        known_success = found
                        store.remember_success(home, meta, found, check=check_secret)
                        store.finalize(home, meta, clock(), doc=found, check=check_secret)
                        known_success = None
                        code, status, data = 0, 'success', found
                        report = ('链接：' + found['url'] + '\n对方需登录且在分享范围内才能打开。'
                                  '\n补发前在清单里找到了同一篇，没有重复发送')
                    else:
                        remote_status, value, headers = api.send_idempotent(
                            'POST' if op == 'upload' else 'PUT', path, raw, meta['idempotency_key'],
                            unknown=command == 'resume')
                        if 200 <= remote_status < 300:
                            if not trusted(value, origin, doc_id):
                                raise client.RemoteUnknown()
                            known_success = value
                            store.remember_success(home, meta, value, check=check_secret)
                            store.finalize(home, meta, clock(), doc=value, check=check_secret)
                            known_success = None
                            code, status, data = 0, 'success', value
                            report = success_report(value, op, remote_status,
                                                    duplicate_hint=command != 'resume' and api.idempotent_attempts == 1)
                        elif 400 <= remote_status < 500 or remote_status == 507:
                            data = error_data(remote_status, value)
                            report = client.describe(remote_status, value, headers)
                            try:
                                store.finalize(home, meta, clock(), error=data['error'], check=check_secret)
                            except local.Stop:
                                report += '；本地记录失败，远程已明确拒绝，请核对本地档案'
                            if op == 'upload' and remote_status == 404:
                                report = '24 小时内用完全相同的内容和参数上传过一次，那篇文档已经删除。确实要重新上传，加 --new'
                        else:
                            raise client.RemoteUnknown()
                except client.RemoteUnknown:
                    raise local.Stop('写操作结果未知', code=4) from None
    except (local.Stop, fields.FieldError, OSError, UnicodeError) as error:
        if known_success is not None:
            code, status, data, report = local_record_failure(meta, known_success)
        else:
            code = error.code if isinstance(error, local.Stop) else 2
            status = 'remote_unknown' if code == 4 else 'failed'
            report = error.message if isinstance(error, local.Stop) else ('本地文件读取失败' if isinstance(error, (OSError, UnicodeError)) else str(error))
            data = error_data(*(last[:2] if last is not None and not 200 <= last[0] < 300 else (None, None)))
            if code == 4 and meta is not None:
                try:
                    store.record(home, meta, 'remote_unknown', clock(), check=check_secret)
                except Exception:
                    # A failed event append must not hide the recovery information.
                    pass
                resume = 'python3 bin/docsctl.py resume ' + meta['client_upload_id']
                data = dict(client_upload_id=meta['client_upload_id'], resume_command=resume)
                report += '\n' + resume
    except Exception as error:
        if known_success is not None:
            code, status, data, report = local_record_failure(meta, known_success)
        else:
            code = 4 if meta is not None or write_sent else 2
            status = 'remote_unknown' if code == 4 else 'failed'
            data = error_data()
            report = type(error).__name__
            if meta is not None:
                try:
                    store.record(home, meta, 'remote_unknown', clock(), check=check_secret)
                except Exception:
                    pass
                resume = 'python3 bin/docsctl.py resume ' + meta['client_upload_id']
                data = dict(client_upload_id=meta['client_upload_id'], resume_command=resume)
                report += '\n' + resume
            elif write_sent:
                report += '\n结果未知，请用 python3 bin/docsctl.py get ' + args.doc_id + ' 核对'
    result = dict(ok=code == 0, command=command, status=status, exit_code=code, data=data, report=report)
    # A rejected credential may itself spell the spaced protocol prefix. It
    # is never a redaction pattern; compact formatting avoids that coincidence.
    text = json.dumps(redact(result, token), ensure_ascii=False,
                      separators=None if token else (',', ':'))
    out.write(text + '\n')
    return code


if __name__ == '__main__':
    sys.exit(run())
