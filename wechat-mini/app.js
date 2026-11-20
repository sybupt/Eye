const store = require('./utils/store.js')

App({
  globalData: {
    appName: '眼健康记录',
    currentProfileId: '',
  },
  onLaunch() {
    // 异步恢复会话：未登录→演示模式；已登录→云端模式（数据按身份隔离）
    store.bootstrap()
  },
});
