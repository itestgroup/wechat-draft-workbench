import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor

from article_store import Store, Conflict
from cos_store import CosStore, StorageError
from ai_submission import submit, status
from cloud_api import handle
from platform_preview import preview
from published_index import PublishedIndex
from test_cloud import FakeCOS


class AISubmissionTests(unittest.TestCase):
    def data(self, **extra):
        return dict({'submission_id':'labview-test-20261018','title':'AI测试稿','markdown':'# 测试\n\n待人工审核'},**extra)

    def test_both_stores_create_only_and_replay_after_owner_edit(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            for store in (Store(Path(directory)/'articles.sqlite3'),CosStore(FakeCOS(),'example-123','dummy')):
                result=submit(store,self.data())
                self.assertEqual(result['status'],'saved')
                article=store.get(result['article_id'])
                edit=dict(article,title='人工修改',revision=1)
                store.save(edit,preview(edit))
                self.assertEqual(submit(store,self.data())['revision'],2)
                self.assertEqual(store.get(article['id'])['title'],'人工修改')
                self.assertEqual(len(store.list()),1)
                with self.assertRaises(Conflict):submit(store,self.data(markdown='另一版'))
                self.assertEqual(status(store,self.data())['article_id'],article['id'])

    def test_lost_response_is_resolved_by_read_not_duplicate_write(self):
        cos=FakeCOS();store=CosStore(cos,'example-123','dummy')
        cos.lose_response=True
        with self.assertRaises(StorageError):submit(store,self.data())
        cos.lose_response=False
        self.assertEqual(status(store,self.data())['status'],'saved')
        before=len(cos.objects)
        submit(store,self.data())
        self.assertEqual(len(cos.objects),before)
        self.assertEqual(status(store,self.data(submission_id='unknown-submission-id'))['status'],'not_found')

    def test_concurrent_submissions_produce_one_record(self):
        store=CosStore(FakeCOS(),'example-123','dummy')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:submit(store,self.data()),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(len(store.list()),1)

    def test_ai_key_scope_and_no_wechat_initialization(self):
        store=CosStore(FakeCOS(),'example-123','dummy');client=Mock()
        env={'PLATFORM_API_KEY':'a'*40,'AI_SUBMIT_KEY':'b'*40,'ENABLE_DRAFT_SYNC':'true'}
        event={'httpMethod':'POST','path':'/api/ai/submit','headers':{'Authorization':'Bearer '+'b'*40},'body':json.dumps(self.data())}
        with patch.dict('os.environ',env,clear=True):
            response=handle(event,lambda:store,client)
            self.assertEqual(response['statusCode'],200)
            for path in ('/api/articles','/api/articles/save','/api/articles/sync','/api/articles/reconcile','/api/index/importone','/api/images/upload'):
                self.assertEqual(handle(dict(event,path=path),lambda:store,client)['statusCode'],403)
            self.assertEqual(handle(dict(event,headers={}),lambda:store,client)['statusCode'],401)
            self.assertEqual(handle(dict(event,headers={'Authorization':'Bearer '+'a'*40}),lambda:store,client)['statusCode'],401)
            result=handle(dict(event,path='/api/ai/status'),lambda:store,client)
            self.assertEqual(json.loads(result['body'])['status'],'saved')
            listing=handle(dict(event,httpMethod='GET',path='/api/ai/index'),lambda:store,client)
            self.assertEqual(json.loads(listing['body'])['entries'],[])
            client.assert_not_called()
        for key in ('','a'*40):
            with patch.dict('os.environ',dict(env,AI_SUBMIT_KEY=key),clear=True):
                self.assertEqual(handle(event,lambda:store,client)['statusCode'],503)

    def test_invalid_input_cannot_claim_existing_article_or_assets(self):
        store=CosStore(FakeCOS(),'example-123','dummy')
        for data in (self.data(id='a'*32),self.data(cover_id='a'*64),self.data(submission_id='../path'),
                     self.data(markdown='x'*30001),self.data(markdown='![私有图片](asset:'+'a'*64+')')):
            with self.assertRaises(ValueError):submit(store,data)
        self.assertEqual(store.list(),[])

    def test_index_pagination_excludes_private_metadata(self):
        store=CosStore(FakeCOS(),'example-123','dummy')
        index=PublishedIndex(store)
        for number in range(22):
            index.save({'title':'文章'+str(number),'url':'https://mp.weixin.qq.com/s/example'+str(number),'confirmed_published':True})
        env={'PLATFORM_API_KEY':'a'*40,'AI_SUBMIT_KEY':'b'*40}
        event={'httpMethod':'GET','path':'/api/ai/index','headers':{'Authorization':'Bearer '+'b'*40}}
        with patch.dict('os.environ',env,clear=True):
            first=json.loads(handle(event,lambda:store)['body'])
            second=json.loads(handle(dict(event,queryStringParameters={'offset':'20'}),lambda:store)['body'])
            self.assertEqual(len(first['entries']),20)
            self.assertEqual(first['next_offset'],20)
            self.assertEqual(len(second['entries']),2)
            self.assertIsNone(second['next_offset'])
            self.assertEqual(set(first['entries'][0]),{'title','url','published_date','collection','tags'})
            self.assertEqual(handle(dict(event,queryStringParameters={'offset':'-1'}),lambda:store)['statusCode'],400)

    def test_openapi_contract_has_only_scoped_actions(self):
        schema=json.loads((Path(__file__).parent/'preview/ai-openapi.json').read_text(encoding='utf-8'))
        self.assertEqual(set(schema['paths']),{'/api/ai/submit','/api/ai/status','/api/ai/index'})
        self.assertTrue(schema['paths']['/api/ai/submit']['post']['x-openai-isConsequential'])
        self.assertFalse(schema['paths']['/api/ai/status']['post']['x-openai-isConsequential'])
        self.assertEqual(schema['components']['securitySchemes']['submissionKey']['scheme'],'bearer')


if __name__=='__main__':
    unittest.main()
