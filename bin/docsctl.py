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
