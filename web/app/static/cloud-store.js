/* cloud-store.js — 网页版云服务数据层。
 * 与微信小程序共用同一应用、同一云数据库（eye_profiles / eye_encounters / eye_documents）。
 * 未登录时为「演示模式」：用内置演示数据，界面明确标注；登录后读写当前用户的云端数据。
 * 对外暴露与旧后端 /api 等价的异步方法，供 app.js 调用。
 */
(function (global) {
  'use strict';

  // publicConfig 来自云服务开通返回值，可随前端发布（publishableKey 本身无权限，服务端校验 Origin）。
  var PUBLIC_CONFIG = {
    endpoint: 'https://mp-api.app.workbuddy.host',
    oauthRelayBaseUrl: 'https://www.workbuddy.cn/v2/as/genie-baas/oauth',
    publishableKey: 'wbpk_wSaAPxIbwJ3yX4DFXUPW5v_Zif0mKQJtmbGKHJuSB6qbBXUDQht1Kcg',
  };

  var cloud = null;
  try {
    cloud = global.WorkBuddyCloud.createWorkBuddyCloud({
      endpoint: PUBLIC_CONFIG.endpoint,
      oauthRelayBaseUrl: PUBLIC_CONFIG.oauthRelayBaseUrl,
      publishableKey: PUBLIC_CONFIG.publishableKey,
    });
  } catch (e) {
    console.error('[Eye] cloud init failed', e && e.message);
  }

  function ok() { return !!(cloud && cloud.auth && cloud.database); }

  // ---------- 指标元信息（与后端 analyzer.MATRIX_GROUPS 对齐，前端展示用） ----------
  var MATRIX_GROUPS = [
    { key: 'VA', name: '视力', codes: [['VA_UCVA', '裸眼视力', ''], ['VA_BCVA', '矫正视力', '']] },
    { key: 'REF', name: '屈光 · 远视储备', codes: [
      ['REF_SPH', '球镜 S', 'D'], ['REF_CYL', '柱镜 C', 'D'], ['REF_AXIS', '轴位', '°'],
      ['REF_SE', '等效球镜 SE', 'D'], ['PED_HYPEROPIA_RESERVE', '远视储备', 'D'],
      ['REF_PD', '瞳距 PD', 'mm']] },
    { key: 'BIO', name: '眼生物测量', codes: [
      ['BIO_AL', '眼轴长度 AL', 'mm'], ['BIO_K1', '角膜曲率 K1', 'D'], ['BIO_K2', '角膜曲率 K2', 'D'],
      ['BIO_AST', '角膜散光 ΔK', 'D'], ['BIO_ALCR', 'AL/CR 比', ''], ['BIO_ACD', '前房深度 ACD', 'mm'],
      ['BIO_WTW', '白到白 WTW', 'mm']] },
    { key: 'IOP', name: '眼压', codes: [['IOP_NCT', '眼压 NCT', 'mmHg']] },
  ];
  var CODE_META = {};
  MATRIX_GROUPS.forEach(function (g) {
    g.codes.forEach(function (c) { CODE_META[c[0]] = { name: c[1], unit: c[2], group: g.key }; });
  });
  var KP_ORDER = ['BIO_AL', 'REF_SPH', 'REF_CYL', 'REF_SE', 'BIO_AST', 'VA_UCVA', 'REF_PD', 'IOP_NCT'];
  var OU_ONLY = { REF_PD: true };

  function calcAge(birth, exam) {
    if (!birth || !exam) return null;
    var b = new Date(birth), e = new Date(exam);
    if (isNaN(b.getTime()) || isNaN(e.getTime())) return null;
    var age = e.getFullYear() - b.getFullYear();
    if (e.getMonth() < b.getMonth() || (e.getMonth() === b.getMonth() && e.getDate() < b.getDate())) age--;
    return age >= 0 ? age : null;
  }

  // 参考区间提示：与小程序 REFERENCE 一致，仅作随访提示
  var REF_RANGE = {
    BIO_AL: [22, 24], REF_SPH: [-0.5, 1.0], REF_SE: [-0.5, 1.0], REF_CYL: [-1.5, 1.5],
    BIO_AST: [0, 1.5], VA_UCVA: [0.7, 2], REF_PD: [50, 70], IOP_NCT: [10, 21],
  };
  function flagOf(code, v) {
    if (v == null) return '';
    var r = REF_RANGE[code];
    if (!r) return '';
    if (code === 'VA_UCVA') return v >= r[0] ? 'NORMAL' : 'WARN';
    return (v >= r[0] && v <= r[1]) ? 'NORMAL' : 'WARN';
  }

  // 把云档案/检查/文件，转换成 app.js 需要的 summary 结构（与后端 build_summary 对齐）
  function buildSummary(profile, encounters, documents) {
    var encs = (encounters || []).slice().sort(function (a, b) {
      return String(a.exam_date).localeCompare(String(b.exam_date));
    });
    var dates = encs.map(function (e) { return e.exam_date; });
    var ages = encs.map(function (e) { return calcAge(profile && profile.birth_date, e.exam_date); });
    var cyclo = encs.map(function (e) { return !!e.cyclo; });
    var visitTypes = encs.map(function (e) { return e.visit_type || ''; });
    var institutions = encs.map(function (e) { return e.institution || ''; });
    var ids = encs.map(function (e) { return e.id; });

    var groups = [];
    MATRIX_GROUPS.forEach(function (g) {
      var rows = [];
      g.codes.forEach(function (spec) {
        var code = spec[0];
        var sides = {};
        ['OD', 'OS'].forEach(function (side) {
          var cells = encs.map(function (e) {
            var vals = e.values_json || {};
            var qSide = OU_ONLY[code] ? 'OD' : side;
            var v = null;
            if (OU_ONLY[code] && side === 'OS') v = null;
            else if (vals[code] && vals[code][qSide] != null) v = vals[code][qSide];
            return { v: v, flag: flagOf(code, v) };
          });
          var present = cells.map(function (c) { return c.v; }).filter(function (v) { return v != null; });
          var delta = present.length >= 2 ? present[present.length - 1] - present[0] : null;
          sides[side] = { cells: cells, delta: delta };
        });
        var hasData = sides.OD.cells.some(function (c) { return c.v != null; }) ||
                      sides.OS.cells.some(function (c) { return c.v != null; });
        if (hasData) rows.push({ code: code, name: spec[1], unit: spec[2], ref: '', sides: sides });
      });
      if (rows.length) groups.push({ key: g.key, name: g.name, rows: rows });
    });

    var series = {};
    MATRIX_GROUPS.forEach(function (g) {
      g.codes.forEach(function (spec) {
        var code = spec[0];
        ['OD', 'OS'].forEach(function (side) {
          var pts = [];
          encs.forEach(function (e) {
            var vals = e.values_json || {};
            var v = vals[code] && vals[code][side];
            if (v != null) pts.push({ date: e.exam_date, value: v });
          });
          if (pts.length) series[code + '_' + side] = pts;
        });
      });
    });

    var timeline = encs.map(function (e) {
      var cnt = Object.keys(e.values_json || {}).filter(function (code) {
        var p = e.values_json[code];
        return p && (p.OD != null || p.OS != null);
      }).length;
      return { id: e.id, date: e.exam_date, exam_date: e.exam_date, age: calcAge(profile && profile.birth_date, e.exam_date),
        institution: e.institution || '', visit_type: e.visit_type || '', indicator_count: cnt };
    });

    // 原值明细：云检查里的 raw_json 若存在则透传
    var rawReports = [];
    encs.forEach(function (e) {
      var raw = e.raw_json;
      if (Array.isArray(raw) && raw.length) rawReports.push({ filename: e.visit_type || e.exam_date, encounter_id: e.id, raw_measurements: rawToMeasurements(raw) });
    });

    var latest = {}, latestDate = dates[dates.length - 1] || '';
    KP_ORDER.forEach(function (code) {
      ['OD', 'OS'].forEach(function (side) {
        for (var i = encs.length - 1; i >= 0; i--) {
          var v = (encs[i].values_json || {})[code];
          if (v && v[side] != null) { latest[code + '_' + side] = { value: v[side], date: dates[i] }; break; }
        }
      });
    });

    return {
      patient: profile || {}, encounters: timeline, series: series, raw_reports: rawReports,
      assessment: buildAssessment(latest, ages[ages.length - 1], cyclo[cyclo.length - 1]),
      matrix: { dates: dates, ages: ages, groups: groups, cyclo: cyclo, visit_types: visitTypes,
        encounter_ids: ids, institutions: institutions },
      _meta: { documents: documents || [], latestDate: latestDate },
    };
  }

  function rawToMeasurements(raw) {
    var out = { REF: { OD: { measurements: [] }, OS: { measurements: [] } }, KRT: { OD: null, OS: null }, pd: null };
    (raw || []).forEach(function (r) {
      var label = r.label || '';
      (r.rows || []).forEach(function (line) {
        var m = String(line).match(/([+-]?\d+\.\d+)\s*\/\s*([+-]?\d+\.\d+)/);
        if (m && label.indexOf('右') >= 0) out.REF.OD.measurements.push({ s: +m[1], c: +m[2] });
        else if (m && label.indexOf('左') >= 0) out.REF.OS.measurements.push({ s: +m[1], c: +m[2] });
      });
      var pdm = String((r.rows || []).join(' ')).match(/瞳距[^0-9]*(\d+(\.\d+)?)/);
      if (pdm) out.pd = +pdm[1];
    });
    return out;
  }

  function buildAssessment(latest, age, cyclo) {
    var findings = [];
    var alOD = latest.BIO_AL_OD, alOS = latest.BIO_AL_OS;
    if (alOD || alOS) findings.push({ level: 'info', text: '眼轴：右 ' + (alOD ? alOD.value : '—') + ' / 左 ' + (alOS ? alOS.value : '—') + ' mm，建议按固定设备定期随访。' });
    var seOD = latest.REF_SE_OD, seOS = latest.REF_SE_OS;
    if (seOD || seOS) findings.push({ level: cyclo ? 'info' : 'warn', text: '等效球镜 SE：右 ' + (seOD ? seOD.value : '—') + ' / 左 ' + (seOS ? seOS.value : '—') + ' D' + (cyclo ? '（散瞳，可作远视储备参考）。' : '（非散瞳，不能当远视储备或配镜处方）。') });
    var headline = findings.length ? '已整理历次检查的关键指标，双眼分色呈现，便于随访对比。' : '暂无足够数据生成态势总结。';
    return { headline: headline, findings: findings, actions: [], age: age, cycloplegic: !!cyclo,
      disclaimer: '健康管理记录，仅供随访参考，不构成医疗诊断。' };
  }

  // ---------- 演示模式数据（未登录，别人打开即可试用） ----------
  var DEMO = {
    profile: { id: 'demo', name: '演示档案 · 小明', birth_date: '2017-05-20', sex: 'M', relation: 'CHILD' },
    encounters: [
      { id: 5, exam_date: '2026-11-16', visit_type: '散瞳验光', cyclo: true, institution: '示例眼科医院',
        values_json: { BIO_AL: { OD: 23.80, OS: 24.09 }, REF_SPH: { OD: 0.11, OS: -0.29 }, REF_CYL: { OD: 0.30, OS: -0.09 },
          REF_SE: { OD: 0.26, OS: -0.33 }, BIO_AST: { OD: 1.36, OS: 0.64 }, VA_UCVA: { OD: 1.0, OS: 0.8 }, REF_PD: { OD: 60 } } },
      { id: 4, exam_date: '2026-10-03', visit_type: '眼生物测量', cyclo: false, institution: '示例眼科医院',
        values_json: { BIO_AL: { OD: 23.79, OS: 24.05 }, BIO_AST: { OD: 1.32, OS: 0.61 }, REF_PD: { OD: 60 } } },
      { id: 3, exam_date: '2026-08-12', visit_type: '医学验光', cyclo: false, institution: '示例视光中心',
        values_json: { REF_SPH: { OD: 0.25, OS: -0.12 }, REF_SE: { OD: 0.38, OS: -0.15 }, REF_CYL: { OD: 0.25, OS: -0.10 }, VA_UCVA: { OD: 1.0, OS: 0.9 } } },
      { id: 2, exam_date: '2026-05-20', visit_type: '年度体检', cyclo: false, institution: '示例社区卫生中心',
        values_json: { BIO_AL: { OD: 23.61, OS: 23.88 }, VA_UCVA: { OD: 1.0, OS: 1.0 } } },
      { id: 1, exam_date: '2026-01-08', visit_type: '学校筛查', cyclo: false, institution: '示例小学',
        values_json: { VA_UCVA: { OD: 1.0, OS: 0.9 }, REF_PD: { OD: 59 } } },
    ],
  };

  // ---------- 会话 ----------
  function isDemo() { return _state.demo; }
  var _state = { demo: true, phone: '', uid: '' };
  var pendingOtp = null;

  async function getSession() {
    if (!ok()) { _state.demo = true; return { demo: true, user: null }; }
    var r = await cloud.auth.getSession();
    var session = r && r.data;
    if (r && r.error || !session || !session.user) { _state.demo = true; return { demo: true, user: null }; }
    var u = session.user || {};
    _state.demo = false; _state.phone = u.phone || ''; _state.uid = u.id || '';
    return { demo: false, user: { id: u.id, phone: u.phone } };
  }

  async function sendPhoneCode(phone) {
    if (!/^1\d{10}$/.test(phone)) return { error: { message: '请输入正确的 11 位手机号' } };
    if (!ok()) return { error: { message: '云服务未就绪，请稍后重试' } };
    var sent = await cloud.auth.sendOtp({ phone: phone });
    if (sent.error) return { error: sent.error };
    pendingOtp = { phone: phone, verificationId: sent.data.verificationId, isExistingUser: sent.data.isExistingUser };
    return { data: { phone: phone } };
  }

  async function verifyPhoneCode(token) {
    if (!pendingOtp || !ok()) return { error: { message: '请先获取验证码' } };
    var done = await cloud.auth.verifyOtp({ phone: pendingOtp.phone, verificationId: pendingOtp.verificationId,
      isExistingUser: pendingOtp.isExistingUser, token: token });
    if (done.error) return { error: done.error };
    pendingOtp = null;
    return await getSession();
  }

  async function signOut() {
    if (ok() && !_state.demo) { try { await cloud.auth.signOut(); } catch (e) {} }
    _state.demo = true; _state.phone = ''; _state.uid = '';
  }

  // ---------- 档案 ----------
  async function listProfiles() {
    if (_state.demo) return [{ id: 'demo', display_name: DEMO.profile.name, relation: 'CHILD',
      birth_date: DEMO.profile.birth_date, sex: 'M', _demo: true }];
    var r = await cloud.database.from('eye_profiles').select('*').order('created_at', { ascending: true });
    if (r.error) throw r.error;
    return (r.data || []).map(function (row) {
      return { id: row.id, display_name: row.name, relation: row.relation, birth_date: row.birth_date, sex: row.sex };
    });
  }

  async function createProfile(data) {
    if (_state.demo) return { error: { message: '演示模式下不能新建档案，请先登录' } };
    var r = await cloud.database.from('eye_profiles').insert({
      name: data.display_name, relation: data.relation || '其他',
      birth_date: data.birth_date || null, sex: (data.sex === 'M' || data.sex === 'F') ? data.sex : 'U',
    }).select();
    if (r.error) return { error: r.error };
    return { id: r.data[0].id };
  }

  async function deleteProfile(id) {
    if (_state.demo) return { error: { message: '演示模式下不能删除档案' } };
    var pid = Number(id);
    var docs = await cloud.database.from('eye_documents').select('storage_path').eq('profile_id', pid);
    var paths = ((docs.data || []).map(function (d) { return d.storage_path; }).filter(Boolean));
    if (paths.length) { try { await cloud.storage.remove(paths); } catch (e) {} }
    await cloud.database.from('eye_documents').delete().eq('profile_id', pid);
    await cloud.database.from('eye_encounters').delete().eq('profile_id', pid);
    var r = await cloud.database.from('eye_profiles').delete().eq('id', pid);
    return r.error ? { error: r.error } : { data: true };
  }

  // ---------- 汇总数据 ----------
  async function getSummary(profileId) {
    if (_state.demo) {
      var s = buildSummary(DEMO.profile, DEMO.encounters, []);
      s.patient.id = 'demo';
      return s;
    }
    var pid = Number(profileId);
    var pr = await cloud.database.from('eye_profiles').select('*').eq('id', pid).maybeSingle();
    if (pr.error) throw pr.error;
    var er = await cloud.database.from('eye_encounters').select('*').eq('profile_id', pid).order('exam_date', { ascending: true });
    if (er.error) throw er.error;
    var dr = await cloud.database.from('eye_documents').select('*').eq('profile_id', pid).order('created_at', { ascending: true });
    if (dr.error) throw dr.error;
    var profile = pr.data || { id: pid };
    var docs = (dr.data || []).map(function (d) {
      return { id: d.id, encounter_id: d.encounter_id, filename: d.file_name, file_type: (d.mime_type || '').indexOf('pdf') >= 0 ? 'pdf' : 'jpg',
        storage_path: d.storage_path, mime_type: d.mime_type, exam_date: '', doc_category: '' };
    });
    var summary = buildSummary(profile, er.data || [], docs);
    // 文件按检查日期归集 exam_date，供「来源报告」按日期分组
    var encDateById = {};
    (er.data || []).forEach(function (e) { encDateById[e.id] = e.exam_date; });
    docs.forEach(function (d) { d.exam_date = encDateById[d.encounter_id] || ''; });
    summary._meta.documents = docs;
    return summary;
  }

  async function getDocuments(profileId) {
    if (_state.demo) return [];
    var r = await cloud.database.from('eye_documents').select('*').eq('profile_id', Number(profileId)).order('created_at', { ascending: true });
    if (r.error) return [];
    return (r.data || []).map(function (d) {
      return { id: d.id, encounter_id: d.encounter_id, filename: d.file_name,
        file_type: (d.mime_type || '').indexOf('pdf') >= 0 ? 'pdf' : 'jpg', storage_path: d.storage_path, mime_type: d.mime_type };
    });
  }

  // 云存储签名 URL（原始报告查看/导出用）
  async function fileUrl(storagePath) {
    if (!storagePath) return '';
    if (!ok() || _state.demo) return '';
    var r = await cloud.storage.createSignedUrl(storagePath, 1800);
    if (r.error) throw r.error;
    return r.data && (r.data.signedUrl || r.data.url) ? (r.data.signedUrl || r.data.url) : (typeof r.data === 'string' ? r.data : '');
  }

  // ---------- 上传：AI 解析 + 云存储 + 写库 ----------
  var PARSE_SYSTEM = [
    '你是眼科检查报告的"数值转录员"，不是医生，不做诊断。',
    '只把图片上清晰可见的检查数值转录为 JSON，右眼=OD、左眼=OS。',
    '规则：看不清的一律 null，禁止猜测；散瞳状态输出 false 由用户确认；不把未散瞳 SE 当远视储备。',
    '球镜/柱镜/SE/角膜散光单位 D，眼轴/瞳距 mm，视力为小数。',
    '只输出 JSON：{"date":"YYYY-MM-DD 或 null","values":{"BIO_AL":{"OD":数字|null,"OS":数字|null},"REF_SPH":{"OD":数字|null,"OS":数字|null},"REF_CYL":{"OD":数字|null,"OS":数字|null},"REF_SE":{"OD":数字|null,"OS":数字|null},"BIO_AST":{"OD":数字|null,"OS":数字|null},"VA_UCVA":{"OD":数字|null,"OS":数字|null},"REF_PD":{"OD":数字|null,"OS":null}},"note":""}',
  ].join('\n');
  var MODEL_CHAIN = ['glm-5.3-flash', 'auto', 'fast-model'];

  function fileToDataUrl(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = function () { reject(new Error('读取文件失败')); };
      reader.readAsDataURL(file);
    });
  }

  async function callModel(model, content) {
    var answer = '';
    for await (var chunk of cloud.llm.chat.completions.create({
      model: model,
      messages: [{ role: 'system', content: PARSE_SYSTEM }, { role: 'user', content: content }],
      stream: true,
    })) {
      var d = chunk.choices && chunk.choices[0] && chunk.choices[0].delta;
      if (d && d.content) answer += d.content;
    }
    return answer;
  }

  function parseAnswer(answer) {
    var s = String(answer || '').trim();
    s = s.replace(/^```(json)?/, '').replace(/```$/, '').trim();
    var a = s.indexOf('{'), b = s.lastIndexOf('}');
    if (a >= 0 && b > a) s = s.slice(a, b + 1);
    var obj;
    try { obj = JSON.parse(s); } catch (e) { return null; }
    var values = {};
    Object.keys(obj.values || {}).forEach(function (code) {
      var p = obj.values[code] || {};
      values[code] = { OD: typeof p.OD === 'number' && isFinite(p.OD) ? p.OD : null,
        OS: typeof p.OS === 'number' && isFinite(p.OS) ? p.OS : null };
    });
    var cnt = Object.keys(values).filter(function (c) { return values[c].OD != null || values[c].OS != null; }).length;
    if (!cnt) return null;
    return { date: typeof obj.date === 'string' ? obj.date : null, values: values, count: cnt };
  }

  async function parseImage(file) {
    if (!ok()) return { error: { message: '云服务未就绪' } };
    var dataUrl;
    try { dataUrl = await fileToDataUrl(file); } catch (e) { return { error: e }; }
    var content = [
      { type: 'text', text: '请转录这张眼科检查报告上的数值，按约定 JSON 输出。' },
      { type: 'image_url', image_url: { url: dataUrl, detail: 'auto' } },
    ];
    var lastErr = null;
    for (var i = 0; i < MODEL_CHAIN.length; i++) {
      try {
        var answer = await callModel(MODEL_CHAIN[i], content);
        var parsed = parseAnswer(answer);
        if (parsed) return { data: parsed };
        lastErr = new Error('未识别出可用数值');
      } catch (e) { lastErr = e; }
    }
    return { error: { message: '识别失败：' + ((lastErr && lastErr.message) || '未知错误') } };
  }

  // 上传一批文件：解析 + 存原图 + 登记文件 + 合并指标写入一次检查
  async function uploadFiles(profileId, files, opts) {
    if (_state.demo) return { error: { message: '请先登录后再上传报告' } };
    var pid = Number(profileId);
    var merged = {}, recognized = [], warnings = [], savedPaths = [];
    var anyDate = opts.date || '';
    for (var i = 0; i < files.length; i++) {
      var f = files[i];
      var parsed = await parseImage(f);
      if (parsed.error) { warnings.push(f.name + '：' + parsed.error.message); continue; }
      Object.keys(parsed.data.values).forEach(function (code) {
        var p = parsed.data.values[code];
        if (!merged[code]) merged[code] = { OD: null, OS: null };
        if (p.OD != null) merged[code].OD = p.OD;
        if (p.OS != null) merged[code].OS = p.OS;
      });
      if (!anyDate && parsed.data.date) anyDate = parsed.data.date;
      recognized.push({ filename: f.name, n_fields: parsed.data.count, date: parsed.data.date });
      // 原图存云存储
      try {
        var path = cloud.storage.userPath(_state.uid, 'reports/' + Date.now() + '_' + i + '_' + f.name.replace(/[^\w.\-]/g, '_'));
        var up = await cloud.storage.upload(path, f, { contentType: f.type || 'application/octet-stream', upsert: false });
        if (!up.error) savedPaths.push({ path: path, file: f });
      } catch (e) { warnings.push(f.name + '：原件保存失败'); }
    }
    if (!recognized.length) return { error: { message: warnings.join('；') || '未识别出可用数值' } };
    var examDate = anyDate || new Date().toISOString().slice(0, 10);
    var er = await cloud.database.from('eye_encounters').insert({
      profile_id: pid, exam_date: examDate, visit_type: opts.visitType || '检查复查',
      cyclo: !!opts.cyclo, institution: opts.institution || '',
      indicator_count: Object.keys(merged).length, values_json: merged, raw_json: [],
    }).select();
    if (er.error) return { error: er.error };
    var eid = er.data[0].id;
    for (var j = 0; j < savedPaths.length; j++) {
      var sp = savedPaths[j];
      await cloud.database.from('eye_documents').insert({
        profile_id: pid, encounter_id: eid, storage_path: sp.path,
        file_name: sp.file.name, mime_type: sp.file.type || 'image/jpeg',
      });
    }
    return { data: { encounter_id: eid, exam_date: examDate, recognized: recognized, warnings: warnings,
      stats: { observations: Object.keys(merged).length, skipped: 0 } }, summary: await getSummary(pid) };
  }

  global.EyeCloud = {
    ok: ok, isDemo: isDemo, getSession: getSession,
    sendPhoneCode: sendPhoneCode, verifyPhoneCode: verifyPhoneCode, signOut: signOut,
    listProfiles: listProfiles, createProfile: createProfile, deleteProfile: deleteProfile,
    getSummary: getSummary, getDocuments: getDocuments, fileUrl: fileUrl,
    parseImage: parseImage, uploadFiles: uploadFiles,
  };
})(window);
