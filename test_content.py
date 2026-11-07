import base64
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

from article_store import Store
from cloud_api import handle
from cover_assets import validate_png, decode_upload, description
from cos_store import CosStore
from draft_service import Drafts
from markdown_render import render_markdown
from platform_preview import preview
from test_cloud import FakeCOS
from wechat_draft_probe import cover_png
from wechat_draft_probe import ProbeError
from article_store import Conflict
from wechat_client import WeChat


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.raw = cover_png()
        self.cover_id = validate_png(self.raw)

    def test_png_validation_and_integrity(self):
        self.assertEqual(decode_upload({'png_base64':base64.b64encode(self.raw).decode()}), (self.cover_id,self.raw))
        self.assertEqual(description(self.cover_id,self.raw)['width'],900)
        for bad in (b'', self.raw+b'x', self.raw[:-1], self.raw[:40]+b'broken'+self.raw[46:], b'<svg/>'):
            with self.assertRaises(ValueError): validate_png(bad)

    def test_large_dimensions_and_decompression_bomb_rejected(self):
        def chunk(kind, data):
            return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
        for width, content in [(1000000,b'x'),(900,b'\0'*(900*4+1)*501)]:
            header=struct.pack('!2I5B',width,500,8,6,0,0,0)
            raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',header)+chunk(b'IDAT',zlib.compress(content))+chunk(b'IEND',b'')
            with self.assertRaises(ValueError): validate_png(raw)

    def test_local_and_cos_cover_idempotency(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            local=Store(Path(directory)/'articles.sqlite3')
            cloud=CosStore(FakeCOS(),'example-123','dummy-app')
            for store in (local,cloud):
                store.put_cover(self.cover_id,self.raw)
                store.put_cover(self.cover_id,self.raw)
                self.assertEqual(store.get_cover(self.cover_id),self.raw)
                with self.assertRaises(ValueError): store.get_cover('../bad')

    def test_custom_cover_passes_to_wechat_and_changes_article_hash(self):
        store=CosStore(FakeCOS(),'example-123','dummy-app')
        store.put_cover(self.cover_id,self.raw)
        data={'title':'封面测试','markdown':'正文','cover_id':self.cover_id}
        article=store.save(data,preview(data))
        self.assertNotEqual(article['content_hash'], preview(dict(data,cover_id=''))['content_hash'])
        client=Mock(); client.upload_cover.return_value='thumb'
        service=Drafts(store,client)
        client.call.side_effect=[{'media_id':'draft'},{'news_item':[{'title':article['title'],'thumb_media_id':'thumb','content':service.content(article)}]}]
        self.assertEqual(service.sync(article['id'],article['content_hash'])['status'],'synced')
        client.upload_cover.assert_called_once_with(self.raw)

    def test_authenticated_cover_upload_read_and_article_association(self):
        store=CosStore(FakeCOS(),'example-123','dummy-app')
        headers={'Authorization':'Bearer '+'x'*40}
        def api(path,data=None):
            return handle({'httpMethod':'POST' if data is not None else 'GET','path':path,
                'headers':headers,'body':json.dumps(data) if data else ''},lambda:store)
        with patch.dict('os.environ',{'PLATFORM_API_KEY':'x'*40},clear=True):
            payload={'png_base64':base64.b64encode(self.raw).decode()}
            self.assertEqual(api('/api/covers/upload',payload)['statusCode'],200)
            self.assertEqual(json.loads(api('/api/covers/'+self.cover_id)['body'])['cover_id'],self.cover_id)
            data={'title':'中文','markdown':'正文','cover_id':self.cover_id}
            saved=api('/api/articles/save',data)
            self.assertEqual(saved['statusCode'],200)
            self.assertEqual(json.loads(saved['body'])['cover_id'],self.cover_id)

    def test_markdown_features_and_escaping(self):
        source='# 标题\n\n- 第一项\n- **重点**\n\n1. 步骤\n2. 步骤二\n\n> 引用\n\n|列A|列B|\n|---|---|\n|内容|*斜体*|\n\n[链接](https://example.com/?a=1&b=2)\n\n<script>alert(1)</script>\n\n```\n**not bold** <script>\n```'
        result=render_markdown(source)
        for tag in ('<ul ', '<ol ', '<blockquote ', '<table ', '<em>斜体</em>', '<strong>重点</strong>', 'href="https://example.com/?a=1&amp;b=2"'):
            self.assertIn(tag,result)
        self.assertNotIn('<script>',result)
        self.assertIn('**not bold** &lt;script&gt;',result)
        unsafe=render_markdown('[危险](javascript:alert(1))\n\n![图片](https://example.com/x.png)')
        self.assertNotIn('href=',unsafe)
        self.assertNotIn('<img',unsafe)

    def body_article(self):
        store=CosStore(FakeCOS(),'example-123','dummy-app')
        store.put_image(self.cover_id,self.raw)
        data={'title':'正文图验证','markdown':'# 图文\n\n![说明](asset:'+self.cover_id+')\n\n![重复](asset:'+self.cover_id+')'}
        article=store.save(data,preview(data))
        self.assertEqual(article['image_ids'],[self.cover_id])
        return store,article

    def test_body_image_upload_is_deduplicated_and_readback_verified(self):
        store,article=self.body_article()
        client=Mock();client.upload_body_image.return_value='http://mmbiz.qpic.cn/example/0'
        client.upload_cover.return_value='thumb'
        service=Drafts(store,client)
        def call(path,**kwargs):
            if path=='draft/add':
                content=kwargs['payload']['articles'][0]['content']
                self.assertEqual(content.count('src="http://mmbiz.qpic.cn/example/0"'),2)
                self.assertNotIn('data-image-id=',content)
                return {'media_id':'draft'}
            saved=store.get(article['id'])
            return {'news_item':[{'title':article['title'],'thumb_media_id':'thumb','content':service.content(saved)}]}
        client.call.side_effect=call
        self.assertEqual(service.sync(article['id'],article['content_hash'])['status'],'synced')
        client.upload_body_image.assert_called_once_with(self.raw)
        saved=store.get(article['id'])
        self.assertFalse(service.matches(saved,{'title':article['title'],'thumb_media_id':'thumb','content':'稿件编号：'+article['id']}))

    def test_body_image_timeout_blocks_cover_draft_and_repeat(self):
        store,article=self.body_article();client=Mock()
        client.upload_body_image.side_effect=ProbeError('timeout')
        service=Drafts(store,client)
        with self.assertRaises(ProbeError):service.sync(article['id'],article['content_hash'])
        self.assertEqual(store.get(article['id'])['status'],'image_pending')
        with self.assertRaises(Conflict):service.sync(article['id'],article['content_hash'])
        with self.assertRaises(Conflict):service.reconcile(article['id'])
        client.upload_body_image.assert_called_once()
        client.upload_cover.assert_not_called();client.call.assert_not_called()

    def test_body_image_api_and_size_limit(self):
        store=CosStore(FakeCOS(),'example-123','dummy-app')
        headers={'Authorization':'Bearer '+'x'*40}
        with patch.dict('os.environ',{'PLATFORM_API_KEY':'x'*40},clear=True):
            result=handle({'httpMethod':'POST','path':'/api/images/upload','headers':headers,
                'body':json.dumps({'png_base64':base64.b64encode(self.raw).decode()})},lambda:store)
            self.assertEqual(result['statusCode'],200)
            result=handle({'httpMethod':'GET','path':'/api/images/'+self.cover_id,'headers':headers},lambda:store)
            self.assertEqual(json.loads(result['body'])['image_id'],self.cover_id)
        source='\n\n'.join('![图](asset:'+str(n)*64+')' for n in range(6))
        with self.assertRaises(ValueError):preview({'title':'太多图片','markdown':source})

    def test_wechat_body_url_validation_and_real_multipart_path(self):
        client=WeChat('dummy','dummy')
        client.call=Mock(return_value={'url':'http://mmbiz.qpic.cn/example/0'})
        self.assertEqual(client.upload_body_image(self.raw),'http://mmbiz.qpic.cn/example/0')
        self.assertEqual(client.call.call_args.args[0],'media/uploadimg')
        self.assertIn(self.raw,client.call.call_args.kwargs['body'])
        client.call.return_value={'url':'https://evil.example/image.png'}
        with self.assertRaises(ProbeError):client.upload_body_image(self.raw)


if __name__=='__main__': unittest.main()
