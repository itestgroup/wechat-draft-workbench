import tempfile
import unittest
import json
import threading
from http.client import HTTPConnection
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

from article_store import Store, Conflict
from draft_service import Drafts
from platform_preview import preview, Handler, ThreadingHTTPServer
from wechat_draft_probe import ProbeError
from wechat_client import WeChat


class PlatformTests(unittest.TestCase):
    def test_whitelist_error_only_displays_validated_ip(self):
        with self.assertRaises(ProbeError) as caught:
            WeChat.check({'errcode': 40164, 'errmsg': 'invalid ip 203.0.113.8, secret=DO_NOT_PRINT'})
        self.assertIn('203.0.113.8', str(caught.exception))
        self.assertNotIn('DO_NOT_PRINT', str(caught.exception))
        with self.assertRaises(ProbeError) as caught:
            WeChat.check({'errcode': 40164, 'errmsg': 'invalid ip DO_NOT_PRINT, token=DO_NOT_PRINT'})
        self.assertNotIn('DO_NOT_PRINT', str(caught.exception))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'articles.sqlite3'
        self.store = Store(self.path)
        self.data = {'title': '中文测试', 'markdown': '# 标题\n\n**重点** <script>x</script>'}
        self.article = self.store.save(self.data, preview(self.data))
        self.client = Mock()
        self.client.upload_cover.return_value = 'cover'
        self.service = Drafts(self.store, self.client)

    def sync(self):
        return self.service.sync(self.article['id'], self.article['content_hash'])

    def remote_article(self):
        return {'title': self.article['title'], 'thumb_media_id': 'cover',
                'content': self.service.content(self.article)}

    def test_persistence_duplicate_save_and_stale_edit(self):
        self.assertEqual(Store(self.path).get(self.article['id']), self.article)
        again = self.store.save(self.data, preview(self.data))
        self.assertEqual(again['id'], self.article['id'])
        edit = dict(self.data, id=self.article['id'], revision=1)
        self.store.save(edit, preview(edit))
        with self.assertRaises(Conflict):
            self.store.save(edit, preview(edit))

    def test_success_then_no_second_write(self):
        self.client.call.side_effect = [{'media_id': 'draft'}, {'news_item': [self.remote_article()]}]
        self.assertEqual(self.sync()['status'], 'synced')
        with self.assertRaises(Conflict):
            self.sync()
        self.client.upload_cover.assert_called_once()
        self.assertEqual(self.client.call.call_count, 2)

    def test_cover_timeout_persists_barrier_after_restart(self):
        self.client.upload_cover.side_effect = ProbeError('timeout')
        with self.assertRaises(ProbeError):
            self.sync()
        self.service = Drafts(Store(self.path), self.client)
        with self.assertRaises(Conflict):
            self.sync()
        with self.assertRaises(Conflict):
            self.service.reconcile(self.article['id'])
        self.client.upload_cover.assert_called_once()
        self.client.call.assert_not_called()

    def test_lost_add_response_recovered_by_reads_only(self):
        self.client.call.side_effect = ProbeError('timeout')
        with self.assertRaises(ProbeError):
            self.sync()
        with self.assertRaises(Conflict):
            self.sync()
        self.client.reset_mock()
        self.client.call.side_effect = [
            {'total_count': 1, 'item': [{'media_id': 'recovered', 'content': {'news_item': [self.remote_article()]}}]},
            {'news_item': [self.remote_article()]}]
        result = self.service.reconcile(self.article['id'])
        self.assertEqual(result['status'], 'synced')
        self.assertEqual([call.args[0] for call in self.client.call.call_args_list], ['draft/batchget', 'draft/get'])
        self.client.upload_cover.assert_not_called()

    def test_no_match_never_unlocks_write(self):
        self.store.change(self.article['id'], lambda a: a.update(status='draft_pending', thumb_media_id='cover'))
        self.client.call.return_value = {'item': [], 'total_count': 0}
        with self.assertRaises(Conflict):
            self.service.reconcile(self.article['id'])
        with self.assertRaises(Conflict):
            self.sync()

    def test_concurrent_claim_allows_only_one_upload(self):
        self.client.call.side_effect = ProbeError('timeout')
        def run(_):
            try:
                self.sync()
            except (ProbeError, Conflict):
                pass
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run, range(4)))
        self.client.upload_cover.assert_called_once()

    def test_version_and_render_safety(self):
        with self.assertRaises(Conflict):
            self.service.sync(self.article['id'], 'stale')
        self.client.upload_cover.assert_not_called()
        rendered = preview(self.data)['html']
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('<strong>重点</strong>', rendered)

    def test_http_save_reload_and_write_guards(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.store, server.drafts = self.store, None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def call(path, data=None, headers=None):
            connection = HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request('POST' if data is not None else 'GET', path,
                                   body=json.dumps(data).encode() if data is not None else None,
                                   headers=headers or {})
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()
        try:
            self.assertEqual(call('/')[0], 200)
            self.assertEqual(call('/app.js')[0], 200)
            self.assertEqual(call('/api/articles/save', self.data)[0], 403)
            headers = {'X-Preview-Request': '1', 'Content-Type': 'application/json'}
            code, body = call('/api/articles/save', self.data, headers)
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)['id'], self.article['id'])
            self.assertEqual(len(json.loads(call('/api/articles')[1])['articles']), 1)
            self.assertEqual(call('/api/articles/sync', {'id': self.article['id'], 'confirm': True}, headers)[0], 409)
            self.assertEqual(call('/api/articles', headers={'Host': 'untrusted.example'})[0], 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
