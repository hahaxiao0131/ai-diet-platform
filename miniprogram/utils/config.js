module.exports = {
  // local：开发者工具直连本地 FastAPI；cloud：通过微信云托管调用 FastAPI。
  // 云托管发布完成后使用 cloud。切回本地调试时改为 local，并打开 useMockLogin。
  apiMode: 'cloud',
  apiBaseUrl: 'http://127.0.0.1:8000/api/v1',
  // 普通云开发环境（wx.cloud.init 使用）。
  cloudDevEnvId: 'cloud1-d1g3lu8hzfb1b2e46',
  // 微信云托管环境和服务（wx.cloud.callContainer 使用）。
  cloudRunEnvId: 'prod-d6gxudc0qd046c37a',
  cloudServiceName: 'flask-df81',
  cloudApiPrefix: '/api/v1',
  // 云端必须使用正式微信登录；本地调试才使用 mock 登录。
  useMockLogin: false,
  // 与本地 mock 登录分开，避免切换环境后复用旧 token。
  tokenStorageKey: 'diet_access_token_cloud_v1',
  productName: '食记 AI',
  disclaimer: '本结果仅供日常饮食健康参考，不构成医疗诊断或治疗建议。\n如出现持续或严重不适，请及时咨询医生或注册营养师。',
};
