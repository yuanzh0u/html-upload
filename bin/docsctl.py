"""JSON command entry point for the V2 docs client (Python 3.12+)."""
import argparse
import base64
from datetime import datetime
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


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise local.Stop('参数错误：' + message)


def build_parser():
    parser = Parser()
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


def success_report(doc, op, status):
    lines = ['链接：' + doc['url'], '对方需登录且在分享范围内才能打开。']
    if op == 'upload' and status == 200:
        lines.append('24 小时内提交过完全相同的内容和参数，返回的是同一篇；要另起一篇就加 --new。')
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
    else:
        lines.append('未指定分享对象，目前只有你自己能看。')
    return '\n'.join(lines)


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


def manage(api, args, body, origin):
    command = args.command
    if command == 'list':
        query = [('sort', args.sort), ('limit', str(args.limit))]
        for name, value in (('q', args.query), ('uploaded_from', args.uploaded_from), ('uploaded_to', args.uploaded_to), ('cursor', args.cursor)):
            if value is not None:
                query.append((name, value))
        query.extend(('tag', tag) for tag in args.tag)
        items, ids, cursors = [], set(), set()
        while True:
            page = api.get('/api/v2/documents', query)
            entries, cursor = page.get('items'), page.get('next_cursor')
            reason = None
            if not isinstance(entries, list) or not (cursor is None or isinstance(cursor, str)):
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
                return 2, 'failed', dict(items=items, count=len(items)), '服务端分页异常（' + reason + '），已停止，结果不完整'
            items.extend(entries)
            ids.update(page_ids)
            if not args.all or not cursor:
                break
            cursors.add(cursor)
            query = [(k, v) for k, v in query if k != 'cursor'] + [('cursor', cursor)]
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


def run(argv=None, *, env=None, out=None, clock=time.time, sleep=time.sleep, rng=random.uniform, transport=None):
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else None
    token = env.get('DOCS_API_KEY', '')
    meta = None
    last = None
    code, status, data, report = 2, 'failed', error_data(), ''
    try:
        args = build_parser().parse_args(argv)
        command = args.command
        home = Path(env.get('HOME', ''))
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
            html = local.read_html(args.html)
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
            token = local.credential(env, home)
            raw = canonical(body)
            if len(raw) > fields.MAX_BODY:
                raise local.Stop('JSON 请求体超过大小上限')
        elif command == 'resume':
            meta, raw = store.load_pending(home, args.client_upload_id)
            if clock() - meta['prepared_at'] > 86400:
                raise local.Stop('超过 24 小时，服务端已不记得这次请求，不会自动重发；请先用 list 核对是否已经上传成功', code=4)
            token = local.credential(env, home)
        else:
            token = local.credential(env, home)

        def capture(*a, **kw):
            nonlocal last
            last = None
            result = (transport or client.http_request)(*a, **kw)
            last = result
            return result

        api = client.Client(origin, token, transport=capture, sleep=sleep, rng=rng)
        me = api.me()
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
                meta = store.prepare(home, command, doc_id, raw, body['sha256'], idem, clock(), title=title)
            op, doc_id = meta['op'], meta['doc_id']
            path = '/api/v2/documents' if op == 'upload' else '/api/v2/documents/' + doc_id + '/content'
            try:
                remote_status, value, headers = api.send_idempotent('POST' if op == 'upload' else 'PUT', path, raw, meta['idempotency_key'])
                if 200 <= remote_status < 300:
                    if not trusted(value, origin, doc_id):
                        raise client.RemoteUnknown()
                    store.finalize(home, meta, clock(), doc=value)
                    code, status, data = 0, 'success', value
                    report = success_report(value, op, remote_status)
                elif 400 <= remote_status < 500 or remote_status == 507:
                    data = error_data(remote_status, value)
                    report = client.describe(remote_status, value, headers)
                    try:
                        store.finalize(home, meta, clock(), error=data['error'])
                    except local.Stop:
                        report += '；本地记录失败，远程已明确拒绝，请核对本地档案'
                    if op == 'upload' and remote_status == 404:
                        report = '24 小时内用完全相同的内容和参数上传过一次，那篇文档已经删除。确实要重新上传，加 --new'
                else:
                    raise client.RemoteUnknown()
            except client.RemoteUnknown:
                try:
                    store.record(home, meta, 'remote_unknown', clock())
                except (OSError, local.Stop):
                    pass
                raise local.Stop('写操作结果未知', code=4) from None
    except (local.Stop, fields.FieldError, OSError, UnicodeError) as error:
        code = error.code if isinstance(error, local.Stop) else 2
        status = 'remote_unknown' if code == 4 else 'failed'
        report = error.message if isinstance(error, local.Stop) else ('本地文件读取失败' if isinstance(error, (OSError, UnicodeError)) else str(error))
        data = error_data(*(last[:2] if last is not None and not 200 <= last[0] < 300 else (None, None)))
        if code == 4 and meta is not None:
            resume = 'python3 bin/docsctl.py resume ' + meta['client_upload_id']
            data = dict(client_upload_id=meta['client_upload_id'], resume_command=resume)
            report += '\n' + resume
    result = dict(ok=code == 0, command=command, status=status, exit_code=code, data=data, report=report)
    text = json.dumps(result, ensure_ascii=False)
    if isinstance(token, str) and token:
        text = text.replace(token, '[已隐藏]')
    out.write(text + '\n')
    return code


if __name__ == '__main__':
    sys.exit(run())
