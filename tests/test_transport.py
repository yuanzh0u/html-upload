import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bin'))
import upload

class Wire(unittest.TestCase):
    def test_urllib_builds_bounded_exact_body_without_cookie_or_redirect(self):
        seen={}
        class Response(io.BytesIO):
            code=200
            headers={'Content-Type':'application/json'}
        class Opener:
            def open(self,request,timeout):
                seen['request']=request
                seen['timeout']=timeout
                return Response(b'{"ok":true}')
        def build(*handlers):
            seen['handlers']=handlers
            return Opener()
        with patch('upload.urllib.request.build_opener',side_effect=build):
            code,value,headers=upload.http_request('POST','http://127.0.0.1:18080/api/v1/documents','test-key-not-secret',b'{"title":"a"}','b'*32)
        self.assertEqual(code,200)
        self.assertEqual(value,{'ok':True})
        request=seen['request']
        self.assertEqual(seen['timeout'],10)
        self.assertEqual(request.data,b'{"title":"a"}')
        self.assertEqual(request.get_header('Content-length'),str(len(request.data)))
        self.assertEqual(request.get_header('Content-type'),'application/json')
        self.assertIsNone(request.get_header('Cookie'))
        self.assertIsNone(request.get_header('Transfer-encoding'))
        self.assertTrue(any(isinstance(x,upload.NoRedirect) for x in seen['handlers']))
        proxies=next(x for x in seen['handlers'] if isinstance(x,urllib.request.ProxyHandler))
        self.assertEqual(proxies.proxies,{})
        self.assertIsNone(upload.NoRedirect().redirect_request(request,None,302,'',{},'https://elsewhere.invalid/'))
