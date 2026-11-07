import test from 'node:test';
import assert from 'node:assert/strict';
import {cloudEndpoint, createClient} from './preview/api-client.mjs';
import {upsertHistory} from './preview/history.mjs';
import {authorizationRequest,authorizationRedirect} from './preview/oauth-consent.mjs';

test('OAuth fragment accepts only one signed request and redirects only to registered ChatGPT callback',()=>{
  assert.equal(authorizationRequest('#oauth_request=aaa.bbb.ccc'),'aaa.bbb.ccc');
  assert.equal(authorizationRequest('#other=value'),'');
  assert.throws(()=>authorizationRequest('#oauth_request=aaa.bbb.ccc&oauth_request=xxx.yyy.zzz'));
  assert.throws(()=>authorizationRequest('#oauth_request=not-a-token'));
  const issuer='https://example.tencentscf.com';
  const callback='https://chatgpt.com/connector/oauth/fixture-only';
  const url=callback+'?code=fixture&state=example&iss='+encodeURIComponent(issuer);
  assert.equal(authorizationRedirect(url,issuer,callback),url);
  for(const bad of [url.replace('chatgpt.com','evil.example'),url.replace('fixture-only','different'),
    url+'&code=second',url+'#fragment',url.replace('code=fixture','error=denied&code=fixture'),
    callback+'?code=fixture&state=example&iss=wrong']) {
    assert.throws(()=>authorizationRedirect(bad,issuer,callback));
  }
});

test('history update preserves following sections and is idempotent',()=>{
  const block='## 往期回顾\n\n- [新链接](https://mp.weixin.qq.com/s/new)\n';
  const source='# 正文\n\n内容\n\n## 往期回顾\n\n旧内容\n\n## 联系方式\n\n保留内容';
  const result=upsertHistory(source,block);
  assert.ok(result.includes('## 联系方式\n\n保留内容'));
  assert.ok(!result.includes('旧内容'));
  assert.equal(upsertHistory(result,block),result);
});
test('history heading in code fences is not replaced; duplicates stop editing',()=>{
  const source='```markdown\n## 往期回顾\n示例\n```';
  const result=upsertHistory(source,'## 往期回顾\n\n新链接');
  assert.ok(result.startsWith(source));
  assert.throws(()=>upsertHistory('## 往期回顾\na\n## 往期回顾\nb','new'),/多个/);
});

test('only SCF HTTPS root endpoints accept credentials', () => {
  assert.equal(cloudEndpoint('https://example.ap-guangzhou.tencentscf.com/'),'https://example.ap-guangzhou.tencentscf.com');
  for (const value of ['http://example.tencentscf.com/','https://evil.example/','https://example.tencentscf.com/?key=x',
    'https://user:password@example.tencentscf.com/','https://example.tencentscf.com/path','https://example.tencentscf.com:8888/']) {
    assert.throws(() => cloudEndpoint(value));
  }
});
test('cloud key uses headers only; disconnect removes authorization', async () => {
  const calls=[];
  const client=createClient(async (...args) => {
    calls.push(args); return {ok:true,status:200,text:async()=>'{"status":"ok"}'};
  });
  client.connect('https://example.ap-guangzhou.tencentscf.com','x'.repeat(40));
  await client.request('/api/health');
  assert.equal(calls[0][1].headers.Authorization,'Bearer '+'x'.repeat(40));
  assert.equal(calls[0][1].redirect,'error');
  assert.ok(!calls[0][0].includes('x'.repeat(40)));
  client.disconnect();
  await client.request('/api/articles');
  assert.equal(calls[1][0],'/api/articles');
  assert.equal(calls[1][1].headers.Authorization,undefined);
});
test('network failures do not expose fetch exceptions or credentials', async () => {
  const client=createClient(async()=>{throw new Error('DO_NOT_PRINT');});
  await assert.rejects(client.request('/api/articles/save',{}), error => !error.message.includes('DO_NOT_PRINT'));
});
test('rejects wrapped function responses and unsafe request paths', async () => {
  const client=createClient(async()=>({ok:true,text:async()=>'{"statusCode":200,"body":"{}"}'}));
  await assert.rejects(client.request('/api/health'), /响应格式/);
  await assert.rejects(client.request('https://evil.example/'), /无效接口/);
});
