"""Confirmed create-only WeChat draft access for the owner's AI submissions."""
import copy
import hashlib
import json
import os

from ai_submission import identity
from article_store import Conflict
from draft_service import Drafts


def article_for(store, data):
    submission_id, article_id = identity(data)
    article = store.get(article_id)
    if article.get('source')!='ai' or article.get('submission_id')!=submission_id:
        raise Conflict('Only the original AI submission is accessible.')
    return article


def review_hash(article):
    fields = [article['id'],article['revision'],article['content_hash'],article['title'],
              article['markdown'],article.get('cover_id',''),article.get('image_ids',[])]
    return hashlib.sha256(json.dumps(fields,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def summary(article):
    return {'submission_id':article['submission_id'],'article_id':article['id'],
            'status':article['status'],'platform_status':article['status'],
            'revision':article['revision'],'title':article['title'],'markdown':article['markdown'],
            'review_hash':review_hash(article),
            'cover':'custom' if article.get('cover_id') else 'default',
            'cover_notice':'Uses saved custom cover.' if article.get('cover_id') else
                           'Uses platform default cover; automatic illustration is not available.',
            'body_image_count':len(article.get('image_ids',[])),
            'wechat_writes_performed':False,'publishing_performed':False}


class DraftSnapshot:
    """Reconcile against a private memory copy: a status query never writes COS."""
    def __init__(self, article):
        self.article = copy.deepcopy(article)

    def get(self, article_id):
        if article_id!=self.article['id']:
            raise KeyError(article_id)
        return copy.deepcopy(self.article)

    def change(self, article_id, update):
        article = self.get(article_id)
        update(article)
        self.article = article
        return copy.deepcopy(article)


def client_from(client_factory):
    if os.environ.get('ENABLE_DRAFT_SYNC')!='true':
        raise Conflict('WeChat draft creation is disabled.')
    return client_factory(os.environ['WECHAT_APP_ID'],os.environ['WECHAT_APP_SECRET'])


def inspect(store, data, client_factory):
    article = article_for(store,data)
    value = summary(article)
    if article['status']=='saved':
        value['remote_verification']='not_started'
        return value
    if article['status'] in {'image_pending','cover_pending'}:
        value['remote_verification']='material_result_unknown; inspect WeChat materials; no write retry'
        return value
    checked = Drafts(DraftSnapshot(article),client_from(client_factory)).reconcile(article['id'])
    value.update(status=checked['status'],remote_verification='matched_title_cover_and_article_marker')
    # Never return media identifiers or infer that a draft has been published.
    return value


def create(store, data, client_factory):
    if data.get('confirm') is not True:
        raise Conflict('Explicit confirmation to create a WeChat draft is required.')
    article = article_for(store,data)
    if article['status']!='saved':
        # A repeat can only inspect the original result, never reset the barrier.
        return inspect(store,data,client_factory)
    if data.get('review_hash')!=review_hash(article):
        raise Conflict('Article changed; read and review the current version again.')
    client = client_from(client_factory)
    permission = client.call('draft/count')  # Actual permission check before the write barrier.
    if type(permission.get('total_count')) is not int or permission['total_count']<0:
        raise Conflict('WeChat permission check returned an unexpected result; no writes started.')
    result = Drafts(store,client).sync(article['id'],article['content_hash'],article['revision'])
    value = summary(result)
    value.update(wechat_writes_performed=True,remote_verification='matched_title_cover_and_article_marker')
    return value
