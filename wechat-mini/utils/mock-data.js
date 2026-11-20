// utils/mock-data.js — 原型用模拟数据。接入后端时替换本文件的数据来源即可。
// 文案严格遵循个人主体"健康管理记录"边界：只呈现数值记录与参考区间，不含诊断/处方表述。

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

// 参考区间仅作人群参考提示，不构成诊断。
const REFERENCE = {
  BIO_AL: { name: '眼轴长度', unit: 'mm', min: 22.0, max: 24.0, note: '随年龄增长，仅供随访参考' },
  REF_SPH: { name: '球镜 S', unit: 'D', min: -0.5, max: 1.0, note: '电脑验光值，非配镜处方' },
  REF_SE: { name: '等效球镜 SE', unit: 'D', min: -0.5, max: 1.0, note: '儿童以散瞳检查为准' },
  REF_CYL: { name: '柱镜 C', unit: 'D', min: -1.5, max: 1.5, note: '生理性散光一般较小' },
  BIO_AST: { name: '角膜散光', unit: 'D', min: 0, max: 1.5, note: '角膜曲率差值' },
  VA_UCVA: { name: '裸眼视力', unit: '', min: 0.7, max: 2.0, note: '不同年龄段参考不同' },
  REF_PD: { name: '瞳距', unit: 'mm', min: 50, max: 70, note: '配镜时需现场复核' },
};

const PROFILES = [
  {
    id: 'p1',
    name: '小明',
    relation: '子女',
    birthDate: '2017-05-20',
    sex: '男',
    age: 9,
    visits: 5,
    latestDate: '2026-11-16',
  },
  {
    id: 'p2',
    name: '我',
    relation: '本人',
    birthDate: '1990-03-12',
    sex: '女',
    age: 36,
    visits: 2,
    latestDate: '2026-09-08',
  },
];

// 每次检查：date 类型/散瞳标记 + 双眼指标
const ENCOUNTERS = [
  {
    id: 'e5', patientId: 'p1', date: '2026-11-16', type: '散瞳验光', cyclo: true,
    institution: '示例眼科医院', indicatorCount: 12,
    values: { BIO_AL: { OD: 23.80, OS: 24.09 }, REF_SPH: { OD: 0.11, OS: -0.29 },
      REF_SE: { OD: 0.26, OS: -0.33 }, REF_CYL: { OD: 0.30, OS: -0.09 },
      BIO_AST: { OD: 1.36, OS: 0.64 }, VA_UCVA: { OD: 1.0, OS: 0.8 }, REF_PD: { OD: 60 } },
    raw: [
      { label: '屈光 REF · 右眼', rows: ['第1次  +0.12 / +0.30 / 78°', '第2次  +0.11 / +0.28 / 78°', '代表值 +0.11 / +0.30 / 78°'] },
      { label: '屈光 REF · 左眼', rows: ['第1次  -0.30 / -0.07 / 171°', '第2次  -0.29 / -0.09 / 167°', '代表值 -0.29 / -0.09 / 167°'] },
      { label: '角膜曲率 KRT', rows: ['右眼 42.24 / 43.60 D', '左眼 42.35 / 42.99 D'] },
    ],
  },
  {
    id: 'e4', patientId: 'p1', date: '2026-10-03', type: '眼生物测量', cyclo: false,
    institution: '示例眼科医院', indicatorCount: 8,
    values: { BIO_AL: { OD: 23.79, OS: 24.05 }, BIO_AST: { OD: 1.32, OS: 0.61 },
      REF_PD: { OD: 60 } },
    raw: [{ label: '眼生物测量', rows: ['右眼 眼轴 23.79 mm', '左眼 眼轴 24.05 mm'] }],
  },
  {
    id: 'e3', patientId: 'p1', date: '2026-08-12', type: '医学验光', cyclo: false,
    institution: '示例视光中心', indicatorCount: 6,
    values: { REF_SPH: { OD: 0.25, OS: -0.12 }, REF_SE: { OD: 0.38, OS: -0.15 },
      REF_CYL: { OD: 0.25, OS: -0.10 }, VA_UCVA: { OD: 1.0, OS: 0.9 } },
    raw: [{ label: '电脑验光', rows: ['右眼 S +0.25 / C +0.25', '左眼 S -0.12 / C -0.10'] }],
  },
  {
    id: 'e2', patientId: 'p1', date: '2026-05-20', type: '年度体检', cyclo: false,
    institution: '示例社区卫生中心', indicatorCount: 4,
    values: { BIO_AL: { OD: 23.61, OS: 23.88 }, VA_UCVA: { OD: 1.0, OS: 1.0 } },
    raw: [{ label: '筛查记录', rows: ['右眼 眼轴 23.61 mm', '左眼 眼轴 23.88 mm'] }],
  },
  {
    id: 'e1', patientId: 'p1', date: '2026-01-08', type: '学校筛查', cyclo: false,
    institution: '示例小学', indicatorCount: 3,
    values: { VA_UCVA: { OD: 1.0, OS: 0.9 }, REF_PD: { OD: 59 } },
    raw: [{ label: '筛查记录', rows: ['右眼 视力 1.0', '左眼 视力 0.9'] }],
  },
  {
    id: 'e6', patientId: 'p2', date: '2026-09-08', type: '医学验光', cyclo: false,
    institution: '示例眼科医院', indicatorCount: 5,
    values: { REF_SPH: { OD: -2.10, OS: -1.85 }, REF_SE: { OD: -2.25, OS: -2.00 }, REF_PD: { OD: 63 } },
    raw: [{ label: '电脑验光', rows: ['右眼 S -2.10', '左眼 S -1.85'] }],
  },
  {
    id: 'e7', patientId: 'p2', date: '2026-03-02', type: '检查复查', cyclo: false,
    institution: '示例眼科医院', indicatorCount: 4,
    values: { REF_SPH: { OD: -1.95, OS: -1.70 }, REF_SE: { OD: -2.10, OS: -1.85 } },
    raw: [{ label: '电脑验光', rows: ['右眼 S -1.95', '左眼 S -1.70'] }],
  },
];

const GROUPS = [
  { key: 'BIO', name: '眼轴与角膜', codes: ['BIO_AL', 'BIO_AST'] },
  { key: 'REF', name: '屈光度数', codes: ['REF_SPH', 'REF_CYL', 'REF_SE', 'REF_PD'] },
  { key: 'VA', name: '视力', codes: ['VA_UCVA'] },
];

function profiles() {
  return clone(PROFILES);
}

function encountersOf(patientId) {
  return clone(ENCOUNTERS.filter(function (e) { return e.patientId === patientId; }));
}

function encounterById(id) {
  const found = ENCOUNTERS.filter(function (e) { return e.id === id; });
  return found.length ? clone(found[0]) : null;
}

function formatValue(value, unit) {
  if (value === null || value === undefined) return '—';
  let text = Number(value).toFixed(2);
  if (unit === 'D' && value > 0) text = '+' + text;
  return text;
}

function flagOf(code, value) {
  const ref = REFERENCE[code];
  if (!ref || value === null || value === undefined) return 'normal';
  const v = Number(value);
  if (code === 'VA_UCVA') return v >= ref.min ? 'normal' : 'warn';
  if (v < ref.min || v > ref.max) return 'warn';
  return 'normal';
}

module.exports = {
  REFERENCE: REFERENCE,
  GROUPS: GROUPS,
  profiles: profiles,
  encountersOf: encountersOf,
  encounterById: encounterById,
  formatValue: formatValue,
  flagOf: flagOf,
};
