/* app.js — 简洁汇总：以时间为线，一眼看清历次检查的关键指标 */
let patients = [], currentPid = null, summary = null, chartIns = null, upFiles = [], allDocs = [];

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const api = async (url, opt) => { const r = await fetch(url, opt); if(!r.ok) throw new Error(await r.text()); return r.json(); };

/* ---------- 初始化 ---------- */
async function init(){
  try{
    patients = await api('/api/patients');
    renderPatientSelect();
    if(patients.length){
      const latest = patients.reduce((a,b)=>(b.id>a.id?b:a), patients[0]);
      currentPid = latest.id; $('patientSel').value = currentPid;
      await loadPatient();
    }else{
      showEmptyAll();
    }
  }catch(e){ console.error(e); }
}
function renderPatientSelect(){
  $('patientSel').innerHTML = patients.length
    ? patients.map(p=>`<option value="${p.id}">${esc(p.display_name)}${p.relation==='CHILD'?'（儿童）':''}</option>`).join('')
    : '<option value="">暂无档案</option>';
}
async function onPatientChange(){ currentPid = +$('patientSel').value || null; await loadPatient(); }

function showEmptyAll(){
  $('verdictCard').innerHTML='<div class="empty">请先新建档案并上传报告</div>';
  ['kpiCard','matrixCard','chartCard','rawDetails','tlDetails','srcDetails'].forEach(id=>$(id).style.display='none');
  $('footNote').textContent='本工具为健康管理记录与人群参考判读，不构成医疗诊断；具体请遵医嘱并携带原件就诊。';
}

async function loadPatient(){
  if(!currentPid){ showEmptyAll(); return; }
  try{
    const [s, docs] = await Promise.all([
      api('/api/summary/'+currentPid),
      api('/api/documents/'+currentPid).catch(()=>[])
    ]);
    summary = s; allDocs = docs || [];
    renderAll();
  }catch(e){ console.error(e); }
}

function renderAll(){
  renderVerdict();
  renderKpi();
  renderTimelineMatrix();
  renderSeriesBtns();
  renderRaw();
  renderTimeline();
  renderSource();
  if($('chartCard').style.display!=='none') setTimeout(renderChart,40);
}

/* ---------- 新建档案 ---------- */
function openPatientModal(){ $('patientModal').classList.add('on'); }
function closeModal(id){ $(id).classList.remove('on'); if(id==='docModal') $('docPrevBody').innerHTML='<div class="empty">加载中…</div>'; }
async function createPatient(){
  const name = $('npName').value.trim();
  if(!name){ alert('请填写姓名'); return; }
  const fd = new FormData();
  fd.append('display_name',name); fd.append('birth_date',$('npBirth').value);
  fd.append('sex',$('npSex').value); fd.append('relation',$('npRel').value);
  const r = await api('/api/patients',{method:'POST',body:fd});
  closeModal('patientModal'); patients = await api('/api/patients');
  currentPid = r.id; renderPatientSelect(); $('patientSel').value=r.id;
  $('npName').value=''; $('npBirth').value='';
  await loadPatient();
}

/* ---------- 上传：上传即入库即汇总 ---------- */
const drop = $('drop');
drop.addEventListener('dragover', e=>{e.preventDefault();drop.classList.add('over')});
drop.addEventListener('dragleave', ()=>drop.classList.remove('over'));
drop.addEventListener('drop', e=>{e.preventDefault();drop.classList.remove('over');addUpFiles([...e.dataTransfer.files])});
$('fileInput').addEventListener('change', e=>{addUpFiles([...e.target.files]);e.target.value=''});
function addUpFiles(list){ upFiles.push(...list); $('upBtn').disabled = upFiles.length===0 || !currentPid;
  $('recvMsg').innerHTML=`<span class="sub">已选择 ${upFiles.length} 个文件：${esc(upFiles.map(f=>f.name).join('、'))}</span>`; }

