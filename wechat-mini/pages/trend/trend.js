const mock = require('../../utils/mock-data.js');
const store = require('../../utils/store.js');
const app = getApp();

const SERIES = [
  { code: 'BIO_AL', name: '眼轴长度', unit: 'mm', color: '#1d4ed8' },
  { code: 'REF_SE', name: '等效球镜 SE', unit: 'D', color: '#7c3aed' },
  { code: 'BIO_AST', name: '角膜散光', unit: 'D', color: '#1d4ed8' },
  { code: 'VA_UCVA', name: '裸眼视力', unit: '', color: '#0f766e' },
];

Page({
  data: {
    series: SERIES,
    activeIndex: 0,
    meta: SERIES[0],
    gridLines: [],
    odSegments: [],
    osSegments: [],
    odDots: [],
    osDots: [],
    xLabels: [],
    minText: '',
    maxText: '',
    dateLabel: '',
    hasData: false,
  },

  onLoad() { this.render(); },
  onShow() {
    const self = this
    store.ready().then(function () { self.render() })
  },

  onSeriesTap(e) {
    this.setData({ activeIndex: Number(e.currentTarget.dataset.index) }, () => this.render());
  },

  render() {
    const meta = this.data.series[this.data.activeIndex];
    const profiles = store.getProfiles();
    const profile = profiles.filter(p => p.id === app.globalData.currentProfileId)[0] || profiles[0];
    const encounters = profile ? store.encountersOf(profile.id).slice().reverse() : [];
    const ref = mock.REFERENCE[meta.code];
    const collected = [];
    encounters.forEach(e => {
      const pair = e.values[meta.code];
      if (pair) collected.push({ date: e.date.slice(5), od: pair.OD, os: pair.OS });
    });

    if (!collected.length) {
      this.setData({ meta: meta, hasData: false });
      return;
    }

    const values = [];
    collected.forEach(p => {
      if (typeof p.od === 'number') values.push(p.od);
      if (typeof p.os === 'number') values.push(p.os);
    });
    let min = Math.min.apply(null, values);
    let max = Math.max.apply(null, values);
    if (max - min < 0.01) { min -= 0.5; max += 0.5; }
    const pad = (max - min) * 0.18;
    min -= pad; max += pad;

    const left = 74;
    const right = 24;
    const top = 26;
    const bottom = 56;
    const innerW = 690 - left - right;
    const innerH = 340 - top - bottom;
    const n = collected.length;
    const xOf = i => left + (n === 1 ? innerW / 2 : (innerW * i) / (n - 1));
    const yOf = v => top + innerH - ((v - min) / (max - min)) * innerH;

    function makeSegments(key) {
      const segs = [];
      const pts = [];
      collected.forEach((p, i) => {
        if (typeof p[key] === 'number') pts.push({ x: xOf(i), y: yOf(p[key]), label: mock.formatValue(p[key], ref.unit) });
      });
      for (let i = 0; i < pts.length - 1; i += 1) {
        const a = pts[i];
        const b = pts[i + 1];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        segs.push({
          left: a.x,
          top: a.y,
          width: Math.sqrt(dx * dx + dy * dy),
          angle: Math.atan2(dy, dx) * 180 / Math.PI,
        });
      }
      const dots = pts.map(p => ({ left: p.x - 7, top: p.y - 7, label: p.label, date: '' }));
      return { segs: segs, dots: dots };
    }

    const od = makeSegments('od');
    const os = makeSegments('os');
    const gridLines = [];
    for (let i = 0; i <= 3; i += 1) {
      gridLines.push({ left: left, top: top + (innerH * i) / 3, width: innerW });
    }
    const xLabels = collected.map((p, i) => ({ left: xOf(i) - 34, text: p.date }));

    this.setData({
      meta: meta,
      hasData: true,
      gridLines: gridLines,
      odSegments: od.segs,
      osSegments: os.segs,
      odDots: od.dots,
      osDots: os.dots,
      xLabels: xLabels,
      minText: mock.formatValue(min, ref.unit),
      maxText: mock.formatValue(max, ref.unit),
      dateLabel: collected.length ? collected[0].date + ' 至 ' + collected[collected.length - 1].date : '',
    });
  },
});
