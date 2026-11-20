const store = require('../../utils/store.js')
const mock = require('../../utils/mock-data.js')
const parser = require('../../utils/parse-report.js')
const diag = require('../../utils/workbuddy-cloud-diagnostics.js')

const SOURCES = [
  { key: 'album', label: '相册图片' },
  { key: 'camera', label: '拍照' },
  { key: 'pdf', label: 'PDF 文件' },
]

function readAsBase64(path) {
  return new Promise(function (resolve, reject) {
    wx.getFileSystemManager().readFile({
      filePath: path,
      encoding: 'base64',
      success: function (res) { resolve(res.data) },
      fail: reject,
    })
  })
}

// 供 AI 识别用：先压缩到合适尺寸/质量，避免大图 base64 导致上传慢、推理超时。
// 压缩失败则回退原图，不阻断流程。
function readCompressedBase64(path) {
  return new Promise(function (resolve) {
    if (typeof wx.compressImage !== 'function') { readAsBase64(path).then(resolve, function () { resolve('') }); return }
    wx.compressImage({
      src: path,
      quality: 70,
      compressedWidth: 1280,
      success: function (res) { readAsBase64(res.tempFilePath || path).then(resolve, function () { resolve('') }) },
      fail: function () { readAsBase64(path).then(resolve, function () { resolve('') }) },
    })
  })
}