async function doUpload(){
  if(!upFiles.length){ alert('请先选择报告照片'); return; }
  if(!currentPid){ alert('请先新建或选择一个档案'); return; }
  $('upBtn').disabled=true; $('prog').style.display='block'; $('progBar').style.width='25%';
  $('recvMsg').innerHTML='<span class="sub"><span class="spin"></span> 正在识别并并入时间线，请稍候…</span>';
  const fd = new FormData();
  upFiles.forEach(f=>fd.append('files',f));
  fd.append('patient_id', currentPid);
  fd.append('exam_date', $('upDate').value);
  fd.append('visit_type', $('upVisit').value);
  fd.append('institution', $('upInst').value.trim());
  fd.append('cycloplegic', $('upCyclo').checked ? 'true' : 'auto');
  try{
    $('progBar').style.width='60%';
    const r = await api('/api/quick-add',{method:'POST',body:fd});
    $('progBar').style.width='100%';
    summary = r.summary;
    allDocs = await api('/api/documents/'+currentPid).catch(()=>[]);
    renderAll();
    const ok = r.recognitions.filter(x=>!x.error);
    const warns = r.recognitions.flatMap(x=>x.warnings||[]);
    let msg = `<div class="alert ok">✓ 已并入档案：识别 ${ok.length} 份报告，入库 ${r.stats.observations} 项指标（检查日期 ${esc(r.chosen_date)}）。`+
      (r.stats.skipped?` 跳过 ${r.stats.skipped} 项低质量数值。`:'')+`</div>`;
    ok.forEach(x=>msg+=`<div class="sub">📄 ${esc(x.filename)} · ${esc(x.category||'未分类')} · ${x.n_fields} 项 ${x.date?'· '+esc(x.date):''}</div>`);
    warns.forEach(w=>msg+=`<div class="alert warn" style="margin:4px 0">⚠ ${esc(w)}</div>`);
    upFiles=[]; $('fileInput').value=''; $('upDate').value=''; $('upInst').value=''; $('upCyclo').checked=false;
    $('recvMsg').innerHTML=msg;
    setTimeout(()=>$('prog').style.display='none',400);
  }catch(e){
    $('recvMsg').innerHTML=`<div class="alert">上传失败：${esc(e.message.slice(0,200))}</div>`;
    $('prog').style.display='none';
  }finally{ $('upBtn').disabled = upFiles.length===0 || !currentPid; }
}

/* ---------- 按日期合并批次（与小程序 groupByDate 一致） ---------- */
const EYE_OD = '#2563eb', EYE_OS = '#ea580c'; // 右眼蓝 / 左眼橙，一眼可辨
const KP_ORDER = ['BIO_AL','REF_SPH','REF_CYL','REF_SE','PED_HYPEROPIA_RESERVE','BIO_AST','VA_UCVA','REF_PD','IOP_NCT'];
const BAD_FLAGS = ['CRITICAL','WARN','HIGH','LOW'];
const fmtVal = (v,u)=> (v===null||v===undefined) ? '—' : ((u==='D'&&v>0?'+':'')+Number(v).toFixed(2));
const isWarn = f => BAD_FLAGS.includes(f);

// 同一天多次上传/多份报告合并为一个批次：日期只出现一次，指标与原件按日期归集
function buildBatches(){
  if(!summary) return [];
  const mx = summary.matrix || {};
  const dates = mx.dates || [], ids = mx.encounter_ids || [];
  const cyclo = mx.cyclo || [], vts = mx.visit_types || [];
  const insts = mx.institutions || [], ages = mx.ages || [];
  const encById = {};
  (summary.encounters||[]).forEach(e=>{ encById[e.id] = e; });
  const buckets = [], index = {};
  dates.forEach((d,i)=>{
    const key = d || '未注明日期';
    if(index[key]===undefined){
      index[key] = buckets.length;
      buckets.push({date:key, idx:[], ids:[], types:[], institutions:[], cyclo:false, ages:[]});
    }
    const b = buckets[index[key]];
    b.idx.push(i);
    const eid = ids[i];
    b.ids.push(eid);
    if(cyclo[i]) b.cyclo = true;
    const vt = vts[i] || (encById[eid] && encById[eid].visit_type) || '';
    if(vt && b.types.indexOf(vt)<0) b.types.push(vt);
    const ins = insts[i] || (encById[eid] && encById[eid].institution) || '';
    if(ins && b.institutions.indexOf(ins)<0) b.institutions.push(ins);
    if(ages[i]!=null) b.ages.push(ages[i]);
  });
  buckets.forEach(b=>{
    b.docs = allDocs.filter(d=>b.ids.map(String).indexOf(String(d.encounter_id))>=0);
    b.type = b.types.join(' / ') || '检查';
    b.institution = b.institutions.join(' / ') || '自录';
    b.short = b.date.slice(5);
    b.reportCount = b.docs.length || b.ids.length;
    b.age = b.ages.length ? b.ages[b.ages.length-1] : null;
  });
  return buckets;
}

