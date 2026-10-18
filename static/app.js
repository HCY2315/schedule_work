'use strict';

/* ---------------- 基础工具 ---------------- */
function $(sel) { return document.querySelector(sel); }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function pad2(n) { return String(n).padStart(2, '0'); }
function today() {
  const d = new Date();
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}
function currentMonth() {
  const d = new Date();
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1);
}

async function api(method, url, body) {
  const opt = { method: method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opt.body = JSON.stringify(body);
  const r = await fetch(url, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* ignore */ }
  if (!r.ok || data.error) throw new Error(data.error || ('请求失败 HTTP ' + r.status));
  return data;
}
const get = function (u) { return api('GET', u); };
const post = function (u, b) { return api('POST', u, b); };
const put = function (u, b) { return api('PUT', u, b); };
const del = function (u) { return api('DELETE', u); };

let toastTimer = null;
function toast(msg, isErr) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = isErr ? 'show err' : 'show';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { t.className = ''; }, 2800);
}

function openModal(title, inner, opts) {
  opts = opts || {};
  const root = $('#modal-root');
  root.innerHTML =
    '<div class="overlay" onclick="if(event.target===this)closeModal()">' +
    '<div class="modal' + (opts.wide ? ' wide' : '') + '">' +
    '<div class="m-head"><h3>' + esc(title) + '</h3>' +
    '<button class="m-close" onclick="closeModal()">×</button></div>' +
    '<div class="m-body"></div></div></div>';
  const body = root.querySelector('.m-body');
  if (typeof inner === 'string') body.innerHTML = inner;
  else body.appendChild(inner);
  if (opts.onMount) opts.onMount(body);
}
function closeModal() { $('#modal-root').innerHTML = ''; }

/* ---------------- 状态 ---------------- */
const state = {
  meta: { permissions: [], categories: [] },
  rosters: [],
  tasks: [],
  persons: [],
  showDisabled: false,
  activeRosterId: null,
  scheduleTaskId: null,
  scheduleData: null,
  scheduleMsg: null,
  editTask: null,
  month: currentMonth(),
  attendanceDate: today(),
  statsYear: new Date().getFullYear(),
  statsMonth: 0,
  attClosed: {},
  statsClosed: {},
  statsGroups: null,
  statsKinds: null,
  statsLabelCols: 0,
  statsRendered: {},
};

async function loadMeta() {
  state.meta = await get('/api/meta');
  const s = (state.meta && state.meta.settings) || {};
  state.showDisabled = (s.show_disabled || '0').toString().toLowerCase() === '1';
}

function attKindList() { return (state.meta && state.meta.att_kinds) || []; }
function kindMeta(kind) {
  const list = attKindList();
  return list.find(function (x) { return x.kind === kind; }) || { kind: kind, label: kind, weight: 0, unavailable: true };
}
function kindLabel(kind) { return kindMeta(kind).label; }
function kindWeight(kind) { return kindMeta(kind).weight; }
function fmtNum(n) { return String(Number(n)); }
function weightHint() {
  return attKindList().map(function (x) { return x.label + '×' + fmtNum(x.weight); }).join(' + ');
}
function absentLabels() {
  return attKindList().filter(function (x) { return x.unavailable; }).map(function (x) { return x.label; }).join('/');
}
function isAbsentKind(kind) {
  const m = attKindList().find(function (x) { return x.kind === kind; });
  return !!(m && m.unavailable);
}
function kindTagClass(kind) { return kindWeight(kind) === 0 ? 'red' : 'orange'; }
async function loadRosters() {
  state.rosters = await get('/api/rosters');
  if (!state.activeRosterId || !state.rosters.some(function (r) { return r.id === state.activeRosterId; })) {
    state.activeRosterId = state.rosters.length ? state.rosters[0].id : null;
  }
}
async function loadTasks() { state.tasks = await get('/api/tasks'); }

function permName(id) {
  if (id == null) return '';
  const p = state.meta.permissions.find(function (x) { return x.id === id; });
  return p ? p.name : '';
}
function catName(id) {
  if (id == null) return '';
  const c = state.meta.categories.find(function (x) { return x.id === id; });
  return c ? c.name : '';
}

