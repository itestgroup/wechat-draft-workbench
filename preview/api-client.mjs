// Access credentials stay in this page's memory; never persist or print them.
export function cloudEndpoint(value) {
  const url = new URL(value.trim());
  if (url.protocol !== 'https:' || !url.hostname.endsWith('.tencentscf.com') ||
      url.username || url.password || url.search || url.hash || url.port || url.pathname !== '/') {
    throw new Error('请填写完整的腾讯 SCF HTTPS 函数 URL，不含路径、参数或密钥。');
  }
  return url.origin;
}

export function createClient(fetcher = globalThis.fetch) {
  let session = null;
  return {
    connect(endpoint, key) {
      const base = cloudEndpoint(endpoint);
      if (key.length < 32) throw new Error('平台访问密钥至少32字符。');
      session = {base, key};
    },
    disconnect() { session = null; },
    async request(path, data) {
      if (!/^\/api\/[a-z0-9/]+$/.test(path)) throw new Error('无效接口路径。');
      const headers = data === undefined ? {} : {'Content-Type':'application/json','X-Preview-Request':'1'};
      if (session) headers.Authorization = 'Bearer ' + session.key;
      const options = {method:data === undefined ? 'GET':'POST', headers,
        credentials:'omit', cache:'no-store', redirect:'error'};
      if (data !== undefined) options.body = JSON.stringify(data);
      let response;
      try { response = await fetcher((session?.base || '') + path, options); }
      catch { throw new Error('连接失败：请检查函数 URL、网页来源白名单及网络。写入请求请先核对状态，勿重复提交。'); }
      const body = await response.text();
      let result;
      try { result = JSON.parse(body); }
      catch { if (!response.ok) throw new Error('请求失败（HTTP ' + response.status + '）。'); }
      if (!response.ok) {
        throw new Error(result?.error || '请求失败（HTTP ' + response.status + '）。');
      }
      if (!result || typeof result !== 'object' || 'statusCode' in result) {
        throw new Error('接口响应格式不匹配，请检查函数 URL 的响应配置。');
      }
      return result;
    }
  };
}