// 批次内每项指标的代表值：同日多份报告合并，后一次非空值覆盖前一次
function batchValues(b){
  const out = {};
  if(!b || !summary) return out;
  (summary.matrix.groups||[]).forEach(g=>{
    g.rows.forEach(r=>{
      ['OD','OS'].forEach(side=>{
        const cells = (r.sides[side] && r.sides[side].cells) || [];
        let v = null, flag = '';
        b.idx.forEach(i=>{
          const c = cells[i];
          if(c && c.v!=null){ v = c.v; flag = c.flag || ''; }
        });
        if(v!=null){
          if(!out[r.code]) out[r.code] = {code:r.code,name:r.name,unit:r.unit,ref:r.ref||'',OD:null,OS:null};
          out[r.code][side] = v;
          out[r.code][side+'Flag'] = flag;
        }
      });
    });
  });
  return out;
}

/* ---------- 态势总结 ---------- */
function renderVerdict(){
  const s=summary, a=s.assessment||{}, ped=s.pediatric||{};
  const findings=a.findings||[];
  let level='ok';
  if(findings.some(f=>f.level==='danger')) level='high';
  else if(findings.some(f=>f.level==='warn')) level='warn';
  const lv2cls={ok:'ok',info:'info',warn:'warn',danger:'high'};
  let h=`<h3>🩺 眼睛情况 <span class="sub">${a.age!=null?a.age+' 岁 · ':''}${a.cycloplegic?'散瞳验光':'未散瞳'} · 基于最近一次检查</span></h3>`;
  h+=`<div class="headline ${level}">${esc(a.headline||'暂无足够数据生成态势总结')}</div>`;
  if(a.stage) h+=`<div class="stage">当前评估：<b>${esc(a.stage)}</b>${ped.stage_basis?' · '+esc(ped.stage_basis):''}</div>`;
  if(findings.length){
    h+=`<div class="findings">`+findings.map(f=>
      `<div class="find ${lv2cls[f.level]||'info'}">${esc(f.text)}</div>`).join('')+`</div>`;
  }
  if((a.actions||[]).length){
    h+=`<details style="margin-top:12px;border-color:#dbe6fb"><summary style="color:var(--pri)">💡 防控建议（${a.actions.length} 条）</summary><div class="dbody"><ol class="assess-actions" style="padding-left:20px;font-size:12.5px;line-height:1.9">${a.actions.map(x=>`<li>${esc(x)}</li>`).join('')}</ol><div class="hint">${esc(a.disclaimer||'')}</div></div></details>`;
  }
  $('verdictCard').innerHTML=h;
}

/* 从矩阵中取某指标某眼的最新非空值 */
function latestOf(code, side){
  const mx=summary.matrix||{};
  for(const g of mx.groups) for(const r of g.rows) if(r.code===code){
    const cells=(r.sides[side]&&r.sides[side].cells)||[];
    for(let i=cells.length-1;i>=0;i--) if(cells[i].v!=null) return {v:cells[i].v,flag:cells[i].flag,date:mx.dates[i]};
  }
  return null;
}
const fv=(v,d=2)=>v==null?'—':Number(v).toFixed(d).replace(/\.00$/,'');
/* 屈光度(D)：始终带符号、两位小数，符合验光记法 */
const fd=v=>v==null?'—':(v>0?'+':'')+Number(v).toFixed(2);

/* 指标类别配色：同类同色；主色用于右眼，浅色用于左眼 */
const GRP_THEME={
  BIO:{c:'#1d4ed8',bg:'#eaf0ff',soft:'#6d88d6'},
  REF:{c:'#7c3aed',bg:'#f3edff',soft:'#a98ce0'},
  VA:{c:'#0f766e',bg:'#e7f6f2',soft:'#6aada3'},
  IOP:{c:'#c2410c',bg:'#fff1e7',soft:'#dd946e'},
  OCT:{c:'#be185d',bg:'#fdeef4',soft:'#d98cb0'},
  VF:{c:'#0369a1',bg:'#e9f4fb',soft:'#6ba5cb'},
  BV:{c:'#4d7c0f',bg:'#f1f7e7',soft:'#94b86a'},
  DRY:{c:'#a16207',bg:'#fdf6e3',soft:'#c9a85f'},
};
const DEF_THEME={c:'#475569',bg:'#eef2f8',soft:'#94a3b8'};

