#!/usr/bin/env python3
"""Only upload entrypoint. Python 3.12 stdlib; see ../SKILL.md."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import random
import re
import secrets
import ssl
import stat
import sys
import time
import urllib.error
import urllib.request
from check_html import inspect_html, read_html, valid_title, utc_time
from match_taxonomy import match, PLURAL
from normalize import normalize

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = 'https://docs.zh0uyuan.com'
SAFE_ID = re.compile(r'[0-9A-Za-z_-]{1,64}')
HEX_ID = re.compile(r'[0-9a-f]{32}')
MAX_BODY = 14046552
STATUSES = {'prepared','upload_failed','remote_unknown','uploaded','local_finalize_failed','reconciled'}
SCOPES = {'me:read','taxonomy:read','documents:create'}
GUIDANCE = '登录 https://docs.zh0uyuan.com，进入控制台 → 我的文档视图 → API Key，创建后明文只显示一次。现行页面地址是 https://docs.zh0uyuan.com/console/keys 。不要把 Key 贴进聊天，不要写进带明文的命令或 shell history。'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def stamp(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


class Stop(Exception):
    def __init__(self, message, code=2, status='upload_failed', error_code=None, backup=None):
        super().__init__(message)
        self.code, self.status, self.error_code, self.backup = code, status, error_code, backup


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse otherwise reflects arbitrary argv into logs.
        raise Stop('参数无效；请查看 --help 中的参数、互斥组与必填路径')


def arguments(argv):
    parser = Parser(description=__doc__)
    parser.add_argument('html')
    parser.add_argument('--title')
    for kind in ('project','category','type','model'):
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--'+kind+'-id')
        group.add_argument('--'+kind+'-name')
    parser.add_argument('--generated-at')
    parser.add_argument('--visibility', choices=('private','restricted','public'), default='private')
    parser.add_argument('--acl-user', action='append', default=[])
    parser.add_argument('--acl-group', action='append', default=[])
    for flag in ('force-confirm','dry-run','non-interactive'):
        parser.add_argument('--'+flag, action='store_true')
    parser.add_argument('--confirm-file')
    parser.add_argument('--resume', metavar='CLIENT_UPLOAD_ID', help='仅从 0600 pending 恢复原请求或本地收尾')
    args = parser.parse_args(argv)
    if args.resume is not None and args.dry_run: raise Stop('--resume 与 --dry-run 不能同时使用')
    if args.title is not None and not valid_title(args.title):
        raise Stop('显式标题必须为 1–200 码位，无控制字符和首尾空白')
    for kind in ('project','category','type','model'):
        value = getattr(args,kind+'_id')
        if value is not None and not SAFE_ID.fullmatch(value): raise Stop('taxonomy id 格式非法')
    if args.resume is not None and not HEX_ID.fullmatch(args.resume): raise Stop('恢复 id 必须为 32 位小写十六进制')
    if len(args.acl_user)>32 or len(args.acl_group)>16: raise Stop('ACL 最多 32 用户、16 组')
    for value in args.acl_user + args.acl_group:
        if not SAFE_ID.fullmatch(value) or value.isdigit():
            raise Stop('ACL 需要 user id 或 group id，不接受显示名、手机号或邮箱')
    if args.visibility != 'restricted' and (args.acl_user or args.acl_group): raise Stop('private/public 的 ACL 目标必须为空')
    if args.visibility == 'restricted' and not (args.acl_user or args.acl_group): raise Stop('restricted 至少需要一个 ACL 目标')
    return args


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
    if create and not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o700 or info.st_uid!=os.geteuid()):
        raise OSError('本地私有目录类型、属主或权限不符')


def private_read(path, limit=MAX_BODY+1):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600 or info.st_uid!=os.geteuid():
        raise OSError('私有文件类型、属主或权限不符')
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        actual = os.fstat(stream.fileno())
        if (actual.st_dev,actual.st_ino)!=(info.st_dev,info.st_ino): raise OSError('文件打开时变化')
        data = stream.read(limit)
    return data


def credential(env, home):
    if env.get('DOCS_API_KEY') is not None:
        token, source = env['DOCS_API_KEY'], '环境变量'
    else:
        path = home/'.config/docs/api-key'
        try:
            secure_directory(path.parent)
            raw = private_read(path, 515)
            if len(raw)>514:
                raise Stop('凭证来源是文件，该来源无效；不回退。\n'+GUIDANCE)
            token, source = raw.decode('ascii'), '文件'
        except (OSError,UnicodeError):
            raise Stop('凭证文件缺失或权限、属主、类型不符。本轮停止。\n'+GUIDANCE) from None
    if isinstance(token,str): token = token.rstrip('\r\n')
    if (not isinstance(token,str) or len(token)<16 or len('Bearer ')+len(token)>512
            or any(ord(c)<33 or ord(c)>126 for c in token)):
        raise Stop('凭证来源是'+source+'，该来源无效；不回退。\n'+GUIDANCE)
    return token, source


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


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
    def http_open(self, req): return self.do_open(HTTPConnection, req)


class HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req): return self.do_open(HTTPSConnection, req, context=ssl.create_default_context())


def http_request(method, url, token, body=None, idem=None):
    headers = {'Authorization':'Bearer '+token, 'Accept':'application/json'}
    if body is not None:
        headers.update({'Content-Type':'application/json', 'Origin':url.rsplit('/api/',1)[0], 'Idempotency-Key':idem, 'Content-Length':str(len(body))})
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), HTTPHandler(), HTTPSHandler())
    try:
        response = opener.open(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read(8*1024*1024+1)
        if len(data)>8*1024*1024: raise ValueError('响应过大')
        try: result = json.loads(data)
        except (ValueError, UnicodeError, RecursionError): result = None
        return response.code, result, dict(response.headers)


def report(status, *, url=None, docid=None, backup=None, manifest_status=None, home=None, external=None, todo='无'):
    notice = external['notice'] if external else '外链提示：无（尚未完成扫描）'
    if external and external['total']:
        notice += '\n'+'\n'.join(external['urls'])+'\n总条数: '+str(external['total'])
    text = (ROOT/'templates/report.txt').read_text().format(status=status,url=url or '无',docid=docid or '无',backup=str(backup) if backup else '无',manifest_status=manifest_status or '无',manifest_path=str(home/'docs/manifest.jsonl') if home else '无',external_notice=notice,todo=todo)
    return ('未提交，没有 docid\n' if status=='dry_run' else '')+text


def self_check(request, origin, token, source):
    try: code, value, _ = request('GET',origin+'/api/v1/me',token)
    except (OSError,ValueError,urllib.error.URLError,http.client.HTTPException): raise Stop('/me 自检失败；尚未上传正文') from None
    if code in (401,403):
        raise Stop('凭证来源是'+source+'，该来源无效；不回退。'+str(code)+'，停止。\n'+GUIDANCE,error_code=str(code))
    if (code!=200 or not isinstance(value,dict) or value.get('kind')!='api_key'
            or not all(isinstance(value.get(k),str) for k in ('user_id','display_name','role'))
            or not isinstance(value.get('scopes'),list) or not SCOPES.issubset(value['scopes'])):
        raise Stop('/me 自检不通过：响应、身份或 scope 不满足上传要求')
    return {**{k:value[k] for k in ('user_id','display_name','role','scopes')},'notes':['服务端未返回 Key 有效期。','服务端未返回上传限制。']}


def get_taxonomy(request, origin, token):
    try: code, value, _ = request('GET',origin+'/api/v1/taxonomy?include=aliases,counts',token)
    except (OSError,ValueError,urllib.error.URLError,http.client.HTTPException): raise Stop('taxonomy 请求失败，停止') from None
    if code!=200 or not isinstance(value,dict) or type(value.get('version')) is not int or value['version']<0 or any(not isinstance(value.get(k),list) for k in PLURAL.values()):
        raise Stop('taxonomy 请求失败或响应字段不合法，停止')
    return value


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def exclusive_write(path, data, before_sync=None):
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'wb') as stream:
        os.fchmod(stream.fileno(),0o600)
        stream.write(data)
        stream.flush()
        if before_sync: before_sync(path)
        os.fsync(stream.fileno())
    sync_dir(path.parent)


def replace_json(path, value):
    temporary = path.parent/('.'+path.name+'.'+secrets.token_hex(8))
    try:
        exclusive_write(temporary,canonical(value))
        os.replace(temporary,path)
        sync_dir(path.parent)
    finally:
        if temporary.exists(): temporary.unlink()


def acl_for(args):
    return dict(visibility=args.visibility,users=args.acl_user,groups=args.acl_group)


def make_plan(args, checked, taxonomy, data, identity, home, client_id):
    items = []
    for kind in ('project','type','model'):
        intent = {'id':getattr(args,kind+'_id'), 'name':getattr(args,kind+'_name')}
        if kind=='model': intent = {'id':args.model_id,'name':checked['model_name']}
        result = match(taxonomy,kind,intent)
        result['field'] = kind
        if kind=='model' and result['status']!='matched' and checked['worker']:
            result['reason'] = '这是模型名，还是执行者名称？若是执行者，请给出模型名。'+result['reason']
        if kind=='model' and args.model_id and checked['worker'] and result['status']=='matched' and not any(x['field']=='model' for x in checked['issues']):
            if normalize(result['term']['name'])[1] != normalize(checked['worker'])[1] and normalize(checked['worker'])[1] not in result['term'].get('aliases',[]):
                result['metadata_conflict'] = True
                result['reason'] = '模型 id 与 worker 冲突，请选定模型'
        items.append(result)
    if items[0]['status']!='matched':
        category = match(taxonomy,'project_category',dict(id=args.category_id,name=args.category_name))
        category['field'] = 'project.category'
        items.append(category)
    elif args.category_id is not None or args.category_name is not None:
        raise Stop('分类参数只在新建项目时使用；已命中项目不带分类')
    items.extend(checked['issues'])
    explicit = args.force_confirm or any(x.get('status')!='matched' or x.get('metadata_conflict') for x in items)
    plan = dict(status='needs_confirmation',client_upload_id=client_id, items=items,
                generated_at=dict(original=checked['generated_at_raw'],utc=checked['generated_at']),
                model=checked['model_name'] or args.model_id,title=checked['title'],acl=acl_for(args),
                external_links=checked['external_links'],prompt=(ROOT/'templates/confirm-prompt.txt').read_text(),
                intent=dict(source_path=str(Path(args.html).absolute()),sha256=digest(data),parameters={k:v for k,v in vars(args).items() if k not in ('confirm_file','dry_run','non_interactive','resume')}),
                self_check=identity,report=report('needs_confirmation',home=home,external=checked['external_links'],todo='把清单交给用户，逐项得到确认文件后再运行；Agent 不代替确认'))
    plan['plan_sha256'] = digest(canonical(plan))
    return plan, explicit


def save_plan(home, plan):
    root = home/'docs'
    try:
        secure_directory(root,True)
        secure_directory(root/'.confirm',True)
        replace_json(root/'.confirm'/(plan['client_upload_id']+'.plan.json'),plan)
    except OSError: raise Stop('确认清单无法私有落盘，网络未发送；先修本地权限或磁盘',5,'prepared_only') from None


def confirmation_input(path, home):
    try:
        file = Path(path)
        if not stat.S_ISREG(file.lstat().st_mode) or file.stat().st_size>1024*1024: raise ValueError()
        value = json.loads(file.read_bytes())
        client_id = value['client_upload_id']
        if not isinstance(client_id,str) or not HEX_ID.fullmatch(client_id): raise ValueError()
        secure_directory(home/'docs')
        secure_directory(home/'docs/.confirm')
        saved = json.loads(private_read(home/'docs/.confirm'/(client_id+'.plan.json')))
        if value.get('accepted') is not True or value.get('plan_sha256')!=saved['plan_sha256']:
            raise ValueError()
        if digest(canonical({k:v for k,v in saved.items() if k!='plan_sha256'}))!=saved['plan_sha256']: raise ValueError()
        return value, saved
    except (OSError,ValueError,KeyError,TypeError,RecursionError): raise Stop('确认文件的 accepted、client_upload_id 或 plan_sha256 不符') from None


def confirmation_choices(plan, confirmation):
    incoming = confirmation.get('choices',{})
    if not isinstance(incoming,dict): raise Stop('choices 必须是按已知 field 索引的对象')
    choices = {**plan.get('confirmed_choices',{}), **incoming}
    if set(choices)-{'project','type','model','project.category','title','generated_at'}: raise Stop('choices 必须是按已知 field 索引的对象')
    return choices


def select_confirmed(args, checked, taxonomy, plan, confirmation, now):
    choices = confirmation_choices(plan,confirmation)
    new_terms = []
    selected = {}
    for kind in ('project_category','project','type','model'):
        field = 'project.category' if kind=='project_category' else kind
        item = next((x for x in plan['items'] if x['field']==field and 'status' in x),None)
        if item is None: continue
        choice = choices.get(field)
        if choice is None and item['status']=='matched' and not item.get('metadata_conflict'):
            selected[kind] = item['selector']
            continue
        # Category is unnecessary when the user reuses an existing project.
        if kind=='project_category' and not choice:
            project_choice = choices.get('project',{})
            if project_choice.get('action')=='reuse': continue
        if not isinstance(choice,dict): raise Stop('每个待选择或新建 field 需要 choices 项：'+field)
        action = choice.get('action')
        if action=='reuse':
            result = match(taxonomy,kind,{'id':choice.get('id')})
            if result['status']!='matched': raise Stop('确认的复用 id 不存在或不可用于上传')
            selected[kind]=result['selector']
        elif action=='create' and choice.get('create_confirmed') is True:
            display,key = normalize(choice.get('name'))
            result = match(taxonomy,kind,{'name':display})
            if result['status']=='matched': selected[kind]=result['selector']
            else:
                selected[kind]=dict(name=display,create_confirmed=True)
                new_terms.append(dict(kind=kind,normalized=key,confirmed_at=stamp(now)))
        else: raise Stop('新建必须逐项 create/name/create_confirmed: true')
    if 'name' in selected.get('project',{}):
        category = selected.get('project_category')
        if not category: raise Stop('新建项目缺分类；不默认其他分类')
        if 'id' in category: selected['project']['category_id']=category['id']
        else: selected['project']['category']=category
    if 'id' in selected.get('project',{}):
        new_terms=[x for x in new_terms if x['kind']!='project_category']
        selected.pop('project_category',None)
    title = checked['title']
    generated = checked['generated_at']
    sources = dict(time=checked['time_source'],model=checked['model_source'])
    for field in ('title','generated_at','model'):
        issues = [x for x in checked['issues'] if x['field']==field]
        if field=='model':
            item=next(x for x in plan['items'] if x['field']=='model' and 'status' in x)
            if choices.get('model'): sources['model']='confirmed'
            if (issues or item.get('metadata_conflict')) and not choices.get('model'): raise Stop('模型冲突需要明确选择')
            continue
        choice=choices.get(field)
        if issues and not isinstance(choice,dict): raise Stop('元数据缺失或冲突需明确选择：'+field)
        if choice is None: continue
        value = choice.get('value')
        if field=='title':
            if not valid_title(value): raise Stop('确认标题非法')
            title = value
        else:
            value = value if value is not None else checked['generated_at_raw']
            tz = choice.get('timezone',confirmation.get('timezone'))
            if tz:
                if not isinstance(tz,str) or not re.fullmatch(r'[+-](?:[01]\d|2[0-3]):[0-5]\d',tz): raise Stop('timezone 需要明确数字偏移')
                if not isinstance(value,str): raise Stop('缺失时间，需要给出 value')
                if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value): raise Stop('只有日期时还需明确时分秒')
                if re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}',value): value += ':00'
                value=value.replace(' ','T')+tz
            generated=utc_time(value)
            sources['time']='confirmed'
    return selected,title,generated,sources,new_terms


def request_hash(body, data):
    return digest(b'docs-upload/v1\n'+canonical({k:v for k,v in body.items() if k!='html_base64'})+b'\n'+data)


def checked_fd(path, flags):
    fd = os.open(path,flags|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600 or info.st_uid!=os.geteuid():
        os.close(fd)
        raise OSError('私有文件权限或类型不符')
    return fd


@contextmanager
def file_lock(path):
    import fcntl
    fd=checked_fd(path,os.O_RDWR)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)


def append_event(home, meta, status, now, error=None, success=None):
    if status not in STATUSES: raise ValueError('manifest 状态不在闭集')
    root=home/'docs'
    normalized=success['normalized_metadata'] if success else {}
    body=meta['metadata']
    def term(kind):
        value=normalized.get(kind)
        if value is not None: return {'id':value['id'],'name':value['name']}
        return None
    docid=success['id'] if success else None
    row=dict(schema_version=1,event_id=secrets.token_hex(16),client_upload_id=meta['client_upload_id'],
             attempt=max(1,meta['attempt']),status=status,docid=docid,title=normalized.get('title',body['title']),
             project=term('project'),type=term('type'),generated_at=normalized.get('generated_at',body['generated_at']),
             generated_model=term('model'),pending_reasons=[] if success else ['尚未取得可信服务端规范化结果和 docid'],
             permission=body['acl'],backup_filename=docid+'.html' if status in ('uploaded','reconciled') else '.pending/'+meta['client_upload_id']+'.html',
             uploaded_at=success['uploaded_at'] if success else None,attempted_at=stamp(now),sha256=body['sha256'],
             idempotency_key=meta['idempotency_key'],request_hash=meta['request_hash'],error_code=error)
    with file_lock(root/'.manifest.lock'):
        fd=checked_fd(root/'manifest.jsonl',os.O_RDWR|os.O_APPEND)
        with os.fdopen(fd,'a+b') as stream:
            stream.seek(0,os.SEEK_END)
            if stream.tell():
                stream.seek(-1,os.SEEK_END)
                if stream.read(1)!=b'\n': raise OSError('manifest 尾行不完整')
            stream.write(canonical(row)+b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        sync_dir(root)
    meta['manifest_status']=status


def prepare(home, args, body, data, client_id, now, backup_hook, origin, confirmed_choices=None):
    root=home/'docs'
    pending=root/'.pending'
    raw=canonical(body)
    for item in body['confirmation']['new_terms']:
        seconds=datetime.fromisoformat(item['confirmed_at'].replace('Z','+00:00')).timestamp()
        if not now-7*86400 <= seconds <= now+300:
            raise Stop('新词确认时间超出过去 7 天至未来 5 分钟窗口；需要重新确认')
    if len(raw)>MAX_BODY: raise Stop('JSON 体超过 14046552 字节；解码后 HTML 上限 10 MiB')
    meta=dict(client_upload_id=client_id,idempotency_key=secrets.token_hex(16),request_hash=request_hash(body,data),
              attempt=0,retries={'processing':0,'rate':0,'transient':0,'internal':0},phase='prepared',
              metadata={k:v for k,v in body.items() if k!='html_base64'},source_path=str(Path(args.html).absolute()),
              origin=origin,manifest_status=None,parameters=vars(args).copy(),confirmed_choices=confirmed_choices or {})
    path=pending/(client_id+'.html')
    try:
        secure_directory(root,True)
        secure_directory(pending,True)
        exclusive_write(path,data,backup_hook)
        if digest(private_read(path))!=body['sha256']: raise OSError('预备份哈希不符')
        exclusive_write(pending/(client_id+'.request.json'),raw)
        exclusive_write(pending/(client_id+'.meta.json'),canonical(meta))
    except OSError: raise Stop('本地预备份失败；网络未发送，先修磁盘或权限',5,'prepared_only',backup=path if path.exists() else None) from None
    try: append_event(home,meta,'prepared',now)
    except OSError: raise Stop('预备份在，manifest 没写上；日志没写上，网络未发送',5,'prepared_only',backup=path) from None
    return meta,raw


def meta_path(home, meta):
    return home/'docs/.pending'/(meta['client_upload_id']+'.meta.json')


def persist(home, meta):
    replace_json(meta_path(home,meta),meta)


def valid_success(value, origin, sha, token):
    try:
        if (not isinstance(value,dict) or not isinstance(value.get('id'),str) or not HEX_ID.fullmatch(value['id'])
                or value.get('url')!=origin+'/'+value['id']+'.html' or value.get('sha256')!=sha
                or type(value.get('acl_version')) is not int or value['acl_version']<1
                or not isinstance(value.get('normalized_metadata'),dict) or (token and token in canonical(value).decode())): return False
        utc_time(value['uploaded_at'])
        normalized=value['normalized_metadata']
        if not valid_title(normalized.get('title')): return False
        utc_time(normalized['generated_at'])
        sources=normalized['metadata_source']
        if set(sources)!={'time','model'} or any(v not in ('html_meta','explicit','confirmed') for v in sources.values()): return False
        for kind in ('project','type','model'):
            term=normalized[kind]
            if not isinstance(term,dict) or not SAFE_ID.fullmatch(term['id']): return False
            normalize(term['name'])
        return True
    except (KeyError,TypeError,ValueError,OverflowError): return False


def outcome_report(home, meta, status, external, todo='无', success=None):
    backup=home/'docs/.pending'/(meta['client_upload_id']+'.html')
    if status=='uploaded': backup=home/'docs'/(success['id']+'.html')
    return report(status,home=home,external=external,todo=todo,backup=backup,
                  manifest_status=meta.get('manifest_status'),url=success['url'] if success else None,docid=success['id'] if success else None)


def finish_local(home, meta, external, now, recovering=False):
    success=meta['success']
    target=home/'docs'/(success['id']+'.html')
    pending=home/'docs/.pending'/(meta['client_upload_id']+'.html')
    retained=False
    try:
        with file_lock(home/'docs/.finalize.lock'):
            if target.exists() or target.is_symlink():
                if digest(private_read(target))!=success['sha256']: raise OSError('同名目标哈希冲突')
                retained=pending.exists()
            else:
                if digest(private_read(pending))!=success['sha256']: raise OSError('pending 哈希不符')
                os.replace(pending,target)
                if digest(private_read(target))!=success['sha256']: raise OSError('目标哈希不符')
                sync_dir(target.parent)
        append_event(home,meta,'reconciled' if recovering else 'uploaded',now,success=success)
        meta['phase']='complete'
        persist(home,meta)
        todo='pending 仍在且未覆盖已有相同哈希副本：'+str(pending) if retained else '无'
        return 0,outcome_report(home,meta,'uploaded',external,todo,success)
    except OSError:
        meta['phase']='local_finalize_failed'
        try:
            append_event(home,meta,'local_finalize_failed',now,success=success,error='local_finalize_failed')
            persist(home,meta)
            detail=''
        except OSError: detail='；日志没写上，不声称已入账'
        return 4,outcome_report(home,meta,'local_finalize_failed',external,'远程已成功、本地待修复；检查目标 '+str(target)+' 的哈希、权限与磁盘，禁止覆盖冲突或新开逻辑上传'+detail,success)


class TaxonomyPause(Exception):
    def __init__(self, details): self.details=details


def failure_event(home, meta, status, now, error):
    try:
        append_event(home,meta,status,now,error=error)
        meta['phase']=status
        persist(home,meta)
        return ''
    except OSError: return '；日志没写上，不声称本次事件已入账'


def retry_after(headers):
    value=next((v for k,v in headers.items() if k.lower()=='retry-after'),None)
    return min(120,int(value)) if isinstance(value,str) and re.fullmatch(r'[0-9]+',value) else 1


def send_upload(home, meta, raw, request, token, source, external, clock, sleep, rng, recovering=False):
    while True:
        if meta.get('sleep_until',0)>clock():
            sleep(min(120.25,meta['sleep_until']-clock()))
        meta.pop('sleep_until',None)
        meta['attempt']+=1
        meta['phase']='sending'
        try: persist(home,meta)
        except OSError:
            code=4 if meta.get('remote_uncertain') else 5
            status='remote_unknown' if code==4 else 'prepared_only'
            return code,outcome_report(home,meta,status,external,'本次网络未发送；先修本地元数据或磁盘，不新开逻辑上传')
        try:
            code,value,headers=request('POST',meta['origin']+'/api/v1/documents',token,raw,meta['idempotency_key'])
        except (OSError,ValueError,urllib.error.URLError,http.client.HTTPException):
            code,value,headers=0,None,{}
        if code in (200,201) and valid_success(value,meta['origin'],meta['metadata']['sha256'],token):
            meta['success']=value
            meta['phase']='remote_success'
            try: persist(home,meta)
            except OSError:
                return 4,outcome_report(home,meta,'local_finalize_failed',external,'远程已成功、本地待修复；日志没写上，保留 docid，禁止新建第二篇',value)
            return finish_local(home,meta,external,clock(),recovering)
        reason=value.get('reason') if isinstance(value,dict) else None
        allowed={'taxonomy_confirmation_required','taxonomy_invalid','html_rejected','hash_mismatch','acl_invalid','upload_in_progress','idempotency_conflict','quota_exceeded','storage_unavailable','busy'}
        reason=reason if reason in allowed else None
        error=str(code)+((':'+reason) if reason else '')
        unknown=code==0 or 300<=code<400 or code>=500 or code in (200,201) or (code==409 and reason=='upload_in_progress')
        if unknown: meta['remote_uncertain']=True
        status='remote_unknown' if meta.get('remote_uncertain') else 'upload_failed'
        suffix=failure_event(home,meta,status,clock(),error)
        if suffix: return 4,outcome_report(home,meta,status,external,'先修磁盘，保留原请求与原幂等键'+suffix)
        bucket=limit=None
        delay=0
        if code==409 and reason=='upload_in_progress':
            bucket,limit='processing',3
            delay=retry_after(headers)+rng(0,.25)
        elif code==429:
            bucket,limit='rate',3
            delay=retry_after(headers)+rng(0,.25)
        elif code in (0,503):
            bucket,limit='transient',5
            index=meta['retries']['transient']
            delay=rng(0,min(30,2**index))
            if code==503 and reason=='busy' and index==0: delay=max(1,delay)
        elif code==500:
            bucket,limit='internal',1
            delay=rng(0,1)
        if bucket is not None and meta['retries'][bucket]<limit:
            meta['retries'][bucket]+=1
            meta['sleep_until']=clock()+delay
            try: persist(home,meta)
            except OSError: return 4,outcome_report(home,meta,status,external,'重试预算未能落盘；日志没写上，停止')
            # Sleep exactly once per retry under an injected clock as well.
            sleep(delay)
            meta.pop('sleep_until',None)
            try: persist(home,meta)
            except OSError: return 4,outcome_report(home,meta,status,external,'重试状态未能落盘，停止')
            continue
        if code==422 and (reason=='taxonomy_confirmation_required' or reason=='taxonomy_invalid' and isinstance(value.get('details'),dict) and value['details'].get('successor_id')):
            raise TaxonomyPause(value.get('details',{}))
        unknown=bool(meta.get('remote_uncertain'))
        todo='用原请求与原幂等键恢复，不要新开逻辑上传' if unknown else '停止并按 references/protocol.md 的错误恢复表处理 '+error
        if code in (401,403):
            todo='凭证来源是'+source+'，该来源无效，不回退。'+GUIDANCE
            if code==403: todo+=' 403 可能是 scope 不足、Origin 不符或 public 未授权，对外同形，不能猜测或改成 private。'
        if code==413: todo='解码后 HTML 上限 10 MiB，JSON 体上限 14046552'
        if reason=='quota_exceeded':
            scope=value.get('details',{}).get('scope') if isinstance(value.get('details'),dict) else None
            todo='配额已用尽'+('（'+scope+'）' if scope in ('user','site') else '')+'；停止，不重试'
        if reason=='idempotency_conflict': todo='幂等键绑定不同正文；停止，不重试、不新键'
        return (4 if unknown or reason=='idempotency_conflict' else 2),outcome_report(home,meta,status,external,todo)


def merge_server_items(plan, details, confirmed_choices=None):
    # Only taxonomy choice fields, never arbitrary HTML or error bodies.
    clean={}
    if isinstance(details,dict):
        if details.get('field') in ('project','type','model','project.category'): clean['field']=details['field']
        if isinstance(details.get('successor_id'),str) and SAFE_ID.fullmatch(details['successor_id']): clean['successor_id']=details['successor_id']
        clean['items']=[]
        for raw in details.get('items',[]) if isinstance(details.get('items',[]),list) else []:
            if not isinstance(raw,dict) or raw.get('field') not in ('project','type','model','project.category'): continue
            item={k:raw[k] for k in ('field','kind','input','normalized') if isinstance(raw.get(k),str) and len(raw[k])<=200}
            item['candidates']=[]
            candidates=raw.get('candidates',[])
            for candidate in candidates[:5] if isinstance(candidates,list) else []:
                if (not isinstance(candidate,dict) or not isinstance(candidate.get('id'),str)
                        or not SAFE_ID.fullmatch(candidate['id']) or not isinstance(candidate.get('name'),str)
                        or len(candidate['name'])>80): continue
                try: display,_=normalize(candidate['name'])
                except ValueError: continue
                if 'score' in candidate and (type(candidate['score']) not in (int,float) or not 0<=candidate['score']<=1): continue
                safe=dict(id=candidate['id'],name=display)
                if 'score' in candidate: safe['score']=candidate['score']
                item['candidates'].append(safe)
            clean['items'].append(item)
    plan['server_details']=clean
    changes=clean.get('items',[])
    if clean.get('field') or clean.get('successor_id'):
        candidates=[dict(id=clean['successor_id'],name='继任 id，请确认')] if clean.get('successor_id') else []
        changes=changes+[dict(field=clean.get('field','project'),candidates=candidates)]
    for change in changes:
        item=next((x for x in plan['items'] if x['field']==change['field'] and 'status' in x),None)
        if item is not None:
            item.update(status='unresolved',reason='服务端要求重新确认；不自动 create_confirmed',server_required=True,candidates=change.get('candidates',[]))
    affected={change['field'] for change in changes} or {'project','type','model','project.category'}
    if 'project' in affected: affected.add('project.category')
    retained={field:choice for field,choice in (confirmed_choices or {}).items() if field not in affected}
    if retained:
        # Covered by the new plan hash and revalidated against fresh taxonomy on acceptance.
        plan['confirmed_choices']=retained
    else:
        plan.pop('confirmed_choices',None)
    # Even empty server items require a new whole-order accepted confirmation.
    plan.pop('plan_sha256',None)
    plan['plan_sha256']=digest(canonical(plan))


def load_pending(home, client_id, origin):
    try:
        secure_directory(home/'docs')
        secure_directory(home/'docs/.pending')
        meta=json.loads(private_read(home/'docs/.pending'/(client_id+'.meta.json')))
        raw=private_read(home/'docs/.pending'/(client_id+'.request.json'))
        body=json.loads(raw)
        data=base64.b64decode(body['html_base64'],validate=True)
        if (meta['client_upload_id']!=client_id or meta['origin']!=origin
                or not HEX_ID.fullmatch(meta['idempotency_key']) or len(raw)>MAX_BODY
                or request_hash(body,data)!=meta['request_hash'] or digest(data)!=body['sha256']
                or {k:v for k,v in body.items() if k!='html_base64'}!=meta['metadata']): raise ValueError()
        if meta.get('phase') not in ('remote_success','local_finalize_failed','complete'):
            if digest(private_read(home/'docs/.pending'/(client_id+'.html')))!=body['sha256']: raise ValueError()
        if meta.get('success') and not valid_success(meta['success'],origin,body['sha256'],None): raise ValueError()
        return meta,raw,data
    except (OSError,ValueError,TypeError,KeyError,RecursionError): raise Stop('恢复档案缺失、权限不符或原请求哈希不符；停止，不新开逻辑上传',4,'remote_unknown') from None


def run(argv=None, *, env=None, out=None, clock=time.time, sleep=time.sleep, rng=random.uniform, transport=None, backup_hook=None):
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    home = Path(env.get('HOME') or Path.home())
    token = None
    external = None
    streamed = False
    identity_output = None
    meta = None
    def emit(value):
        if isinstance(value,str):
            if token: value=value.replace(token,'[已隐藏]')
            if streamed:
                status=value.splitlines()[0].split(':',1)[-1].strip()
                value={'status':status,'report':value,'self_check':identity_output}
            else:
                print(value,file=out)
                return
        text=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if token: text=text.replace(token,'[已隐藏]')
        if streamed:
            text=json.dumps({k:v for k,v in value.items() if k!='external_links'},ensure_ascii=False,allow_nan=False)[1:]
            if token: text=text.replace(token,'[已隐藏]')
        print(text,file=out,flush=True)
    def begin_submission():
        nonlocal streamed
        print('{"external_links":'+json.dumps(external,ensure_ascii=False)+',',end='',file=out,flush=True)
        streamed=True
    try:
        args = arguments(argv)
        origin = origin_from(env)
        if os.name=='nt': raise Stop('凭证引导尚未在本 PowerShell 版本验证')
        request = transport or http_request
        if args.resume and not args.dry_run:
            secure_directory(home/'docs')
            secure_directory(home/'docs/.pending')
            with file_lock(home/'docs/.pending'/(args.resume+'.lock')):
                meta,raw,data=load_pending(home,args.resume,origin)
                external=inspect_html(data)['external_links']
                if meta.get('success'):
                    code,text=finish_local(home,meta,external,clock(),True)
                    emit(text)
                    return code
                try:
                    token,source=credential(env,home)
                    identity=self_check(request,origin,token,source)
                    identity_output=identity
                    get_taxonomy(request,origin,token)
                except Stop as error:
                    if meta['attempt']>0:
                        suffix=failure_event(home,meta,'remote_unknown',clock(),error.error_code or 'credential_check')
                        raise Stop(str(error)+'；上一回结果仍未知，用原请求恢复'+suffix,4,'remote_unknown') from None
                    raise
                begin_submission()
                try:
                    code,text=send_upload(home,meta,raw,request,token,source,external,clock,sleep,rng,True)
                except TaxonomyPause as pause:
                    original_args=argparse.Namespace(**meta['parameters'])
                    checked=inspect_html(data,title=original_args.title,generated_at=original_args.generated_at,model_name=original_args.model_name,model_id=original_args.model_id)
                    refreshed=get_taxonomy(request,origin,token)
                    plan,_=make_plan(original_args,checked,refreshed,data,identity,home,secrets.token_hex(16))
                    if token in canonical(pause.details).decode(): raise Stop('服务端错误含凭证内容，停止且不回显',4,'remote_unknown')
                    merge_server_items(plan,pause.details,meta.get('confirmed_choices'))
                    save_plan(home,plan)
                    emit(plan)
                    return 3
            emit(text)
            return code
        data = read_html(args.html)
        checked = inspect_html(data,title=args.title,generated_at=args.generated_at,model_name=args.model_name,model_id=args.model_id)
        external = checked['external_links']
        token, source = credential(env,home)
        request = transport or http_request
        identity = self_check(request,origin,token,source)
        identity_output=identity
        if identity['user_id'] in args.acl_user: raise Stop('ACL 不能包含上传者自己的 user id')
        taxonomy = get_taxonomy(request,origin,token)
        if token in canonical([identity,taxonomy,checked,vars(args)]).decode() or token.encode() in data:
            raise Stop('输入或响应包含凭证内容，停止且不回显')
        confirmation = saved = None
        client_id = secrets.token_hex(16)
        if args.confirm_file and not args.dry_run:
            confirmation, saved = confirmation_input(args.confirm_file,home)
            client_id = saved['client_upload_id']
        plan, explicit = make_plan(args,checked,taxonomy,data,identity,home,client_id)
        if args.dry_run:
            plan['status']='dry_run'
            plan['report']=report('dry_run',home=home,external=external)
            plan.pop('plan_sha256')
            plan['plan_sha256']=digest(canonical(plan))
            emit(plan)
            return 0
        if saved and 'server_details' in saved:
            merge_server_items(plan,saved['server_details'],saved.get('confirmed_choices'))
            explicit=True
        if saved and saved['plan_sha256']!=plan['plan_sha256']:
            raise Stop('当前 HTML、参数、身份或 taxonomy 与暂停计划不同；请重新取得并确认清单')
        if explicit and confirmation is None:
            save_plan(home,plan)
            emit(plan)
            return 3
        if confirmation:
            selected,title,generated,sources,new_terms = select_confirmed(args,checked,taxonomy,plan,confirmation,clock())
            choices=confirmation_choices(plan,confirmation)
            if 'generated_at' in choices: choices['generated_at']={'value':generated}
            if 'title' in choices: choices['title']={'value':title}
        else:
            selected={x['kind']:x['selector'] for x in plan['items'] if x.get('status')=='matched'}
            title,generated=checked['title'],checked['generated_at']
            sources=dict(time=checked['time_source'],model=checked['model_source'])
            new_terms=[]
            choices={}
        body=dict(title=title,project=selected['project'],type=selected['type'],model=selected['model'],
                  generated_at=generated,metadata_source=sources,taxonomy_version=taxonomy['version'],
                  confirmation=dict(mode='explicit' if explicit or confirmation else 'existing_default',new_terms=new_terms),
                  acl=acl_for(args),sha256=digest(data),html_base64=base64.b64encode(data).decode('ascii'))
        if confirmation: client_id=secrets.token_hex(16)
        if token in canonical(body).decode(): raise Stop('提交元数据包含凭证内容，停止且不回显')
        meta,raw=prepare(home,args,body,data,client_id,clock(),backup_hook,origin,choices)
        begin_submission()
        with file_lock(home/'docs/.pending'/(client_id+'.lock')):
            try:
                code,text=send_upload(home,meta,raw,request,token,source,external,clock,sleep,rng)
            except TaxonomyPause as pause:
                taxonomy=get_taxonomy(request,origin,token)
                plan,_=make_plan(args,checked,taxonomy,data,identity,home,secrets.token_hex(16))
                if token in canonical(pause.details).decode(): raise Stop('服务端错误含凭证内容，停止且不回显',4,'remote_unknown')
                merge_server_items(plan,pause.details,meta.get('confirmed_choices'))
                save_plan(home,plan)
                # stdout must remain a single JSON on exit 3; notice is included in plan.
                emit(plan)
                return 3
        emit(text)
        return code
    except Stop as error:
        text = (outcome_report(home,meta,error.status,external,str(error)) if meta is not None else report(error.status,home=home,external=external,todo=str(error),backup=error.backup))
        emit(text)
        return error.code
    except (OSError,ValueError,TypeError,KeyError):
        if meta is not None:
            status='remote_unknown' if meta['attempt']>0 else 'prepared_only'
            emit(outcome_report(home,meta,status,external,'本地文件或响应不合法，停止；保留原请求，不新开逻辑上传'))
            return 4 if meta['attempt']>0 else 5
        emit(report('upload_failed',home=home,external=external,todo='本地文件或响应不合法，停止；未输出原始异常'))
        return 2
    finally:
        del token


if __name__=='__main__':
    raise SystemExit(run())
