// utils/parse-report.js — 用多模态大模型把检查报告图片转录成结构化双眼指标。
// 医学口径：只转录图上明确可见的数值；不确定一律留空；不做诊断、不推断散瞳状态。
const { cloud } = require('./cloud.js')

// 模型降级链：全部为本机实测"带真实报告图可调用"的模型。
// 踩坑记录（2026-11-20 本机实测）：
//   - glm-4.6v / hy4flash-free / deep-model → 403「该模型未对当前应用开放」（目录显示 enabled 也不可调）
//   - 带图耗时：glm-5.3-flash 3.5s / auto 3.7s / fast-model 7.2s / balanced-model 33s
// 任一模型 403、超时或解析不出数值，自动切下一个，避免单点失败卡死。
const MODEL_CHAIN = ['glm-5.3-flash', 'auto', 'fast-model']
const PER_MODEL_TIMEOUT = 20000

const SYSTEM_PROMPT = [
  '你是眼科检查报告的"数值转录员"，不是医生，不做诊断。',
  '任务：只把图片上清晰可见的检查数值转录为 JSON，右眼=OD、左眼=OS。',
  '严格规则：',
  '1) 只转录图上确实看得到的数字，看不清/没有的一律用 null，禁止猜测或用示例值填充。',
  '2) 散瞳状态不要自己判断，统一输出 cyclo=false，由用户在界面上确认。',
  '3) 不要把未散瞳的等效球镜(SE)当作远视储备或近视确诊，只记录原始数值。',
  '4) 数值用数字：球镜/柱镜/等效球镜/角膜散光单位 D，眼轴/瞳距单位 mm，裸眼视力为小数。',
  '5) 只输出以下 JSON（无多余文字、无 Markdown）：',
  '{"date":"YYYY-MM-DD 或 null","values":{"BIO_AL":{"OD":数字|null,"OS":数字|null},"REF_SPH":{"OD":数字|null,"OS":数字|null},"REF_CYL":{"OD":数字|null,"OS":数字|null},"REF_SE":{"OD":数字|null,"OS":数字|null},"BIO_AST":{"OD":数字|null,"OS":数字|null},"VA_UCVA":{"OD":数字|null,"OS":数字|null},"REF_PD":{"OD":数字|null,"OS":null}},"note":"简短备注或空字符串"}',
].join('\n')

function stripJson(text) {
  if (!text) return ''
  let s = String(text).trim()
  if (s.indexOf('```') === 0) {
    s = s.replace(/^```(json)?/, '').replace(/```$/, '').trim()
  }
  const a = s.indexOf('{')
  const b = s.lastIndexOf('}')
  if (a >= 0 && b > a) s = s.slice(a, b + 1)
  return s
}

function sanitizeValues(values) {
  const out = {}
  if (!values || typeof values !== 'object') return out
  Object.keys(values).forEach(function (code) {
    const pair = values[code]
    if (!pair || typeof pair !== 'object') return
    const od = pair.OD
    const os = pair.OS
    out[code] = {
      OD: typeof od === 'number' && isFinite(od) ? od : null,
      OS: typeof os === 'number' && isFinite(os) ? os : null,
    }
  })
  return out
}

// 单个模型调用：收集流式结果，带业务层超时保护（小程序无法可靠 AbortController，用 Promise.race）。
function callModel(model, content) {
  let answer = ''
  const run = (async function () {
    for await (const chunk of cloud.llm.chat.completions.create({
      model: model,
      messages: [
        { role: 'system', content: SYSTEM_PROMPT },
        { role: 'user', content: content },
      ],
      stream: true,
    })) {
      const delta = chunk.choices && chunk.choices[0] && chunk.choices[0].delta
      if (delta && delta.content) answer += delta.content
    }
    return answer
  })()
  let timer = null
  const timeout = new Promise(function (_, reject) {
    timer = setTimeout(function () { reject(new Error('模型响应超时')) }, PER_MODEL_TIMEOUT)
  })
  return Promise.race([run, timeout]).then(
    function (v) { clearTimeout(timer); return v },
    function (e) { clearTimeout(timer); throw e }
  )
}

function parseAnswer(answer) {
  const json = stripJson(answer)
  let parsed
  try {
    parsed = JSON.parse(json)
  } catch (e) {
    return null
  }
  const values = sanitizeValues(parsed.values)
  const count = Object.keys(values).filter(function (c) {
    return values[c].OD !== null || values[c].OS !== null
  }).length
  if (!count) return null
  return {
    data: {
      date: typeof parsed.date === 'string' ? parsed.date : null,
      values: values,
      note: typeof parsed.note === 'string' ? parsed.note.slice(0, 80) : '',
      indicatorCount: count,
    },
  }
}

// base64DataUrl: 'data:image/jpeg;base64,xxxx'
async function parseImage(base64DataUrl) {
  const content = [
    { type: 'text', text: '请转录这张眼科检查报告上的数值，按约定 JSON 输出。' },
    { type: 'image_url', image_url: { url: base64DataUrl, detail: 'auto' } },
  ]
  let lastErr = null
  for (let i = 0; i < MODEL_CHAIN.length; i++) {
    const model = MODEL_CHAIN[i]
    try {
      const answer = await callModel(model, content)
      const result = parseAnswer(answer)
      if (result) return result
      lastErr = new Error('未识别出可用数值')
    } catch (e) {
      lastErr = e
    }
  }
  return {
    error: {
      message: '图片解析失败：' + ((lastErr && lastErr.message) || '未知错误'),
    },
  }
}

module.exports = { parseImage: parseImage }
