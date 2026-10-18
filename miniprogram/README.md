# 食记 AI 微信小程序前端

这是可直接用微信开发者工具导入的原生小程序前端，复用仓库中的 FastAPI 后端。

## 本地导入

1. 启动仓库的 FastAPI 后端，默认地址为 `http://127.0.0.1:8000`。
2. 将 `utils/config.js` 的 `apiMode` 改为 `local`，并将 `useMockLogin` 改为 `true`。
3. 用微信开发者工具导入本 `miniprogram` 目录，编译根目录必须是当前目录，不是内层 `miniprogram` 文件夹。
4. 开发阶段可以暂时关闭域名校验。

导入前请在 `project.config.json` 中将 `your-mini-program-appid` 替换为你自己的小程序 AppID；仓库不保存真实 AppID 或 AppSecret。

## 微信云托管

当前云托管配置在 `utils/config.js`：

- 普通云开发环境：`cloudDevEnvId`
- 微信云托管环境：`cloudRunEnvId`
- 云托管服务：`cloudServiceName`

云托管部署 FastAPI 时，将 `backend` 目录作为代码包上传，使用其中的 `Dockerfile`，并在云托管控制台填写 `backend/cloudrun.env.example` 中的环境变量。正式环境必须设置 `DIET_AUTH_DEV_MODE=0`、真实 `WECHAT_APP_SECRET` 和持久化 `DATABASE_URL`，不能继续使用本地 JSON 文件。

部署完成后保持：

```js
apiMode: 'cloud'
useMockLogin: false
```

## 提审前

在真机上验证微信登录、饮食记录、运动记录、AI 对话、导出和删除账户。使用 `wx.cloud.callContainer` 时不需要把云托管默认域名写成前端 API 地址；若改回 `wx.request` 直连公网域名，则必须配置微信公众平台的 request 合法域名。

