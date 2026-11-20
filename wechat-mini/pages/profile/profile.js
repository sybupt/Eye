const store = require('../../utils/store.js')
const app = getApp()

Page({
  data: {
    avatarText: '眼',
    nickname: '未登录',
    intro: '手机号登录后，记录只属于你自己（存于云端按身份隔离）',
    phoneText: '',
    loggedIn: false,
    cloudMode: false,
    profiles: [],
    pendingCode: null,
    menuGroups: [
      {
        key: 'about',
        items: [
          { key: 'privacy', text: '隐私保护说明', path: '' },
          { key: 'disclaimer', text: '使用说明与免责声明', path: '' },
          { key: 'version', text: '关于本工具', path: '' },
        ],
      },
    ],
  },

  onShow() {
    const self = this
    store.ready().then(function () { self.refresh() })
  },

  refresh() {
    const login = store.getLogin()
    const cloudMode = store.isCloudMode()
    this.setData({
      loggedIn: !!login,
      cloudMode: cloudMode,
      nickname: login ? ('用户 ' + store.maskPhone(login.phone || '')) : (cloudMode ? '已登录' : '未登录'),
      phoneText: login ? store.maskPhone(login.phone || '') : '',
      intro: cloudMode ? '云端模式 · 记录按手机号隔离保存' : '演示模式 · 登录后可真实保存自己的记录',
      profiles: store.getProfiles(),
    })
  },

  handleMenuTap(e) {
    const dataset = e.currentTarget.dataset
    const group = this.data.menuGroups[Number(dataset.group)]
    const item = group && group.items[Number(dataset.index)]
    if (!item) return
    const tips = {
      privacy: '本工具仅采集手机号用于登录识别、你上传的检查报告用于生成个人记录；数据保存在云端并按手机号隔离，你可随时删除档案及原始文件。数据仅供随访参考，不构成医疗诊断。',
      disclaimer: '本工具为健康管理记录工具，不构成医疗诊断或处方建议；检查数值请以纸质报告原件为准。',
      version: '眼健康记录 · 上云版',
    }
    wx.showModal({ title: item.text, content: tips[item.key] || '暂未开放', showCancel: false })
  },

  onLoginTap() {
    if (this.data.loggedIn) {
      const self = this
      wx.showModal({
        title: '退出登录',
        content: '退出后云端记录仍安全保存，重新用该手机号登录即可继续查看。确定退出吗？',
        success: function (res) {
          if (!res.confirm) return
          store.logout().then(function () {
            app.globalData.currentProfileId = ''
            self.refresh()
            wx.showToast({ title: '已退出登录', icon: 'success' })
          })
        },
      })
      return
    }
    this.startPhoneLogin()
  },

  startPhoneLogin() {
    const self = this
    wx.showModal({
      title: '手机号登录',
      content: '请输入 11 位手机号',
      editable: true,
      placeholderText: '例如 13800138000',
      success: function (res) {
        if (!res.confirm) return
        const phone = (res.content || '').trim()
        if (!/^1\d{10}$/.test(phone)) {
          wx.showToast({ title: '手机号格式不正确', icon: 'none' })
          return
        }
        wx.showLoading({ title: '发送验证码', mask: true })
        store.sendPhoneCode(phone).then(function (r) {
          wx.hideLoading()
          if (r.error) {
            wx.showToast({ title: (r.error.message || '发送失败').slice(0, 14), icon: 'none' })
            return
          }
          self.setData({ pendingCode: r.data })
          self.askCode()
        })
      },
    })
  },

  askCode() {
    const self = this
    wx.showModal({
      title: '输入验证码',
      content: '验证码已短信发送至你填写的手机号',
      editable: true,
      placeholderText: '请输入收到的验证码',
      success: function (r2) {
        if (!r2.confirm) return
        const token = (r2.content || '').trim()
        if (!token) { wx.showToast({ title: '请输入验证码', icon: 'none' }); return }
        wx.showLoading({ title: '登录中', mask: true })
        store.verifyPhoneCode(self.data.pendingCode, token).then(function (r) {
          wx.hideLoading()
          self.setData({ pendingCode: null })
          if (r.error) {
            wx.showToast({ title: (r.error.message || '登录失败').slice(0, 14), icon: 'none' })
            return
          }
          const profiles = store.getProfiles()
          app.globalData.currentProfileId = profiles.length ? profiles[0].id : ''
          self.refresh()
          wx.showToast({ title: '登录成功', icon: 'success' })
        })
      },
    })
  },

  onProfileTap(e) {
    const id = e.currentTarget.dataset.id
    app.globalData.currentProfileId = id
    wx.switchTab({ url: '/pages/index/index' })
  },

  onAddProfile() {
    const self = this
    wx.showModal({
      title: '新建档案',
      content: '输入档案名称（如：孩子姓名 / 本人）',
      editable: true,
      placeholderText: '例如 小明',
      success: function (res) {
        if (!res.confirm) return
        const name = (res.content || '').trim()
        if (!name) { wx.showToast({ title: '请输入档案名称', icon: 'none' }); return }
        wx.showLoading({ title: '保存中', mask: true })
        store.addProfile({ name: name, relation: '其他' }).then(function (r) {
          wx.hideLoading()
          if (r.error) { wx.showToast({ title: '保存失败', icon: 'none' }); return }
          app.globalData.currentProfileId = r.data.id
          self.refresh()
          wx.showToast({ title: '档案已创建', icon: 'success' })
        })
      },
    })
  },

  onDeleteProfile(e) {
    const self = this
    const id = e.currentTarget.dataset.id
    const name = e.currentTarget.dataset.name
    wx.showModal({
      title: '删除档案',
      content: '确定删除「' + name + '」吗？该档案的检查记录和已上传的原始报告文件将一并从云端永久删除，删除后不可恢复。',
      confirmText: '删除',
      confirmColor: '#dc2626',
      success: function (res) {
        if (!res.confirm) return
        wx.showLoading({ title: '删除中', mask: true })
        store.deleteProfile(id).then(function (r) {
          wx.hideLoading()
          if (r.error) { wx.showToast({ title: '删除失败', icon: 'none' }); return }
          if (app.globalData.currentProfileId === id) {
            const list = store.getProfiles()
            app.globalData.currentProfileId = list.length ? list[0].id : ''
          }
          self.refresh()
          wx.showToast({ title: '档案及文件已删除', icon: 'success' })
        })
      },
    })
  },

  onRebuildProfiles() {
    if (store.isCloudMode()) {
      wx.showToast({ title: '云端模式请用「新建档案」', icon: 'none' })
      return
    }
    const self = this
    wx.showModal({
      title: '重建演示档案',
      content: '将恢复为初始的两个演示档案，当前演示数据会被清除。确定重建吗？',
      confirmText: '重建',
      confirmColor: '#dc2626',
      success: function (res) {
        if (!res.confirm) return
        store.resetProfiles()
        app.globalData.currentProfileId = ''
        self.refresh()
        wx.showToast({ title: '已重建演示档案', icon: 'success' })
      },
    })
  },
})
