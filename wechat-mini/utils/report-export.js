// utils/report-export.js — 把同一天的原始报告合并导出。
// 图片：走云存储签名 URL 下载 → canvas 纵向拼接 → 一张长图 jpg → 预览/保存相册。
// PDF：小程序 canvas 无法绘制 PDF，逐个用 wx.openDocument 打开（可转发/另存）。
const store = require('./store.js')

function isPdf(doc) {
  return /pdf/i.test(doc.mimeType || '') || /\.pdf$/i.test(doc.fileName || '')
}

// 下载单个文件到本地临时路径
function downloadOne(cloud, doc) {
  return new Promise(function (resolve, reject) {
    if (!doc.storagePath) { reject(new Error('缺少文件路径')); return }
    cloud.storage.createSignedUrl(doc.storagePath, 300).then(function (r) {
      if (r.error || !r.data || !r.data.signedUrl) {
        reject(r.error || new Error('获取下载链接失败')); return
      }
      wx.downloadFile({
        url: r.data.signedUrl,
        success: function (res) {
          if (res.statusCode === 200 && res.tempFilePath) resolve(res.tempFilePath)
          else reject(new Error('下载失败(' + res.statusCode + ')'))
        },
        fail: function (e) { reject(e) },
      })
    }).catch(reject)
  })
}

// 读取图片像素尺寸（用于按比例缩放拼图）
function imageInfo(path) {
  return new Promise(function (resolve, reject) {
    wx.getImageInfo({ src: path, success: resolve, fail: reject })
  })
}

// 把多张图片纵向拼成一张长图，返回临时文件路径
function stitchImages(paths) {
  return new Promise(function (resolve, reject) {
    if (!paths.length) { reject(new Error('没有可合并的图片')); return }
    if (paths.length === 1) { resolve(paths[0]); return }
    const CANVAS_W = 750 // 统一宽度，长图按比例缩放
    const GAAP = 16
    let infos = []
    let chain = Promise.resolve()
    paths.forEach(function (p) {
      chain = chain.then(function () {
        return imageInfo(p).then(function (info) {
          const h = Math.round(CANVAS_W * info.height / info.width)
          infos.push({ path: p, h: h })
        })
      })
    })
    chain.then(function () {
      const totalH = infos.reduce(function (s, it) { return s + it.h + GAAP }, GAAP)
      const query = wx.createSelectorQuery()
      query.select('#export-canvas').fields({ node: true, size: true }).exec(function (res) {
        const node = res && res[0] && res[0].node
        if (!node) { reject(new Error('画布未就绪')); return }
        const ctx = node.getContext('2d')
        const dpr = 1
        node.width = CANVAS_W * dpr
        node.height = totalH * dpr
        ctx.scale(dpr, dpr)
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, CANVAS_W, totalH)
        let chain2 = Promise.resolve()
        let y = GAAP
        infos.forEach(function (it) {
          chain2 = chain2.then(function () {
            return new Promise(function (resolve2) {
              const img = node.createImage()
              img.onload = function () {
                ctx.drawImage(img, 0, y, CANVAS_W, it.h)
                y += it.h + GAAP
                resolve2()
              }
              img.onerror = function () { resolve2() } // 单张失败不阻断整体
              img.src = it.path
            })
          })
        })
        chain2.then(function () {
          wx.canvasToTempFilePath({
            canvas: node,
            fileType: 'jpg',
            quality: 0.9,
            success: function (r) { resolve(r.tempFilePath) },
            fail: reject,
          })
        })
      })
    }).catch(reject)
  })
}

// 主入口：reports = 同一天的文档数组（来自 store.groupByDate().reports）
// 返回 { imagePath, pdfs, demo }
async function exportDayReports(reports) {
  const cloud = store.cloud
  const pdfs = reports.filter(isPdf)
  const images = reports.filter(function (d) { return !isPdf(d) })
  const demo = reports.some(function (d) { return d.demo })

  // 演示模式：无真实原件，返回占位说明
  if (demo) return { demo: true, imagePath: '', pdfs: [], images: images }

  if (!cloud) throw new Error('云服务未就绪')

  const downloaded = []
  for (let i = 0; i < images.length; i++) {
    try {
      const p = await downloadOne(cloud, images[i])
      downloaded.push(p)
    } catch (e) {}
  }
  let imagePath = ''
  if (downloaded.length) {
    imagePath = await stitchImages(downloaded)
  }
  // PDF 需要先下载到本地供 openDocument 使用
  const pdfItems = []
  for (let i = 0; i < pdfs.length; i++) {
    try {
      const p = await downloadOne(cloud, pdfs[i])
      pdfItems.push({ path: p, name: pdfs[i].fileName })
    } catch (e) {}
  }
  return { demo: false, imagePath: imagePath, pdfs: pdfItems, images: images }
}

module.exports = { exportDayReports: exportDayReports, isPdf: isPdf }