/* ---------- 最近一次 · 关键指标：每项取各自最近一次有值的记录（含眼轴），右眼蓝 / 左眼橙 ---------- */
function renderKpi(){
  const batches = buildBatches();
  if(!batches.length){ $('kpiCard').style.display='none'; return; }
  const allVals = batches.map(b=>batchValues(b));
  const latestDate = batches[batches.length-1].date;
  let h = '<table class="kpi-table"><thead><tr><th>检查项目</th><th class="od">右眼 OD</th><th class="os">左眼 OS</th></tr></thead><tbody>';
  let any = false;
  KP_ORDER.forEach(code=>{
    let rec = null;
    for(let i=batches.length-1;i>=0;i--){
      const r = allVals[i][code];
      if(r && (r.OD!=null || r.OS!=null)){ rec = {r:r, date:batches[i].date}; break; }
    }
    if(!rec) return;
    const r = rec.r;
    any = true;
    const cell = side=>{
      const v = r[side];
      const shared = (side==='OS' && r.OD!=null && r.OS==null);
      const txt = v!=null ? fmtVal(v,r.unit) : (shared?'同右眼':'—');
      const cls = v!=null ? side.toLowerCase() : (shared?'os':'none');
      const dot = isWarn(r[side+'Flag']) ? '<span class="warn-dot">!</span>' : '';
      return `<td><span class="kt-val ${cls}">${txt}</span>${dot}</td>`;
    };
    const dayTag = rec.date!==latestDate ? `<span class="kt-day">${esc(rec.date.slice(5))}</span>` : '';
    h += `<tr><td class="kt-name">${esc(r.name)}${r.unit?`<span class="u">${esc(r.unit)}</span>`:''}${dayTag}</td>${cell('OD')}${cell('OS')}</tr>`;
  });
  h += '</tbody></table>';
  if(!any){ $('kpiCard').style.display='none'; return; }
  $('kpiCard').style.display='block';
  $('kpiDate').textContent = '截至 '+latestDate+' · 每项取最近一次记录（标注日期的为更早测量）';
  $('kpiBox').innerHTML = h;
}

/* ---------- 时间线矩阵：按日期合并为列，指标名独立一行 + 右眼一行 + 左眼一行 ---------- */
const GROUP_ORDER={BIO:0,REF:1,VA:2,IOP:3,OCT:4,VF:5,BV:6,DRY:7};
const ROW_ORDER={BIO:['BIO_AL','PED_AL_GROWTH','BIO_K1','BIO_K2','BIO_AST','BIO_ALCR','BIO_ACD','BIO_WTW'],
  REF:['REF_SPH','REF_CYL','REF_AXIS','REF_SE','PED_HYPEROPIA_RESERVE','PED_SE_PROGRESS','REF_PD']};
