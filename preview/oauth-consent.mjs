// OAuth authorization requests stay in memory and are removed from browser history.
export function authorizationRequest(hash) {
  if(!hash.startsWith('#oauth_request=')) return '';
  const params=new URLSearchParams(hash.slice(1));
  const value=params.get('oauth_request') || '';
  if([...params].length!==1 || value.length>16000 || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(value)) {
    throw new Error('授权请求格式错误，请从插件连接页面重新发起。');
  }
  return value;
}

export function authorizationRedirect(value, issuer, registeredRedirect) {
  const url=new URL(value);
  if(url.protocol!=='https:' || url.host!=='chatgpt.com' || url.username || url.password || url.hash ||
      url.origin+url.pathname!==registeredRedirect || url.searchParams.get('iss')!==issuer ||
      !url.searchParams.has('state') || (!!url.searchParams.get('code')===!!url.searchParams.get('error'))) {
    throw new Error('授权回调与已核实信息不符，已停止跳转。');
  }
  const keys=[...url.searchParams.keys()];
  if(new Set(keys).size!==keys.length || keys.some(key=>!['iss','state','code','error'].includes(key))) {
    throw new Error('授权回调参数无效，已停止跳转。');
  }
  return url.href;
}
