// utils/store.js — 云端数据层。
// 设计：
//  1) 手机号验证码登录成功后，进入「云端模式」：档案/检查/文件全部存云数据库与云存储，按登录身份隔离。
//  2) 未登录时为「演示模式」：只用本地演示数据供预览，界面明确标注，不冒充真实数据。
//  3) 页面通过同步 getter 读内存缓存；bootstrap()/刷新时异步从云端同步。
const { cloud } = require('./cloud.js')
const mock = require('./mock-data.js')

const LOGIN_KEY = 'eye_login_cache'
const DEMO_PROFILES_KEY = 'eye_demo_profiles'
const DEMO_ENC_KEY = 'eye_demo_encounters'
const DEMO_INIT_KEY = 'eye_demo_init'

// 内存缓存
let cache = {
  profiles: [],
  encounters: [],
  documents: [],
  login: null,
  cloudMode: false,
}
let readyPromise = null

function isValidCloud() {
  return !!(cloud && typeof cloud.auth === 'object' && typeof cloud.database === 'object')
}

function maskPhone(phone) {
  if (!phone || phone.length < 7) return phone || ''
  return phone.slice(0, 3) + '****' + phone.slice(-4)
}

function calcAge(birthDate) {
  if (!birthDate) return 0
  const p = birthDate.split('-')
  const b = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]))
  const now = new Date()
  let age = now.getFullYear() - b.getFullYear()
  if (now.getMonth() < b.getMonth() || (now.getMonth() === b.getMonth() && now.getDate() < b.getDate())) age--
  return age
}

// ---------- 演示模式（仅未登录预览） ----------
function loadDemo() {
  let profiles = wx.getStorageSync(DEMO_PROFILES_KEY)
  let encounters = wx.getStorageSync(DEMO_ENC_KEY)
  if (!wx.getStorageSync(DEMO_INIT_KEY) || !Array.isArray(profiles)) {
    profiles = mock.profiles()
    encounters = mock.encountersOf('p1').concat(mock.encountersOf('p2'))
    wx.setStorageSync(DEMO_PROFILES_KEY, profiles)
    wx.setStorageSync(DEMO_ENC_KEY, encounters)
    wx.setStorageSync(DEMO_INIT_KEY, true)
  }
  cache.profiles = Array.isArray(profiles) ? profiles : []
  cache.encounters = Array.isArray(encounters) ? encounters : []
  // 演示模式无真实原始文件；为每条演示检查造一个占位文件项，便于查看合并导出交互。
  cache.documents = cache.encounters.map(function (e) {
    return {
      id: 'doc_' + e.id,
      profileId: e.patientId,
      encounterId: e.id,
      storagePath: '',
      fileName: (e.date || '检查') + '_' + e.type + '.jpg',
      mimeType: 'image/jpeg',
      demo: true,
    }
  })
}

// ---------- 云端映射 ----------
function rowToProfile(row) {
  return {
    id: String(row.id),
    name: row.name,
    relation: row.relation || '其他',
    birthDate: row.birth_date || '',
    sex: row.sex || 'M',
    age: calcAge(row.birth_date),
    visits: 0,
    latestDate: '',
    custom: true,
  }
}
function rowToEncounter(row) {
  let values = {}
  let raw = []
  try { values = row.values_json && typeof row.values_json === 'object' ? row.values_json : JSON.parse(row.values_json || '{}') } catch (e) { values = {} }
  try { raw = Array.isArray(row.raw_json) ? row.raw_json : JSON.parse(row.raw_json || '[]') } catch (e) { raw = [] }
  const codes = Object.keys(values).filter(function (c) { return values[c] && (values[c].OD !== undefined || values[c].OS !== undefined) })
  return {
    id: 'e' + row.id,
    patientId: String(row.profile_id),
    date: row.exam_date,
    type: row.visit_type || '医学验光',
    cyclo: !!row.cyclo,
    institution: row.institution || ' self-recorded',
    indicatorCount: row.indicator_count || codes.length,
    values: values,
    raw: raw,
  }
}

function rowToDocument(row) {
  const encId = row.encounter_id === null || row.encounter_id === undefined ? '' : String(row.encounter_id)
  return {
    id: 'doc' + row.id,
    profileId: String(row.profile_id || ''),
    encounterId: encId ? 'e' + encId : '',
    storagePath: row.storage_path || '',
    fileName: row.file_name || '报告文件',
    mimeType: row.mime_type || 'image/jpeg',
  }
}

// ---------- 启动 / 会话 ----------
function bootstrap() {
  if (readyPromise) return readyPromise
  readyPromise = (async function () {
    if (!isValidCloud()) { loadDemo(); return }
    const { data: session, error } = await cloud.auth.getSession()
    if (error || !session) { cache.login = null; cache.cloudMode = false; loadDemo(); return }
    cache.login = { phone: (session.user && session.user.phone) || '', uid: session.user && session.user.id }
    cache.cloudMode = true
    try { wx.setStorageSync(LOGIN_KEY, { phone: cache.login.phone, uid: cache.login.uid }) } catch (e) {}
    await refreshFromCloud()
  })().catch(function () { loadDemo() })
  return readyPromise
}

