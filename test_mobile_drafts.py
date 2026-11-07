import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from ai_submission import submit
from article_store import Store, Conflict
from mobile_drafts import create, inspect
from wechat_draft_probe import ProbeError


class MobileDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'articles.sqlite3')
        self.data={'submission_id':'mobile-fixture-submission-0001','title':'手机草稿测试','markdown':'# 待确认的完整正文'}
        self.receipt=submit(self.store,self.data)
        self.article_id=self.receipt['article_id']
        self.env=patch.dict(os.environ,{'ENABLE_DRAFT_SYNC':'true','WECHAT_APP_ID':'fixture','WECHAT_APP_SECRET':'fixture-secret'})
        self.env.start();self.addCleanup(self.env.stop)
        self.client=Mock()
        self.client.upload_cover.return_value='cover-fixture'
        self.factory=Mock(return_value=self.client)
        self.remote=None
        self.lose_add=False
        def call(path,**kwargs):
            if path=='draft/count':return {'total_count':0}
            if path=='draft/add':
                self.remote=copy.deepcopy(kwargs['payload']['articles'][0])
                if self.lose_add:raise ProbeError('timeout secret-must-not-leak')
                return {'media_id':'draft-fixture'}
            if path=='draft/get':return {'news_item':[self.remote] if self.remote else []}
            if path=='draft/batchget':return {'total_count':1 if self.remote else 0,'item':[
                {'media_id':'draft-fixture','content':{'news_item':[self.remote]}}] if self.remote else []}
            raise AssertionError('Forbidden endpoint '+path)
        self.client.call.side_effect=call

    def prepared(self):
        value=inspect(self.store,self.data,self.factory)
        return dict(submission_id=self.data['submission_id'],review_hash=value['review_hash'],confirm=True)

    def test_create_readback_repeat_and_readonly_query(self):
        data=self.prepared()
        self.factory.assert_not_called()
        value=create(self.store,data,self.factory)
        self.assertEqual(value['status'],'synced')
        self.assertTrue(value['wechat_writes_performed'])
        self.assertFalse(value['publishing_performed'])
        self.assertEqual(value['cover'],'default')
        self.assertNotIn('media_id',value)
        before=self.store.get(self.article_id)
        self.assertEqual(inspect(self.store,self.data,self.factory)['status'],'synced')
        self.assertEqual(self.store.get(self.article_id),before)
        self.assertFalse(create(self.store,data,self.factory)['wechat_writes_performed'])
        self.client.upload_cover.assert_called_once()
        self.assertEqual([c.args[0] for c in self.client.call.call_args_list].count('draft/add'),1)

    def test_requires_explicit_confirmation_and_current_revision(self):
        data=self.prepared()
        for confirm in (False,None,'true',1):
            with self.assertRaises(Conflict):create(self.store,dict(data,confirm=confirm),self.factory)
        current=self.store.get(self.article_id)
        self.store.save(dict(title=current['title'],markdown=current['markdown'],id=current['id'],revision=current['revision']),current)
        with self.assertRaises(Conflict):create(self.store,data,self.factory)
        self.factory.assert_not_called()

    def test_racing_edit_after_review_before_claim_prevents_upload(self):
        data=self.prepared()
        original=self.client.call.side_effect
        def call(path,**kwargs):
            if path=='draft/count':
                article=self.store.get(self.article_id)
                self.store.save(dict(title=article['title'],markdown=article['markdown'],id=article['id'],revision=article['revision']),article)
            return original(path,**kwargs)
        self.client.call.side_effect=call
        with self.assertRaises(Conflict):create(self.store,data,self.factory)
        self.client.upload_cover.assert_not_called()
        self.assertEqual(self.store.get(self.article_id)['status'],'saved')

    def test_lost_add_response_is_found_by_reads_without_state_or_write_retry(self):
        data=self.prepared();self.lose_add=True
        with self.assertRaises(ProbeError):create(self.store,data,self.factory)
        before=self.store.get(self.article_id)
        self.assertEqual(before['status'],'draft_pending')
        result=inspect(self.store,self.data,self.factory)
        self.assertEqual(result['status'],'synced')
        self.assertEqual(result['platform_status'],'draft_pending')
        self.assertEqual(self.store.get(self.article_id),before)
        self.assertFalse(create(self.store,data,self.factory)['wechat_writes_performed'])
        self.assertEqual([c.args[0] for c in self.client.call.call_args_list].count('draft/add'),1)

    def test_cover_unknown_never_reuploads_and_no_match_never_unlocks(self):
        data=self.prepared()
        self.client.upload_cover.side_effect=ProbeError('timeout')
        with self.assertRaises(ProbeError):create(self.store,data,self.factory)
        self.assertEqual(inspect(self.store,self.data,self.factory)['status'],'cover_pending')
        create(self.store,data,self.factory)
        self.client.upload_cover.assert_called_once()
        self.store.change(self.article_id,lambda a:a.update(status='draft_pending',thumb_media_id='cover-fixture'))
        with self.assertRaises(Conflict):inspect(self.store,self.data,self.factory)
        self.assertEqual(self.store.get(self.article_id)['status'],'draft_pending')

    def test_permission_failure_and_disable_do_not_write(self):
        data=self.prepared()
        with patch.dict(os.environ,{'ENABLE_DRAFT_SYNC':'false'}):
            with self.assertRaises(Conflict):create(self.store,data,self.factory)
        self.factory.assert_not_called()
        self.client.call.side_effect=ProbeError('WeChat errcode=48001')
        with self.assertRaises(ProbeError):create(self.store,data,self.factory)
        self.client.upload_cover.assert_not_called()
        self.assertEqual(self.store.get(self.article_id)['status'],'saved')

    def test_workbench_and_mobile_share_a_single_durable_barrier(self):
        data=self.prepared()
        def run(_):
            try:return create(self.store,data,self.factory)
            except Conflict:return None
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,range(2)))
        self.client.upload_cover.assert_called_once()
        self.assertEqual([c.args[0] for c in self.client.call.call_args_list].count('draft/add'),1)


class MobileCosDraftTests(MobileDraftTests):
    def setUp(self):
        super().setUp()
        from cos_store import CosStore
        from test_cloud import FakeCOS
        self.cos=FakeCOS()
        self.store=CosStore(self.cos,'example-123','dummy')
        self.receipt=submit(self.store,self.data)
        self.article_id=self.receipt['article_id']


if __name__=='__main__':unittest.main()
