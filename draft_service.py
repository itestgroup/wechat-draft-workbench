"""One-shot draft creation with durable barriers and read-only reconciliation."""
from article_store import Conflict
import html
import re


class Drafts:
    def __init__(self, store, client):
        self.store, self.client = store, client

    @staticmethod
    def content(article):
        # Persisted unique marker permits exact reconciliation after a lost response.
        content = article['html']
        for image_id in article.get('image_ids', []):
            url = article.get('image_urls', {}).get(image_id)
            if not url:
                raise Conflict('正文图片还未确认上传完成，禁止创建草稿。')
            content = content.replace('data-image-id="'+image_id+'"', 'src="'+html.escape(url,quote=True)+'"')
        if re.search(r'data-image-id=', content):
            raise Conflict('正文存在未处理的图片引用。')
        return content + '<p style="font-size:10px;color:#999">稿件编号：%s</p>' % article['id']

    def sync(self, article_id, expected_hash, expected_revision=None):
        before = self.store.get(article_id)
        cover_bytes = self.store.get_cover(before['cover_id']) if before.get('cover_id') else None
        images = {image_id:self.store.get_image(image_id) for image_id in before.get('image_ids', [])}
        def claim(article):
            if article['status'] != 'saved':
                raise Conflict('已有同步记录；请查询状态或核对结果，不会重复创建。')
            if expected_hash != article['content_hash']:
                raise Conflict('文章版本已变化；请重新加载并检查预览。')
            if expected_revision is not None and expected_revision != article['revision']:
                raise Conflict('文章版本已变化；请重新核对当前稿件。')
            article['status'] = 'image_pending' if images else 'cover_pending'
        article = self.store.change(article_id, claim)
        # Any exception leaves a durable pending barrier; never retry the write.
        for image_id, raw in images.items():
            image_url = self.client.upload_body_image(raw)
            def save_image(item):
                item.setdefault('image_urls', {})[image_id] = image_url
            article = self.store.change(article_id, save_image)
        if images:
            article = self.store.change(article_id, lambda item:item.update(status='cover_pending'))
        cover = self.client.upload_cover(cover_bytes) if cover_bytes is not None else self.client.upload_cover()
        def prepare(item):
            item.update(thumb_media_id=cover, status='draft_pending')
        article = self.store.change(article_id, prepare)
        payload = {'title': article['title'], 'author': 'LabVIEW伯乐',
                   'content': self.content(article), 'thumb_media_id': cover,
                   'need_open_comment': 0, 'only_fans_can_comment': 0}
        result = self.client.call('draft/add', payload={'articles': [payload]})
        if not result.get('media_id'):
            raise Conflict('创建响应缺少标识；请只读核对，禁止重新提交。')
        self.store.change(article_id, lambda item: item.update(media_id=result['media_id'], status='readback_pending'))
        return self.reconcile(article_id)

    def reconcile(self, article_id):
        article = self.store.get(article_id)
        if article['status'] == 'saved':
            return article
        if not article.get('media_id'):
            if article['status'] == 'image_pending':
                raise Conflict('正文图片上传结果待核对，未创建草稿；平台不会自动重复上传。')
            if article['status'] == 'cover_pending':
                raise Conflict('封面上传结果待核对。请检查公众号永久图片素材；平台不会自动重复上传。')
            # bounded scan; no match is never permission to retry a write.
            matches = set()
            for offset in range(0, 1000, 20):
                data = self.client.call('draft/batchget', payload={'offset': offset, 'count': 20, 'no_content': 0})
                items = data.get('item', [])
                for entry in items:
                    news = entry.get('content', {}).get('news_item', [])
                    if any(self.matches(article, item) for item in news):
                        matches.add(entry['media_id'])
                if not items or offset + len(items) >= data.get('total_count', 0):
                    break
            if len(matches) != 1:
                raise Conflict('未唯一定位草稿（最多核对1000条）；保持禁止写入，请在公众号后台检查。')
            article = self.store.change(article_id, lambda item: item.update(media_id=next(iter(matches)), status='readback_pending'))
        result = self.client.call('draft/get', payload={'media_id': article['media_id']})
        if not any(self.matches(article, item) for item in result.get('news_item', [])):
            raise Conflict('草稿读回未匹配标题、封面或稿件编号；保留状态，需人工检查。')
        return self.store.change(article_id, lambda item: item.update(status='synced'))

    @staticmethod
    def matches(article, item):
        content = html.unescape(item.get('content', '')).replace('http://','https://')
        return (item.get('title') == article['title'] and
                item.get('thumb_media_id') == article.get('thumb_media_id') and
                '稿件编号：' + article['id'] in item.get('content', '') and
                all(url.replace('http://','https://') in content for url in article.get('image_urls',{}).values()))
