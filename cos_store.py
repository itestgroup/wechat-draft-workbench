"""Append-only COS revisions; create-only writes elect one transition winner."""
import hashlib
import json
import os
import re
import copy
from datetime import datetime, timezone

from article_store import Conflict
from cover_assets import validate_id as validate_cover_id, validate_png, MAX_COVER_BYTES


class StorageError(Exception):
    pass


def safe_diagnostic(error):
    """Only fixed messages/codes; exception text and response bodies stay private."""
    known = {
        'Configure COS_BUCKET and WECHAT_APP_ID in SCF.': ('CONFIGURATION_REQUIRED', 'Check COS_BUCKET and WECHAT_APP_ID environment variables.'),
        'Attach a COS-authorized execution role to SCF.': ('EXECUTION_ROLE_REQUIRED', 'Select and save the SCF execution role; temporary role credentials are missing.'),
        'Bundled COS SDK lacks required create-only support.': ('SDK_UPGRADE_REQUIRED', 'COS SDK lacks required forbid-overwrite support.'),
        'COS bucket must never have enabled versioning; no writes attempted.': ('UNSAFE_BUCKET_VERSIONING', 'Use a bucket that has never enabled versioning; Suspended is also unsafe.')
    }
    if isinstance(error, StorageError) and str(error) in known:
        code, reason = known[str(error)]
        return {'code': code, 'reason': reason}
    if isinstance(error, ImportError):
        return {'code': 'SDK_UNAVAILABLE', 'reason': 'COS SDK is missing or incomplete in this runtime; deploy the SDK dependency.'}
    allowed = {
        'AccessDenied': 'Execution role is not authorized for the bucket or requested COS operation.',
        'NoSuchBucket': 'Bucket does not exist in the configured account/region.',
        'SignatureDoesNotMatch': 'COS temporary credential signature failed.',
        'InvalidAccessKeyId': 'COS temporary credentials were not accepted.',
        'ExpiredToken': 'COS execution-role credentials have expired.',
        'InvalidSecurity': 'COS temporary session token was not accepted.',
        'RequestTimeTooSkewed': 'COS request clock mismatch.'
    }
    if hasattr(error, 'get_error_code'):
        code = error.get_error_code()
        if code in allowed:
            return {'code': code, 'reason': allowed[code]}
    return {'code': 'STORAGE_CHECK_FAILED', 'reason': 'Storage operation failed; raw exception and credentials withheld.'}