const FLAG_CLS={CRITICAL:'cell-CRITICAL',WARN:'cell-WARN',LOW:'cell-WARN',HIGH:'cell-WARN',NORMAL:'cell-NORMAL'};
function renderTimelineMatrix(){
  const batches = buildBatches();
  if(!batches.length || !(summary.matrix.groups||[]).some(g=>g.rows.length)){
    $('matrixCard').style.display='none'; return;
  }
  $('matrixCard').style.display='block';
  const n = batches.length;
  const vals = batches.map(b=>batchValues(b)); // 每列先按日期合并好
  let h = '<table class="tl-matrix"><thead><tr><th class="col-metric">关键指标</th>';
  batches.forEach(b=>{
    h += `<th><div class="tl-head-date">${esc(b.date)}</div>`+
         `<div class="tl-head-sub">${esc(b.type||'检查')}${b.age!=null?' · '+fv(b.age,1)+'岁':''}</div>`+
         `<div class="tl-head-sub">${b.reportCount} 份报告</div>`+
         (b.cyclo?'<span class="tl-head-tag">散瞳</span>':'')+`</th>`;
  });
  h += '</tr></thead><tbody>';

  const groups=[...(summary.matrix.groups||[])].sort((a,b)=>(GROUP_ORDER[a.key]??9)-(GROUP_ORDER[b.key]??9));
  groups.forEach(g=>{
    const th=GRP_THEME[g.key]||DEF_THEME;
    const rows=[...g.rows].sort((a,b)=>{
      const order=ROW_ORDER[g.key]||[];
      const ia=order.indexOf(a.code), ib=order.indexOf(b.code);
      return (ia<0?99:ia)-(ib<0?99:ib);
    });
    if(!rows.length) return;
    h += `<tr class="tl-grp" style="--gc:${th.c};--gc-bg:${th.bg}"><td colspan="${n+1}">${esc(g.name)}</td></tr>`;
    rows.forEach(r=>{
      const odAny = vals.some(m=>m[r.code] && m[r.code].OD!=null);
      const osAny = vals.some(m=>m[r.code] && m[r.code].OS!=null);
      // 指标名独立一行（同类同色）
      h += `<tr class="tl-metric-row" style="--gc:${th.c}"><td class="col-metric">${esc(r.name)}<span class="u">${esc(r.unit)}</span></td><td colspan="${n}"></td></tr>`;
      ['OD','OS'].forEach(side=>{
        if(side==='OS' && !osAny && odAny) return; // 瞳距等双眼共用值不显示左眼行
        const series = vals.map(m=>(m[r.code]&&m[r.code][side]!=null)?m[r.code][side]:null);
        const present = series.filter(v=>v!=null);
        const delta = present.length>=2 ? present[present.length-1]-present[0] : null;
        h += `<tr class="eye-${side.toLowerCase()}" style="--gc:${th.c};--gc-soft:${th.soft}"><td class="col-metric"><span class="ebadge">${side==='OD'?'右':'左'}</span></td>`;
        for(let i=0;i<n;i++){
          const rec = vals[i][r.code];
          const v = rec ? rec[side] : null;
          const flag = rec ? rec[side+'Flag'] : '';
          let cell = '<span class="dash">—</span>';
          if(v!=null){
            const chg = (delta!=null && i===n-1) ? `<span class="chg ${delta>0?'up':delta<0?'down':''}">首末 ${delta>0?'+':''}${fd(delta)}</span>` : '';
            const fcls = (flag && flag!=='NORMAL') ? (FLAG_CLS[flag]||'') : '';
            cell = `<span class="tl-val ${fcls}">${fmtVal(v,r.unit)}${chg}</span>`;
          }
          h += `<td class="${i===n-1?'tl-latest':''}">${cell}</td>`;
        }
        h += '</tr>';
      });
    });
  });
  h += '</tbody></table>';
  h += '<div class="hint">每列为同一个日期（同日多份报告已合并）：<b>同类指标同色</b>，右眼蓝色 / 左眼橙色；红/橙为人群参考提示，<b>最新</b>列标注首末变化。散瞳检查的 SE 才可作远视储备判读；数值请以纸质原件为准。</div>';
  $('tlMatrix').innerHTML=h;
}

/* ---------- 趋势曲线 ---------- */
const SERIES_META={
  BIO_AL:{name:'眼轴长度',unit:'mm'}, REF_SE:{name:'等效球镜 SE',unit:'D'},
  REF_SPH:{name:'球镜 S',unit:'D'}, PED_HYPEROPIA_RESERVE:{name:'远视储备',unit:'D'},
  BIO_AST:{name:'角膜散光',unit:'D'}, VA_UCVA:{name:'裸眼视力',unit:''}, IOP_NCT:{name:'眼压',unit:'mmHg'}
};
let activeSeries='BIO_AL';
function renderSeriesBtns(){
  const s=summary; if(!s) return;
  const avail=Object.keys(SERIES_META).filter(k=>s.series[k+'_OD']||s.series[k+'_OS']);
  if(!avail.length){ $('chartCard').style.display='none'; return; }
  $('chartCard').style.display='block';
  if(!avail.includes(activeSeries)) activeSeries=avail[0];
  $('seriesBtns').innerHTML=avail.map(k=>`<button class="${k===activeSeries?'btn-p':'btn-o'}" onclick="activeSeries='${k}';renderSeriesBtns();renderChart()">${SERIES_META[k].name}</button>`).join('');
}
function renderChart(){
  const s=summary; if(!s||$('chartCard').style.display==='none') return;
  if(!window.echarts){ return; }
  if(!chartIns) chartIns=echarts.init($('chart'));
  const meta=SERIES_META[activeSeries];
  const od=s.series[activeSeries+'_OD']||[], os=s.series[activeSeries+'_OS']||[];
  const dates=[...new Set([...od,...os].map(p=>p.date))].sort();
  const mk=arr=>dates.map(d=>{const p=arr.find(x=>x.date===d);return p?p.value:null});
  chartIns.setOption({
    title:{text:`${meta.name} 进展${meta.unit?'（'+meta.unit+'）':''}`,left:'center',textStyle:{fontSize:14}},
    tooltip:{trigger:'axis'},legend:{data:['右眼 OD','左眼 OS'],top:28},
    grid:{left:50,right:24,top:70,bottom:46},
    xAxis:{type:'category',data:dates,axisLabel:{rotate:dates.length>6?30:0,fontSize:10}},
    yAxis:{type:'value',scale:true,name:meta.unit,splitLine:{lineStyle:{color:'#eef1f5'}}},
    series:[
      {name:'右眼 OD',type:'line',data:mk(od),smooth:true,symbolSize:8,lineStyle:{width:3},itemStyle:{color:'#1e40af'}},
      {name:'左眼 OS',type:'line',data:mk(os),smooth:true,symbolSize:8,lineStyle:{width:3,type:'dashed'},itemStyle:{color:'#0f766e'}}
    ]
  },true);
  chartIns.resize();
}
window.addEventListener('resize',()=>chartIns?.resize());