async function refreshFromCloud() {
  if (!cache.cloudMode) return
  const pr = await cloud.database.from('eye_profiles').select('*').order('created_at', { ascending: true })
  if (pr.error) throw pr.error
  const profiles = (pr.data || []).map(rowToProfile)
  const er = await cloud.database.from('eye_encounters').select('*').order('exam_date', { ascending: false })
  if (er.error) throw er.error
  const encounters = (er.data || []).map(rowToEncounter)
  // 原始报告文件登记（用于"获取原始报告"按日期合并导出）
  let documents = []
  try {
    const dr = await cloud.database.from('eye_documents').select('*').order('created_at', { ascending: true })
    if (!dr.error) documents = (dr.data || []).map(rowToDocument)
  } catch (e) {}
  // 汇总每个档案的次数与最近日期
  profiles.forEach(function (p) {
    const mine = encounters.filter(function (e) { return e.patientId === p.id })
    p.visits = mine.length
    p.latestDate = mine.length ? mine[0].date : ''
  })
  cache.profiles = profiles
  cache.encounters = encounters
  cache.documents = documents
}

function ready() { return bootstrap() }

function isCloudMode() { return cache.cloudMode }
function getLogin() { return cache.cloudMode ? cache.login : null }
function getProfiles() { return cache.profiles }
function encountersOf(patientId) {
  return cache.encounters.filter(function (e) { return e.patientId === String(patientId) })
}

// 同一天分多次上传的检查，合并为一个"日期批次"：日期只出现一次，检查项/原始报告按日期归集。
function mergeValues(list) {
  const values = {}
  list.forEach(function (e) {
    Object.keys(e.values || {}).forEach(function (code) {
      const pair = e.values[code]
      if (!values[code]) values[code] = { OD: null, OS: null }
      if (pair && pair.OD !== null && pair.OD !== undefined) values[code].OD = pair.OD
      if (pair && pair.OS !== null && pair.OS !== undefined) values[code].OS = pair.OS
    })
  })
  return values
}

function groupByDate(patientId) {
  const list = encountersOf(patientId)
  const buckets = []
  const index = {}
  // 依赖 encounters 已按日期倒序；用 Map 保证同日归到一起
  list.forEach(function (e) {
    const day = e.date || '未注明日期'
    if (index[day] === undefined) {
      index[day] = buckets.length
      buckets.push({ date: day, items: [] })
    }
    buckets[index[day]].items.push(e)
  })
  return buckets.map(function (b) {
    const values = mergeValues(b.items)
    const counts = Object.keys(values).filter(function (c) {
      return values[c].OD !== null || values[c].OS !== null
    }).length
    const cyclo = b.items.some(function (e) { return !!e.cyclo })
    const ids = b.items.map(function (e) { return e.id })
    const docs = cache.documents.filter(function (d) {
      return d.profileId === String(patientId) && ids.indexOf(d.encounterId) >= 0
    })
    const types = []
    const institutions = []
    b.items.forEach(function (e) {
      if (e.type && types.indexOf(e.type) < 0) types.push(e.type)
      if (e.institution && institutions.indexOf(e.institution) < 0) institutions.push(e.institution)
    })
    const raws = []
    b.items.forEach(function (e) { (e.raw || []).forEach(function (r) { raws.push(r) }) })
    return {
      id: 'day_' + b.date,
      date: b.date,
      type: types.length ? types.join(' / ') : '检查',
      institution: institutions.join(' / ') || '自录',
      cyclo: cyclo,
      indicatorCount: counts,
      values: values,
      raw: raws,
      reports: docs,
      reportCount: docs.length || b.items.length,
      encounterIds: ids,
    }
  })
}

function documentsOf(patientId) {
  return cache.documents.filter(function (d) { return d.profileId === String(patientId) })
}

// ---------- 登录（手机号 OTP） ----------
async function sendPhoneCode(phone) {
  if (!/^1\d{10}$/.test(phone)) return { error: { message: '请输入正确的 11 位手机号' } }
  if (!isValidCloud()) return { error: { message: '云服务未就绪' } }
  const sent = await cloud.auth.sendOtp({ phone })
  if (sent.error) return { error: sent.error }
  return { data: { phone: phone, verificationId: sent.data.verificationId, isExistingUser: sent.data.isExistingUser } }
}

async function verifyPhoneCode(pending, token) {
  if (!pending || !cloud) return { error: { message: '请先获取验证码' } }
  const done = await cloud.auth.verifyOtp({
    phone: pending.phone,
    verificationId: pending.verificationId,
    isExistingUser: pending.isExistingUser,
    token: token,
  })
  if (done.error) return { error: done.error }
  cache.cloudMode = true
  const { data: session } = await cloud.auth.getSession()
  cache.login = { phone: (session && session.user && session.user.phone) || pending.phone, uid: session && session.user && session.user.id }
  await refreshFromCloud()
  return { data: cache.login }
}

