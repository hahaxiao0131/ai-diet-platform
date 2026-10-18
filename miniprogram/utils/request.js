const {
  apiBaseUrl,
  apiMode,
  cloudApiPrefix,
  cloudRunEnvId,
  cloudServiceName,
  tokenStorageKey,
  useMockLogin,
} = require('./config');

function getToken() { return wx.getStorageSync(tokenStorageKey || 'diet_access_token') || ''; }
function setToken(token) {
  const key = tokenStorageKey || 'diet_access_token';
  if (token) wx.setStorageSync(key, token); else wx.removeStorageSync(key);
}

function parsePayload(data) {
  if (typeof data !== 'string') return data || {};
  try { return JSON.parse(data); } catch (_) { return data; }
}

function requestViaCloud(path, options = {}) {
  return new Promise((resolve, reject) => {
    if (!wx.cloud || typeof wx.cloud.callContainer !== 'function') {
      reject(new Error('当前基础库不支持云托管调用，请升级微信开发者工具或基础库'));
      return;
    }
    wx.cloud.callContainer({
      config: { env: cloudRunEnvId },
      path: `${cloudApiPrefix || ''}${path}`,
      method: options.method || 'GET',
      data: options.data,
      timeout: options.timeout || 15000,
      header: {
        'Content-Type': 'application/json',
        'X-WX-SERVICE': cloudServiceName,
        ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
        ...(options.header || {}),
      },
      success(response) {
        const payload = parsePayload(response.data);
        if (response.statusCode < 200 || response.statusCode >= 300) {
          const detail = payload && payload.detail ? payload.detail : {};
          const message = typeof detail === 'string' ? detail : detail.message;
          reject(new Error(message || (typeof payload === 'string' ? payload : '请求失败，请稍后重试')));
          return;
        }
        resolve(Object.prototype.hasOwnProperty.call(payload, 'data') ? payload.data : payload);
      },
      fail(error) {
        console.error('云托管请求失败', error);
        const rawMessage = error && error.errMsg ? error.errMsg : '';
        const message = rawMessage.includes('no baseresponse') || rawMessage.includes('NETWORK_IO_SUSPENDED')
          ? '云端服务正在启动或暂时不可用，请稍后重试'
          : '网络请求失败，请检查网络后重试';
        reject(new Error(message));
      },
    });
  });
}

function requestViaLocal(path, options = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${apiBaseUrl}${path}`,
      method: options.method || 'GET',
      data: options.data,
      timeout: options.timeout || 15000,
      header: { 'Content-Type': 'application/json', ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}), ...(options.header || {}) },
      success(response) {
        const payload = parsePayload(response.data);
        if (response.statusCode < 200 || response.statusCode >= 300) {
          const detail = payload.detail || {};
          reject(new Error(detail.message || (typeof detail === 'string' ? detail : '请求失败，请稍后重试')));
          return;
        }
        resolve(Object.prototype.hasOwnProperty.call(payload, 'data') ? payload.data : payload);
      },
      fail: reject,
    });
  });
}

function request(path, options = {}) {
  return apiMode === 'cloud' ? requestViaCloud(path, options) : requestViaLocal(path, options);
}

function ensureAuth() {
  if (getToken()) return Promise.resolve(getToken());
  if (useMockLogin) {
    return request('/auth/mock-login', { method: 'POST', data: { mock_user_key: 'wechat-devtools-user' } })
      .then((session) => { setToken(session.access_token); return session.access_token; });
  }
  return new Promise((resolve, reject) => {
    wx.login({
      success(login) {
        if (!login.code) { reject(new Error('未获得微信登录凭证')); return; }
        request('/auth/wechat/login', { method: 'POST', data: { code: login.code } })
          .then((session) => { setToken(session.access_token); resolve(session.access_token); })
          .catch(reject);
      },
      fail: reject,
    });
  });
}

module.exports = { request, ensureAuth, getToken, setToken };