/* ---------------- 导航 ---------------- */
let currentTab = 'tasks';
function renderTab(name) {
  currentTab = name;
  document.querySelectorAll('#nav button').forEach(function (b) {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  document.querySelectorAll('.tab').forEach(function (t) {
    t.classList.toggle('active', t.id === 'tab-' + name);
  });
  if (name === 'tasks') renderTasks();
  else if (name === 'roster') renderRoster();
  else if (name === 'perms') renderPerms();
  else if (name === 'attendance') renderAttendance();
  else if (name === 'stats') renderStats();
  else if (name === 'settings') renderSettings();
}

/* ================= 排班任务 ================= */
function renderTasks() {
  if (state.editTask) { renderTaskEditor(); return; }
  if (state.scheduleTaskId) { renderSchedule(); return; }
  const el = $('#tab-tasks');
  let h = '<div class="toolbar"><h3>排班任务</h3><span class="muted">每个任务：独立名单 + 12个可自定义时段 + 各时段岗位与权限</span>' +
    '<span class="spacer"></span><button class="btn primary" onclick="newTask()">+ 新建任务</button></div>';
  if (!state.tasks.length) {
    h += '<div class="card muted">还没有排班任务。点击「新建任务」创建：选择名单、配置12个时段和各时段岗位，然后生成排班。</div>';
  }
  state.tasks.forEach(function (t) {
    h += '<div class="task-card"><div style="flex:1">' +
      '<div class="tname">' + esc(t.name) + '</div>' +
      '<div class="tmeta">名单：' + esc(t.roster_name) + '（' + t.person_count + '人） · 每天 ' + t.slot_count + ' 个班次</div>' +
      '</div>' +
      '<button class="btn primary" onclick="openSchedule(' + t.id + ')">排班表</button>' +
      '<button class="btn" onclick="editTask(' + t.id + ')">编辑</button>' +
      '<button class="btn danger" onclick="deleteTask(' + t.id + ')">删除</button></div>';
  });
  el.innerHTML = h;
}

function defaultSlots() {
  const a = [];
  for (let i = 0; i < 12; i++) {
    a.push({ start: pad2(i * 2) + ':00', end: pad2(i * 2 + 2) + ':00', positions: [{ name: '岗位1', perm_id: null, perm_ids: [] }] });
  }
  return a;
}

function newTask() {
  state.editTask = {
    id: null,
    name: '',
    roster_id: state.rosters.length ? state.rosters[0].id : null,
    slots: defaultSlots(),
  };
  renderTasks();
}
async function editTask(id) {
  try {
    const t = await get('/api/tasks/' + id);
    state.editTask = { id: t.id, name: t.name, roster_id: t.roster_id, slots: t.slots };
    renderTasks();
  } catch (e) { toast(e.message, true); }
}
async function deleteTask(id) {
  const t = state.tasks.find(function (x) { return x.id === id; });
  if (!confirm('确定删除任务「' + (t ? t.name : '') + '」及其全部排班数据？')) return;
  try {
    await del('/api/tasks/' + id);
    if (state.scheduleTaskId === id) { state.scheduleTaskId = null; state.scheduleData = null; }
    await loadTasks();
    renderTasks();
    toast('已删除');
  } catch (e) { toast(e.message, true); }
}

function posLineHTML(i, p, j) {
  const pset = p.perm_ids || (p.perm_id != null ? [p.perm_id] : []);
  const tags = pset.length
    ? pset.map(function (id) { return '<span class="tag">' + esc(permName(id)) + '</span>'; }).join('')
    : '<span class="muted">不限</span>';
  return '<div class="pos-line">' +
    '<span class="muted" style="width:52px">岗位' + (j + 1) + '</span>' +
    '<input type="text" class="pname" placeholder="岗位名称" value="' + esc(p.name) + '" onchange="setPos(' + i + ',' + j + ',\'name\',this.value)">' +
    '<span class="muted">需权限：</span><span class="pos-perm-tags">' + tags + '</span>' +
    '<button class="btn small" onclick="openPosPermPicker(' + i + ',' + j + ')">+ 权限</button>' +
    '<button class="btn small danger" onclick="delPos(' + i + ',' + j + ')">删岗位</button>' +
    '</div>';
}
function openPosPermPicker(i, j) {
  const pos = state.editTask.slots[i].positions[j];
  const cur = new Set(pos.perm_ids || (pos.perm_id != null ? [pos.perm_id] : []));
  let h = '<div class="muted" style="margin-bottom:8px">勾选该岗位需要的权限（人员需<b>全部满足</b>才能胜任）：</div><div class="perm-box">';
  state.meta.permissions.forEach(function (pm) {
    h += '<label class="ck"><input type="checkbox" value="' + pm.id + '"' + (cur.has(pm.id) ? ' checked' : '') + '>' + esc(pm.name) + '</label>';
  });
  if (!state.meta.permissions.length) h += '<span class="muted">还没有权限，请先到「权限类别」添加</span>';
  h += '</div><div class="m-foot"><button class="btn" onclick="closeModal()">取消</button>' +
    '<button class="btn primary" onclick="applyPosPerms(' + i + ',' + j + ')">确定</button></div>';
  openModal('岗位「' + pos.name + '」所需权限', h);
}
function applyPosPerms(i, j) {
  const pos = state.editTask.slots[i].positions[j];
  const ids = Array.from(document.querySelectorAll('#modal-root .perm-box input:checked')).map(function (cb) { return Number(cb.value); });
  pos.perm_ids = ids.sort(function (a, b) { return a - b; });
  pos.perm_id = pos.perm_ids.length ? pos.perm_ids[0] : null;
  closeModal();
  renderTasks();
}
function slotRowHTML(s, i) {
  let h = '<div class="slot-editor"><div class="s-head">' +
    '<b>时段 ' + (i + 1) + '</b>' +
    '<input type="text" size="6" value="' + esc(s.start) + '" onchange="setSlot(' + i + ',\'start\',this.value)">' +
    '<span class="muted">至</span>' +
    '<input type="text" size="6" value="' + esc(s.end) + '" onchange="setSlot(' + i + ',\'end\',this.value)">' +
    '<span class="muted" style="margin-left:8px">岗位数：</span>' +
    '<button class="btn small" onclick="setPosCount(' + i + ',1)">1人</button>' +
    '<button class="btn small" onclick="setPosCount(' + i + ',2)">2人</button>' +
    '<button class="btn small" onclick="setPosCount(' + i + ',3)">3人</button>' +
    '<span class="spacer"></span>' +
    '<button class="btn small" onclick="addSlot(' + i + ')">+插入时段</button>' +
    '<button class="btn small danger" onclick="removeSlot(' + i + ')">删除时段</button>' +
    '</div><div class="s-body">';
  s.positions.forEach(function (p, j) { h += posLineHTML(i, p, j); });
  if (!s.positions.length) h += '<div class="muted">未配置岗位</div>';
  h += '<div><button class="btn small" onclick="addPos(' + i + ')">+ 添加岗位</button></div>';
  h += '</div></div>';
  return h;
}
function renderTaskEditor() {
  const t = state.editTask;
  const isEdit = !!t.id;
  let rosters = '<option value="">请选择名单</option>';
  state.rosters.forEach(function (r) {
    rosters += '<option value="' + r.id + '"' + (r.id === t.roster_id ? ' selected' : '') + '>' + esc(r.name) + '（' + r.count + '人）</option>';
  });
  let h = '<div class="toolbar"><button class="btn" onclick="cancelEditTask()">← 返回</button>' +
    '<h3>' + (isEdit ? '编辑任务' : '新建任务') + '</h3></div>' +
    '<div class="card"><div class="form-row"><label>任务名称 *</label>' +
    '<input type="text" id="tf-name" value="' + esc(t.name) + '" placeholder="如：白班岗位排班" oninput="state.editTask.name=this.value">' +
    '<label>名单</label><select id="tf-roster" onchange="state.editTask.roster_id=Number(this.value)">' + rosters + '</select>' +
    '</div><div class="muted">每个班次时段可自定义时间，点击 1人/2人/3人 快速设置该班次的岗位数；每个岗位可单独指定所需权限。</div></div>';
  h += '<div class="toolbar"><h3>班次时段（共 ' + t.slots.length + ' 个）</h3><span class="spacer"></span>' +
    '<button class="btn" onclick="restoreDefaultSlots()">恢复默认12时段</button>' +
    '<button class="btn" onclick="addSlot(-1)">+ 添加时段</button></div>';
  t.slots.forEach(function (s, i) { h += slotRowHTML(s, i); });
  h += '<div class="toolbar" style="margin-top:14px"><span class="spacer"></span>' +
    '<button class="btn" onclick="cancelEditTask()">取消</button>' +
    '<button class="btn primary" onclick="saveTask()">保存任务</button></div>';
  $('#tab-tasks').innerHTML = h;
}
function cancelEditTask() { state.editTask = null; renderTasks(); }
function restoreDefaultSlots() {
  if (!confirm('将时段恢复为默认的12个（00:00-02:00 ... 22:00-24:00）？保存后该任务已生成的排班会一并清除。')) return;
  state.editTask.slots = defaultSlots();
  renderTasks();
}
function setSlot(i, field, v) { state.editTask.slots[i][field] = v.trim(); }
function setPos(i, j, field, v) { state.editTask.slots[i].positions[j][field] = v; }
function setPosCount(i, n) {
  const s = state.editTask.slots[i];
  while (s.positions.length > n) s.positions.pop();
  while (s.positions.length < n) s.positions.push({ name: '岗位' + (s.positions.length + 1), perm_id: null, perm_ids: [] });
  renderTasks();
}
function addPos(i) {
  const s = state.editTask.slots[i];
  s.positions.push({ name: '岗位' + (s.positions.length + 1), perm_id: null, perm_ids: [] });
  renderTasks();
}
function delPos(i, j) {
  state.editTask.slots[i].positions.splice(j, 1);
  renderTasks();
}
function addSlot(i) {
  state.editTask.slots.splice(i + 1, 0, { start: '00:00', end: '01:00', positions: [{ name: '岗位1', perm_id: null, perm_ids: [] }] });
  renderTasks();
}
function removeSlot(i) {
  if (state.editTask.slots.length <= 1) { toast('至少保留一个时段', true); return; }
  const msg = state.editTask.id
    ? '删除该时段？保存后该时段已生成的排班也会一并删除。'
    : '删除该时段？';
  if (!confirm(msg)) return;
  state.editTask.slots.splice(i, 1);
  renderTasks();
}
async function saveTask() {
  const t = state.editTask;
  if (!t) return;
  if (!t.name || !t.name.trim()) { toast('请输入任务名称', true); return; }
  if (!t.roster_id) { toast('请选择名单', true); return; }
  t.slots.forEach(function (s) {
    s.positions.forEach(function (p, j) { if (!p.name || !p.name.trim()) p.name = '岗位' + (j + 1); });
  });
  const body = { name: t.name, roster_id: t.roster_id, slots: t.slots };
  try {
    if (t.id) await put('/api/tasks/' + t.id, body);
    else await post('/api/tasks', body);
    state.editTask = null;
    await loadTasks();
    renderTasks();
    toast('任务已保存');
  } catch (e) { toast(e.message, true); }
}

/* ---------------- 排班表 ---------------- */
function openSchedule(id) {
  state.scheduleTaskId = id;
  state.scheduleData = null;
  state.scheduleMsg = null;
  renderTasks();
}
function closeSchedule() {
  state.scheduleTaskId = null;
  state.scheduleData = null;
  state.scheduleMsg = null;
  renderTasks();
}
function defaultFrom() {
  const t = today();
  return t.slice(0, 7) === state.month ? t : state.month + '-01';
}
function minFrom() {
  return state.month === currentMonth() ? today() : state.month + '-01';
}

function renderSchedule() {
  const tid = state.scheduleTaskId;
  const task = state.tasks.find(function (t) { return t.id === tid; });
  const el = $('#tab-tasks');
  el.innerHTML =
    '<div class="toolbar">' +
    '<button class="btn" onclick="closeSchedule()">← 返回任务列表</button>' +
    '<h3>' + esc(task ? task.name : '') + '</h3>' +
    '<span class="spacer"></span>' +
    '<label>月份</label><input type="month" id="sch-month" value="' + state.month + '" onchange="state.month=this.value;renderTasks()">' +
    '<label class="chk"><input type="checkbox" id="sch-inc"> 从指定日期开始</label>' +
    '<input type="date" id="sch-from" min="' + minFrom() + '" value="' + defaultFrom() + '" style="width:132px">' +
    '<button class="btn primary" onclick="doGenerate()">重新生成排班</button>' +
    (state.month === currentMonth()
      ? '<button class="btn" onclick="doGenerateFromToday()" title="以今天为界，仅重排今天及之后（保留之前不变）">从今天起重排</button>'
      : '') +
    '</div><div id="sched-msg"></div><div class="sched-wrap" id="sched-wrap"><div class="muted" style="padding:14px">加载中…</div></div>';
  loadScheduleGrid();
}
function showScheduleMsg() {
  const box = $('#sched-msg');
  if (!box) return;
  if (!state.scheduleMsg) { box.innerHTML = ''; return; }
  box.innerHTML = '<div class="warn-box">' + esc(state.scheduleMsg).replace(/\n/g, '<br>') + '</div>';
}
async function loadScheduleGrid() {
  const parts = state.month.split('-');
  try {
    const data = await get('/api/tasks/' + state.scheduleTaskId + '/schedule?year=' + Number(parts[0]) + '&month=' + Number(parts[1]));
    state.scheduleData = data;
    const wrap = $('#sched-wrap');
    if (wrap) wrap.innerHTML = gridHTML(data);
    showScheduleMsg();
  } catch (e) {
    const wrap = $('#sched-wrap');
    if (wrap) wrap.innerHTML = '<div class="card error-text">' + esc(e.message) + '</div>';
  }
}
function kindMark(item) {
  if (!item || !item.kind || item.kind === 'normal') return '';
  return '<span class="tag ' + kindTagClass(item.kind) + '">' + esc(item.kind_label || kindLabel(item.kind)) + '</span>';
}
function gridHTML(d) {
  const wd = ['一', '二', '三', '四', '五', '六', '日'];
  const td = today();
  let h = '<table class="sched"><thead><tr><th class="corner">日期＼ 班次</th>';
  d.slots.forEach(function (s) {
    h += '<th>' + esc(s.start) + '-' + esc(s.end) + '</th>';
  });
  h += '</tr></thead><tbody>';
  d.days.forEach(function (day) {
    const isToday = day.date === td;
    h += '<tr><td class="timecol' + (isToday ? ' today' : '') + '">' + day.day + '日 周' + wd[day.weekday] + '</td>';
    d.slots.forEach(function (s) {
      let inner = '';
      s.positions.forEach(function (p) {
        const rows = (d.cells[day.date] || {})[p.id] || [];
        const base = rows.find(function (r) { return r.cover_of == null; });
        const covers = rows.filter(function (r) { return r.cover_of != null; });
        if (!base) {
          inner += '<div class="sched-pos"><span class="pn">' + esc(p.name) + '</span> <span class="sched-empty">—</span></div>';
          return;
        }
        let who;
        if (covers.length) {
          const eff = covers[covers.length - 1];
          who = '<span class="cover-mark">' + esc(eff.name) + '</span> <span class="muted">(代' + esc(base.name) + ')</span>';
        } else {
          who = esc(base.name) + kindMark(base);
        }
        if (covers.length && base.kind && base.kind !== 'normal') who += ' ' + kindMark(base);
        inner += '<div class="sched-pos"><span class="pn">' + esc(p.name) + '</span> ' + who + '</div>';
      });
      h += '<td class="cell' + (isToday ? ' today' : '') + '" onclick="openAssignModal(\'' + day.date + '\',' + s.id + ')">' + (inner || '<span class="sched-empty">—</span>') + '</td>';
    });
    h += '</tr>';
  });
  return h + '</tbody></table>';
}
async function runGenerate(startDate) {
  const parts = state.month.split('-');
  const body = { year: Number(parts[0]), month: Number(parts[1]) };
  if (startDate) body.start_date = startDate;
  const r = await post('/api/tasks/' + state.scheduleTaskId + '/generate', body);
  const head = r.incremental
    ? '已从 ' + startDate + ' 重新生成 ' + r.days + ' 天（保留前 ' + r.kept_days + ' 天不变）'
    : '已生成 ' + r.days + ' 天';
  if (r.warnings && r.warnings.length) {
    state.scheduleMsg = head + '，但以下时段未能排满（人员/权限不足）：\n' + r.warnings.join('\n');
    toast('已生成，存在未排满岗位', true);
  } else {
    state.scheduleMsg = head + '。';
    toast('排班已生成');
  }
  await loadScheduleGrid();
}
async function doGenerate() {
  const parts = state.month.split('-');
  const incEl = $('#sch-inc'), fromEl = $('#sch-from');
  const inc = !!(incEl && incEl.checked);
  const from = (fromEl && fromEl.value) || '';
  if (inc) {
    if (!from || from.slice(0, 7) !== state.month) {
      toast('请选择本月内的「从指定日期开始」日期', true);
      return;
    }
    const kept = (Number(from.slice(8, 10)) || 1) - 1;
    if (!confirm('将只重新生成 ' + from + ' 至月末的排班（该月 1 日 ~ ' +
        String(kept).padStart(2, '0') + ' 日的排班保持不变），按当前名单分配这段排班。继续？')) return;
  } else {
    if (!confirm('将重新生成 ' + Number(parts[0]) + '年' + Number(parts[1]) + '月 的排班，该月已有排班会被覆盖，继续？')) return;
  }
  try {
    await runGenerate(inc ? from : null);
  } catch (e) { toast(e.message, true); }
}
async function doGenerateFromToday() {
  const t = today();
  if (t.slice(0, 7) !== state.month) {
    toast('当前查看的不是本月，「从今天起重排」仅对本月可用', true);
    return;
  }
  const kept = Number(t.slice(8, 10)) - 1;
  if (!confirm('将从今天 ' + t + ' 起重新生成排班（该月 1 日 ~ ' +
      String(kept).padStart(2, '0') + ' 日的排班保持不变），按当前名单分配这段排班。继续？')) return;
  try {
    await runGenerate(t);
  } catch (e) { toast(e.message, true); }
}
async function openAssignModal(date, slotId) {
  const d = state.scheduleData;
  if (!d) return;
  const slot = d.slots.find(function (x) { return x.id === slotId; });
  const task = state.tasks.find(function (t) { return t.id === state.scheduleTaskId; });
  if (!slot || !task) return;
  try {
    const res = await Promise.all([
      get('/api/rosters/' + task.roster_id + '/persons'),
      get('/api/attendance?date=' + date),
    ]);
    const persons = res[0], att = res[1];
    const busy = new Set();
    Object.values(d.cells[date] || {}).forEach(function (rows) {
      rows.forEach(function (r) { busy.add(r.person_id); });
    });
    const absent = new Set();
    att.groups.forEach(function (g) {
      const all = (g.base ? [g.base] : []).concat(g.covers);
      all.forEach(function (it) {
        if (isAbsentKind(it.kind)) absent.add(it.person_id);
      });
    });
    att.extra_records.forEach(function (r) {
      if (r.kind && isAbsentKind(r.kind)) absent.add(r.person_id);
    });

    let h = '<div class="muted" style="margin-bottom:10px">' + date + ' · ' + esc(slot.start) + '-' + esc(slot.end) +
      '　点击下拉更换该岗位人员（✗ 表示不符合条件；「当天已排」的人仍可选择，选中后会自动从原岗位移入本岗位）</div>';
    slot.positions.forEach(function (p) {
      const rows = (d.cells[date] || {})[p.id] || [];
      const base = rows.find(function (r) { return r.cover_of == null; });
      const covers = rows.filter(function (r) { return r.cover_of != null; });
      const currentId = base ? (covers.length ? covers[covers.length - 1].person_id : base.person_id) : null;
      h += '<div class="form-row"><label style="min-width:110px">岗位「' + esc(p.name) + '」</label>';
      h += '<select onchange="doAssign(\'' + date + '\',' + slotId + ',' + p.id + ',this.value)">';
      h += '<option value="">' + (base ? '保持不变' : '（空缺）') + '</option>';
      persons.forEach(function (pp) {
        const flags = [];
        let blocked = false;
        if (absent.has(pp.id)) { flags.push('不在岗'); blocked = true; }
        const need = p.perm_ids || (p.perm_id != null ? [p.perm_id] : []);
        if (need.length && need.some(function (id) { return !(pp.perm_ids || []).includes(id); })) { flags.push('无权限'); blocked = true; }
        if (busy.has(pp.id) && pp.id !== currentId) flags.push('当天已排');
        const dis = blocked ? ' disabled' : '';
        const sel = pp.id === currentId ? ' selected' : '';
        h += '<option value="' + pp.id + '"' + dis + sel + '>' + esc(pp.name) +
          (pp.code ? '（' + esc(pp.code) + '）' : '') + (flags.length ? ' ✗' + flags.join(' ✗') : '') + '</option>';
      });
      h += '</select>';
      if (base) h += '<button class="btn small danger" onclick="doAssign(\'' + date + '\',' + slotId + ',' + p.id + ',\'\')">清空</button>';
      h += '<span class="muted">需权限：' + esc(p.perm_name || '不限') + '</span></div>';
      if (covers.length && base) {
        h += '<div class="muted" style="margin:-2px 0 10px 120px">底班：' + esc(base.name) +
          '，当前顶班：' + esc(covers[covers.length - 1].name) + '（撤销顶班请到「考勤与顶班」）</div>';
      }
    });
    openModal('调整排班', h);
  } catch (e) { toast(e.message, true); }
}
async function doAssign(date, slotId, positionId, val) {
  const personId = val === '' ? null : Number(val);
  try {
    await put('/api/schedule/assign', {
      task_id: state.scheduleTaskId, date: date, position_id: positionId, person_id: personId,
    });
    closeModal();
    await loadScheduleGrid();
    toast('排班已更新');
  } catch (e) {
    toast(e.message, true);
    openAssignModal(date, slotId);
  }
}

/* ================= 人员名单 ================= */
function renderRoster() {
  const el = $('#tab-roster');
  el.innerHTML = '<div class="grid2"><div id="roster-list"></div><div id="roster-right"></div></div>';
  renderRosterList();
  renderPersons();
}
function renderRosterList() {
  let h = '<div class="toolbar"><h3>名单</h3><span class="spacer"></span><button class="btn primary" onclick="newRoster()">+ 新建名单</button></div>';
  if (!state.rosters.length) h += '<div class="card muted">还没有名单，点击「新建名单」。</div>';
  state.rosters.forEach(function (r) {
    h += '<div class="roster-item' + (r.id === state.activeRosterId ? ' active' : '') + '" onclick="selectRoster(' + r.id + ')">' +
      '<span class="rn">' + esc(r.name) + '</span><span class="muted">' + r.count + '人</span>' +
      '<button class="btn small" onclick="event.stopPropagation();renameRoster(' + r.id + ')">改名</button>' +
      '<button class="btn small danger" onclick="event.stopPropagation();removeRoster(' + r.id + ')">删除</button></div>';
  });
  $('#roster-list').innerHTML = h;
}
async function newRoster() {
  const name = prompt('新名单名称（如：运维一班名单）：');
  if (!name || !name.trim()) return;
  try {
    const r = await post('/api/rosters', { name: name.trim() });
    await loadRosters();
    state.activeRosterId = r.id;
    renderRoster();
    toast('名单已创建');
  } catch (e) { toast(e.message, true); }
}
async function renameRoster(id) {
  const r = state.rosters.find(function (x) { return x.id === id; });
  const name = prompt('名单名称：', r ? r.name : '');
  if (!name || !name.trim()) return;
  try {
    await put('/api/rosters/' + id, { name: name.trim() });
    await loadRosters();
    renderRoster();
    toast('已保存');
  } catch (e) { toast(e.message, true); }
}
async function removeRoster(id) {
  const r = state.rosters.find(function (x) { return x.id === id; });
  if (!confirm('确定删除名单「' + (r ? r.name : '') + '」及其全部人员？')) return;
  try {
    await del('/api/rosters/' + id);
    if (state.activeRosterId === id) state.activeRosterId = null;
    await loadRosters();
    renderRoster();
    toast('已删除');
  } catch (e) { toast(e.message, true); }
}
function selectRoster(id) { state.activeRosterId = id; renderRoster(); }

async function renderPersons() {
  const box = $('#roster-right');
  if (!box) return;
  if (!state.activeRosterId) { box.innerHTML = '<div class="card muted">请先创建名单，再录入人员。</div>'; return; }
  box.innerHTML = '<div class="card">加载中…</div>';
  let persons;
  try {
    persons = await get('/api/rosters/' + state.activeRosterId + '/persons');
  } catch (e) {
    box.innerHTML = '<div class="card error-text">' + esc(e.message) + '</div>';
    return;
  }
  state.persons = persons;
  const disabledN = persons.filter(function (x) { return x.status === 'disabled'; }).length;
  const activeN = persons.length - disabledN;
  const rr = state.rosters.find(function (x) { return x.id === state.activeRosterId; });
  let h = '<div class="card"><div class="toolbar"><h3>人员：' + esc(rr ? rr.name : '') +
    '（在用 ' + activeN + ' 人' + (disabledN ? '，停用 ' + disabledN + ' 人' : '') + '）</h3>' +
    '<span class="spacer"></span>' +
    '<label class="chk"><input type="checkbox" ' + (state.showDisabled ? 'checked' : '') +
    ' onchange="toggleShowDisabled(this.checked)">显示已停用</label>' +
    '<button class="btn primary" onclick="openPersonModal(null)">+ 新增人员</button></div>';
  if (!persons.length) {
    h += '<div class="muted">还没有人员。点击「新增人员」录入姓名、类别与权限。</div>';
  } else {
    h += '<table class="list"><thead><tr><th>姓名</th><th>工号</th><th>电话</th><th>类别</th><th>权限</th><th>偏好权限</th><th>备注</th><th>操作</th></tr></thead><tbody>';
    persons.forEach(function (p) {
      const dis = p.status === 'disabled';
      h += '<tr' + (dis ? ' class="row-disabled"' : '') + '><td><b>' + esc(p.name) + '</b>' +
        (dis ? ' <span class="tag orange">已停用</span>' : '') + '</td><td>' + esc(p.code) + '</td><td>' + esc(p.phone) + '</td>' +
        '<td>' + esc(catName(p.category_id)) + '</td><td>' +
        (p.perm_ids || []).map(function (id) { return '<span class="tag">' + esc(permName(id)) + '</span>'; }).join('') +
        ((p.perm_ids || []).length ? '' : '<span class="muted">无</span>') +
        '</td><td>' + (esc(permName(p.preferred_perm_id)) || '<span class="muted">无</span>') + '</td>' +
        '<td>' + esc(p.remark) + '</td><td class="act">' +
        (dis
          ? '<button class="btn small" onclick="restorePerson(' + p.id + ')">恢复</button>'
          : '<button class="btn small" onclick="openPersonModal(' + p.id + ')">编辑</button> ' +
            '<button class="btn small danger" onclick="removePerson(' + p.id + ')">停用</button>') +
        '</td></tr>';
    });
    h += '</tbody></table>';
  }
  h += '</div>';
  box.innerHTML = h;
}
async function toggleShowDisabled(on) {
  state.showDisabled = on;
  try {
    await post('/api/settings', { show_disabled: on ? '1' : '0' });
  } catch (e) {
    toast('保存设置失败：' + e.message, true);
    return;
  }
  await renderPersons();
}
function openPersonModal(pid) {
  const p = pid ? state.persons.find(function (x) { return x.id === pid; }) : null;
  const catId = p ? p.category_id : null;
  const permSet = new Set(p ? (p.perm_ids || []) : []);
  let h = '<input type="hidden" id="pf-id" value="' + (p ? p.id : '') + '">' +
    '<div class="form-row"><label>姓名 *</label><input type="text" id="pf-name" value="' + esc(p ? p.name : '') + '"></div>' +
    '<div class="form-row"><label>工号</label><input type="text" id="pf-code" value="' + esc(p ? p.code : '') + '">' +
    '<label>电话</label><input type="text" id="pf-phone" value="' + esc(p ? p.phone : '') + '"></div>' +
    '<div class="form-row"><label>类别</label><select id="pf-cat" onchange="onCatChange()"><option value="">无</option>';
  state.meta.categories.forEach(function (c) {
    h += '<option value="' + c.id + '"' + (c.id === catId ? ' selected' : '') + '>' + esc(c.name) + '</option>';
  });
  h += '</select><span class="muted">选择类别自动带出默认权限，可再手动勾选调整</span></div>' +
    '<div class="form-row"><label>权限</label><div class="perm-box" id="pf-perms">';
  state.meta.permissions.forEach(function (pm) {
    h += '<label class="ck"><input type="checkbox" value="' + pm.id + '"' + (permSet.has(pm.id) ? ' checked' : '') + '>' + esc(pm.name) + '</label>';
  });
  h += '</div></div><div class="form-row"><label>偏好权限</label><select id="pf-pref"><option value="">无</option>';
  state.meta.permissions.forEach(function (pm) {
    h += '<option value="' + pm.id + '"' + (p && p.preferred_perm_id === pm.id ? ' selected' : '') + '>' + esc(pm.name) + '</option>';
  });
  h += '</select><span class="muted">自动排班时优先安排与其偏好相符的岗位</span></div>' +
    '<div class="form-row"><label>备注</label><input type="text" id="pf-remark" style="min-width:320px" value="' + esc(p ? p.remark : '') + '"></div>' +
    '<div class="m-foot"><button class="btn" onclick="closeModal()">取消</button>' +
    '<button class="btn primary" onclick="savePerson()">保存</button></div>';
  openModal(p ? '编辑人员' : '新增人员', h);
}
function onCatChange() {
  const cid = Number($('#pf-cat').value) || null;
  const c = state.meta.categories.find(function (x) { return x.id === cid; });
  if (!c) return;
  document.querySelectorAll('#pf-perms input').forEach(function (cb) {
    cb.checked = (c.perm_ids || []).includes(Number(cb.value));
  });
  if (c.preferred_perm_id) $('#pf-pref').value = String(c.preferred_perm_id);
}
async function savePerson() {
  const id = $('#pf-id').value;
  const body = {
    roster_id: state.activeRosterId,
    name: $('#pf-name').value,
    code: $('#pf-code').value,
    phone: $('#pf-phone').value,
    category_id: Number($('#pf-cat').value) || null,
    perm_ids: Array.from(document.querySelectorAll('#pf-perms input:checked')).map(function (cb) { return Number(cb.value); }),
    preferred_perm_id: Number($('#pf-pref').value) || null,
    remark: $('#pf-remark').value,
  };
  try {
    if (id) await put('/api/persons/' + id, body);
    else await post('/api/persons', body);
    closeModal();
    toast('已保存');
    await loadRosters();
    renderRoster();
  } catch (e) { toast(e.message, true); }
}
async function removePerson(id) {
  const p = state.persons.find(function (x) { return x.id === id; });
  if (!confirm('确定停用人员「' + (p ? p.name : '') + '」？\n' +
    '停用后不会再被排班，但历史排班与考勤记录保留；可随时恢复。')) return;
  try {
    await del('/api/persons/' + id);
    await loadRosters();
    renderRoster();
    toast('已停用');
  } catch (e) { toast(e.message, true); }
}

async function restorePerson(id) {
  const p = state.persons.find(function (x) { return x.id === id; });
  try {
    await post('/api/persons/' + id + '/restore', {});
    await loadRosters();
    renderRoster();
    toast('已恢复「' + (p ? p.name : '') + '」');
  } catch (e) { toast(e.message, true); }
}

/* ================= 权限类别 ================= */
function renderPerms() {
  const el = $('#tab-perms');
  let h = '<div class="grid2b">';
  h += '<div class="card"><h3>权限管理</h3>' +
    '<div class="muted" style="margin-bottom:10px">权限名称可自定义、数量可自行添加；人员勾选权限、岗位指定所需权限都从这里取。</div>';
  state.meta.permissions.forEach(function (p) {
    h += '<div class="form-row"><input type="text" id="pm-' + p.id + '" value="' + esc(p.name) + '" style="min-width:150px">' +
      '<button class="btn small" onclick="renamePerm(' + p.id + ')">保存</button>' +
      '<button class="btn small danger" onclick="removePerm(' + p.id + ')">删除</button></div>';
  });
  h += '<div class="form-row" style="border-top:1px dashed #e1e6ec;padding-top:10px">' +
    '<input type="text" id="new-perm" placeholder="新权限名称" style="min-width:150px">' +
    '<button class="btn" onclick="addPerm()">+ 添加权限</button></div></div>';

  h += '<div class="card"><h3>人员类别与默认权限</h3>' +
    '<div class="muted" style="margin-bottom:10px">录入人员时选择类别，自动带出默认勾选的权限与偏好（可再手动调整）。</div>';
  state.meta.categories.forEach(function (c) {
    h += '<div class="form-row"><input type="text" id="cat-' + c.id + '" value="' + esc(c.name) + '" style="min-width:80px">';
    state.meta.permissions.forEach(function (pm) {
      h += '<label class="ck"><input type="checkbox" data-cat="' + c.id + '" value="' + pm.id + '"' +
        ((c.perm_ids || []).includes(pm.id) ? ' checked' : '') + '>' + esc(pm.name) + '</label>';
    });
    h += '<label class="ck">偏好 <select id="catpref-' + c.id + '"><option value="">无</option>';
    state.meta.permissions.forEach(function (pm) {
      h += '<option value="' + pm.id + '"' + (c.preferred_perm_id === pm.id ? ' selected' : '') + '>' + esc(pm.name) + '</option>';
    });
    h += '</select></label><button class="btn small" onclick="saveCat(' + c.id + ')">保存</button></div>';
  });
  h += '</div></div>';
  el.innerHTML = h;
}
async function addPerm() {
  const name = $('#new-perm').value.trim();
  if (!name) { toast('请输入权限名称', true); return; }
  try {
    await post('/api/permissions', { name: name });
    await loadMeta(); renderPerms(); toast('已添加');
  } catch (e) { toast(e.message, true); }
}
async function renamePerm(id) {
  const name = $('#pm-' + id).value.trim();
  if (!name) { toast('名称不能为空', true); return; }
  try {
    await put('/api/permissions/' + id, { name: name });
    await loadMeta(); renderPerms(); toast('已保存');
  } catch (e) { toast(e.message, true); }
}
async function removePerm(id) {
  if (!confirm('确定删除该权限？人员与岗位上对它的关联会一并清除。')) return;
  try {
    await del('/api/permissions/' + id);
    await loadMeta(); renderPerms(); toast('已删除');
  } catch (e) { toast(e.message, true); }
}
async function saveCat(id) {
  const body = {
    name: $('#cat-' + id).value.trim(),
    perm_ids: Array.from(document.querySelectorAll('input[data-cat="' + id + '"]:checked')).map(function (cb) { return Number(cb.value); }),
    preferred_perm_id: Number($('#catpref-' + id).value) || null,
  };
  try {
    await put('/api/categories/' + id, body);
    await loadMeta(); renderPerms(); toast('已保存');
  } catch (e) { toast(e.message, true); }
}

/* ================= 设置：考勤类型 ================= */
function nextFreeKind() {
  const used = {};
  attKindList().forEach(function (k) { used[k.kind] = 1; });
  document.querySelectorAll('#kinds-body .k-kind').forEach(function (inp) { if (inp.value.trim()) used[inp.value.trim()] = 1; });
  for (const ch of 'cdefghijklmnopqrstuvwxyz') { if (!used[ch]) return ch; }
  return 'k' + Date.now();
}
function kindRowHTML(k, isNew) {
  const locked = k.kind === 'normal';
  return '<tr>' +
    '<td>' + (isNew
      ? '<input class="k-kind" value="' + esc(k.kind) + '" style="width:70px" placeholder="代码">'
      : '<span class="k-kind-txt">' + esc(k.kind) + '</span>') + '</td>' +
    '<td><input class="k-label" value="' + esc(k.label) + '" style="min-width:110px" placeholder="显示名称"></td>' +
    '<td><input type="number" step="0.1" class="k-weight" value="' + fmtNum(k.weight) + '" style="width:80px"></td>' +
    '<td>' + (locked
      ? '<span class="muted">内置（计分基准）</span>'
      : '<label class="ck"><input type="checkbox" class="k-unavail"' + (k.unavailable ? ' checked' : '') + '>不在岗</label>') + '</td>' +
    '<td>' + (locked
      ? '<span class="muted">—</span>'
      : '<button class="btn small danger" onclick="this.closest(\'tr\').remove()">删除</button>') + '</td>' +
    '</tr>';
}
function renderSettings() {
  const el = $('#tab-settings');
  const multi = ((state.meta.settings || {}).multi_shift || '0') === '1';
  const coverMulti = ((state.meta.settings || {}).cover_multi_shift || '0') === '1';
  const avoidRepeat = ((state.meta.settings || {}).avoid_repeat_slot || '1') === '1';
  let h = '<div class="card"><h3>排班规则</h3>' +
    '<label class="ck"><input type="checkbox" id="multi-shift"' + (multi ? ' checked' : '') +
    ' onchange="toggleMultiShift(this.checked)"> 允许每人每天上多个不重叠班次</label>' +
    '<div class="muted" style="margin-top:6px">开启（默认）：同一人当天可在不同排班表兼班（时间不重叠）；<b>时间重叠仍拦截</b>，同一排班表内仍为「一人一岗」。关闭＝每人每天最多一班，跨排班表也拦截。</div>' +
    '<label class="ck" style="margin-top:10px"><input type="checkbox" id="avoid-repeat"' + (avoidRepeat ? ' checked' : '') +
    ' onchange="toggleAvoidRepeat(this.checked)"> 同任务不连续两天排同一班次</label>' +
    '<div class="muted" style="margin-top:6px">开启（默认）：自动排班与手工指派都会拦「昨天上过同一时段的人今天再上同一时段」。关闭后允许连着上同一班次。</div>' +
    '<label class="ck" style="margin-top:10px"><input type="checkbox" id="cover-multi-shift"' + (coverMulti ? ' checked' : '') +
    ' onchange="toggleCoverMultiShift(this.checked)"> 顶班允许一人一天多班</label>' +
    '<div class="muted" style="margin-top:6px">仅影响<b>顶班</b>：开启后顶班人无需让出自己当天的班，可同时保留原班并接手被顶班次（<b>时间不重叠</b>，重叠仍拦截）。<b>不影响自动排班算法</b>（仍以均匀分配为主）。</div></div>';
  const showDis = ((state.meta.settings || {}).show_disabled || '0') === '1';
  h += '<div class="card"><h3>人员显示</h3>' +
    '<label class="ck"><input type="checkbox" id="show-disabled"' + (showDis ? ' checked' : '') +
    ' onchange="toggleShowDisabledSetting(this.checked)"> 在人员列表中显示「已停用」人员</label>' +
    '<div class="muted" style="margin-top:6px">停用（软删除）不会删除历史排班与考勤记录，可在人员页随时恢复。</div></div>';
  h += '<div class="card"><h3>数据自检</h3>' +
    '<div class="muted">检查孤立引用、同日冲突、时段重叠、重复底班、悬空顶班、失效权限与考勤类型等。</div>' +
    '<div style="margin-top:8px"><button class="btn" onclick="runSelfcheck()">开始自检</button> ' +
    '<button class="btn" onclick="repairCovers()">一键修复悬空顶班</button></div></div>' +
    '<div id="selfcheck-result"></div>';
  h += '<div class="toolbar"><h3>考勤类型设置</h3>' +
    '<span class="muted">修改名称与分数后保存即生效（无需重启）。「不在岗」类型当天不参与排班。</span>' +
    '<span class="spacer"></span>' +
    '<button class="btn" onclick="addKindRow()">+ 新增类型</button>' +
    '<button class="btn primary" onclick="saveAttKinds()">保存</button></div>';
  h += '<div class="card"><table class="list"><thead><tr>' +
    '<th>代码</th><th>名称</th><th>分数</th><th>是否不在岗</th><th>操作</th>' +
    '</tr></thead><tbody id="kinds-body">';
  attKindList().forEach(function (k) { h += kindRowHTML(k, false); });
  h += '</tbody></table>' +
    '<div class="muted" style="margin-top:10px">分数用于统计加权（正常×1）。「缺勤」通常为 0 分；「事假/病假」按实际折算。</div></div>';
  if (state.meta.db_path) {
    h += '<div class="card"><h3>数据文件</h3><div class="muted">所有配置与排班都保存在：<code>' + esc(state.meta.db_path) + '</code></div>' +
      '<div class="muted" style="margin-top:6px">若重启后配置丢失，通常是启动的「程序位置」变了 → 读到了另一个 data.db。请固定从同一处启动，并定期备份该文件。</div></div>';
  }
  el.innerHTML = h;
}
async function toggleMultiShift(on) {
  try {
    await post('/api/settings', { multi_shift: on ? '1' : '0' });
    if (!state.meta.settings) state.meta.settings = {};
    state.meta.settings.multi_shift = on ? '1' : '0';
    toast(on ? '已开启：每人每天可上多个不重叠班次' : '已恢复：每人每天最多一班');
  } catch (e) {
    toast(e.message, true);
    renderSettings();
  }
}
async function toggleAvoidRepeat(on) {
  try {
    await post('/api/settings', { avoid_repeat_slot: on ? '1' : '0' });
    if (!state.meta.settings) state.meta.settings = {};
    state.meta.settings.avoid_repeat_slot = on ? '1' : '0';
    toast(on ? '已开启：同任务不连续两天排同一班次' : '已关闭：允许连着上同一班次');
  } catch (e) {
    toast(e.message, true);
    renderSettings();
  }
}
async function toggleCoverMultiShift(on) {
  try {
    await post('/api/settings', { cover_multi_shift: on ? '1' : '0' });
    if (!state.meta.settings) state.meta.settings = {};
    state.meta.settings.cover_multi_shift = on ? '1' : '0';
    toast(on ? '已开启：顶班可一天多班（时间不重叠）' : '已关闭：顶班仍为一人一天一岗');
  } catch (e) {
    toast(e.message, true);
    renderSettings();
  }
}
async function toggleShowDisabledSetting(on) {
  try {
    await post('/api/settings', { show_disabled: on ? '1' : '0' });
    if (!state.meta.settings) state.meta.settings = {};
    state.meta.settings.show_disabled = on ? '1' : '0';
    state.showDisabled = on;
    toast(on ? '已显示停用人员' : '已隐藏停用人员');
  } catch (e) {
    toast(e.message, true);
    renderSettings();
  }
}
async function repairCovers() {
  if (!confirm('将修复「顶班记录指向不存在的底班」：\n' +
    '· 该岗位当天已有其它底班 → 删除这条冗余顶班；\n' +
    '· 否则 → 把该顶班升为正常排班。\n继续？')) return;
  try {
    const r = await post('/api/selfcheck/repair', {});
    toast('已修复：删除 ' + (r.removed || 0) + ' 条，转为底班 ' + (r.promoted || 0) + ' 条');
    await runSelfcheck();
  } catch (e) { toast(e.message, true); }
}
async function runSelfcheck() {
  const box = $('#selfcheck-result');
  if (!box) return;
  box.innerHTML = '<div class="muted" style="margin-top:10px">检测中…</div>';
  let d;
  try {
    d = await get('/api/selfcheck');
  } catch (e) {
    box.innerHTML = '<div class="card error-text" style="margin-top:10px">' + esc(e.message) + '</div>';
    return;
  }
  let h = '<div class="card" style="margin-top:10px"><b>' +
    (d.ok ? '未发现问题' : '发现 ' + d.issues + ' 个问题') + '</b>' +
    '<span class="muted"> 排班 ' + d.counts.schedules + ' 行 · 考勤 ' + d.counts.attendance +
    ' 行 · 人员 ' + d.counts.persons + ' 人 · 名单 ' + d.counts.rosters + ' 个</span></div>';
  d.checks.forEach(function (c) {
    const info = c.name.indexOf('已启用多班次') === 0;
    let tag;
    if (info) tag = '<span class="tag gray">' + c.count + ' 条</span>';
    else if (c.count === 0) tag = '<span class="tag green">正常</span>';
    else tag = '<span class="tag red">' + c.count + ' 项</span>';
    h += '<div class="card"><div><b>' + esc(c.name) + '</b>：' + tag + '</div>';
    if (!info && c.items && c.items.length) {
      const cols = Object.keys(c.items[0]);
      h += '<table class="list" style="margin-top:6px"><thead><tr>';
      cols.forEach(function (k) { h += '<th>' + esc(k) + '</th>'; });
      h += '</tr></thead><tbody>';
      c.items.forEach(function (it) {
        h += '<tr>';
        cols.forEach(function (k) {
          const v = it[k];
          h += '<td>' + esc(Array.isArray(v) ? v.join('、') : (v == null ? '' : String(v))) + '</td>';
        });
        h += '</tr>';
      });
      h += '</tbody></table>';
      if (c.count > c.items.length) h += '<div class="muted">仅显示前 ' + c.items.length + ' 条</div>';
    }
    h += '</div>';
  });
  box.innerHTML = h;
}
function addKindRow() {
  const body = $('#kinds-body');
  if (!body) return;
  body.insertAdjacentHTML('beforeend', kindRowHTML({ kind: nextFreeKind(), label: '', weight: 0, unavailable: true }, true));
}
async function saveAttKinds() {
  const rows = Array.from(document.querySelectorAll('#kinds-body tr'));
  const kinds = rows.map(function (tr) {
    const codeEl = tr.querySelector('.k-kind');
    const code = codeEl ? codeEl.value.trim() : tr.querySelector('.k-kind-txt').textContent.trim();
    const unavailEl = tr.querySelector('.k-unavail');
    return {
      kind: code,
      label: tr.querySelector('.k-label').value.trim(),
      weight: Number(tr.querySelector('.k-weight').value),
      unavailable: unavailEl ? unavailEl.checked : false,
    };
  });
  try {
    await post('/api/att_kinds', { kinds: kinds });
    await loadMeta();
    renderSettings();
    toast('已保存');
  } catch (e) { toast(e.message, true); }
}

/* ================= 考勤与顶班 ================= */
function kindOptions(sel) {
  return attKindList().map(function (o) {
    return '<option value="' + o.kind + '"' + (o.kind === sel ? ' selected' : '') + '>' +
      esc(o.label) + ' (' + fmtNum(o.weight) + ')</option>';
  }).join('');
}
function renderAttendance() {
  const el = $('#tab-attendance');
  el.innerHTML =
    '<div class="toolbar"><label>日期</label>' +
    '<input type="date" id="att-date" value="' + state.attendanceDate + '" onchange="state.attendanceDate=this.value;renderAttendance()">' +
    '<span class="spacer"></span>' +
    '<button class="btn primary" onclick="openLeaveModal()">+ 录入请假/不在岗时段</button></div>' +
    '<div class="muted" style="margin:-4px 0 10px">按「起止日期 + 事由」批量录入某人连续多天的不在岗（' + esc(absentLabels()) + '），重新生成排班时系统会自动跳过这些日期。</div>' +
    '<div id="att-body"><div class="card">加载中…</div></div>';
  loadAttendance();
}
async function loadAttendance() {
  const box = $('#att-body');
  if (!box) return;
  let data;
  try {
    data = await get('/api/attendance?date=' + state.attendanceDate);
  } catch (e) {
    box.innerHTML = '<div class="card error-text">' + esc(e.message) + '</div>';
    return;
  }
  if (!data.groups.length && !data.extra_records.length) {
    box.innerHTML = '<div class="card muted">当天没有排班记录，也没有考勤记录。请先在「排班任务」生成排班。</div>';
    return;
  }
  const buckets = [];
  const bindex = {};
  function bucketOf(id, name) {
    const key = (id == null ? 0 : id);
    if (!(key in bindex)) {
      bindex[key] = buckets.length;
      buckets.push({ key: String(key), name: name || '（未分组）', groups: [], extras: [] });
    }
    return buckets[bindex[key]];
  }
  data.groups.forEach(function (g) { bucketOf(g.roster_id, g.roster_name).groups.push(g); });
  data.extra_records.forEach(function (r) { bucketOf(r.roster_id, r.roster_name).extras.push(r); });

  const needSug = [];
  let h = '';
  buckets.forEach(function (ro) {
    const closed = !!state.attClosed[ro.key];
    h += '<div class="att-roster"><div class="att-roster-head" onclick="toggleAttRoster(\'' + ro.key + '\')">' +
      '<span class="chev" id="atchev-' + ro.key + '">' + (closed ? '▸' : '▾') + '</span>名单：' + esc(ro.name) + '</div>' +
      '<div class="att-roster-body" id="attbody-' + ro.key + '"' + (closed ? ' style="display:none"' : '') + '>';
    ro.groups.forEach(function (g) {
      const eff = g.covers.length ? g.covers[g.covers.length - 1] : g.base;
      const effAbsent = eff && isAbsentKind(eff.kind);
      h += '<div class="att-group"><div class="att-head">' + esc(g.start) + '-' + esc(g.end) +
        '<span class="tag gray">' + esc(g.position_name) + '</span>' +
        '<span class="tag">' + esc(g.task_name) + '</span>' +
        (g.perm_name ? '<span class="tag green">需权限 ' + esc(g.perm_name) + '</span>' : '') + '</div>';
      if (g.base) h += attRowHTML(g, 'base', g.base);
      g.covers.forEach(function (c) { h += attRowHTML(g, 'cover', c); });
      if (effAbsent) {
        needSug.push(g);
        h += '<div class="att-row att-sug"><span class="att-role">顶班建议</span>' +
          '<select id="sug-' + g.position_id + '"><option value="">加载中…</option></select>' +
          '<button class="btn primary small" onclick="applyCoverSug(' + g.task_id + ',' + g.position_id + ')">安排顶班</button>' +
          '<span class="muted">列出全部有该岗位权限的人员，★ 为建议人选；选择「当天已排」者会自动从其原岗位移入本岗位</span></div>';
      }
      h += '</div>';
    });
    if (ro.extras.length) {
      h += '<div class="card"><h3>当天无排班的考勤记录</h3><div class="muted" style="margin-bottom:6px">这些人员当天没有排班，记录仅作留存，不计入总班次。</div>';
      ro.extras.forEach(function (r) {
        h += '<div class="att-row"><span class="att-name">' + esc(r.name) + '</span>' +
          '<select id="k-' + r.person_id + '">' + kindOptions(r.kind) + '</select>' +
          '<input type="text" id="r-' + r.person_id + '" placeholder="事由" style="width:170px" value="' + esc(r.reason) + '">' +
          '<button class="btn small" onclick="saveAtt(' + r.person_id + ')">保存</button>' +
          '<button class="btn small danger" onclick="delAtt(' + r.person_id + ')">删除记录</button></div>';
      });
      h += '</div>';
    }
    h += '</div></div>';
  });
  box.innerHTML = h;
  needSug.forEach(function (g) { loadSug(g); });
}
function toggleAttRoster(key) {
  const body = document.getElementById('attbody-' + key);
  const chev = document.getElementById('atchev-' + key);
  if (!body) return;
  const hide = body.style.display !== 'none';
  body.style.display = hide ? 'none' : '';
  state.attClosed[key] = hide;
  if (chev) chev.textContent = hide ? '▸' : '▾';
}
function attRowHTML(g, role, person) {
  const pid = person.person_id;
  let h = '<div class="att-row"><span class="att-role">' + (role === 'base' ? '底班' : '顶班') + '</span>' +
    '<span class="att-name">' + esc(person.name) + (role === 'cover' ? ' <span class="tag orange">代班</span>' : '') + '</span>' +
    '<select id="k-' + pid + '">' + kindOptions(person.kind) + '</select>' +
    '<input type="text" id="r-' + pid + '" placeholder="事由备注" style="width:170px" value="' + esc(person.reason) + '">' +
    '<button class="btn small" onclick="saveAtt(' + pid + ')">保存</button>';
  if (role === 'cover') {
    h += '<button class="btn small danger" onclick="revokeCover(' + g.task_id + ',' + g.position_id + ')">撤销顶班</button>';
  }
  h += '</div>';
  return h;
}
async function loadSug(g) {
  try {
    const list = await get('/api/cover/suggest?task_id=' + g.task_id + '&date=' + state.attendanceDate + '&position_id=' + g.position_id);
    const sel = document.getElementById('sug-' + g.position_id);
    if (!sel) return;
    if (!list.length) {
      sel.innerHTML = '<option value="">无符合条件的人员</option>';
      return;
    }
    let html = '<option value="">请选择顶班人员…</option>';
    list.forEach(function (x) {
      const tags = [];
      if (x.suggested) tags.push('建议');
      if (x.busy) tags.push('当天已排');
      if (x.risky) tags.push('昨天同班次');
      html += '<option value="' + x.person_id + '"' + (x.suggested ? ' selected' : '') + '>' +
        (x.suggested ? '★ ' : '') + esc(x.name) + (x.code ? '（' + esc(x.code) + '）' : '') +
        ' · 总班次 ' + x.total + (tags.length ? '［' + tags.join('·') + '］' : '') + '</option>';
    });
    sel.innerHTML = html;
  } catch (e) { /* ignore */ }
}
async function saveAtt(pid) {
  const kindEl = document.getElementById('k-' + pid);
  const reasonEl = document.getElementById('r-' + pid);
  try {
    await post('/api/attendance', {
      person_id: pid,
      date: state.attendanceDate,
      kind: kindEl.value,
      reason: reasonEl ? reasonEl.value : '',
    });
    toast('已记录');
    loadAttendance();
  } catch (e) { toast(e.message, true); }
}
async function delAtt(pid) {
  if (!confirm('删除该考勤记录（恢复为正常）？')) return;
  try {
    await del('/api/attendance?person_id=' + pid + '&date=' + state.attendanceDate);
    toast('已删除');
    loadAttendance();
  } catch (e) { toast(e.message, true); }
}
async function openLeaveModal() {
  if (!state.rosters.length) { toast('请先在「人员名单」创建名单并录入人员', true); return; }
  let groups;
  try {
    groups = await Promise.all(state.rosters.map(function (r) { return get('/api/rosters/' + r.id + '/persons'); }));
  } catch (e) { toast(e.message, true); return; }
  let opts = '';
  let empty = true;
  state.rosters.forEach(function (r, i) {
    if (!groups[i].length) return;
    empty = false;
    opts += '<optgroup label="' + esc(r.name) + '">';
    groups[i].forEach(function (p) {
      opts += '<option value="' + p.id + '">' + esc(p.name) + (p.code ? '（' + esc(p.code) + '）' : '') + '</option>';
    });
    opts += '</optgroup>';
  });
  if (empty) { toast('名单里还没有人员', true); return; }
  const h = '<div class="form-row"><label>人员 *</label><select id="lv-person" style="min-width:220px">' + opts + '</select></div>' +
    '<div class="form-row"><label>开始日期 *</label><input type="date" id="lv-start" value="' + state.attendanceDate + '">' +
    '<label>结束日期 *</label><input type="date" id="lv-end" value="' + state.attendanceDate + '"></div>' +
    '<div class="form-row"><label>不在岗类型 *</label><select id="lv-kind">' +
    attKindList().filter(function (x) { return x.unavailable; }).map(function (x) {
      return '<option value="' + x.kind + '">' + esc(x.label) + '（计 ' + fmtNum(x.weight) + ' 分）</option>';
    }).join('') +
    '<option value="normal">清除该时段记录（恢复正常）</option></select></div>' +
    '<div class="form-row"><label>事由</label><input type="text" id="lv-reason" style="min-width:320px" placeholder="如：家中有事 / 出差 / 病假"></div>' +
    '<div class="m-foot"><button class="btn" onclick="closeModal()">取消</button>' +
    '<button class="btn primary" onclick="saveLeave()">保存</button></div>';
  openModal('录入请假 / 不在岗时段', h);
}
async function saveLeave() {
  const body = {
    person_id: Number($('#lv-person').value) || null,
    start: $('#lv-start').value,
    end: $('#lv-end').value,
    kind: $('#lv-kind').value,
    reason: $('#lv-reason').value,
  };
  if (!body.person_id) { toast('请选择人员', true); return; }
  if (!body.start || !body.end) { toast('请选择起止日期', true); return; }
  if (body.end < body.start) { toast('结束日期不能早于开始日期', true); return; }
  try {
    const r = await post('/api/attendance/range', body);
    closeModal();
    toast(body.kind === 'normal' ? ('已清除 ' + r.days + ' 天记录') : ('已录入 ' + r.days + ' 天不在岗'));
    loadAttendance();
  } catch (e) { toast(e.message, true); }
}
async function applyCoverSug(taskId, positionId) {
  const sel = document.getElementById('sug-' + positionId);
  if (!sel || !sel.value) { toast('请先选择顶班人员', true); return; }
  try {
    await post('/api/cover', {
      task_id: taskId, date: state.attendanceDate, position_id: positionId, person_id: Number(sel.value),
    });
    toast('顶班已安排');
    loadAttendance();
  } catch (e) { toast(e.message, true); }
}
async function revokeCover(taskId, positionId) {
  if (!confirm('撤销该岗位当天的顶班安排（由底班人员复岗）？')) return;
  try {
    await del('/api/cover?task_id=' + taskId + '&date=' + state.attendanceDate + '&position_id=' + positionId);
    toast('已撤销');
    loadAttendance();
  } catch (e) { toast(e.message, true); }
}

/* ================= 统计 ================= */
function statsRangeLabel() {
  if (state.statsYear === 0) return '全部年份';
  if (state.statsMonth) return state.statsYear + '年' + state.statsMonth + '月';
  return state.statsYear + '年';
}
function statsQuery() {
  let q = '?year=' + state.statsYear;
  if (state.statsYear !== 0 && state.statsMonth) q += '&month=' + state.statsMonth;
  return q;
}
function renderStats() {
  const el = $('#tab-stats');
  const cur = new Date().getFullYear();
  let yopts = '';
  [cur, cur - 1, cur - 2].forEach(function (v) {
    yopts += '<option value="' + v + '"' + (v === state.statsYear ? ' selected' : '') + '>' + v + '年</option>';
  });
  yopts += '<option value="0"' + (state.statsYear === 0 ? ' selected' : '') + '>全部</option>';
  const yearAll = state.statsYear === 0;
  let mopts = '<option value="0"' + (!yearAll && state.statsMonth === 0 ? ' selected' : '') + '>全部月份</option>';
  for (let m = 1; m <= 12; m++) {
    mopts += '<option value="' + m + '"' + (!yearAll && state.statsMonth === m ? ' selected' : '') + '>' + m + '月</option>';
  }
  el.innerHTML =
    '<div class="toolbar"><label>统计时间</label>' +
    '<select onchange="state.statsYear=Number(this.value);state.statsMonth=0;renderStats()">' + yopts + '</select>' +
    '<select ' + (yearAll ? 'disabled ' : '') + 'onchange="state.statsMonth=Number(this.value);renderStats()">' + mopts + '</select>' +
    '<span class="muted">范围：' + esc(statsRangeLabel()) + ' · 总分数 = ' + esc(weightHint()) + '</span></div>' +
    '<div id="stats-body"><div class="card">加载中…</div></div>';
  loadStats();
}
async function loadStats() {
  const box = $('#stats-body');
  if (!box) return;
  let data;
  try {
    data = await get('/api/stats' + statsQuery());
  } catch (e) {
    box.innerHTML = '<div class="card error-text">' + esc(e.message) + '</div>';
    return;
  }
  if (!data.length) {
    box.innerHTML = '<div class="card muted">暂无人员，请先在「人员名单」录入。</div>';
    return;
  }
  const kinds = attKindList();
  const labelCols = 2 + kinds.length;
  const groups = [];
  const gindex = {};
  data.forEach(function (r) {
    const key = r.task_id;
    if (!(key in gindex)) {
      gindex[key] = groups.length;
      groups.push({ key: String(key), id: key, name: r.task_id ? (r.task_name || '（无名排班表）') : '未参与排班', roster: r.roster_name || '', rows: [] });
    }
    groups[gindex[key]].rows.push(r);
  });
  state.statsGroups = groups;
  state.statsKinds = kinds;
  state.statsLabelCols = labelCols;
  state.statsRendered = {};
  const bigStats = data.length > 300;
  let h = '<div class="card"><table class="list"><thead><tr><th>姓名</th><th>工号</th>';
  kinds.forEach(function (k) { h += '<th class="num">' + esc(k.label) + '</th>'; });
  h += '<th class="num">总班次</th><th class="num">总分数</th><th>明细</th></tr></thead><tbody>';
  let gCount = 0, gTotal = 0;
  const seen = {};
  groups.forEach(function (g) {
    if (!(g.key in state.statsClosed)) state.statsClosed[g.key] = bigStats;
    const closed = !!state.statsClosed[g.key];
    let subCount = 0, subTotal = 0;
    g.rows.forEach(function (r) {
      seen[r.person_id] = 1;
      subCount += r.count;
      subTotal += r.total;
    });
    g.subCount = subCount;
    g.subTotal = subTotal;
    h += '<tr class="group-row" id="gh-' + g.key + '" onclick="toggleStatsGroup(\'' + g.key + '\')"><td colspan="' + (labelCols + 3) + '">' +
      '<span class="chev" id="chev-' + g.key + '">' + (closed ? '▸' : '▾') + '</span>排班表：' + esc(g.name) +
      (g.id ? ' <span class="tag gray">名单：' + esc(g.roster) + '</span>' : '') +
      '（' + g.rows.length + '人）</td></tr>';
    if (closed) {
      state.statsRendered[g.key] = 0;
    } else {
      h += statsRowsHTML(g, kinds, labelCols);
      state.statsRendered[g.key] = 1;
    }
    gCount += subCount;
    gTotal += subTotal;
  });
  h += '</tbody><tfoot><tr class="total-row"><td colspan="' + labelCols + '">全员合计（' + Object.keys(seen).length + '人 · ' + groups.length + ' 个排班表）</td>' +
    '<td class="num">' + gCount + '</td><td class="num">' + gTotal.toFixed(2) + '</td><td></td></tr></tfoot></table></div>';
  box.innerHTML = h;
}
function statsRowsHTML(g, kinds, labelCols) {
  let h = '';
  g.rows.forEach(function (r) {
    let kcells = '';
    kinds.forEach(function (k) { kcells += '<td class="num">' + (r[k.kind] || 0) + '</td>'; });
    h += '<tr class="gbody gbody-' + g.key + '"><td><b>' + esc(r.name) + '</b></td><td>' + esc(r.code) + '</td>' + kcells +
      '<td class="num">' + r.count + '</td>' +
      '<td class="num"><b>' + r.total.toFixed(2) + '</b></td>' +
      '<td><button class="btn small" onclick="openDetail(' + r.person_id + ',\'' + esc(String(r.name).replace(/'/g, '')) + '\')">查看</button></td></tr>';
  });
  h += '<tr class="subtotal-row gbody gbody-' + g.key + '"><td colspan="' + labelCols + '">' + esc(g.name) + ' 小计</td>' +
    '<td class="num">' + g.subCount + '</td><td class="num">' + g.subTotal.toFixed(2) + '</td><td></td></tr>';
  return h;
}
function toggleStatsGroup(key) {
  const gh = document.getElementById('gh-' + key);
  if (!gh) return;
  const willClose = !state.statsClosed[key];
  if (!willClose && !state.statsRendered[key]) {
    const g = (state.statsGroups || []).filter(function (x) { return x.key === key; })[0];
    if (g) {
      gh.insertAdjacentHTML('afterend', statsRowsHTML(g, state.statsKinds || [], state.statsLabelCols));
      state.statsRendered[key] = 1;
    }
  }
  document.querySelectorAll('.gbody-' + key).forEach(function (el) {
    el.style.display = willClose ? 'none' : '';
  });
  state.statsClosed[key] = willClose;
  const chev = document.getElementById('chev-' + key);
  if (chev) chev.textContent = willClose ? '▸' : '▾';
}
async function openDetail(pid, name) {
  openModal('明细：' + name, '<div class="muted">加载中…</div>');
  try {
    const rows = await get('/api/stats/detail?person_id=' + pid + '&' + statsQuery().slice(1));
    let h = '';
    if (!rows.length) {
      h = '<div class="muted">该范围（' + esc(statsRangeLabel()) + '）内没有排班记录。</div>';
    } else {
      let total = 0;
      rows.forEach(function (r) {
        total += r.weight;
        const ktag = r.kind === 'normal' ? 'green' : kindTagClass(r.kind);
        h += '<div class="stats-detail-row"><span class="d">' + r.date + '</span><span class="w">' + esc(r.time) + '</span>' +
          '<span style="flex:1">' + esc(r.task_name) + ' · ' + esc(r.position_name) +
          (r.is_cover ? ' <span class="tag orange">顶班</span>' : '') + '</span>' +
          '<span class="tag ' + ktag + '">' + esc(r.kind_label) + '</span>' +
          '<span class="muted">' + r.weight + ' 分</span></div>';
      });
      h += '<div class="stats-detail-row" style="border-top:2px solid #e1e6ec"><b>合计：' + total.toFixed(2) +
        ' 分（' + rows.length + ' 个班次 = ' + esc(weightHint()) + '）</b></div>';
    }
    openModal('明细：' + name, h);
  } catch (e) {
    openModal('明细：' + name, '<div class="error-text">' + esc(e.message) + '</div>');
  }
}

/* ================= 启动 ================= */
async function init() {
  try {
    await loadMeta();
    await loadRosters();
    await loadTasks();
    renderTab('tasks');
  } catch (e) {
    toast('初始化失败：' + e.message, true);
  }
}
document.querySelectorAll('#nav button').forEach(function (b) {
  b.addEventListener('click', function () { renderTab(b.dataset.tab); });
});
init();