/* ---------- 原值明细 ---------- */
function renderRaw(){
  const reports=summary.raw_reports||[];
  if(!reports.length){ $('rawDetails').style.display='none'; return; }
  $('rawDetails').style.display='block';
  const f2=v=>v==null?'—':Number(v).toFixed(2);
  let h='';
  reports.forEach(rp=>{
    const rm=rp.raw_measurements||{}, ref=rm.REF||{}, krt=rm.KRT||{}, pd=rm.pd;
    let body='';
    if(ref.OD||ref.OS){
      body+=`<div class="rv-sec-t">屈光 REF · 每次测量 S / C / A（正柱镜记法）</div>`;
      ['OD','OS'].forEach(side=>{
        const e=ref[side]; if(!e) return;
        const rows=(e.measurements||[]).map((m,i)=>`<tr><td class="sub">${i+1}</td><td>${m.s>=0?'+':''}${f2(m.s)}</td><td>${m.c>=0?'+':''}${f2(m.c)}</td><td>${m.a!=null?m.a:'—'}</td></tr>`).join('');
        body+=`<div class="rv-eye"><b>${side==='OD'?'右眼 OD':'左眼 OS'}</b>
          <table class="rv-tbl"><thead><tr><th>#</th><th>球镜 S</th><th>柱镜 C</th><th>轴位 A</th></tr></thead><tbody>${rows||'<tr><td colspan="4" class="sub">—</td></tr>'}</tbody></table>
          <div class="rv-sum">S.E. <b>${e.se!=null?(e.se>=0?'+':'')+f2(e.se)+' D':'—'}</b>${e.representative?` · 代表值 S ${(e.representative.s>=0?'+':'')+f2(e.representative.s)} D`:''}</div></div>`;
      });
    }
    if(krt.OD||krt.OS){
      body+=`<div class="rv-sec-t">角膜曲率 KRT · R1 / R2 / AVE / CYL</div><table class="rv-tbl"><thead><tr><th>眼别</th><th>R1</th><th>R2</th><th>AVE</th><th>CYL</th></tr></thead><tbody>`;
      ['OD','OS'].forEach(side=>{const k=krt[side]; if(!k) return;
        body+=`<tr><td>${side==='OD'?'右 OD':'左 OS'}</td><td>${f2(k.r1)}</td><td>${f2(k.r2)}</td><td>${f2(k.ave)}</td><td>${k.cyl!=null?'+'+f2(k.cyl):'—'}</td></tr>`;});
      body+='</tbody></table>';
    }
    if(pd!=null) body+=`<div class="rv-sum">瞳距 PD：<b>${f2(pd)} mm</b></div>`;
    if(!body) body='<div class="sub">该文件无可展示的原始数值</div>';
    const enc=(summary.encounters||[]).find(e=>e.id===rp.encounter_id);
    h+=`<div class="rv-report"><div class="rv-head">📄 ${esc(rp.filename)} <span class="sub">${esc(enc?enc.exam_date:'')}</span></div>${body}</div>`;
  });
  $('rawBox').innerHTML=h;
}

/* ---------- 历次检查：以日期为准，同日多份报告合并为一个批次 ---------- */
function renderTimeline(){
  const batches = buildBatches();
  if(!batches.length){ $('tlDetails').style.display='none'; return; }
  $('tlDetails').style.display='block';
  $('encCount').textContent = batches.length;
  $('tlList').innerHTML = [...batches].reverse().map(b=>{
    const cnt = Object.keys(batchValues(b)).length;
    return `<div class="tl-item ${b.cyclo?'cyc':''}" style="cursor:pointer" onclick="openBatch('${esc(b.date)}')"><div class="tl-head"><div><span class="tl-date">${esc(b.date)}</span>
      <span class="tl-meta">${b.age!=null?fv(b.age,1)+'岁':''} · ${esc(b.institution)}</span>
      ${b.cyclo?'<span class="pill cyc">散瞳</span>':''}</div><span class="sub">${esc(b.type)} →</span></div>
      <div class="tl-meta">${cnt} 项指标 · ${b.reportCount} 份报告 · 点击查看当日汇总与原始报告</div></div>`;
  }).join('');
}

