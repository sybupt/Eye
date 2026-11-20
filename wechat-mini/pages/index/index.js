const store = require('../../utils/store.js');
const mock = require('../../utils/mock-data.js');
const app = getApp();

// 左右眼主题色：右眼蓝、左眼橙，一眼可辨
const EYE_COLORS = { OD: '#2563eb', OS: '#ea580c' };
const NULL_COLOR = '#cbd5e1';
const GROUP_COLORS = { BIO: '#1d4ed8', REF: '#7c3aed', VA: '#0f766e' };
// 关键指标展示顺序（每项单独一行）
const KP_ORDER = ['BIO_AL', 'REF_SPH', 'REF_CYL', 'REF_SE', 'BIO_AST', 'VA_UCVA', 'REF_PD'];

Page({
  data: {
    profiles: [],
    profileIndex: 0,
    profile: null,
    encounters: [],
    visits: [],
    groups: [],
    kpis: [],
    latestDate: '',
    visitCount: 0,
    loading: true,
  },

  onLoad() {
    const profiles = store.getProfiles();
    let idx = 0;
    for (let i = 0; i < profiles.length; i++) {
      if (profiles[i].id === app.globalData.currentProfileId) { idx = i; break; }
    }
    this.setData({ profiles: profiles, profileIndex: idx });
  },

  onShow() {
    const self = this
    // 档案可能在"我的"页被增删改，等待会话/数据就绪后重新读取
    store.ready().then(function () { self.loadData() })
  },

  loadData() {
    const profiles = store.getProfiles()
    const profile = profiles[this.data.profileIndex] || profiles[0]
    if (!profile) {
      this.setData({ profiles: [], profileIndex: 0, profile: null, encounters: [], visits: [], groups: [], kpis: [], visitCount: 0, latestDate: '暂无', loading: false })
      return
    }
    // 同步当前档案 id 与列表索引
    let idx = this.data.profileIndex
    if (!profiles[idx] || profiles[idx].id !== profile.id) {
      idx = 0
      for (let i = 0; i < profiles.length; i++) { if (profiles[i].id === profile.id) { idx = i; break } }
    }
    app.globalData.currentProfileId = profile.id
    // 按日期聚合成批次：同一天的多次上传合并为一条，指标与原始报告归集到一起
    const batches = store.groupByDate(profile.id)
    const visits = batches.map(function (b) {
      return { id: b.id, short: b.date.slice(5), date: b.date, type: b.type, cyclo: b.cyclo }
    })
    this.setData({
      profiles: profiles,
      profileIndex: idx,
      profile: profile,
      encounters: batches,
      visits: visits,
      groups: this.buildGroups(batches),
      kpis: this.buildKpis(batches[0]),
      latestDate: batches.length ? batches[0].date : '暂无',
      visitCount: batches.length,
      loading: false,
    });
  },

  // 最近一次：每个指标单独一行，右眼蓝、左眼橙
  buildKpis(encounter) {
    if (!encounter) return [];
    const result = [];
    KP_ORDER.forEach(code => {
      const ref = mock.REFERENCE[code];
      const pair = encounter.values[code];
      if (!ref || !pair) return;
      const hasOd = pair.OD !== null && pair.OD !== undefined;
      const hasOs = pair.OS !== null && pair.OS !== undefined;
      const isShared = hasOd && !hasOs; // 瞳距等双眼共用值
      result.push({
        code: code,
        name: ref.name + (isShared ? '（双眼）' : ''),
        unit: ref.unit,
        od: hasOd ? mock.formatValue(pair.OD, ref.unit) : '—',
        os: hasOs ? mock.formatValue(pair.OS, ref.unit) : (isShared ? '同右眼' : '—'),
        odColor: hasOd ? EYE_COLORS.OD : NULL_COLOR,
        osColor: (hasOs || isShared) ? EYE_COLORS.OS : NULL_COLOR,
        odFlag: mock.flagOf(code, pair.OD),
        osFlag: mock.flagOf(code, pair.OS),
      });
    });
    return result;
  },

  // 时间线矩阵：左右眼用固定主题色，异常值红字 + 角标
  buildGroups(encounters) {
    const result = [];
    mock.GROUPS.forEach(g => {
      const rows = [];
      g.codes.forEach(code => {
        const ref = mock.REFERENCE[code];
        if (!ref) return;
        let hasValue = false;
        const sides = ['OD', 'OS'].map(side => {
          const cells = encounters.map(e => {
            const v = e.values[code] && e.values[code][side];
            if (v !== null && v !== undefined) hasValue = true;
            const flag = mock.flagOf(code, v);
            return {
              date: e.date,
              text: v !== null && v !== undefined ? mock.formatValue(v, ref.unit) : '—',
              flag: flag,
              valStyle: (v === null || v === undefined) ? '' : 'color:' + EYE_COLORS[side],
            };
          });
          return {
            side: side,
            eye: side === 'OD' ? '右' : '左',
            badgeStyle: 'background:' + EYE_COLORS[side],
            cells: cells,
          };
        });
        if (!hasValue) return;
        rows.push({ code: code, name: ref.name, unit: ref.unit, color: GROUP_COLORS[g.key], sides: sides });
      });
      if (rows.length) result.push({ key: g.key, name: g.name, color: GROUP_COLORS[g.key], rows: rows });
    });
    return result;
  },

  onProfileChange(e) {
    this.setData({ profileIndex: Number(e.detail.value) }, () => this.loadData());
  },

  goUpload() {
    wx.navigateTo({ url: '/pages/upload/upload' });
  },

  goTrend() {
    wx.switchTab({ url: '/pages/trend/trend' });
  },

  goRecord(e) {
    const date = e.currentTarget.dataset.date;
    const pid = this.data.profile ? this.data.profile.id : '';
    wx.navigateTo({ url: '/pages/record/record?date=' + encodeURIComponent(date) + '&pid=' + encodeURIComponent(pid) });
  },
});
