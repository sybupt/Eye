const mock = require('../../utils/mock-data.js');
const store = require('../../utils/store.js');
const exporter = require('../../utils/report-export.js');

Page({
  data: {
    batch: null,
    sections: [],
    metrics: [],
    date: '',
    pid: '',
    reports: [],
    exporting: false,
  },

  onLoad(options) {
    this.setData({
      date: decodeURIComponent(options.date || ''),
      pid: decodeURIComponent(options.pid || ''),
    })
  },

  onShow() {
    const self = this
    store.ready().then(function () {
      const batches = store.groupByDate(self.data.pid)
      let batch = null
      for (let i = 0; i < batches.length; i++) {
        if (batches[i].date === self.data.date) { batch = batches[i]; break }
      }
      if (!batch) return
      const metrics = []
      Object.keys(batch.values).forEach(function (code) {
        const ref = mock.REFERENCE[code]
        if (!ref) return
        const pair = batch.values[code]
        metrics.push({
          code: code,
          name: ref.name,
          unit: ref.unit,
          od: pair.OD !== undefined && pair.OD !== null ? mock.formatValue(pair.OD, ref.unit) : '—',
          os: pair.OS !== undefined && pair.OS !== null ? mock.formatValue(pair.OS, ref.unit) : '—',
        })
      })
      const reports = (batch.reports || []).map(function (d) {
        return {
          id: d.id,
          name: d.fileName || '报告文件',
          isPdf: exporter.isPdf(d),
        }
      })
      self.setData({ batch: batch, metrics: metrics, sections: batch.raw || [], reports: reports })
    })
  },

  // 获取原始报告：同一天的报告合并为一张长图；PDF 另列供打开
  async onGetReports() {
    const self = this
    if (!this.data.batch) return
    if (!this.data.reports.length) {
      wx.showToast({ title: '该日期暂无原始报告', icon: 'none' })
      return
    }
    this.setData({ exporting: true })
    wx.showLoading({ title: '正在整理报告…' })
    try {
      const r = await exporter.exportDayReports(this.data.batch.reports)
      wx.hideLoading()
      self.setData({ exporting: false })
      if (r.demo) {
        wx.showModal({
          title: '演示模式',
          content: '演示数据没有真实原件。手机号登录后上传的报告原件可在此合并导出为长图。',
          showCancel: false,
        })
        return
      }
      // 图片长图：预览 + 保存入口
      if (r.imagePath) {
        wx.previewImage({ urls: [r.imagePath] })
      }
      // PDF：逐个打开
      if (r.pdfs && r.pdfs.length) {
        const p = r.pdfs[0]
        wx.openDocument({ filePath: p.path, fileType: 'pdf', showMenu: true })
      }
      if (!r.imagePath && !(r.pdfs && r.pdfs.length)) {
        wx.showToast({ title: '原件下载失败，请重试', icon: 'none' })
      }
    } catch (e) {
      wx.hideLoading()
      self.setData({ exporting: false })
      wx.showModal({
        title: '整理失败',
        content: (e && (e.message || e.error_description) || '未知错误').slice(0, 120),
        showCancel: false,
      })
    }
  },

  // 保存合并后的长图到相册（在预览后用右上角，或单独入口）
  onSaveImage() {
    wx.showToast({ title: '预览时长按图片可保存到相册', icon: 'none' })
  },

  onShareTap() {
    wx.showToast({ title: '原型演示：接入后可生成限时分享链接', icon: 'none' });
  },

  onExportTap() {
    wx.showToast({ title: '原型演示：接入后可导出汇总图片', icon: 'none' });
  },
});
