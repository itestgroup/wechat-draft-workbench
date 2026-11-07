"""User-confirmed published-link index. No WeChat writes or URL fetching."""
import hashlib
import csv
import io
import re
from datetime import date, datetime, timezone, timedelta
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from article_store import Conflict


class IndexValidation(ValueError):
    pass


def article_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise IndexValidation('请填写微信公众号已发表文章链接。')
    try:
        url = urlsplit(value.strip())
        if url.scheme != 'https' or url.netloc != 'mp.weixin.qq.com' or url.fragment not in ('','rd','wechat_redirect'):
            raise ValueError()
        pairs = parse_qsl(url.query, keep_blank_values=True)
        if any(re.search(r'token|secret|password|credential', key, re.I) for key, _ in pairs):
            raise IndexValidation('链接包含疑似凭据参数，请使用公众号公开文章链接。')
        if re.fullmatch(r'/s/[A-Za-z0-9_-]+', url.path):
            return urlunsplit(('https','mp.weixin.qq.com',url.path,'',''))
        if url.path != '/s':
            raise ValueError()
        selected = {}
        for key, val in pairs:
            if key in ('__biz','mid','idx','sn'):
                if key in selected:
                    raise ValueError()
                selected[key] = val
        if not (re.fullmatch(r'[A-Za-z0-9_+=/-]{1,128}', selected.get('__biz','')) and
                re.fullmatch(r'\d{1,30}', selected.get('mid','')) and
                re.fullmatch(r'\d{1,3}', selected.get('idx','')) and
                re.fullmatch(r'[A-Fa-f0-9]{32}', selected.get('sn',''))):
            raise ValueError()
        query = urlencode([(key,selected[key]) for key in ('__biz','mid','idx','sn')])
        return urlunsplit(('https','mp.weixin.qq.com','/s',query,''))
    except IndexValidation:
        raise
    except (ValueError,TypeError):
        raise IndexValidation('请使用 https://mp.weixin.qq.com/s/… 或完整的公开文章长链接。') from None


def clean_text(value, limit, required=False):
    if not isinstance(value,str) or len(value.strip()) > limit or (required and not value.strip()) or any(ord(c)<32 for c in value):
        raise IndexValidation('索引字段格式或长度不正确。')
    return value.strip()


