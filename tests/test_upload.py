import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import upload
from fake_server import server
from test_html import page

class Client(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.html = self.home / 'input.html'
        self.html.write_bytes(page())
        self.args = [str(self.html), '--project-name', 'Docs', '--type-name', 'Design', '--non-interactive']
    def invoke(self, state, *extra, env_extra=None, **hooks):
        env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
        env.update(env_extra or {})
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = upload.run(self.args + list(extra), env=env, out=out, transport=state.transport, sleep=lambda n: state.sleep_events.append(n), **hooks)
        result = out.getvalue()
        self.assertNotIn('test-key-not-secret', result + err.getvalue())
        return code, result
    def test_01_me_and_dry_run(self):
        with server() as state:
            code, text = self.invoke(state, '--dry-run')
            self.assertEqual(code, 0)
            self.assertEqual(len(state.posts), 0)
            self.assertIn('未提交，没有 docid', text)
            self.assertIn('服务端未返回 Key 有效期', text)
            self.assertEqual(len(state.gets), 2)
            self.assertFalse((self.home/'docs/.pending').exists())
    def test_01_me_scope_and_auth(self):
        for status in (200,401,403):
            with server() as state:
                state.me_status = status
                state.me['scopes'] = ['me:read']
                code, text = self.invoke(state)
                self.assertEqual(code, 2)
                self.assertEqual(state.gets, ['/api/v1/me'])
                self.assertEqual(state.posts, [])
                if status == 401: self.assertIn('来源是环境变量', text)
    def test_01_credential_file(self):
        directory = self.home/'.config/docs'
        directory.mkdir(parents=True,mode=0o700)
        keyfile = directory/'api-key'
        keyfile.write_text('must-not-read-or-print')
        keyfile.chmod(0o644)
        with server() as state:
            code, text = self.invoke(state, env_extra={'DOCS_API_KEY':None})
            self.assertEqual(code, 2)
            self.assertEqual(state.gets, [])
            self.assertNotIn('must-not-read-or-print', text)
        keyfile.chmod(0o600)
        keyfile.write_text('test-key-not-secret\n')
        with server() as state:
            code, text = self.invoke(state, '--dry-run', env_extra={'DOCS_API_KEY':None})
            self.assertEqual(code, 0)
    def test_01_origin_and_redirect(self):
        with server() as state:
            for origin in ('https://evil.invalid', 'http://localhost.evil.invalid', state.origin+'/path', state.origin+'/', 'http://user@localhost', 'http://localhost?x=1'):
                code, text = self.invoke(state, env_extra={'DOCS_ORIGIN':origin})
                self.assertEqual(code, 2)
            state.redirect = 'https://must-not-connect.invalid/'
            code, text = self.invoke(state)
            self.assertEqual(code, 2)
            self.assertEqual(len(state.gets), 1)
    def test_02_force_and_plan_digest(self):
        with server() as state:
            code, text = self.invoke(state, '--force-confirm')
            self.assertEqual(code, 3)
            plan = json.loads(text)
            self.assertEqual(plan['status'], 'needs_confirmation')
            expected = hashlib.sha256(json.dumps({k:v for k,v in plan.items() if k!='plan_sha256'},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            self.assertEqual(plan['plan_sha256'], expected)
            self.assertEqual(state.posts, [])
            file = self.home/'confirm.json'
            file.write_text(json.dumps(dict(client_upload_id=plan['client_upload_id'], plan_sha256='0'*64, accepted=True)))
            self.assertEqual(self.invoke(state, '--force-confirm','--confirm-file',str(file))[0], 2)
    def test_02_missing_and_approximate(self):
        self.html.write_bytes(page().replace(b'<meta name="plans:worker" content="ExampleModel">',b''))
        with server() as state:
            code,text = self.invoke(state)
            self.assertEqual(code, 3)
            plan = json.loads(text)
            self.assertTrue(any(x['field']=='model' for x in plan['items']))
            self.assertFalse((self.home/'docs/.pending').exists())
        self.html.write_bytes(page())
        with server() as state:
            self.args[self.args.index('Design')] = 'Designs'
            code,text = self.invoke(state)
            self.assertEqual(code, 3)
            plan = json.loads(text)
            item = next(x for x in plan['items'] if x['field']=='type')
            self.assertEqual(item['candidates'][0]['id'],'type_design')
    def test_02_bad_html_and_metadata(self):
        for data in (b'\xef\xbb\xbf'+page(),page()+b'\0',page()+b'\xff',b'<body></html>',b'<html>'):
            self.html.write_bytes(data)
            with server() as state:
                self.assertEqual(self.invoke(state)[0], 2)
                self.assertEqual(state.gets, [])
        self.html.write_bytes(page().replace(b'12:00:00+08:00',b'12:00:00'))
        with server() as state:
            code,text=self.invoke(state)
            self.assertEqual(code,3)
            self.assertEqual(json.loads(text)['status'],'needs_confirmation')
    def test_03_backup_hash_failure(self):
        def corrupt(path): path.write_bytes(b'corrupted')
        with server() as state:
            code,text=self.invoke(state,backup_hook=corrupt)
            self.assertEqual(code,5)
            self.assertEqual(state.posts,[])
    def test_03_manifest_prepared(self):
        with server() as state:
            self.invoke(state)
            manifest=self.home/'docs/manifest.jsonl'
            self.assertTrue(manifest.exists(), 'prepared manifest 尚未实现')
            rows=[json.loads(x) for x in manifest.read_text().splitlines()]
            required={'schema_version','event_id','client_upload_id','attempt','status','docid','title','project','type','generated_at','generated_model','pending_reasons','permission','backup_filename','uploaded_at','attempted_at','sha256','idempotency_key','request_hash','error_code'}
            self.assertEqual(set(rows[0]),required)
            self.assertEqual(rows[0]['status'],'prepared')
            self.assertEqual(manifest.stat().st_mode & 0o777,0o600)
            self.assertNotEqual(rows[0]['client_upload_id'],rows[0]['idempotency_key'])
    def test_03_manifest_cannot_write(self):
        root=self.home/'docs'
        root.mkdir(mode=0o700)
        (root/'manifest.jsonl').mkdir()
        with server() as state:
            code,text=self.invoke(state)
            self.assertEqual(code,5)
            self.assertEqual(state.posts,[])
            self.assertIn('manifest 没写上',text)
    def confirm(self, state, extra=(), choices=None):
        code,text=self.invoke(state,*extra)
        self.assertEqual(code,3)
        plan=json.loads(text)
        file=self.home/'confirm.json'
        file.write_text(json.dumps(dict(client_upload_id=plan['client_upload_id'],plan_sha256=plan['plan_sha256'],accepted=True,choices=choices or {})))
        return file
    def test_04_success_protocol_and_backup(self):
        before=self.html.read_bytes()
        with server() as state:
            code,text=self.invoke(state)
            self.assertEqual(code,0)
            body=state.posts[0]['body']
            self.assertEqual(base64.b64decode(body['html_base64'],validate=True),before)
            self.assertEqual(body['sha256'],hashlib.sha256(before).hexdigest())
            self.assertEqual(body['acl'],dict(visibility='private',users=[],groups=[]))
            self.assertEqual(body['generated_at'],'2026-10-15T04:00:00Z')
            self.assertEqual(body['metadata_source']['time'],'html_meta')
            self.assertEqual(body['confirmation'],dict(mode='existing_default',new_terms=[]))
            headers=state.posts[0]['headers']
            self.assertEqual(headers['Content-Type'],'application/json')
            self.assertEqual(headers['Origin'],state.origin)
            self.assertRegex(headers['Idempotency-Key'],r'^[A-Za-z0-9_-]{16,128}$')
            self.assertTrue(state.posts[0]['auth_ok'])
            self.assertEqual(self.html.read_bytes(),before)
            self.assertEqual((self.home/'docs'/(state.docid+'.html')).read_bytes(),before)
            rows=[json.loads(x) for x in (self.home/'docs/manifest.jsonl').read_text().splitlines()]
            self.assertEqual(rows[-1]['project']['name'],'Server project')
            self.assertEqual(rows[-1]['status'],'uploaded')
            semantic={k:v for k,v in body.items() if k!='html_base64'}
            encoded=json.dumps(semantic,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
            self.assertEqual(rows[0]['request_hash'],hashlib.sha256(b'docs-upload/v1\n'+encoded+b'\n'+before).hexdigest())
            for label in ('线上链接:','docid:','本地副本:','manifest:','待办:'): self.assertIn(label,text)
    def test_04_confirm_reuse_and_create(self):
        with server() as state:
            self.args[self.args.index('Design')]='Designs'
            file=self.confirm(state,choices={'type':{'action':'reuse','id':'type_design'}})
            self.assertEqual(self.invoke(state,'--confirm-file',str(file))[0],0)
            self.assertEqual(state.posts[-1]['body']['type'],{'id':'type_design'})
        with server() as state:
            self.args[self.args.index('Designs')]='NewType'
            file=self.confirm(state,choices={'type':{'action':'create','name':'NewType','create_confirmed':True}})
            self.assertEqual(self.invoke(state,'--confirm-file',str(file))[0],0)
            body=state.posts[-1]['body']
            self.assertEqual(body['type'],{'name':'NewType','create_confirmed':True})
            self.assertEqual(body['confirmation']['new_terms'][0]['normalized'],'newtype')
            self.assertEqual(body['confirmation']['mode'],'explicit')
    def test_04_alias_and_changed_title_new_keys(self):
        self.args[self.args.index('Design')]='draft'
        with server() as state:
            self.assertEqual(self.invoke(state)[0],0)
            self.assertEqual(state.posts[-1]['body']['type'],{'id':'type_design'})
            self.html.write_bytes(page(title='Changed'))
            self.assertEqual(self.invoke(state)[0],0)
            self.assertNotEqual(state.posts[0]['headers']['Idempotency-Key'],state.posts[1]['headers']['Idempotency-Key'])
            rows=[json.loads(x) for x in (self.home/'docs/manifest.jsonl').read_text().splitlines() if json.loads(x)['status']=='prepared']
            self.assertNotEqual(rows[0]['client_upload_id'],rows[1]['client_upload_id'])
    def test_04_local_conflict_and_local_only_resume(self):
        root=self.home/'docs'
        root.mkdir(mode=0o700)
        target=root/('a'*32+'.html')
        target.write_bytes(b'old')
        target.chmod(0o600)
        with server() as state:
            code,text=self.invoke(state)
            self.assertEqual(code,4)
            self.assertIn('远程已成功、本地待修复',text)
            self.assertEqual(target.read_bytes(),b'old')
            meta=next((root/'.pending').glob('*.meta.json'))
            client_id=json.loads(meta.read_text())['client_upload_id']
            target.unlink()
            self.assertEqual(self.invoke(state,'--resume',client_id)[0],0)
            self.assertEqual(len(state.posts),1)
            self.assertEqual(target.read_bytes(),self.html.read_bytes())
            rows=[json.loads(x) for x in (root/'manifest.jsonl').read_text().splitlines()]
            self.assertEqual(rows[-1]['status'],'reconciled')
    def test_04_untrusted_success_and_replay(self):
        with server() as state:
            state.bad_success=True
            self.assertEqual(self.invoke(state)[0],4)
            meta=next((self.home/'docs/.pending').glob('*.meta.json'))
            client_id=json.loads(meta.read_text())['client_upload_id']
            state.bad_success=False
            state.responses=[(200,'success',{})]
            self.assertEqual(self.invoke(state,'--resume',client_id)[0],0)
            self.assertEqual(state.posts[0]['raw'],state.posts[1]['raw'])
            self.assertEqual(state.posts[0]['headers']['Idempotency-Key'],state.posts[1]['headers']['Idempotency-Key'])
            self.assertEqual(len(list((self.home/'docs').glob('*.html'))),1)
    def test_05_retry_reason_matrix(self):
        for status,reason,count,exitcode in ((409,'upload_in_progress',4,4),(409,'idempotency_conflict',1,4),(409,'quota_exceeded',1,2),(429,None,4,2),(503,'storage_unavailable',6,4),(500,None,2,4),(401,None,1,2),(403,None,1,2)):
            with self.subTest(status=status,reason=reason), server() as state:
                body={'error':'error'}
                if reason: body['reason']=reason
                state.responses=[(status,body,{'Retry-After':'2'})]*8
                code,text=self.invoke(state,rng=lambda a,b:b)
                self.assertEqual(code,exitcode)
                self.assertEqual(len(state.posts),count)
                self.assertEqual(len({x['headers']['Idempotency-Key'] for x in state.posts}),1)
                self.assertEqual(len({x['raw'] for x in state.posts}),1)
                self.assertEqual(len(state.sleep_events),count-1)
                if count>1: self.assertTrue(state.posts[1]['sleeps'])
    def test_05_network_retry_original_body(self):
        with server() as state:
            original=state.transport
            calls=[]
            def flaky(method,url,token,body=None,idem=None):
                if method=='POST':
                    calls.append((body,idem))
                    if len(calls)==1: raise TimeoutError()
                return original(method,url,token,body,idem)
            if original is None:
                self.skipTest('仅传输桩注入异常；真实 HTTP 由 fixture 验证')
            state.transport=flaky
            code,text=self.invoke(state)
            self.assertEqual(code,0)
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[0],calls[1])
            self.assertEqual(len(state.sleep_events),1)
    def test_05_422_refresh_requires_new_confirmation(self):
        with server() as state:
            state.responses=[(422,{'error':'unprocessable','reason':'taxonomy_confirmation_required','details':{'items':[{'field':'type','kind':'type','input':'Design','normalized':'design','candidates':[]}]}},{})]
            code,text=self.invoke(state)
            self.assertEqual(code,3)
            plan=json.loads(text)
            self.assertEqual(plan['status'],'needs_confirmation')
            self.assertEqual(len(state.posts),1)
            self.assertEqual(len(state.gets),3)
            self.assertNotIn('create_confirmed',state.posts[0]['body']['type'])
            # Explicitly accepted refreshed plan must not silently reuse old approval.
            file=self.home/'confirm.json'
            file.write_text(json.dumps(dict(client_upload_id=plan['client_upload_id'],plan_sha256=plan['plan_sha256'],accepted=True,choices={'type':{'action':'reuse','id':'type_design'}})))
            self.assertEqual(self.invoke(state,'--confirm-file',str(file))[0],0)
            self.assertNotIn('create_confirmed',state.posts[-1]['body']['type'])
    def test_06_external_notice_before_post_and_json_pause(self):
        self.html.write_bytes(page('<script src="https://example.invalid/a.js"></script>'))
        with server() as state:
            out=io.StringIO()
            original=state.transport
            if original is None: self.skipTest('before-send observation uses local transport stub')
            def observe(method,url,token,body=None,idem=None):
                if method=='POST':
                    self.assertIn('默认无外网资源',out.getvalue())
                    self.assertIn('不拒绝上传',out.getvalue())
                return original(method,url,token,body,idem)
            env=dict(HOME=str(self.home),DOCS_API_KEY='test-key-not-secret',DOCS_ORIGIN=state.origin)
            code=upload.run(self.args,env=env,out=out,transport=observe,sleep=lambda n:None)
            self.assertEqual(code,0)
            self.assertIn('外链提示',out.getvalue())
            self.assertEqual(len(state.posts),1)
    def test_06_dry_run_ignores_confirm_and_no_backup(self):
        with server() as state:
            code,text=self.invoke(state,'--dry-run','--confirm-file',str(self.home/'absent.json'))
            self.assertEqual(code,0)
            self.assertEqual(state.posts,[])
            self.assertFalse((self.home/'docs').exists())
            self.assertEqual(json.loads(text)['status'],'dry_run')
    def test_06_parameter_boundaries(self):
        with server() as state:
            for extras in (['--unknown'],['--project-id','other'],['--visibility','restricted'],['--acl-user','someone@example.invalid'],['--acl-user','13800000000','--visibility','restricted'],['--visibility','restricted','--acl-user','user_demo']):
                with self.subTest(extras=extras):
                    self.assertEqual(self.invoke(state,*extras)[0],2)
                    self.assertEqual(state.posts,[])
            self.assertEqual(upload.run([],env={},out=io.StringIO()),2)
    def test_07_new_project_with_new_category_and_model(self):
        self.args[self.args.index('Docs')]='NewProject'
        self.args+=['--category-name','NewCategory']
        self.html.write_bytes(page().replace(b'ExampleModel',b'NewModel'))
        choices={field:{'action':'create','name':name,'create_confirmed':True} for field,name in [('project','NewProject'),('project.category','NewCategory'),('model','NewModel')]}
        with server() as state:
            file=self.confirm(state,choices=choices)
            code,text=self.invoke(state,'--confirm-file',str(file))
            self.assertEqual(code,0)
            body=state.posts[-1]['body']
            self.assertEqual(body['project']['category'],dict(name='NewCategory',create_confirmed=True))
            self.assertEqual({x['kind'] for x in body['confirmation']['new_terms']},{'project','project_category','model'})
            self.assertEqual(body['metadata_source']['model'],'confirmed')
    def test_07_timezone_confirmation_and_title(self):
        self.html.write_bytes(page().replace(b'2026-10-15T12:00:00+08:00',b'2026-10-15 12:00'))
        with server() as state:
            file=self.confirm(state,choices={'generated_at':{'timezone':'+08:00'}})
            self.assertEqual(self.invoke(state,'--confirm-file',str(file))[0],0)
            self.assertEqual(state.posts[-1]['body']['generated_at'],'2026-10-15T04:00:00Z')
            self.assertEqual(state.posts[-1]['body']['metadata_source']['time'],'confirmed')
    def test_07_invalid_worker_is_clarification(self):
        self.html.write_bytes(page().replace(b'ExampleModel',b'bad&#8203;worker'))
        with server() as state:
            code,text=self.invoke(state,'--model-id','model_example')
            self.assertEqual(code,3)
            self.assertEqual(state.posts,[])
    def test_07_recovery_auth_stays_unknown(self):
        with server() as state:
            state.bad_success=True
            self.assertEqual(self.invoke(state)[0],4)
            meta=next((self.home/'docs/.pending').glob('*.meta.json'))
            client_id=json.loads(meta.read_text())['client_upload_id']
            state.me_status=401
            code,text=self.invoke(state,'--resume',client_id)
            self.assertEqual(code,4)
            rows=[json.loads(x) for x in (self.home/'docs/manifest.jsonl').read_text().splitlines()]
            self.assertEqual(rows[-1]['status'],'remote_unknown')
            self.assertEqual(rows[-1]['error_code'],'401')
    def test_07_me_notes_on_success(self):
        with server() as state:
            code,text=self.invoke(state)
            self.assertEqual(code,0)
            self.assertIn('服务端未返回 Key 有效期',text)
            self.assertIn('服务端未返回上传限制',text)
    def test_07_untrusted_recovery_success_path(self):
        with server() as state:
            self.assertEqual(self.invoke(state)[0],0)
            path=next((self.home/'docs/.pending').glob('*.meta.json'))
            meta=json.loads(path.read_text())
            meta['success']['id']='../../bad'
            path.write_text(json.dumps(meta))
            code,text=self.invoke(state,'--resume',meta['client_upload_id'])
            self.assertEqual(code,4)
            self.assertEqual(len(state.posts),1)
    def test_07_file_and_env_credential_boundaries(self):
        directory=self.home/'.config/docs'
        directory.mkdir(mode=0o700,parents=True)
        keyfile=directory/'api-key'
        keyfile.write_text('test-key-not-secret')
        for mode in (0o400,0o640,0o644,0o700):
            keyfile.chmod(mode)
            with server() as state:
                self.assertEqual(self.invoke(state,env_extra={'DOCS_API_KEY':None})[0],2)
                self.assertEqual(state.gets,[])
        keyfile.chmod(0o600)
        with server() as state:
            state.me_status=401
            self.assertEqual(self.invoke(state)[0],2)
            self.assertEqual(len(state.gets),1)
        with server() as state:
            self.assertEqual(self.invoke(state,env_extra={'DOCS_API_KEY':''})[0],2)
            self.assertEqual(state.gets,[])
    def test_07_symlink_input_and_backup_rejected(self):
        link=self.home/'link.html'
        link.symlink_to(self.html)
        self.args[0]=str(link)
        with server() as state:
            self.assertEqual(self.invoke(state)[0],2)
            self.assertEqual(state.gets,[])
        self.args[0]=str(self.html)
        (self.home/'docs').symlink_to(self.home)
        with server() as state:
            self.assertEqual(self.invoke(state)[0],5)
            self.assertEqual(state.posts,[])
    def test_08_manifest_failure_after_success_local_resume(self):
        original=upload.append_event
        def fail_uploaded(home,meta,status,*args,**kwargs):
            if status=='uploaded': raise OSError('injected disk failure')
            return original(home,meta,status,*args,**kwargs)
        with server() as state:
            with patch('upload.append_event',side_effect=fail_uploaded):
                code,text=self.invoke(state)
            self.assertEqual(code,4)
            self.assertIn('远程已成功、本地待修复',text)
            meta=next((self.home/'docs/.pending').glob('*.meta.json'))
            client_id=json.loads(meta.read_text())['client_upload_id']
            code,text=self.invoke(state,'--resume',client_id)
            self.assertEqual(code,0)
            self.assertEqual(len(state.posts),1)
            self.assertEqual(json.loads((self.home/'docs/manifest.jsonl').read_text().splitlines()[-1])['status'],'reconciled')
    def test_08_retry_budget_persists(self):
        with server() as state:
            state.responses=[(429,{}, {'Retry-After':'0'})]*10
            self.assertEqual(self.invoke(state)[0],2)
            meta=next((self.home/'docs/.pending').glob('*.meta.json'))
            saved=json.loads(meta.read_text())
            self.assertEqual(saved['retries']['rate'],3)
            self.assertEqual(self.invoke(state,'--resume',saved['client_upload_id'])[0],2)
            self.assertEqual(len(state.posts),5)
            self.assertEqual(len({x['raw'] for x in state.posts}),1)
            self.assertEqual(json.loads(meta.read_text())['attempt'],5)
    def test_08_expired_confirmation_before_first_post(self):
        with server() as state:
            self.args[self.args.index('Design')]='NewType'
            file=self.confirm(state,choices={'type':{'action':'create','name':'NewType','create_confirmed':True}})
            times=[1700000000,1700000000+8*86400]
            code,text=self.invoke(state,'--confirm-file',str(file),clock=lambda:times.pop(0) if len(times)>1 else times[0])
            self.assertEqual(code,2)
            self.assertEqual(state.posts,[])
    def test_08_prepared_only_report_keeps_pending_path(self):
        def corrupt(path): path.write_bytes(b'corrupt')
        with server() as state:
            code,text=self.invoke(state,backup_hook=corrupt)
            pending=next((self.home/'docs/.pending').glob('*.html'))
            self.assertEqual(code,5)
            self.assertIn(str(pending),text)
    def test_09_partial_http_response_uses_same_request_retry(self):
        import http.client
        with server() as state:
            original=state.transport
            if original is None: self.skipTest('HTTP exception injection uses local stub')
            calls=[]
            def partial(method,url,token,body=None,idem=None):
                if method=='POST':
                    calls.append((body,idem))
                    if len(calls)==1: raise http.client.IncompleteRead(b'partial',1)
                return original(method,url,token,body,idem)
            state.transport=partial
            code,text=self.invoke(state)
            self.assertEqual(code,0)
            self.assertEqual(calls[0],calls[1])
    def test_10_backup_fault_hook_precedes_fsync(self):
        events=[]
        real_fsync=os.fsync
        def record(fd):
            events.append('fsync')
            return real_fsync(fd)
        def corrupt(path):
            events.append('hook')
            path.write_bytes(b'partial-pending')
        with server() as state, patch('upload.os.fsync',side_effect=record):
            code,text=self.invoke(state,backup_hook=corrupt)
            self.assertEqual(code,5)
            self.assertEqual(state.posts,[])
            self.assertEqual(events[0],'hook')