async function logout() {
  if (isValidCloud() && cache.cloudMode) {
    try { await cloud.auth.signOut() } catch (e) {}
  }
  cache.cloudMode = false
  cache.login = null
  try { wx.removeStorageSync(LOGIN_KEY) } catch (e) {}
  loadDemo()
}

// ---------- 档案 ----------
async function addProfile(data) {
  const profile = {
    id: 'p' + Date.now(),
    name: data.name,
    relation: data.relation || '其他',
    birthDate: data.birthDate || '',
    sex: data.sex === 'F' ? 'F' : 'M',
    age: data.birthDate ? calcAge(data.birthDate) : 0,
  }
  if (cache.cloudMode) {
    const { data: rows, error } = await cloud.database.from('eye_profiles').insert({
      name: profile.name,
      relation: profile.relation,
      birth_date: profile.birthDate || null,
      sex: profile.sex,
    }).select()
    if (error) return { error: error }
    profile.id = String(rows[0].id)
  } else {
    cache.profiles.push(Object.assign({}, profile, { visits: 0, latestDate: '', custom: true }))
    wx.setStorageSync(DEMO_PROFILES_KEY, cache.profiles)
  }
  await refreshIfCloud()
  return { data: profile }
}

// 级联删除：档案 + 检查记录 + 文件登记 + 云端原始文件
async function deleteProfile(id) {
  const sid = String(id)
  if (cache.cloudMode) {
    // 1) 找出该档案下所有文件（含其检查），先取路径
    const docs = await cloud.database.from('eye_documents').select('storage_path').eq('profile_id', Number(sid))
    const paths = (docs.data || []).map(function (d) { return d.storage_path }).filter(Boolean)
    if (paths.length) {
      try { await cloud.storage.remove(paths) } catch (e) {}
    }
    await cloud.database.from('eye_documents').delete().eq('profile_id', Number(sid))
    await cloud.database.from('eye_encounters').delete().eq('profile_id', Number(sid))
    const del = await cloud.database.from('eye_profiles').delete().eq('id', Number(sid)).select()
    if (del.error) return { error: del.error }
  } else {
    cache.profiles = cache.profiles.filter(function (p) { return p.id !== sid })
    cache.encounters = cache.encounters.filter(function (e) { return e.patientId !== sid })
    wx.setStorageSync(DEMO_PROFILES_KEY, cache.profiles)
    wx.setStorageSync(DEMO_ENC_KEY, cache.encounters)
  }
  await refreshIfCloud()
  return { data: true }
}

async function resetProfiles() {
  // 重建演示档案只在未登录演示模式可用
  loadDemo()
  return { data: cache.profiles }
}

// ---------- 检查记录 ----------
async function addEncounter(profileId, data) {
  const enc = {
    id: 'e' + Date.now(),
    patientId: String(profileId),
    date: data.date,
    type: data.visitType || '医学验光',
    cyclo: !!data.cyclo,
    institution: data.institution || '自录',
    indicatorCount: data.indicators || 0,
    values: data.values || {},
    raw: data.raw || [],
  }
  if (cache.cloudMode) {
    const { data: rows, error } = await cloud.database.from('eye_encounters').insert({
      profile_id: Number(profileId),
      exam_date: enc.date,
      visit_type: enc.type,
      cyclo: enc.cyclo,
      institution: enc.institution,
      indicator_count: enc.indicatorCount,
      values_json: enc.values,
      raw_json: enc.raw,
    }).select()
    if (error) return { error: error }
    enc.id = 'e' + rows[0].id
  } else {
    cache.encounters.unshift(enc)
    wx.setStorageSync(DEMO_ENC_KEY, cache.encounters)
  }
  await refreshIfCloud()
  return { data: enc }
}

async function registerDocument(doc) {
  if (!cache.cloudMode) return { data: null }
  const { data: rows, error } = await cloud.database.from('eye_documents').insert({
    profile_id: Number(doc.profileId),
    encounter_id: doc.encounterId ? Number(String(doc.encounterId).replace(/^e/, '')) : null,
    storage_path: doc.storagePath,
    file_name: doc.fileName,
    mime_type: doc.mimeType,
  }).select()
  if (error) return { error: error }
  return { data: rows[0] }
}

async function refreshIfCloud() { if (cache.cloudMode) await refreshFromCloud() }

module.exports = {
  // 会话
  ready: ready,
  bootstrap: bootstrap,
  isCloudMode: isCloudMode,
  getLogin: getLogin,
  maskPhone: maskPhone,
  sendPhoneCode: sendPhoneCode,
  verifyPhoneCode: verifyPhoneCode,
  logout: logout,
  // 档案
  getProfiles: getProfiles,
  addProfile: addProfile,
  deleteProfile: deleteProfile,
  resetProfiles: resetProfiles,
  // 检查
  encountersOf: encountersOf,
  groupByDate: groupByDate,
  documentsOf: documentsOf,
  addEncounter: addEncounter,
  registerDocument: registerDocument,
  // 云实例（供上传/AI 模块使用）
  cloud: cloud,
}
