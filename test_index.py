import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from article_store import Store, Conflict
from cos_store import CosStore
from published_index import PublishedIndex, IndexValidation, article_url
from cloud_api import handle
from test_cloud import FakeCOS


class IndexTests(unittest.TestCase):
    def test_import_preview_validates_all_rows_without_writes(self):
        store=CosStore(FakeCOS(),'example-123','dummy')
        index=PublishedIndex(store)
        original=index.save(self.data())
        text='\ufefftitle,url,tags\r\n"带,逗号",https://mp.weixin.qq.com/s/new,LabVIEW;开发\r\n重复,https://mp.weixin.qq.com/s/new?scene=1,\r\n已存在,https://mp.weixin.qq.com/s/example,\r\n错误,https://evil.example/x,\r\n'
        result=index.import_preview({'format':'csv','text':text})
        self.assertEqual([r['status'] for r in result['rows']],['new','duplicate','existing','invalid'])
        self.assertFalse(result['can_import'])
        self.assertEqual(result['rows'][0]['entry']['title'],'带,逗号')
        self.assertEqual(result['rows'][0]['entry']['tags'],['LabVIEW','开发'])
        self.assertEqual(index.list(),[original])

    def test_import_never_overwrites_and_requires_confirmation_both_stores(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            for store in (Store(Path(folder)/'articles.sqlite3'),CosStore(FakeCOS(),'example-123','dummy')):
                index=PublishedIndex(store)
                data=self.data()
                with self.assertRaises(IndexValidation):index.import_one(dict(data,confirmed_published=False))
                self.assertEqual(index.import_one(data)['result'],'created')
                original=index.list()[0]
                self.assertEqual(index.import_one(dict(data,title='不能覆盖',tags=[]))['result'],'existing')
                self.assertEqual(index.list(),[original])
                self.assertEqual(store.list(),[])

    def test_import_markdown_and_bad_files(self):
        index=PublishedIndex(CosStore(FakeCOS(),'example-123','dummy'))
        result=index.import_preview({'format':'markdown','text':'```md\n- [示例](https://mp.weixin.qq.com/s/ignored)\n```\n\n- [真实](https://mp.weixin.qq.com/s/new)\n不支持的行'})
        self.assertEqual([r['status'] for r in result['rows']],['new','invalid'])
        for data in ({'format':'csv','text':'title,url,url\nx,a,b'},
                     {'format':'csv','text':'title,url\nx,a,b'},
                     {'format':'markdown','text':'```\nexample'},
                     {'format':'markdown','text':'- [x](https://mp.weixin.qq.com/s/new)\n'*101},
                     {'format':'csv','text':'x'*205000}):
            with self.assertRaises(IndexValidation):index.import_preview(data)
        result=index.import_preview({'format':'markdown','text':'- [x](https://mp.weixin.qq.com/s/new?access_token=DO_NOT_RETURN)'})
        self.assertFalse(result['can_import'])
        self.assertNotIn('DO_NOT_RETURN',json.dumps(result))

    def data(self, **extra):
        return dict({'title':'LabVIEW开发经验','url':'https://mp.weixin.qq.com/s/example?scene=1#rd',
                     'published_date':'2025-01-01','collection':'现有合集','tags':['LabVIEW','开发'],
                     'confirmed_published':True},**extra)

    def test_urls_strip_tracking_and_reject_credentials_or_other_hosts(self):
        self.assertEqual(article_url(self.data()['url']),'https://mp.weixin.qq.com/s/example')
        url='https://mp.weixin.qq.com/s?__biz=MzABC%3D%3D&mid=123&idx=1&sn='+'a'*32+'&scene=1'
        self.assertNotIn('scene=',article_url(url))
        for bad in ('http://mp.weixin.qq.com/s/example','https://evil.example/s/x',
                    'https://mp.weixin.qq.com.evil.example/s/x','https://mp.weixin.qq.com/s/x?access_token=DO_NOT_SAVE',
                    'https://mp.weixin.qq.com/s?mid=123','https://user:password@mp.weixin.qq.com/s/x'):
            with self.assertRaises(IndexValidation):article_url(bad)

    def test_local_and_cloud_roundtrip_idempotency_and_stale_revision(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            for store in (Store(Path(folder)/'articles.sqlite3'),CosStore(FakeCOS(),'example-123','dummy')):
                index=PublishedIndex(store)
                first=index.save(self.data())
                self.assertEqual(index.save(self.data()),first)
                self.assertEqual(index.list(),[first])
                self.assertEqual(store.list(),[])
                edit=self.data(id=first['id'],revision=1,title='更新登记标题')
                updated=index.save(edit)
                self.assertEqual(updated['revision'],2)
                with self.assertRaises(Conflict):index.save(self.data(id=first['id'],revision=1,title='陈旧更新'))
                self.assertEqual(index.list()[0]['title'],'更新登记标题')
                with self.assertRaises(IndexValidation):index.save(self.data(id=first['id'],revision=2,url='https://mp.weixin.qq.com/s/other'))

    def test_confirmation_dates_and_metadata_limits(self):
        index=PublishedIndex(CosStore(FakeCOS(),'example-123','dummy'))
        for data in (self.data(confirmed_published=False),self.data(published_date='2999-01-01'),
                     self.data(published_date='2025-99-01'),self.data(tags=['a']*11),self.data(title='\n秘密')):
            with self.assertRaises(IndexValidation):index.save(data)
        self.assertEqual(index.list(),[])

    def test_history_contains_only_selected_published_links_and_no_duplicates(self):
        index=PublishedIndex(CosStore(FakeCOS(),'example-123','dummy'))
        first=index.save(self.data(title='[标题] **测试**'))
        second=index.save(self.data(url='https://mp.weixin.qq.com/s/second',title='第二篇'))
        block=index.history({'ids':[first['id'],first['id'],second['id']]})
        self.assertEqual(block['count'],2)
        self.assertEqual(block['markdown'].count('https://mp.weixin.qq.com/s/example'),1)
        self.assertNotIn('scene=',block['markdown'])
        for ids in ([],['bad'],[first['id']]*11):
            with self.assertRaises(IndexValidation):index.history({'ids':ids})

    def test_index_api_does_not_call_wechat(self):
        store=CosStore(FakeCOS(),'example-123','dummy');client=Mock()
        headers={'Authorization':'Bearer '+'x'*40}
        with patch.dict('os.environ',{'PLATFORM_API_KEY':'x'*40,'ENABLE_DRAFT_SYNC':'true'},clear=True):
            event={'httpMethod':'POST','path':'/api/index/save','headers':headers,'body':json.dumps(self.data())}
            result=handle(event,lambda:store,client)
            self.assertEqual(result['statusCode'],200)
            entry=json.loads(result['body'])
            history=handle(dict(event,path='/api/index/history',body=json.dumps({'ids':[entry['id']]})),lambda:store,client)
            self.assertEqual(json.loads(history['body'])['count'],1)
            listing=handle(dict(event,httpMethod='GET',path='/api/index'),lambda:store,client)
            self.assertEqual(len(json.loads(listing['body'])['entries']),1)
            bad=handle(dict(event,body=json.dumps(self.data(confirmed_published=False))),lambda:store,client)
            self.assertEqual(bad['statusCode'],400)
            batch=handle(dict(event,path='/api/index/importpreview',body=json.dumps({'format':'markdown','text':'- [新文章](https://mp.weixin.qq.com/s/new)'})),lambda:store,client)
            self.assertEqual(batch['statusCode'],200)
            candidate=json.loads(batch['body'])['rows'][0]['entry']
            self.assertEqual(len(PublishedIndex(store).list()),1)
            imported=handle(dict(event,path='/api/index/importone',body=json.dumps(candidate)),lambda:store,client)
            self.assertEqual(json.loads(imported['body'])['result'],'created')
            again=handle(dict(event,path='/api/index/importone',body=json.dumps(dict(candidate,title='不能覆盖'))),lambda:store,client)
            self.assertEqual(json.loads(again['body'])['result'],'existing')
            denied=handle(dict(event,path='/api/index/importone',headers={}),lambda:store,client)
            self.assertEqual(denied['statusCode'],401)
            client.assert_not_called()


if __name__=='__main__':unittest.main()
