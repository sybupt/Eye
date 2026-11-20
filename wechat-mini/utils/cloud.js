// utils/cloud.js — 云服务单例。所有页面共享同一 client。
// endpoint / publishableKey 来自开通云服务时返回的 publicConfig，可随前端发布。
let cloud = null
let initError = ''

try {
  const { createMiniProgramWorkBuddyCloud } = require('@tencent-ai/workbuddy-cloud-sdk/miniprogram')
  const { createDiagnosticWx } = require('./workbuddy-cloud-diagnostics')

  const PUBLIC_CONFIG = {
    endpoint: 'https://mp-api.app.workbuddy.host',
    publishableKey: 'wbpk_wSaAPxIbwJ3yX4DFXUPW5v_Zif0mKQJtmbGKHJuSB6qbBXUDQht1Kcg',
  }

  cloud = createMiniProgramWorkBuddyCloud({
    endpoint: PUBLIC_CONFIG.endpoint,
    publishableKey: PUBLIC_CONFIG.publishableKey,
    wx: createDiagnosticWx(wx),
  })
} catch (e) {
  // H5 预览未构建 npm 时可能加载不到 SDK：降级为演示模式，不影响界面浏览。
  initError = (e && e.message) || 'SDK 未就绪'
  console.info('[EyeRecord] cloud client unavailable, falling back to demo mode:', initError)
}

module.exports = { cloud, initError }