/* ---------- 日期批次详情 + 获取原始报告（与小程序一致） ---------- */
function openBatch(date){
  const b = buildBatches().find(x=>x.date===date);
  if(!b) return;
  const vals = batchValues(b);
  $('batchTitle').textContent = date + ' · 当日合并汇总';
  let h = `<div class="batch-banner"><b>${esc(b.date)}</b> · ${esc(b.type)} · ${esc(b.institution)}${b.age!=null?' · '+fv(b.age,1)+'岁':''}`+
          `${b.cyclo?'<span class="pill cyc">散瞳</span>':''}<br>按日期合并：${b.reportCount} 份报告 · ${b.ids.length} 次上传 · ${Object.keys(vals).length} 项指标</div>`;
  h += '<div class="batch-sec"><h4>指标汇总（当日合并）</h4><table class="batch-metrics"><thead><tr><th>指标</th><th class="od">右眼 OD</th><th class="os">左眼 OS</th></tr></thead><tbody>';
  let any = false;
  KP_ORDER.forEach(code=>{
    const r = vals[code];
    if(!r || (r.OD==null && r.OS==null)) return;
    any = true;
    const cell = side=>{
      const v = r[side];
      const shared = (side==='OS' && r.OD!=null && r.OS==null);
      const txt = v!=null ? fmtVal(v,r.unit) : (shared?'同右眼':'—');
      const dot = isWarn(r[side+'Flag']) ? '<span class="warn-dot">!</span>' : '';
      return `<td class="${v!=null?side.toLowerCase():''}">${txt}${dot}</td>`;
    };
    h += `<tr><td>${esc(r.name)}${r.unit?` <span class="sub">${esc(r.unit)}</span>`:''}</td>${cell('OD')}${cell('OS')}</tr>`;
  });
  if(!any) h += '<tr><td colspan="3" class="sub">该日期未识别到关键指标</td></tr>';
  h += '</tbody></table><div class="hint">数值为报告记录，仅供随访参考，请以纸质原件为准。</div></div>';

  h += `<div class="batch-sec"><h4>原始报告 · ${b.docs.length} 份</h4>`;
  if(b.docs.length){
    h += b.docs.map(d=>`<div class="rep-row"><span>${d.file_type==='pdf'?'📕':'🖼️'} ${esc(d.filename)} <span class="sub">${esc(d.doc_category||'')}</span></span><a href="/api/file/${d.id}" target="_blank" class="tag blue">查看原件</a></div>`).join('');
    h += `<button class="btn-p" style="margin-top:10px" onclick="getReportsForDate('${esc(b.date)}')">获取原始报告（合并为图片 / PDF）</button>`;
    h += '<div class="hint">同日多份图片会合并为一张长图供预览下载；PDF 原件逐个打开。</div>';
  }else{
    h += '<div class="sub">该日期暂无原件记录；上传报告后即可在此获取。</div>';
  }
  h += '</div>';
  $('batchBody').innerHTML = h;
  $('batchModal').classList.add('on');
}

// 同日图片合并为一张长图（浏览器端完成，不上传、不外发）；PDF 逐个打开
async function getReportsForDate(date){
  const b = buildBatches().find(x=>x.date===date);
  let docs = b ? b.docs : [];
  if(!docs.length) docs = allDocs.filter(d=>(d.exam_date||'未注明日期')===date);
  if(!docs.length){ alert('该日期暂无原始报告'); return; }
  const imgs = docs.filter(d=>d.file_type!=='pdf');
  const pdfs = docs.filter(d=>d.file_type==='pdf');
  pdfs.forEach(d=>window.open('/api/file/'+d.id,'_blank')); // 用户点击链路内先打开，避免被拦截
  try{
    if(imgs.length){
      const url = await mergeImagesToDataURL(imgs);
      $('imgTitle').textContent = date+' · 原始报告合并图（'+imgs.length+' 张）';
      $('imgPreview').src = url;
      $('imgDownload').href = url;
      $('imgDownload').setAttribute('download','原始报告_'+date+'.png');
      $('imgModal').classList.add('on');
    }
    if(!imgs.length && !pdfs.length) alert('该日期暂无可获取的原件');
  }catch(e){ alert('整理原始报告失败：'+(e.message||e)); }
}

