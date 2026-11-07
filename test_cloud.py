import io
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from article_store import Conflict
from cloud_api import handle
from cos_store import CosStore, StorageError
from draft_service import Drafts
from platform_preview import preview


class Exists(Exception):
    def get_error_code(self):
        return 'ObjectAlreadyExists'


class FakeCOS:
    def __init__(self):
        self.objects = {}
        self.lock = threading.Lock()
        self.versioning = {}
        self.lose_response = False

    def get_bucket_versioning(self, **kw):
        return self.versioning

    def list_objects(self, Prefix, **kw):
        with self.lock:
            return {'Contents': [{'Key': key} for key in sorted(self.objects) if key.startswith(Prefix)], 'IsTruncated': 'false'}

    def get_object(self, Key, **kw):
        with self.lock:
            stream = io.BytesIO(self.objects[Key])
        return {'Body': Mock(get_raw_stream=lambda: stream)}

    def put_object(self, Key, Body, Metadata, **kw):
        assert Metadata['x-cos-forbid-overwrite'] == 'true'
        with self.lock:
            if Key in self.objects:
                raise Exists()
            self.objects[Key] = Body
            if self.lose_response:
                raise TimeoutError()
        return {}


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.cos = FakeCOS()
        self.store = CosStore(self.cos, 'example-123', 'dummy-app')
        self.data = {'title': '云端测试', 'markdown': '# 正文'}

    def test_save_persist_and_no_overwrite(self):
        article = self.store.save(self.data, preview(self.data))
        self.assertEqual(self.store.save(self.data, preview(self.data)), article)
        other = CosStore(self.cos, 'example-123', 'dummy-app')
        self.assertEqual(other.list(), [article])
        new_data = dict(self.data, id=article['id'], revision=1, markdown='新正文')
        updated = other.save(new_data, preview(new_data))
        self.assertEqual(updated['revision'], 2)
        self.assertEqual(len(self.cos.objects), 2)
        with self.assertRaises(Conflict):
            self.store.save(new_data, preview(new_data))
        with self.assertRaises(Conflict):
            self.store.save(self.data, preview(self.data))

    def test_cross_instance_claim_one_winner(self):
        article = self.store.save(self.data, preview(self.data))
        barrier = threading.Barrier(2)
        def claim(index):
            store = CosStore(self.cos, 'example-123', 'dummy-app')
            def mutate(a):
                barrier.wait(timeout=5)
                a['status'] = 'cover_pending'
            try:
                store.change(article['id'], mutate)
                return True
            except Conflict:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(claim, range(2))), 1)
        self.assertEqual(self.store.get(article['id'])['status'], 'cover_pending')

    def test_lost_storage_response_never_uploads_cover(self):
        article = self.store.save(self.data, preview(self.data))
        client = Mock()
        self.cos.lose_response = True
        with self.assertRaises(StorageError):
            Drafts(self.store, client).sync(article['id'], article['content_hash'])
        client.upload_cover.assert_not_called()
        self.cos.lose_response = False
        with self.assertRaises(Conflict):
            Drafts(self.store, client).sync(article['id'], article['content_hash'])
        client.upload_cover.assert_not_called()

    def test_enabled_or_suspended_versioning_blocks_writes(self):
        for state in ('Enabled', 'Suspended'):
            self.cos.versioning = {'Status': state}
            with self.assertRaises(StorageError):
                self.store.save(self.data, preview(self.data))
        self.assertFalse(self.cos.objects)

    def test_account_isolation(self):
        self.store.save(self.data, preview(self.data))
        self.assertEqual(CosStore(self.cos, 'example-123', 'another-app').list(), [])

    def test_auth_cors_and_no_sync_before_enabled(self):
        env = {'PLATFORM_API_KEY': 'x' * 40, 'PLATFORM_ALLOWED_ORIGIN': 'https://editor.example'}
        headers = {'Authorization': 'Bearer ' + 'x' * 40, 'Origin': 'https://editor.example'}
        factory = Mock(return_value=self.store)
        with patch.dict('os.environ', env, clear=True):
            self.assertEqual(handle({'httpMethod':'GET','path':'/api/articles'}, factory)['statusCode'], 401)
            factory.assert_not_called()
            self.assertEqual(handle({'httpMethod':'GET','path':'/api/articles','headers':dict(headers, Origin='https://evil.example')}, factory)['statusCode'], 403)
            factory.assert_not_called()
            event = {'httpMethod':'POST','path':'/api/articles/save','headers':headers,'body':json.dumps(self.data)}
            result = handle(event, factory)
            self.assertEqual(result['statusCode'], 200)
            article = json.loads(result['body'])
            client_factory = Mock()
            result = handle(dict(event, path='/api/articles/sync', body=json.dumps({'id':article['id'],'confirm':True})), factory, client_factory)
            self.assertEqual(result['statusCode'], 409)
            client_factory.assert_not_called()
            result = handle(dict(event, httpMethod='OPTIONS', body=''), factory)
            self.assertEqual(result['statusCode'], 204)
            self.assertNotIn('x' * 40, json.dumps(result))

    def test_exact_loopback_origin_and_http_methods(self):
        env = {'PLATFORM_API_KEY':'x'*40, 'PLATFORM_ALLOWED_ORIGIN':'http://127.0.0.1:18765'}
        headers = {'Authorization':'Bearer '+'x'*40,'Origin':'http://127.0.0.1:18765'}
        with patch.dict('os.environ', env, clear=True):
            event = {'httpMethod':'GET','path':'/api/articles','headers':headers}
            result = handle(event, lambda:self.store)
            self.assertEqual(result['statusCode'],200)
            self.assertEqual(result['headers']['Access-Control-Allow-Origin'], headers['Origin'])
            self.assertEqual(handle(dict(event,httpMethod='POST'),lambda:self.store)['statusCode'],405)
            self.assertEqual(handle(dict(event,headers=dict(headers,Origin='http://127.0.0.1:18766')),lambda:self.store)['statusCode'],403)
            env['PLATFORM_ALLOWED_ORIGIN']='http://remote.example'
            with patch.dict('os.environ',env):
                self.assertEqual(handle(dict(event,headers=dict(headers,Origin='http://remote.example')),lambda:self.store)['statusCode'],403)


if __name__ == '__main__':
    unittest.main()
