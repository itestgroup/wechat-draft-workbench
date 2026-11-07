import {createClient} from './api-client.mjs';
import {makeCover, makeBodyImage} from './cover.mjs';
import {upsertHistory} from './history.mjs';
import {authorizationRequest,authorizationRedirect} from './oauth-consent.mjs';
const $ = selector => document.querySelector(selector);
const transport = createClient();
const isLocal = ['127.0.0.1','localhost'].includes(location.hostname);
let connected = isLocal;
let cloud = false;
let syncEnabled = false;
let current = null;
let busy = false;
let coverId = '';
let preparedCover = null;
let coverGeneration = 0;
let preparedBodyImage = null;
let bodyGeneration = 0;
let previewGeneration = 0;
let indexEntries=[];
let editingIndex=null;
let importPlan=null;
let oauthRequest='';
let oauthDetails=null;
try {oauthRequest=authorizationRequest(location.hash);}
catch {$('#oauth-status').textContent='授权请求无效，请回到插件页面重新连接。';$('#oauth-consent').hidden=false;}
if(location.hash.startsWith('#oauth_request=')) history.replaceState(null,'',location.pathname+location.search);
if(oauthRequest) {
  $('#oauth-consent').hidden=false;
  $('#oauth-status').textContent='请先用管理密钥连接本工作台，再核对插件权限并批准。不要向ChatGPT提供管理密钥。';
}
const selectedIndex=new Set();
const labels = {saved:'已保存', image_pending:'正文图片结果待核对', cover_pending:'封面结果待核对', draft_pending:'草稿结果待核对', readback_pending:'等待读回', synced:'已同步草稿'};
async function api(path, data) {
  if (!connected) throw new Error('请先连接云端平台。');
  return transport.request(path, data);
}
function fields() { return {title:$('#title').value,markdown:$('#markdown').value,cover_id:coverId}; }
function clearCover() {
  coverGeneration++; coverId=''; preparedCover=null;
  $('#cover-file').value=''; $('#cover-preview').removeAttribute('src');
  $('#cover-preview').hidden=true;
}
function show(article) {
  const generation=++previewGeneration;
  $('#preview-title').textContent = article.title;
  $('#preview').srcdoc = '<!doctype html><meta charset="utf-8">' + article.html;
  const ids=article.image_ids || [];
  if(ids.length) Promise.all(ids.map(async id=>[id,await api('/api/images/'+id)])).then(images=>{
    if(generation!==previewGeneration) return;
    let body=article.html;
    for(const [id,image] of images) {
      if(!/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(image.data_url)) throw new Error('图片预览格式无效。');
      body=body.split('data-image-id="'+id+'"').join('src="'+image.data_url+'"');
    }
    $('#preview').srcdoc='<!doctype html><meta charset="utf-8">'+body;
  }).catch(()=>{if(generation===previewGeneration) $('#status').textContent='部分正文图片未能加载，请检查预览后再同步。';});
}
function clearBodyImage() {
  bodyGeneration++;preparedBodyImage=null;
  $('#body-file').value='';$('#body-preview').hidden=true;$('#body-preview').removeAttribute('src');
}
function select(article) {
  clearCover();clearBodyImage();
  current = article;
  $('#title').value = article.title;
  $('#markdown').value = article.markdown;
  show(article);
  $('#status').textContent = (article.source==='ai'?'AI投稿 · 请审阅内容及配图后再同步 · ':'')+labels[article.status];
  if(article.cover_id) {
    coverId=article.cover_id;
    const generation=coverGeneration;
    api('/api/covers/'+coverId).then(result=>{
      if(generation !== coverGeneration) return;
      $('#cover-preview').src=result.data_url; $('#cover-preview').hidden=false;
    }).catch(()=>{
      if(generation===coverGeneration) $('#status').textContent='文章已加载，封面读取失败。请先检查封面再同步。';
    });
  }
  updateControls();
}
async function refresh() {
  const result = await api('/api/articles');
  syncEnabled = result.wechat_enabled;
  $('#connection').textContent = (cloud ? '已连接云端 COS' : '本地工作台') +
    (syncEnabled ? ' · 微信草稿同步已启用' : ' · 微信草稿同步关闭');
  $('#article-heading').textContent = cloud ? '云端文章' : '本地文章';
  const list = $('#articles');
  list.replaceChildren();
  for (const article of result.articles) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = (article.source==='ai'?'[AI投稿] ':'')+article.title + ' · ' + labels[article.status];
    button.onclick = () => { if (!busy) select(article); };
    list.append(button);
  }
  updateControls();
  try {await refreshIndex();} catch {$('#index-status').textContent='索引读取失败，请稍后重试。';}
}
function updateControls() {
  for(const id of ['oauth-approve','oauth-deny']) $( '#'+id).disabled=busy || !connected || !oauthRequest || !oauthDetails;
  $('#import-run').disabled=busy || !connected || !importPlan?.can_import || !importPlan.rows.some(row=>row.status==='new') || !$('#import-confirm').checked;
  $('#sync').disabled = busy || !connected || !syncEnabled;
  $('#reconcile').disabled = busy || !connected || !syncEnabled;
  $('#upload-cover').disabled = busy || !connected || !preparedCover;
  $('#body-upload').disabled = busy || !connected || !preparedBodyImage;
}
async function action(fn) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button,input,textarea').forEach(b => b.disabled = true);
  try { await fn(); }
  catch (error) { $('#status').textContent = error.message; $('#index-status').textContent=error.message; }
  finally {
    if (connected) try { await refresh(); } catch { $('#connection').textContent = '无法读取文章状态，请检查连接'; }
    busy = false;
    document.querySelectorAll('button,input,textarea').forEach(b => b.disabled = false);
    updateControls();
  }
}
$('#editor').addEventListener('submit', event => {
  event.preventDefault();
  action(async () => { show(await api('/api/preview',fields())); $('#status').textContent='预览已生成，尚未保存本次编辑。'; });
});
$('#save').onclick = () => action(async () => {
  if(preparedCover) throw new Error('封面尚未上传，请先点击“上传并应用封面”，或恢复默认封面。');
  const data = fields();
  if (current) Object.assign(data,{id:current.id,revision:current.revision});
  select(await api('/api/articles/save',data));
});
$('#new').onclick = () => {
  clearCover();clearBodyImage();previewGeneration++;
  current = null; $('#title').value=''; $('#markdown').value='';
  $('#preview-title').textContent='文章预览'; $('#preview').srcdoc='';
  $('#status').textContent='新文章，请填写标题和正文。';
  updateControls();
};
$('#sync').onclick = () => action(async () => {
  if (!current) throw new Error('请先保存文章。');
  const data=fields();
  if(preparedCover || data.cover_id !== (current.cover_id || '') || data.title.trim() !== current.title || data.markdown !== current.markdown) throw new Error('存在未保存修改，请先上传封面并保存文章。');
  if(current.status !== 'saved') throw new Error('已开始同步，请使用只读核对。');
  if (!window.confirm((coverId ? '将上传当前自定义封面' : '将上传默认蓝色封面')+'及正文图片，并创建一篇公众号草稿。不会公开发表。是否继续？')) return;
  const articleId = current.id;
  try { select(await api('/api/articles/sync',{id:articleId,confirm:true,content_hash:current.content_hash})); }
  catch(error) {
    try { const r=await api('/api/articles'); current=r.articles.find(a=>a.id===articleId) || current; } catch {}
    throw error;
  }
});
$('#reconcile').onclick = () => action(async () => {
  if(!current) throw new Error('请先选择文章。');
  select(await api('/api/articles/reconcile',{id:current.id}));
});
$('#cloud-connect').onclick = () => action(async () => {
  clearCover();clearBodyImage();previewGeneration++;
  clearIndexSession();
  transport.disconnect(); connected = false; current = null;
  $('#articles').replaceChildren();
  try {
    transport.connect($('#api-url').value, $('#api-key').value);
    $('#api-key').value = '';
    const health = await transport.request('/api/health');
    if (health.status !== 'ok') throw new Error('云端健康检查未通过。');
    connected = true; cloud = true;
    if(oauthRequest) await loadOAuthConsent();
    $('#status').textContent = '云端已连接。请保存一篇测试文章，再从文章列表重新打开，验证持久存储。';
  } catch (error) {
    transport.disconnect(); connected=false;cloud=false;syncEnabled = false;oauthDetails=null;
    $('#connection').textContent = '云端尚未连接';
    throw error;
  } finally { $('#api-key').value = ''; }
});
$('#disconnect').onclick = () => {
  oauthRequest='';oauthDetails=null;$('#oauth-details').textContent='';
  $('#oauth-status').textContent='授权会话已清除。如需连接插件，请从插件页面重新发起。';
  clearCover();clearBodyImage();previewGeneration++;
  clearIndexSession();
  transport.disconnect(); connected = false; cloud = false; current = null; syncEnabled = false;
  $('#api-key').value = ''; $('#articles').replaceChildren();
  $('#preview').srcdoc = ''; $('#title').value = ''; $('#markdown').value = '';
  $('#connection').textContent = '已断开，访问密钥已从本页会话清除。';
  updateControls();
};
async function prepareCover() {
  const file=$('#cover-file').files[0];
  const generation=++coverGeneration;
  preparedCover=null; coverId='';
  $('#cover-preview').hidden=true; updateControls();
  if(!file) return;
  try {
    const result=await makeCover(file,$('#watermark').checked);
    if(generation!==coverGeneration) return;
    preparedCover=result;
    $('#cover-preview').src=result.url; $('#cover-preview').hidden=false;
    $('#status').textContent='封面预览已生成，尚未上传。请检查裁切与水印，再上传并保存文章。';
  } catch(error) { if(generation===coverGeneration) $('#status').textContent=error.message; }
  finally { updateControls(); }
}
$('#cover-file').onchange=prepareCover;
$('#watermark').onchange=()=>{ if($('#cover-file').files.length) prepareCover(); };
$('#upload-cover').onclick=()=>action(async()=>{
  if(!preparedCover) throw new Error('请先选择图片并检查预览。');
  const result=await api('/api/covers/upload',{png_base64:preparedCover.png_base64});
  coverId=result.cover_id; preparedCover=null;
  $('#status').textContent='封面已保存到私有存储，尚未上传微信。请保存文章以应用。';
});
async function loadOAuthConsent() {
  oauthDetails=await api('/api/oauth/inspect',{request:oauthRequest});
  const scopeNames={'labview:submit':'提交新稿到平台待审','labview:status':'查询本人稿件及微信草稿状态','labview:index':'读取已登记公开文章索引','labview:wechat-draft':'明确确认后新建微信草稿（不覆盖、不发表、不群发）'};
  $('#oauth-details').textContent='客户端：'+oauthDetails.client_id+'\n回调：'+oauthDetails.redirect_uri+'\n权限：'+oauthDetails.scope.split(' ').map(scope=>scopeNames[scope] || scope).join('、');
  $('#oauth-status').textContent=oauthDetails.scope.split(' ').includes('labview:wechat-draft')
    ? '仅在你刚从插件页面发起连接时批准。此授权新增：明确确认后创建微信草稿。不覆盖已有草稿、不发表、不群发。'
    : '仅在你刚从插件页面发起连接时批准。授权不包含编辑已有稿件、同步微信或发表。';
}
async function oauthDecision(approve) {
  if(!oauthRequest || !oauthDetails || !cloud) throw new Error('请先连接云端并核对授权请求。');
  const request=oauthRequest,details=oauthDetails;
  oauthRequest='';oauthDetails=null;
  try {
    const result=await api('/api/oauth/approve',{request,approve});
    location.assign(authorizationRedirect(result.redirect_url,details.issuer,details.redirect_uri));
  } catch {
    $('#oauth-status').textContent='授权结果未能确认，已停止自动重试。请从插件页面重新发起连接；本操作未投稿或同步微信。';
  }
}
$('#oauth-approve').onclick=()=>action(()=>oauthDecision(true));
$('#oauth-deny').onclick=()=>action(()=>oauthDecision(false));
$('#default-cover').onclick=()=>{clearCover(); $('#status').textContent='已选择默认蓝色封面，请保存文章。'; updateControls();};
async function prepareBodyImage() {
  const file=$('#body-file').files[0];const generation=++bodyGeneration;
  preparedBodyImage=null;$('#body-preview').hidden=true;updateControls();
  if(!file) return;
  try {
    const result=await makeBodyImage(file,$('#body-watermark').checked);
    if(generation!==bodyGeneration) return;
    preparedBodyImage=result;$('#body-preview').src=result.url;$('#body-preview').hidden=false;
    $('#status').textContent='正文图片已预处理，请检查水印与清晰度，再上传并插入。';
  } catch(error) {if(generation===bodyGeneration) $('#status').textContent=error.message;}
  finally {updateControls();}
}
$('#body-file').onchange=prepareBodyImage;
$('#body-watermark').onchange=()=>{if($('#body-file').files.length) prepareBodyImage();};
$('#body-upload').onclick=()=>action(async()=>{
  if(!preparedBodyImage) throw new Error('请先选择正文图片。');
  const result=await api('/api/images/upload',{png_base64:preparedBodyImage.png_base64});
  const label=$('#body-alt').value.replace(/[\[\]\\\r\n]/g,' ').slice(0,100) || '正文图片';
  const editor=$('#markdown');
  editor.setRangeText('\n\n!['+label+'](asset:'+result.image_id+')\n\n',editor.selectionStart,editor.selectionEnd,'end');
  clearBodyImage();
  show(await api('/api/preview',fields()));
  $('#status').textContent='正文图片已存入私有存储并插入Markdown，尚未上传微信。请保存文章。';
});
function resetIndexForm() {
  editingIndex=null;$('#index-form').reset();$('#index-url').readOnly=false;
}
function clearIndexSession() {
  clearImport();$('#import-file').value='';$('#import-status').textContent='';
  indexEntries=[];selectedIndex.clear();resetIndexForm();$('#index-list').replaceChildren();$('#index-count').textContent='';
  $('#history-output').value='';$('#history-output').hidden=true;
}
function loadIndex(entry) {
  editingIndex=entry;$('#index-title').value=entry.title;$('#index-url').value=entry.url;$('#index-url').readOnly=true;
  $('#index-date').value=entry.published_date || '';$('#index-collection').value=entry.collection || '';
  $('#index-tags').value=(entry.tags || []).join(', ');$('#index-confirm').checked=entry.confirmed_published===true;
  $('#index-status').textContent='已载入索引，编辑只影响平台登记信息。';
}
function renderIndex() {
  const keyword=$('#index-search').value.trim().toLowerCase();const list=$('#index-list');list.replaceChildren();
  const entries=indexEntries.filter(entry=>[entry.title,entry.collection,...(entry.tags || [])].join(' ').toLowerCase().includes(keyword));
  $('#index-count').textContent='共'+indexEntries.length+'篇，当前显示'+entries.length+'篇';
  for(const entry of entries) {
    const row=document.createElement('div');row.style.cssText='border-bottom:1px solid #dbe4eb;padding:12px 0';
    const label=document.createElement('label');const check=document.createElement('input');
    check.type='checkbox';check.style.width='auto';check.checked=selectedIndex.has(entry.id);check.disabled=busy;
    check.onchange=()=>{if(check.checked) selectedIndex.add(entry.id);else selectedIndex.delete(entry.id);};
    label.append(check,document.createTextNode(' '+entry.title));
    const link=document.createElement('a');link.href=entry.url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='打开原文';
    const edit=document.createElement('button');edit.type='button';edit.textContent='载入登记信息';edit.disabled=busy;edit.onclick=()=>loadIndex(entry);
    const details=document.createElement('small');details.textContent=[entry.published_date,entry.collection,(entry.tags || []).join(' / ')].filter(Boolean).join(' · ');
    row.append(label,details,document.createElement('br'),link,document.createTextNode(' '),edit);list.append(row);
  }
}
async function refreshIndex() {
  const result=await api('/api/index');indexEntries=result.entries;
  for(const id of selectedIndex) if(!indexEntries.some(entry=>entry.id===id)) selectedIndex.delete(id);
  renderIndex();
}
$('#index-search').oninput=renderIndex;
$('#index-new').onclick=()=>{resetIndexForm();$('#index-status').textContent='填写已发表文章的信息；不会创建或修改公众号文章。';};
$('#index-current').onclick=()=>{resetIndexForm();$('#index-title').value=$('#title').value;$('#index-status').textContent='已带入标题。请手动发表后粘贴公开链接并确认，不能用草稿链接。';};
$('#index-form').addEventListener('submit',event=>{
  event.preventDefault();action(async()=>{
    const data={title:$('#index-title').value,url:$('#index-url').value,published_date:$('#index-date').value,
      collection:$('#index-collection').value,tags:$('#index-tags').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean),
      confirmed_published:$('#index-confirm').checked};
    if(editingIndex) Object.assign(data,{id:editingIndex.id,revision:editingIndex.revision});
    loadIndex(await api('/api/index/save',data));$('#index-status').textContent='索引已保存。未修改公众号文章或合集。';
  });
});
function clearImport() {
  importPlan=null;$('#import-confirm').checked=false;$('#import-rows').replaceChildren();updateControls();
}
$('#import-file').onchange=()=>{clearImport();$('#import-status').textContent='请先预览新文件。';};
$('#import-confirm').onchange=updateControls;
$('#import-preview').onclick=()=>action(async()=>{
  clearImport();
  const file=$('#import-file').files[0];
  if(!file || file.size>200*1024) throw new Error('请选择不超过200KiB的UTF-8 CSV或Markdown文件。');
  const format=/\.csv$/i.test(file.name)?'csv':/\.(md|markdown)$/i.test(file.name)?'markdown':null;
  if(!format) throw new Error('只支持.csv、.md或.markdown文件。');
  let text;
  try {text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());}
  catch {throw new Error('文件须为UTF-8编码，请转换后重试。');}
  if(new TextEncoder().encode(JSON.stringify({format,text})).length>256*1024) throw new Error('文件转为请求后过大，请拆分清单后导入。');
  importPlan=await api('/api/index/importpreview',{format,text});
  const labels={new:'待新增',existing:'已存在，跳过',duplicate:'文件内重复，跳过',invalid:'格式错误'};
  for(const row of importPlan.rows) {
    const item=document.createElement('p');
    item.textContent='第'+row.line+'行 · '+labels[row.status]+' · '+(row.entry?.title || row.error);
    if(row.entry) {
      const details=document.createElement('small');
      details.textContent=[row.entry.url,row.entry.published_date,row.entry.collection,row.entry.tags.join('; ')].filter(Boolean).join(' · ');
      item.append(document.createElement('br'),details);
    }
    $('#import-rows').append(item);
  }
  const count=importPlan.rows.filter(row=>row.status==='new').length;
  $('#import-status').textContent=importPlan.can_import?'预览完成：待新增'+count+'条；尚未写入。请核对链接及信息。':'存在格式错误，请修正文件并重新预览；尚未写入。';
});
$('#import-run').onclick=()=>action(async()=>{
  if(!importPlan?.can_import || !$('#import-confirm').checked) throw new Error('请先预览并确认已发表链接。');
  const rows=importPlan.rows.filter(row=>row.status==='new');
  if(!rows.length) throw new Error('没有待新增记录。');
  // Consume the plan before writing: errors never leave a replayable write button.
  importPlan=null;$('#import-confirm').checked=false;
  let created=0,existing=0;
  for(const row of rows) {
    try {
      const result=await api('/api/index/importone',{...row.entry,confirmed_published:true});
      if(result.result==='created') created++;else if(result.result==='existing') existing++;
      else throw new Error('导入返回格式不正确。');
      $('#import-status').textContent='已确认处理'+(created+existing)+'/'+rows.length+'条：新增'+created+'，已存在'+existing+'。';
    } catch {
      let checked=false;
      try {await refreshIndex();checked=true;} catch {}
      $('#import-status').textContent='导入已停止；此前确认新增'+created+'条、跳过'+existing+'条。第'+row.line+'行结果待核对，后续条目未提交。'+(checked?'已重新读取索引，请核对列表后重新预览文件。':'索引重读失败，请恢复连接并核对实际结果后再重新预览。');
      return;
    }
  }
  $('#import-status').textContent='导入完成：新增'+created+'条，已存在跳过'+existing+'条。未修改微信内容。';
});
async function historyBlock() {
  const ids=indexEntries.filter(entry=>selectedIndex.has(entry.id)).map(entry=>entry.id);
  return api('/api/index/history',{ids});
}
$('#history-insert').onclick=()=>action(async()=>{
  if(current && current.status!=='saved') throw new Error('已同步文章不可编辑，请新建文章后插入往期回顾。');
  const result=await historyBlock();$('#markdown').value=upsertHistory($('#markdown').value,result.markdown);
  show(await api('/api/preview',fields()));
  $('#status').textContent='往期回顾已插入编辑区，尚未保存或同步。';$('#index-status').textContent=$('#status').textContent;
});
$('#history-export').onclick=()=>action(async()=>{
  const result=await historyBlock();$('#history-output').value=result.markdown;$('#history-output').hidden=false;
  $('#index-status').textContent='已生成Markdown，可选择文本复制；不包含访问密钥。';
});
if (isLocal) refresh().catch(() => $('#connection').textContent='本地服务未连接，请检查运行终端');
else $('#connection').textContent='请输入函数 URL 和平台访问密钥连接云端。';
updateControls();