Page({
  data: {
    date: '',
    visitType: '医学验光',
    visitTypes: ['医学验光', '散瞳验光', '眼生物测量', '检查复查', '年度体检', '学校筛查'],
    typeIndex: 0,
    cyclo: false,
    sources: SOURCES,
    sourceIndex: 0,
    profiles: [],
    profileIndex: 0,
    files: [],
    step: 'idle',
    progress: 0,
    progressText: '',
    result: null,
    cloudMode: false,
  },

  onLoad() {
    const now = new Date()
    const y = now.getFullYear()
    const m = String(now.getMonth() + 1).padStart(2, '0')
    const d = String(now.getDate()).padStart(2, '0')
    this.setData({ date: y + '-' + m + '-' + d })
  },

  onShow() {
    const self = this
    store.ready().then(function () {
      const profiles = store.getProfiles()
      let idx = 0
      const cur = getApp().globalData.currentProfileId
      for (let i = 0; i < profiles.length; i++) { if (profiles[i].id === cur) { idx = i; break } }
      self.setData({ profiles: profiles, profileIndex: idx, cloudMode: store.isCloudMode() })
    })
  },

  onDateChange(e) { this.setData({ date: e.detail.value }); },
  onProfileChange(e) { this.setData({ profileIndex: Number(e.detail.value) }); },
  onTypeChange(e) {
    const idx = Number(e.detail.value)
    this.setData({
      typeIndex: idx,
      visitType: this.data.visitTypes[idx],
      cyclo: this.data.visitTypes[idx] === '散瞳验光' ? true : this.data.cyclo,
    })
  },
  onCycloChange(e) { this.setData({ cyclo: e.detail.value }); },
  onSourceChange(e) {
    this.setData({ sourceIndex: Number(e.detail.value), files: [], step: 'idle', result: null })
  },

  chooseFiles() {
    const self = this
    const source = SOURCES[this.data.sourceIndex].key
    if (source === 'pdf') {
      wx.chooseMessageFile({
        count: 6,
        type: 'file',
        extension: ['pdf'],
        success(res) {
          const files = res.tempFiles.map(f => ({ path: f.path, name: f.name || '报告.pdf', kind: 'pdf' }))
          self.setData({ files: files, step: 'idle', result: null })
        },
        fail() { wx.showToast({ title: '请从聊天会话中选择 PDF 文件', icon: 'none' }) },
      })
      return
    }
    wx.chooseMedia({
      count: 6,
      mediaType: ['image'],
      sourceType: [source],
      success(res) {
        const files = res.tempFiles.map(f => ({ path: f.tempFilePath, name: '检查报告', kind: 'image' }))
        self.setData({ files: files, step: 'idle', result: null })
      },
    })
  },

  removeFile(e) {
    const idx = Number(e.currentTarget.dataset.index)
    const files = this.data.files.slice()
    files.splice(idx, 1)
    this.setData({ files: files })
  },

  setProgress(p, text) { this.setData({ progress: p, progressText: text }) },

  submit() {
    if (!this.data.files.length) {
      wx.showToast({ title: '请先选择报告文件', icon: 'none' })
      return
    }
    if (!this.data.cloudMode) { this.submitDemo(); return }
    this.submitCloud()
  },

  // 演示模式：保留原型的展示流程，明确标注为演示，不入云端
  submitDemo() {
    const self = this
    this.setData({ step: 'running', progress: 10, progressText: '演示模式：生成示例结果…' })
    let tick = 0
    const timer = setInterval(function () {
      tick += 1
      const p = Math.min(100, 10 + tick * 22)
      if (p >= 99) {
        clearInterval(timer)
        self.setData({
          step: 'done', progress: 100,
          result: {
            demo: true,
            count: self.data.files.length,
            date: self.data.date,
            cyclo: self.data.cyclo,
            sourceText: self.data.files.map(function (f) { return f.kind === 'pdf' ? 'PDF' : '图片' }).join(' + '),
            indicators: 15,
            name: '演示档案',
            preview: [
              { code: 'BIO_AL', name: '眼轴长度', text: '右眼 23.80 / 左眼 24.09 mm' },
              { code: 'REF_SE', name: '等效球镜', text: '右眼 +0.26 / 左眼 −0.33 D' },
              { code: 'BIO_AST', name: '角膜散光', text: '右眼 1.36 / 左眼 0.64 D' },
              { code: 'VA_UCVA', name: '裸眼视力', text: '右眼 1.0 / 左眼 0.8' },
            ],
          },
        })
      } else {
        self.setData({ progress: p })
      }
    }, 300)
  },

  // 云端模式：真实解析 + 原件上传 + 入库
  async submitCloud() {
    const self = this
    const profile = this.data.profiles[this.data.profileIndex]
    if (!profile) {
      wx.showModal({ title: '还没有档案', content: '请先在「我的」页创建一个档案，再上传报告。', showCancel: false })
      return
    }
    const hasPdf = this.data.files.some(function (f) { return f.kind === 'pdf' })
    const images = this.data.files.filter(function (f) { return f.kind === 'image' })
    if (hasPdf && !images.length) {
      wx.showModal({ title: 'PDF 暂不能自动解析', content: 'PDF 原件可保存归档，但自动识别需要报告图片。请用「拍照 / 相册图片」上传，或把 PDF 中报告页截图后再上传。', showCancel: false })
      return
    }
    const cloud = store.cloud
    if (!cloud) { wx.showToast({ title: '云服务未就绪', icon: 'none' }); return }

    this.setData({ step: 'running', progress: 8, progressText: '正在识别报告…' })
    try {
      // 1) 解析所有图片，合并指标（多张取最后一次有值的非 null）
      let merged = { values: {}, note: '', indicatorCount: 0, date: null }
      let done = 0
      for (let i = 0; i < images.length; i++) {
        self.setProgress(8 + Math.round((i / images.length) * 60), '正在识别第 ' + (i + 1) + '/' + images.length + ' 张…')
        const b64 = await readCompressedBase64(images[i].path)
        if (!b64) throw new Error('图片读取失败，请重试')
        const mime = 'image/jpeg'
        const dataUrl = 'data:' + mime + ';base64,' + b64
        const r = await parser.parseImage(dataUrl)
        if (r.error) throw r.error
        Object.keys(r.data.values).forEach(function (code) {
          const pair = r.data.values[code]
          if (!merged.values[code]) merged.values[code] = { OD: null, OS: null }
          if (pair.OD !== null) merged.values[code].OD = pair.OD
          if (pair.OS !== null) merged.values[code].OS = pair.OS
        })
        if (!merged.date && r.data.date) merged.date = r.data.date
        if (r.data.note) merged.note = (merged.note ? merged.note + '；' : '') + r.data.note
        done += 1
      }
      merged.indicatorCount = Object.keys(merged.values).filter(function (c) {
        return merged.values[c].OD !== null || merged.values[c].OS !== null
      }).length

      // 2) 上传原件到云存储（按用户隔离路径）
      self.setProgress(72, '正在上传原件到云端…')
      const session = await cloud.auth.getSession()
      const uid = session.data && session.data.user && session.data.user.id
      const stamp = Date.now()
      const paths = []
      for (let i = 0; i < self.data.files.length; i++) {
        const f = self.data.files[i]
        const ext = f.kind === 'pdf' ? 'pdf' : 'jpg'
        const body = await readAsBase64(f.path)
        const p = cloud.storage.userPath(uid, 'reports/' + stamp + '_' + i + '.' + ext)
        const up = await cloud.storage.upload(p, body, {
          contentType: f.kind === 'pdf' ? 'application/pdf' : 'image/jpeg',
          upsert: false,
        })
        if (up.error) throw up.error
        paths.push({ path: p, name: f.name, mime: f.kind === 'pdf' ? 'application/pdf' : 'image/jpeg' })
      }

      // 3) 入库检查记录
      self.setProgress(88, '正在写入时间线…')
      const examDate = merged.date || self.data.date
      const er = await store.addEncounter(profile.id, {
        date: examDate,
        visitType: self.data.visitType,
        cyclo: self.data.cyclo,
        institution: '自录',
        indicators: merged.indicatorCount,
        values: merged.values,
        raw: merged.note ? [{ label: 'AI 识别备注', rows: [merged.note] }] : [],
      })
      if (er.error) throw er.error

      // 4) 登记文件（用于删除档案时级联删除）
      const encId = er.data.id
      for (let i = 0; i < paths.length; i++) {
        await store.registerDocument({
          profileId: profile.id,
          encounterId: encId,
          storagePath: paths[i].path,
          fileName: paths[i].name,
          mimeType: paths[i].mime,
        })
      }

      self.setProgress(100, '完成')
      self.setData({
        step: 'done',
        result: {
          demo: false,
          count: self.data.files.length,
          date: examDate,
          cyclo: self.data.cyclo,
          name: profile.name,
          indicators: merged.indicatorCount,
          sourceText: self.data.files.map(function (f) { return f.kind === 'pdf' ? 'PDF' : '图片' }).join(' + '),
          preview: self.buildPreview(merged.values),
          note: merged.note,
        },
      })
      wx.showToast({ title: '已保存到云端', icon: 'success' })
    } catch (err) {
      self.setData({ step: 'error' })
      const f = (typeof diag.getLastFailure === 'function') ? diag.getLastFailure() : null
      let detail = ''
      if (f) {
        detail = '阶段:' + (f.stage || '') + ' 状态:' + (f.status || '') +
          (f.code ? ' 代码:' + String(f.code) : '') +
          (f.url ? '\n接口:' + String(f.url).replace(/^https?:\/\/[^/]+/, '') : '') +
          (f.message ? '\n' + String(f.message) : '') +
          (f.raw && f.raw !== f.message ? '\n返回:' + String(f.raw) : '')
      }
      if (!detail) detail = (err && (err.message || err.error_description) || '未知错误')
      wx.showModal({
        title: '处理失败',
        content: String(detail).slice(0, 140),
        showCancel: false,
      })
    }
  },

  buildPreview(values) {
    const out = []
    Object.keys(values).forEach(function (code) {
      const ref = mock.REFERENCE[code]
      if (!ref) return
      const pair = values[code]
      const od = pair.OD !== null ? mock.formatValue(pair.OD, ref.unit) : '—'
      const os = pair.OS !== null ? mock.formatValue(pair.OS, ref.unit) : '—'
      out.push({ code: code, name: ref.name, text: '右眼 ' + od + ' / 左眼 ' + os + (ref.unit ? ' ' + ref.unit : '') })
    })
    return out.slice(0, 8)
  },

  reset() {
    this.setData({ step: 'idle', progress: 0, files: [], result: null, progressText: '' })
  },

  goHome() { wx.switchTab({ url: '/pages/index/index' }); },
})
