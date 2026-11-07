"""Create-only AI submissions. Never initializes a WeChat client."""
import hashlib
import json
import re
from datetime import datetime, timezone

from article_store import Conflict
from platform_preview import preview


def identity(data):
    value = data.get('submission_id')
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',value):
        raise ValueError('Invalid submission ID.')
    return value, hashlib.sha256(('ai-submission:'+value).encode()).hexdigest()[:32]


def receipt(article):
    return {'submission_id':article['submission_id'],'article_id':article['id'],
            'status':article['status'],'revision':article['revision'],
            'wechat_writes_performed':False}


def status(store, data):
    submission_id, article_id = identity(data)
    try:
        article = store.get(article_id)
    except KeyError:
        return {'submission_id':submission_id,'status':'not_found','wechat_writes_performed':False}
    if article.get('source') != 'ai' or article.get('submission_id') != submission_id:
        raise Conflict('Submission identity conflict; contact the platform owner.')
    return receipt(article)


def submit(store, data):
    if set(data) != {'submission_id','title','markdown'}:
        raise ValueError('Only submission_id, title and markdown are accepted.')
    submission_id, article_id = identity(data)
    if (not isinstance(data['markdown'],str) or len(data['markdown'])>30000
            or len(json.dumps(data,ensure_ascii=False))>80000):
        raise ValueError('Submission too large.')
    rendered = preview({'title':data['title'],'markdown':data['markdown']})
    # Images are prepared by the owner in the workbench, not guessed by the AI.
    if rendered['image_ids']:
        raise ValueError('AI submissions cannot reference private image assets.')
    digest = hashlib.sha256(json.dumps([rendered['title'],data['markdown']],ensure_ascii=False).encode()).hexdigest()

    def existing():
        current = store.get(article_id)
        if (current.get('source') != 'ai' or current.get('submission_id') != submission_id
                or current.get('submission_hash') != digest):
            raise Conflict('Submission ID already used with different content; inspect its status.')
        return receipt(current)

    try:
        return existing()
    except KeyError:
        pass
    article = dict(rendered,id=article_id,markdown=data['markdown'],status='saved',revision=1,
                   source='ai',submission_id=submission_id,submission_hash=digest,
                   updated_at=datetime.now(timezone.utc).isoformat())
    try:
        store.create(article)
    except Conflict:
        return existing()  # Resolve a competing create; never update the existing record.
    return receipt(article)