class PublishedIndex:
    def __init__(self, store):
        self.store = store.catalog_view()

    def list(self):
        return sorted(self.store.list(), key=lambda item:(item.get('published_date',''),item['updated_at']), reverse=True)

    @staticmethod
    def normalize(data):
        if data.get('confirmed_published') is not True:
            raise IndexValidation('请先确认文章已经手动发表且链接正确；草稿不能登记为已发表。')
        url = article_url(data.get('url'))
        entry_id = hashlib.sha256(url.encode()).hexdigest()[:32]
        if data.get('id') and data['id'] != entry_id:
            raise IndexValidation('编辑索引时不能替换文章链接；请另行登记新链接。')
        title = clean_text(data.get('title',''),128,True)
        collection = clean_text(data.get('collection',''),100)
        published_date = clean_text(data.get('published_date',''),10)
        if published_date:
            try:
                parsed = date.fromisoformat(published_date)
                if parsed.isoformat()!=published_date or parsed > datetime.now(timezone(timedelta(hours=8))).date():
                    raise ValueError()
            except ValueError:
                raise IndexValidation('发表日期须为YYYY-MM-DD，不能晚于北京时间今天。') from None
        tags = data.get('tags',[])
        if not isinstance(tags,list) or len(tags)>10:
            raise IndexValidation('标签最多10个。')
        tags = list(dict.fromkeys(clean_text(tag,30,True) for tag in tags))
        fields = {'title':title,'url':url,'collection':collection,'published_date':published_date,
                  'tags':tags,'confirmed_published':True}
        return entry_id, fields

    def save(self, data):
        entry_id, fields = self.normalize(data)
        try:
            current = self.store.get(entry_id)
        except KeyError:
            current = None
        if current:
            if all(current.get(key)==value for key,value in fields.items()):
                return current
            if data.get('revision') != current['revision']:
                raise Conflict('该链接已登记或索引已被修改，请从索引中载入后再保存。')
            def update(item):
                if item['revision'] != data['revision']:
                    raise Conflict('索引版本已变化，请重新载入。')
                item.update(fields)
                item['revision'] += 1 if not getattr(self.store,'increments_revision',False) else 0
            return self.store.change(entry_id,update)
        record = dict(fields,id=entry_id,revision=1,updated_at=datetime.now(timezone.utc).isoformat())
        try:
            return self.store.create(record)
        except Conflict:
            current = self.store.get(entry_id)
            if all(current.get(key)==value for key,value in fields.items()):
                return current
            raise

    def import_one(self, data):
        """Add only: existing metadata is never overwritten, including after races."""
        entry_id, fields = self.normalize(data)
        try:
            self.store.get(entry_id)
            return {'id':entry_id,'result':'existing'}
        except KeyError:
            pass
        record = dict(fields,id=entry_id,revision=1,updated_at=datetime.now(timezone.utc).isoformat())
        try:
            self.store.create(record)
            return {'id':entry_id,'result':'created'}
        except Conflict:
            self.store.get(entry_id)  # Confirm the object exists before reporting a skip.
            return {'id':entry_id,'result':'existing'}

    def import_preview(self, data):
        text, kind = data.get('text'), data.get('format')
        if not isinstance(text,str) or len(text.encode()) > 200*1024 or kind not in ('csv','markdown'):
            raise IndexValidation('请选择UTF-8 CSV或Markdown，文件不超过200KiB。')
        text = text.lstrip('\ufeff')
        rows = []
        if kind == 'csv':
            reader = csv.DictReader(io.StringIO(text,newline=''),strict=True)
            try:
                columns = reader.fieldnames or []
            except csv.Error:
                raise IndexValidation('CSV表头格式错误，请检查引号。') from None
            if (len(columns)!=len(set(columns)) or not {'title','url'} <= set(columns)
                    or set(columns)-{'title','url','published_date','collection','tags'}):
                raise IndexValidation('CSV表头须含title,url，可选published_date,collection,tags，不允许重复或其他列。')
            try:
                for item in reader:
                    if None in item or any(value is None for value in item.values()):
                        raise IndexValidation('CSV列数不一致，请修正文件。')
                    item['tags'] = [tag.strip() for tag in item.get('tags','').split(';') if tag.strip()]
                    rows.append((reader.line_num,item))
            except csv.Error:
                raise IndexValidation('CSV格式错误，请检查引号和分隔符。') from None
        else:
            fence = None
            for line_number,line in enumerate(text.splitlines(),1):
                stripped = line.strip()
                marker = re.match(r'^(`{3,}|~{3,})(.*)$',stripped)
                if fence:
                    if marker and marker[1][0]==fence[0] and len(marker[1])>=len(fence) and not marker[2].strip():
                        fence=None
                    continue
                if marker:
                    fence=marker[1]
                    continue
                if not stripped:
                    continue
                match = re.fullmatch(r'- \[([^\[\]]+)\]\((https://[^\s()]+)\)',stripped)
                rows.append((line_number,{'title':match[1],'url':match[2]} if match else None))
            if fence:
                raise IndexValidation('Markdown代码块未闭合，请修正文件。')
        if not 1 <= len(rows) <= 100:
            raise IndexValidation('每批须包含1至100条文章记录。')
        existing = {entry['id'] for entry in self.list()}
        seen, output = set(), []
        for line_number,item in rows:
            row = {'line':line_number}
            try:
                if item is None:
                    raise IndexValidation('只支持独立的 - [标题](公开链接) 行。')
                entry_id, fields = self.normalize(dict(item,confirmed_published=True))
                row.update(id=entry_id,entry=fields,status='duplicate' if entry_id in seen else 'existing' if entry_id in existing else 'new')
                seen.add(entry_id)
            except IndexValidation as error:
                row.update(status='invalid',error=str(error))
            output.append(row)
        return {'rows':output,'can_import':not any(row['status']=='invalid' for row in output)}

    def history(self,data):
        ids = data.get('ids')
        if not isinstance(ids,list) or not 1 <= len(ids) <= 10 or any(not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{32}',v) for v in ids):
            raise IndexValidation('请选择1至10篇已登记文章。')
        entries = [self.store.get(entry_id) for entry_id in dict.fromkeys(ids)]
        lines = ['## 往期回顾','']
        for entry in entries:
            if entry.get('confirmed_published') is not True:
                raise IndexValidation('往期回顾只能引用已确认发表的文章。')
            label = re.sub(r'[\[\]\\`*<>]', '',entry['title'])
            lines.append('- ['+label+']('+article_url(entry['url'])+')')
        return {'markdown':'\n'.join(lines)+'\n','count':len(entries)}