class CosStore:
    increments_revision = True
    def __init__(self, client, bucket, account):
        self.client, self.bucket = client, bucket
        self.prefix = 'labview/' + hashlib.sha256(account.encode()).hexdigest()[:24] + '/articles/'
        self.check_versioning()

    def check_versioning(self):
        # COS forbids overwrite only on buckets that have NEVER enabled versioning.
        if self.client.get_bucket_versioning(Bucket=self.bucket).get('Status'):
            raise StorageError('COS bucket must never have enabled versioning; no writes attempted.')

    def catalog_view(self):
        view = copy.copy(self)
        view.prefix = self.prefix.replace('/articles/','/catalog/')
        return view

    def oauth_view(self):
        view = copy.copy(self)
        view.prefix = self.prefix.replace('/articles/','/oauth/')
        return view

    def create(self, record):
        self.write_revision(record)
        return record

    @staticmethod
    def validate_id(article_id):
        if not isinstance(article_id, str) or not re.fullmatch(r'[0-9a-f]{32}', article_id):
            raise ValueError('Invalid article ID.')

    def keys(self, prefix):
        marker = ''
        for _ in range(100):
            result = self.client.list_objects(Bucket=self.bucket, Prefix=prefix, Marker=marker, MaxKeys=1000)
            for item in result.get('Contents', []):
                yield item['Key']
            if str(result.get('IsTruncated', 'false')).lower() != 'true':
                return
            next_marker = result.get('NextMarker')
            if not next_marker or next_marker == marker:
                raise StorageError('Incomplete COS listing; stopped.')
            marker = next_marker
        raise StorageError('COS listing limit reached; stopped.')

    def read(self, key):
        body = self.client.get_object(Bucket=self.bucket, Key=key)['Body'].get_raw_stream()
        try:
            return json.loads(body.read(512 * 1024))
        finally:
            body.close()

    def get(self, article_id):
        self.validate_id(article_id)
        prefix = self.prefix + article_id + '/'
        keys = [key for key in self.keys(prefix) if re.fullmatch(r'\d{10}\.json', key[len(prefix):])]
        if not keys:
            raise KeyError(article_id)
        return self.read(max(keys))

    def list(self):
        latest = {}
        for key in self.keys(self.prefix):
            match = re.fullmatch(r'([0-9a-f]{32})/(\d{10})\.json', key[len(self.prefix):])
            if match:
                article_id = match.group(1)
                latest[article_id] = max(latest.get(article_id, ''), key)
        if len(latest) > 500:
            raise StorageError('Article list exceeds current limit of 500.')
        return sorted((self.read(key) for key in latest.values()), key=lambda a: a['updated_at'], reverse=True)

    def write_revision(self, article):
        self.check_versioning()
        key = self.prefix + article['id'] + '/%010d.json' % article['revision']
        try:
            self.client.put_object(Bucket=self.bucket, Key=key,
                Body=json.dumps(article, ensure_ascii=False).encode(),
                ContentType='application/json; charset=utf-8',
                Metadata={'x-cos-forbid-overwrite': 'true'})
        except Exception as exc:
            code = exc.get_error_code() if hasattr(exc, 'get_error_code') else ''
            if code in {'ObjectAlreadyExists', 'PreconditionFailed', 'Conflict'}:
                raise Conflict('文章状态已变化或请求结果待核对；请重新加载，不重复写入。') from None
            # Even a lost successful PUT response must not proceed to a WeChat write.
            raise StorageError('COS write result uncertain; reload state before another operation.') from None

    def change(self, article_id, mutate):
        article = self.get(article_id)
        mutate(article)
        article['revision'] += 1
        article['updated_at'] = datetime.now(timezone.utc).isoformat()
        self.write_revision(article)
        return article

    def cover_key(self, cover_id, purpose='cover'):
        validate_cover_id(cover_id)
        return self.prefix.replace('/articles/', '/images/' if purpose == 'body' else '/covers/') + cover_id + '.png'

    def get_cover(self, cover_id, purpose='cover'):
        stream = self.client.get_object(Bucket=self.bucket, Key=self.cover_key(cover_id, purpose))['Body'].get_raw_stream()
        try:
            raw = stream.read(MAX_COVER_BYTES + 1)
        finally:
            stream.close()
        if validate_png(raw, purpose) != cover_id:
            raise StorageError('Stored cover integrity check failed.')
        return raw

    def put_cover(self, cover_id, raw, purpose='cover'):
        if validate_png(raw, purpose) != cover_id:
            raise ValueError('Cover hash mismatch.')
        self.check_versioning()
        try:
            self.client.put_object(Bucket=self.bucket, Key=self.cover_key(cover_id, purpose), Body=raw,
                ContentType='image/png', Metadata={'x-cos-forbid-overwrite': 'true'})
        except Exception as exc:
            if hasattr(exc, 'get_error_code') and exc.get_error_code() in {'ObjectAlreadyExists','PreconditionFailed','Conflict'}:
                if self.get_cover(cover_id, purpose) == raw:
                    return
            raise StorageError('Cover storage result uncertain; no WeChat upload was attempted.') from None

    def put_image(self, image_id, raw):
        return self.put_cover(image_id, raw, 'body')

    def get_image(self, image_id):
        return self.get_cover(image_id, 'body')

    def save(self, data, rendered):
        article_id = data.get('id')
        if article_id:
            def mutate(article):
                if article['status'] != 'saved' or data.get('revision') != article['revision']:
                    raise Conflict('文章已开始同步或版本已变化；请重新加载。')
                article.update(title=rendered['title'], markdown=data['markdown'],
                               html=rendered['html'], content_hash=rendered['content_hash'],
                               cover_id=rendered.get('cover_id', ''), render_version=rendered.get('render_version', 1))
                article['image_ids'] = rendered.get('image_ids', [])
            return self.change(article_id, mutate)
        article_id = rendered['content_hash'][:32]
        try:
            existing = self.get(article_id)
            if existing['content_hash'] != rendered['content_hash']:
                raise Conflict('该文章标识已有后续编辑，请打开原文章继续处理。')
            return existing
        except KeyError:
            pass
        article = dict(rendered, id=article_id, markdown=data['markdown'], status='saved',
                       revision=1, updated_at=datetime.now(timezone.utc).isoformat())
        self.write_revision(article)
        return article


def from_environment():
    from qcloud_cos import CosConfig, CosS3Client
    from qcloud_cos.cos_comm import mapped
    if mapped({'Metadata': {'x-cos-forbid-overwrite': 'true'}}).get('x-cos-forbid-overwrite') != 'true':
        raise StorageError('Bundled COS SDK lacks required create-only support.')
    bucket = os.environ.get('COS_BUCKET', '')
    account = os.environ.get('WECHAT_APP_ID', '')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]+-\d+', bucket) or not account:
        raise StorageError('Configure COS_BUCKET and WECHAT_APP_ID in SCF.')
    credentials = [os.environ.get(name) for name in
                   ('TENCENTCLOUD_SECRETID', 'TENCENTCLOUD_SECRETKEY', 'TENCENTCLOUD_SESSIONTOKEN')]
    if not all(credentials):
        raise StorageError('Attach a COS-authorized execution role to SCF.')
    config = CosConfig(Region='ap-guangzhou', SecretId=credentials[0], SecretKey=credentials[1],
                       Token=credentials[2], Scheme='https', Timeout=15)
    return CosStore(CosS3Client(config), bucket, account)