async function mergeImagesToDataURL(imgs){
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  const pad = 24, titleH = 44, maxImgW = 1200;
  const loaded = [];
  let maxW = 760;
  for(const d of imgs){
    const img = new Image();
    img.src = '/api/file/'+d.id;
    await new Promise((res,rej)=>{
      img.onload = res;
      img.onerror = ()=>rej(new Error('图片加载失败：'+(d.filename||d.id)));
    });
    const w = Math.min(img.naturalWidth, maxImgW);
    const h = Math.round(img.naturalHeight*w/img.naturalWidth);
    loaded.push({img:img, d:d, w:w, h:h});
    maxW = Math.max(maxW, w);
  }
  canvas.width = maxW + pad*2;
  canvas.height = pad + loaded.reduce((s,x)=>s+titleH+x.h+pad, 0);
  ctx.fillStyle = '#fff'; ctx.fillRect(0,0,canvas.width,canvas.height);
  let y = pad;
  ctx.font = 'bold 17px -apple-system, "Microsoft YaHei", sans-serif';
  loaded.forEach(x=>{
    ctx.fillStyle = '#1e40af';
    ctx.fillText(x.d.filename||'报告', pad, y+22);
    y += titleH;
    ctx.drawImage(x.img, pad, y, x.w, x.h);
    y += x.h + pad;
  });
  return canvas.toDataURL('image/png');
}

/* ---------- 来源报告：按日期归集，保留「获取原始报告」入口 ---------- */
function renderSource(){
  if(!currentPid || !allDocs.length){ $('srcDetails').style.display='none'; return; }
  $('srcDetails').style.display='block';
  const byDate = {};
  allDocs.forEach(d=>{
    const k = d.exam_date || '未注明日期';
    (byDate[k] = byDate[k] || []).push(d);
  });
  const dates = Object.keys(byDate).sort().reverse();
  $('srcBox').innerHTML = dates.map(k=>{
    const list = byDate[k];
    const pdfs = list.filter(d=>d.file_type==='pdf').length;
    return `<div class="src-day"><div class="rep-row"><b>${esc(k)} · ${list.length} 份${pdfs?'（含 '+pdfs+' 份 PDF）':''}</b>`+
      `<button class="rv-toggle" onclick="getReportsForDate('${esc(k)}')">获取原始报告</button></div>`+
      list.map(d=>`<div class="doc-item"><span>${d.file_type==='pdf'?'📕':'🖼️'} ${esc(d.filename)} <span class="sub">${esc(d.doc_category||'')}</span></span>
        <a href="javascript:void(0)" onclick="openDocPreview(${d.id},'${esc(d.filename)}','${esc(d.file_type)}')" class="tag blue">查看原件</a></div>`).join('')+
      `</div>`;
  }).join('');
}

function openDocPreview(id, filename, fileType){
  const url='/api/file/'+id;
  $('docPrevTitle').textContent=filename||'原始报告'; $('docPrevOpen').href=url;
  const body=$('docPrevBody');
  if(fileType==='pdf') body.innerHTML=`<iframe src="${url}" title="${esc(filename||'报告')}"></iframe>`;
  else body.innerHTML=`<img src="${url}" alt="${esc(filename||'报告')}" onerror="this.parentElement.innerHTML='<div class=\\'empty\\'>图片加载失败，请点右上角「新标签打开」</div>'">`;
  $('docModal').classList.add('on');
}

/* ---------- 就诊出示 ---------- */
function openShareModal(){
  if(!currentPid){ alert('请先选择档案'); return; }
  $('shareResult').innerHTML=''; $('shareModal').classList.add('on');
}
async function createShare(){
  const r=await api('/api/share',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({patient_id:currentPid,hours:+$('shareHours').value})});
  const url=location.origin+'/share/'+r.token;
  $('shareResult').innerHTML=`<div class="alert ok">✓ 链接已生成（有效期至 ${esc(r.expires_at)}）</div>
    <input type="text" readonly value="${url}" id="shareUrl" style="width:100%;margin:6px 0">
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn-g" onclick="copyShare()">复制链接</button>
      <a class="btn-p" href="${url}" target="_blank" style="text-decoration:none;display:inline-block;padding:8px 15px">打开医生视图</a>
      <button class="btn-d" onclick="revokeShare('${r.token}')">撤销</button></div>`;
}
function copyShare(){const el=$('shareUrl');el.select();document.execCommand('copy');alert('已复制')}
async function revokeShare(t){await api('/api/share/'+t,{method:'DELETE'});$('shareResult').innerHTML='<div class="alert warn">链接已撤销</div>';}

init();
