'use strict';

var state = {
  me: null, auth: null, models: [], materials: [], topics: [],
  selMaterials: [], editing: null,
  conv: null, candidates: [], gens: {}, order: [],
  plans: []
};

var TEMPLATES = {
  review: { duration_minutes: 15 },
  travel: { start_time: '15:15', available_minutes: 40, budget_per_person: 80, people: 1, legs: [{ minutes: 15 }, { minutes: 15 }] },
  activity: { total_minutes: 25, available_minutes: 25, people: 20, fixed_furniture: { '桌椅': 20 }, budget_per_person: 50, items: [{ title: '开场', minutes: 2, must_keep: true }, { title: '互访', minutes: 12, must_keep: true }, { title: '猜词', minutes: 8 }, { title: '收尾', minutes: 3 }] }
};

var TASK_LABEL = { review: '复习笔记', travel: '周末行程', activity: '活动流程' };
var MODE_LABEL = { pending: '排队中', running: '生成中', succeeded: '已完成', blocked: '被拦下', superseded: '已作废', failed: '失败', cancelled: '已取消' };

function byId(id) { return document.getElementById(id); }
function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function el(html) {
  var d = document.createElement('div');
  d.innerHTML = html.trim();
  return d.firstElementChild;
}
function toast(msg, isErr) {
  var t = byId('toast');
  t.textContent = msg;
  t.className = 'toast' + (isErr ? ' err' : '');
  clearTimeout(t._timer);
  t._timer = setTimeout(function () { t.className = 'toast hidden'; }, isErr ? 6000 : 3000);
}
function openModal(title, bodyHtml) {
  byId('modal-title').textContent = title;
  byId('modal-body').innerHTML = bodyHtml;
  byId('modal').classList.remove('hidden');
}
function closeModal() { byId('modal').classList.add('hidden'); }

async function api(method, path, body) {
  var opt = { method: method, headers: {}, credentials: 'same-origin' };
  if (body !== undefined && body !== null) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  var r = await fetch(path, opt);
  if (r.status === 204) return null;
  var text = await r.text();
  var data = null;
  if (text) { try { data = JSON.parse(text); } catch (e) { data = { raw: text }; } }
  if (!r.ok) {
    var code = '';
    var msg = text || ('HTTP ' + r.status);
    if (data) {
      if (data.code) { code = data.code; msg = data.message || code; }
      else if (data.error && data.error.code) { code = data.error.code; msg = data.error.message || code; }
      else if (data.detail) { msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail); }
    }
    var err = new Error(msg + (code ? '  [' + code + ']' : ''));
    err.status = r.status; err.code = code; err.data = data;
    throw err;
  }
  return data;
}

/* ---------- 身份 ---------- */
async function loadWho() {
  var me = null, auth = null;
  try { me = await api('GET', '/api/v1/me'); } catch (e) {}
  try { auth = await api('GET', '/api/v1/auth/me'); } catch (e) {}
  state.me = me; state.auth = auth;
  var box = byId('who');
  var parts = [];
  if (auth && auth.logged_in) {
    parts.push('已登录：' + auth.account.username);
    byId('btn-login').classList.add('hidden');
    byId('btn-logout').classList.remove('hidden');
  } else {
    parts.push('游客身份（换浏览器就是另一个人）');
    byId('btn-login').classList.remove('hidden');
    byId('btn-logout').classList.add('hidden');
  }
  if (me) {
    parts.push(me.writable ? ('可写，剩余 ' + me.days_left + ' 天') : ('只读（试用已到期）'));
  }
  box.textContent = parts.join(' · ');
}

function bindAuth() {
  byId('btn-login').onclick = function () {
    openModal('登录 / 注册', ''
      + '<label>用户名<input id="au-name" placeholder="3-32 位字母数字下划线"></label>'
      + '<label>密码<input id="au-pass" type="password" placeholder="至少 8 位"></label>'
      + '<div class="row"><button class="primary" id="au-login">登录</button>'
      + '<button class="ghost" id="au-register">注册（保留当前数据）</button></div>'
      + '<div class="hint">没账号也能用；注册会把当前浏览器里的材料和方案一起升级到账号下。</div>');
    byId('au-login').onclick = async function () {
      try {
        await api('POST', '/api/v1/auth/login', { username: byId('au-name').value.trim(), password: byId('au-pass').value });
        closeModal(); toast('已登录'); await boot();
      } catch (e) { toast(e.message, true); }
    };
    byId('au-register').onclick = async function () {
      try {
        await api('POST', '/api/v1/auth/register', { username: byId('au-name').value.trim(), password: byId('au-pass').value });
        closeModal(); toast('注册成功，数据已归到账号下'); await boot();
      } catch (e) { toast(e.message, true); }
    };
  };
  byId('btn-logout').onclick = async function () {
    try { await api('POST', '/api/v1/auth/logout'); toast('已登出'); await boot(); }
    catch (e) { toast(e.message, true); }
  };
}

/* ---------- 模型 ---------- */
async function loadModels() {
  var data = await api('GET', '/api/v1/models');
  state.models = data.items || [];
  var box = byId('models');
  box.innerHTML = '';
  state.models.forEach(function (m) {
    var chip = el('<span class="model-chip' + (m.available ? '' : ' off') + '" data-id="' + esc(m.id) + '">' + esc(m.id) + (m.available ? '' : '（未配置）') + '</span>');
    if (m.id === data.default || m.default) chip.classList.add('on');
    if (!m.available) chip.title = m.reason || '不可用';
    chip.onclick = function () {
      if (!m.available) return;
      chip.classList.toggle('on');
    };
    box.appendChild(chip);
  });
  byId('model-hint').textContent = '默认 ' + data.default;
}

function selectedModels() {
  var out = [];
  var chips = byId('models').querySelectorAll('.model-chip.on');
  for (var i = 0; i < chips.length; i++) out.push(chips[i].getAttribute('data-id'));
  return out;
}

/* ---------- 材料 ---------- */
async function loadTopics() {
  try {
    var data = await api('GET', '/api/v1/materials/topics');
    state.topics = (data && (data.items || data.topics)) || (Array.isArray(data) ? data : []);
    var sel = byId('mat-topic');
    var cur = sel.value;
    sel.innerHTML = '<option value="">全部主题</option>';
    state.topics.forEach(function (t) {
      var name = typeof t === 'string' ? t : (t.topic || t.name || '');
      if (!name) return;
      var o = document.createElement('option');
      o.value = name; o.textContent = name;
      sel.appendChild(o);
    });
    sel.value = cur;
  } catch (e) {}
}

async function loadMaterials() {
  var q = byId('mat-q').value.trim();
  var topic = byId('mat-topic').value;
  var url = '/api/v1/materials?limit=50';
  if (q) url += '&q=' + encodeURIComponent(q);
  if (topic) url += '&topic=' + encodeURIComponent(topic);
  var data = await api('GET', url);
  state.materials = data.items || [];
  renderMaterials();
}

var STATUS_LABEL = { citable: '可引用', usable: '可引用', link_only: '仅链接待补', unusable: '不可用', pending: '待补' };

function renderMaterials() {
  var box = byId('mat-list');
  if (!state.materials.length) {
    box.innerHTML = '<div class="hint">还没有材料。先在左边加一条。</div>';
    updateSelInfo();
    return;
  }
  box.innerHTML = '';
  state.materials.forEach(function (m) {
    var st = m.status || 'citable';
    var badgeCls = st === 'citable' || st === 'usable' ? 'ok' : (st === 'link_only' ? 'warn' : 'bad');
    var src = [];
    if (m.source_kind === 'courseware') src.push('课件');
    if (m.source_file) src.push(m.source_file);
    if (m.source_page_start) src.push('第 ' + m.source_page_start + (m.source_page_end && m.source_page_end !== m.source_page_start ? '-' + m.source_page_end : '') + ' 页');
    if (m.source_url) src.push('链接');
    var card = el('<div class="card" data-id="' + esc(m.id) + '">'
      + '<div class="card-head">'
      + '<input type="checkbox" class="pick" ' + (state.selMaterials.indexOf(m.id) >= 0 ? 'checked' : '') + ' style="width:auto;margin-top:3px">'
      + '<div style="flex:1">'
      + '<div class="t">' + esc(m.title) + '<span class="badge ' + badgeCls + '">' + esc(STATUS_LABEL[st] || st) + '</span></div>'
      + '<div class="card-meta">' + esc(m.topic) + ' · ' + esc(src.join(' · ')) + (m.note ? ' · 备注：' + esc(m.note) : '') + '</div>'
      + '<div class="card-meta">' + esc((m.excerpt || '').slice(0, 90)) + ((m.excerpt || '').length > 90 ? '…' : '') + '</div>'
      + '</div>'
      + '<div class="card-actions">'
      + '<button class="ghost small act-edit">编辑</button>'
      + '<button class="ghost small danger act-del">删除</button>'
      + '</div></div></div>');
    card.querySelector('.pick').onchange = function (ev) {
      var id = m.id;
      if (ev.target.checked) { if (state.selMaterials.indexOf(id) < 0) state.selMaterials.push(id); }
      else { state.selMaterials = state.selMaterials.filter(function (x) { return x !== id; }); }
      card.classList.toggle('sel', ev.target.checked);
      updateSelInfo();
    };
    card.classList.toggle('sel', state.selMaterials.indexOf(m.id) >= 0);
    card.querySelector('.act-edit').onclick = function () { fillMaterialForm(m); };
    card.querySelector('.act-del').onclick = async function () {
      if (!confirm('删除「' + m.title + '」？（软删除，出处快照仍可展开）')) return;
      try {
        await api('DELETE', '/api/v1/materials/' + m.id);
        state.selMaterials = state.selMaterials.filter(function (x) { return x !== m.id; });
        toast('已删除'); await loadMaterials(); await loadTopics();
      } catch (e) { toast(e.message, true); }
    };
    box.appendChild(card);
  });
  updateSelInfo();
}

function updateSelInfo() {
  byId('mat-sel-info').textContent = '已选 ' + state.selMaterials.length + ' 条';
}

function fillMaterialForm(m) {
  state.editing = m;
  var f = byId('mat-form');
  f.title.value = m.title || '';
  f.topic.value = m.topic || '';
  f.excerpt.value = m.excerpt || '';
  f.source_kind.value = m.source_kind || 'courseware';
  f.source_file.value = m.source_file || '';
  f.source_page_start.value = m.source_page_start || '';
  f.source_page_end.value = m.source_page_end || '';
  f.source_url.value = m.source_url || '';
  f.note.value = m.note || '';
  byId('mat-form-title').textContent = '编辑材料';
  byId('mat-submit').textContent = '保存修改';
  f.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function resetMaterialForm() {
  state.editing = null;
  byId('mat-form').reset();
  byId('mat-form-title').textContent = '新增材料';
  byId('mat-submit').textContent = '保存材料';
}

function num(v) { return v === '' || v === null || v === undefined ? null : Number(v); }

async function apiForm(path, fd) {
  var r = await fetch(path, { method: 'POST', body: fd, credentials: 'same-origin' });
  var text = await r.text();
  var data = null;
  if (text) { try { data = JSON.parse(text); } catch (e) { data = { raw: text }; } }
  if (!r.ok) {
    var code = '';
    var msg = text || ('HTTP ' + r.status);
    if (data) {
      if (data.error && data.error.code) { code = data.error.code; msg = data.error.message || code; }
      else if (data.detail) { msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail); }
    }
    var err = new Error(msg + (code ? '  [' + code + ']' : ''));
    err.status = r.status; err.code = code;
    throw err;
  }
  return data;
}

async function onFilePicked(ev) {
  var input = ev.target;
  var file = input.files && input.files[0];
  if (!file) return;
  var hint = byId('mat-file-hint');
  hint.textContent = '正在读取 ' + file.name + ' …';
  try {
    var fd = new FormData();
    fd.append('file', file);
    var data = await apiForm('/api/v1/materials/extract', fd);
    var f = byId('mat-form');
    if (data.suggested_title) f.title.value = data.suggested_title;
    f.source_kind.value = 'courseware';
    f.source_file.value = data.source_file || file.name;
    f.source_page_start.value = data.page_start || '';
    f.source_page_end.value = data.page_end || '';
    f.excerpt.value = data.excerpt || '';
    window.__lastMd = data.markdown || data.excerpt || '';
    window.__lastMdName = data.suggested_title || file.name || '材料';
    var dl = byId('mat-dl');
    if (dl) dl.style.display = window.__lastMd ? '' : 'none';
    hint.textContent = '已读取：' + data.page_count + ' 页 / ' + data.chars + ' 字'
      + (data.truncated ? '（超过 20000 字，已截取前段，可自行删减）' : '')
      + '。检查正文，补上主题后保存。';
    if (!f.topic.value.trim()) f.topic.focus();
    toast('已把文件转成 Markdown，填进摘录');
  } catch (e) {
    hint.textContent = '读取失败：' + e.message;
    toast(e.message, true);
  } finally {
    input.value = '';
  }
}

async function submitMaterial(ev) {
  ev.preventDefault();
  var f = byId('mat-form');
  var payload = {
    title: f.title.value.trim(),
    topic: f.topic.value.trim(),
    excerpt: f.excerpt.value.trim(),
    source_kind: f.source_kind.value,
    source_file: f.source_file.value.trim() || null,
    source_page_start: num(f.source_page_start.value),
    source_page_end: num(f.source_page_end.value),
    source_url: f.source_url.value.trim() || null,
    note: f.note.value.trim() || null
  };
  try {
    if (state.editing) {
      payload.version = state.editing.version;
      await api('PATCH', '/api/v1/materials/' + state.editing.id, payload);
      toast('已保存修改');
    } else {
      await api('POST', '/api/v1/materials', payload);
      toast('材料已保存');
    }
    resetMaterialForm();
    await loadMaterials(); await loadTopics();
  } catch (e) { toast(e.message, true); }
}

/* ---------- 对话与生成 ---------- */
function setTemplates() {
  var t = byId('task-type').value;
  byId('conditions').value = JSON.stringify(TEMPLATES[t] || {}, null, 2);
}
function readConditions() {
  var raw = byId('conditions').value.trim();
  if (!raw) return {};
  try { return JSON.parse(raw); } catch (e) { throw new Error('条件不是合法 JSON：' + e.message); }
}

function renderConvState() {
  var box = byId('conv-state');
  if (!state.conv) { box.textContent = '还没有对话'; return; }
  var c = state.conv;
  var st = { active: '进行中', generating: '生成中', confirmed: '已确认', archived: '已归档' }[c.status] || c.status;
  box.textContent = '对话 ' + c.id.slice(0, 8) + ' · ' + (TASK_LABEL[c.task_type] || c.task_type)
    + ' · 材料 ' + (c.material_ids || []).length + ' 条 · ' + st + ' · v' + c.version
    + ((c.turns && c.turns.length) ? ' · 追问 ' + c.turns.length + ' 次' : '');
}

async function startConv() {
  try {
    var conditions = readConditions();
    var goal = byId('goal').value.trim();
    if (!goal) { toast('先写一句「我要什么」', true); return; }
    var data = await api('POST', '/api/v1/conversations', {
      goal: goal, task_type: byId('task-type').value,
      material_ids: state.selMaterials.slice(), conditions: conditions
    });
    state.conv = data.conversation;
    state.candidates = data.candidates || [];
    renderConvState();
    renderCandidates();
    byId('conv-box').classList.remove('hidden');
    if (state.candidates.length) {
      var usable = state.candidates.filter(function (c) { return c.usable; }).length;
      toast('检索到 ' + state.candidates.length + ' 条候选（可引用 ' + usable + ' 条）');
    } else {
      toast('没检索到匹配材料，检查一下目标关键词或先加材料', true);
    }
  } catch (e) { toast(e.message, true); }
}

function renderCandidates() {
  var box = byId('candidates');
  box.innerHTML = '';
  if (!state.candidates.length) { box.innerHTML = '<div class="hint">无候选</div>'; return; }
  state.candidates.forEach(function (c) {
    var row = el('<div class="card" data-id="' + esc(c.material_id) + '">'
      + '<div class="card-head">'
      + '<input type="checkbox" class="use" ' + (c.usable ? 'checked' : '') + ' style="width:auto;margin-top:3px">'
      + '<div style="flex:1"><div class="t">' + esc(c.title)
      + (c.usable ? '' : '<span class="badge bad">仅链接/不可用</span>')
      + '</div><div class="card-meta">' + esc(c.topic || '') + ' · 得分 ' + esc(c.score) + (c.reason ? ' · ' + esc(c.reason) : '') + '</div></div></div></div>');
    box.appendChild(row);
  });
}

async function confirmMaterials() {
  try {
    var rows = byId('candidates').querySelectorAll('.card');
    var materials = [];
    for (var i = 0; i < rows.length; i++) {
      materials.push({ id: rows[i].getAttribute('data-id'), use: rows[i].querySelector('.use').checked });
    }
    var data = await api('POST', '/api/v1/conversations/' + state.conv.id + '/materials/confirm',
      { version: state.conv.version, materials: materials });
    state.conv = data;
    renderConvState();
    toast('已确认 ' + (data.material_ids || []).length + ' 条材料');
  } catch (e) { toast(e.message, true); }
}

async function patchConditions() {
  var conditions = readConditions();
  var data = await api('PATCH', '/api/v1/conversations/' + state.conv.id + '/conditions',
    { version: state.conv.version, conditions: conditions });
  state.conv = data;
  renderConvState();
}

function genCard(modelId) {
  return el('<div class="gen-card" data-model="' + esc(modelId) + '">'
    + '<div class="gen-head"><span class="m">' + esc(modelId) + '</span>'
    + '<span class="badge">排队中</span><span class="spacer" style="flex:1"></span>'
    + '<button class="ghost small act-cancel">取消</button>'
    + '<button class="ghost small act-save">保存方案</button></div>'
    + '<div class="gen-body"><div class="hint">等待生成…</div></div></div>');
}

async function sendTurn() {
  if (!state.conv) { toast('先新建对话', true); return; }
  var text = byId('turn-text').value.trim();
  if (!text) { toast('先说一句要改什么', true); return; }
  var models = selectedModels();
  try {
    var body = { text: text };
    if (models.length) body.model_ids = models;
    var data = await api('POST', '/api/v1/conversations/' + state.conv.id + '/turns', body);
    state.conv = data.conversation;
    renderConvState();
    byId('turn-text').value = '';
    var labels = (data.applied || []).map(function (a) {
      return a.label || (a.field + ': ' + a.from + ' → ' + a.to);
    });
    var tail = (data.unresolved || []).length ? ' ｜未识别：' + data.unresolved.join('、') : '';
    byId('turn-hint').textContent = (labels.length ? '已应用 ' + labels.join('；')
      : (data.summary || '已记录')) + tail;
    if (data.comparisons) {
      data.comparisons.forEach(function (x) { addGenCard(x.generation_id, x.model_id); });
    } else if (data.generation) {
      addGenCard(data.generation.id, data.generation.model_id || models[0] || 'model');
    }
    toast('已按新条件重排');
  } catch (e) { toast(e.message, true); }
}

async function generate() {
  if (!state.conv) { toast('先新建对话', true); return; }
  var models = selectedModels();
  if (!models.length) { toast('至少选一个模型', true); return; }
  try {
    var data = await api('POST', '/api/v1/conversations/' + state.conv.id + '/generations', { model_ids: models });
    var list = [];
    if (data.comparisons) {
      data.comparisons.forEach(function (x) { list.push({ gid: x.generation_id, model: x.model_id }); });
    } else {
      list.push({ gid: data.generation_id || data.id, model: data.model_id || models[0] });
    }
    byId('gen-hint').textContent = '已提交 ' + list.length + ' 个模型，轮询中…';
    list.forEach(function (x) { addGenCard(x.gid, x.model); });
    state.conv = await api('GET', '/api/v1/conversations/' + state.conv.id);
    renderConvState();
  } catch (e) { toast(e.message, true); }
}

function addGenCard(gid, modelId) {
  var card = genCard(modelId);
  card.setAttribute('data-gid', gid);
  card.querySelector('.act-cancel').onclick = async function () {
    try { await api('POST', '/api/v1/generations/' + gid + '/cancel'); toast('已取消'); }
    catch (e) { toast(e.message, true); }
  };
  card.querySelector('.act-save').onclick = function () { openSavePlan(gid, modelId); };
  byId('results').prepend(card);
  poll(gid, card);
}

async function poll(gid, card) {
  var rounds = 0;
  while (rounds < 120) {
    rounds++;
    await new Promise(function (r) { setTimeout(r, 1000); });
    var gen = null;
    try { gen = await api('GET', '/api/v1/generations/' + gid); } catch (e) { return; }
    renderGen(card, gen);
    if (gen.status !== 'pending' && gen.status !== 'running') {
      byId('gen-hint').textContent = '';
      return;
    }
  }
  byId('gen-hint').textContent = '轮询超时（可在 Swagger 里查该 generation）';
}

function badgeFor(status) {
  var cls = status === 'succeeded' ? 'ok' : (status === 'blocked' || status === 'failed' ? 'bad' : (status === 'superseded' || status === 'cancelled' ? 'warn' : ''));
  return '<span class="badge ' + cls + '">' + esc(MODE_LABEL[status] || status) + '</span>';
}

function renderGen(card, gen) {
  card.setAttribute('data-status', gen.status);
  var head = card.querySelector('.gen-head');
  head.innerHTML = '<span class="m">' + esc(card.getAttribute('data-model')) + '</span>'
    + badgeFor(gen.status) + '<span style="flex:1"></span>'
    + '<button class="ghost small act-cancel">取消</button>'
    + '<button class="ghost small act-save">保存方案</button>';
  head.querySelector('.act-cancel').onclick = async function () {
    try { await api('POST', '/api/v1/generations/' + gen.id + '/cancel'); toast('已取消'); } catch (e) { toast(e.message, true); }
  };
  head.querySelector('.act-save').onclick = function () { openSavePlan(gen.id, card.getAttribute('data-model')); };

  var body = card.querySelector('.gen-body');
  var parts = [];
  if (gen.status === 'pending' || gen.status === 'running') {
    parts.push('<div class="hint">模型正在生成…（' + esc(gen.status) + '）</div>');
    body.innerHTML = parts.join('');
    return;
  }
  if (gen.status === 'superseded') {
    parts.push('<div class="notice">这条结果已作废：生成过程中改了条件或又发了一次，旧结果不再使用，也不会覆盖已保存的方案。</div>');
    body.innerHTML = parts.join('');
    return;
  }
  if (gen.error_code) {
    parts.push('<div class="blocked">错误 ' + esc(gen.error_code) + '：' + esc(gen.error_message || '') + '</div>');
  }
  if (gen.status === 'blocked' && gen.block_reason) {
    var br = gen.block_reason;
    parts.push('<div class="blocked"><b>被拦下：</b>' + esc(typeof br === 'string' ? br : JSON.stringify(br)) + '</div>');
  }
  var res = gen.result;
  if (res) {
    if (res.headline) parts.push('<div style="font-weight:600">' + esc(res.headline) + '</div>');
    var items = res.items || [];
    if (items.length) {
      items.forEach(function (it) {
        var cites = (it.citations || []).map(function (c, idx) {
          var cidv = c.citation_id || c.id || '';
          return '<span class="cite" data-cid="' + esc(cidv) + '" data-mat="' + esc(c.material_id) + '">出处 ' + (idx + 1) + '</span>';
        }).join('');
        parts.push('<div class="item-row"><div class="t">' + esc(it.title || '')
          + (it.must_keep ? '<span class="badge warn">必做</span>' : '')
          + (it.minutes ? '<span class="badge">' + esc(it.minutes) + ' 分钟</span>' : '')
          + '</div><div class="d">' + esc(it.detail || '') + '</div><div>' + cites + '</div></div>');
      });
    }
    var tl = res.timeline;
    if (tl && (tl.start || tl.leave || tl.end)) {
      var seg = [];
      if (tl.start) seg.push('出发 ' + tl.start);
      if (tl.leave) seg.push('离开 ' + tl.leave);
      if (tl.end) seg.push('到达 ' + tl.end);
      parts.push('<div class="oked">时间线：' + esc(seg.join(' → ')) + '</div>');
    }
    if (res.budget) parts.push('<div class="small-title">费用</div><div class="pre">' + esc(JSON.stringify(res.budget, null, 2)) + '</div>');
    if ((res.pending_facts || []).length) {
      parts.push('<div class="notice">待确认：' + esc((res.pending_facts || []).join('；')) + '</div>');
    }
    var v = gen.validation;
    if (v) {
      var checks = v.checks || [];
      var bad = checks.filter(function (c) { return c.ok === false && c.level !== 'notice'; });
      var warn = checks.filter(function (c) { return c.level === 'notice'; });
      var head = '程序复核（' + checks.length + ' 项 · 通过 ' + (checks.length - bad.length - warn.length) + ' · 提示 ' + warn.length + ' · 未通过 ' + bad.length + '）';
      parts.push('<div class="small-title">' + esc(head) + '</div><div class="checks">');
      var rowsHtml = checks.map(function (c) {
        var mark = c.ok === false ? '✘' : (c.level === 'notice' ? '⚠' : '✔');
        var detail = c.detail === undefined || c.detail === null ? '' : (typeof c.detail === 'string' ? c.detail : JSON.stringify(c.detail));
        return '<div class="check-row"><span class="c">' + mark + ' ' + esc(c.code) + '</span><span>' + esc(detail.slice(0, 220)) + '</span></div>';
      }).join('');
      parts.push(rowsHtml + '</div>');
      (v.notices || []).forEach(function (n) {
        var ntext = n.message || (n.detail ? (typeof n.detail === 'string' ? n.detail : JSON.stringify(n.detail)) : n.code);
        parts.push('<div class="notice">提示：' + esc(ntext) + '</div>');
      });
      (v.corrections || []).forEach(function (c) {
        var ctext = c.code || JSON.stringify(c);
        if (c.material) ctext += '（' + c.material + (c.to !== undefined ? ' → ' + c.to : '') + '）';
        parts.push('<div class="notice">已自动修正：' + esc(ctext) + '</div>');
      });
    }
    var known = { headline: 1, items: 1, timeline: 1, budget: 1, pending_facts: 1, task_type: 1, materials_needed: 1, private_note: 1, rationale: 1 };
    var extra = {};
    Object.keys(res).forEach(function (k) { if (!known[k]) extra[k] = res[k]; });
    if (Object.keys(extra).length) {
      parts.push('<details><summary class="small-title">其它字段</summary><div class="pre">' + esc(JSON.stringify(extra, null, 2)) + '</div></details>');
    }
  }
  body.innerHTML = parts.join('');
  var cites = body.querySelectorAll('.cite');
  for (var i = 0; i < cites.length; i++) {
    cites[i].onclick = function (ev) {
      var node = ev.currentTarget;
      showCitation(node.getAttribute('data-cid'), node.getAttribute('data-mat'), gen.id);
    };
  }
}

function citationBlock(c) {
  return '<div class="item-row"><div class="t">' + esc(c.material_title || '') + '</div>'
    + '<div class="d">' + esc(c.excerpt_snapshot || '') + '</div>'
    + '<div class="card-meta">' + esc([c.source_file, c.source_page ? '第 ' + c.source_page + ' 页' : ''].filter(Boolean).join(' · '))
    + (c.jump_url ? ' · <a href="' + esc(c.jump_url) + '" target="_blank" rel="noopener">打开原链接</a>' : '')
    + '</div></div>';
}

async function showCitation(cid, mid, gid) {
  try {
    if (cid) {
      var one = await api('GET', '/api/v1/citations/' + cid);
      openModal('出处 · ' + (one.material_title || ''), citationBlock(one));
      return;
    }
    var data = await api('GET', '/api/v1/generations/' + gid + '/citations');
    var hits = (data.items || []).filter(function (x) { return x.material_id === mid; });
    openModal('出处', hits.length ? hits.map(citationBlock).join('') : '<div class="hint">这条引用没有快照</div>');
  } catch (e) { toast(e.message, true); }
}

/* ---------- 保存方案 ---------- */
function resultToText(res) {
  if (!res) return '';
  var lines = [];
  if (res.headline) lines.push('# ' + res.headline);
  (res.items || []).forEach(function (it, i) {
    lines.push('');
    lines.push((i + 1) + '. ' + (it.title || '') + (it.minutes ? '（' + it.minutes + ' 分钟）' : ''));
    if (it.detail) lines.push('   ' + it.detail);
  });
  if (res.timeline) {
    var t = res.timeline;
    lines.push('');
    lines.push('时间线：' + [t.start ? '出发 ' + t.start : '', t.leave ? '离开 ' + t.leave : '', t.end ? '到达 ' + t.end : ''].filter(Boolean).join(' → '));
  }
  return lines.join('\n');
}

async function openSavePlan(gid, modelId) {
  if (!state.conv) { toast('没有对话', true); return; }
  var gen = null;
  try { gen = await api('GET', '/api/v1/generations/' + gid); } catch (e) { toast(e.message, true); return; }
  var res = gen.result || {};
  openModal('保存方案', ''
    + '<label>标题<input id="sp-title" value="' + esc(res.headline || (TASK_LABEL[state.conv.task_type] || '我的方案')) + '"></label>'
    + '<label>正文（可编辑）<textarea id="sp-body" rows="10">' + esc(resultToText(res)) + '</textarea></label>'
    + '<label>私人备注（公开版不含）<input id="sp-note" placeholder="比如：这道题还是没懂"></label>'
    + '<label>选择理由（公开版不含）<input id="sp-why" placeholder="为什么这么安排"></label>'
    + '<div class="hint">同一对话重复保存会更新原来那条，不会多出一份。</div>'
    + '<div class="row"><button class="primary" id="sp-save">保存</button></div>');
  byId('sp-save').onclick = async function () {
    try {
      var body = {
        conversation_id: state.conv.id, generation_id: gid,
        title: byId('sp-title').value.trim() || '我的方案',
        body: byId('sp-body').value,
        body_private_note: byId('sp-note').value.trim() || null,
        rationale: byId('sp-why').value.trim() || null,
        confirmed_conditions: state.conv.conditions || {}
      };
      var out = await api('POST', '/api/v1/plans', body);
      closeModal();
      toast('已保存（版本 v' + out.version + '）');
      await loadPlans();
    } catch (e) {
      toast(e.message, true);
      if (e.code === 'STALE_GENERATION') toast('这条结果已过期：条件改过了，请用最新一次生成保存', true);
    }
  };
}

/* ---------- 方案 ---------- */
async function loadPlans() {
  var data = await api('GET', '/api/v1/plans?limit=50');
  state.plans = data.items || [];
  renderPlans();
}

function renderPlans() {
  var box = byId('plan-list');
  var filter = byId('plan-q').value.trim();
  var rows = state.plans.filter(function (p) { return !filter || (p.title || '').indexOf(filter) >= 0; });
  if (!rows.length) { box.innerHTML = '<div class="hint">还没有保存的方案。</div>'; return; }
  box.innerHTML = '';
  rows.forEach(function (p) {
    var card = el('<div class="card">'
      + '<div class="card-head"><div style="flex:1">'
      + '<div class="t">' + esc(p.title) + '<span class="badge">' + esc(TASK_LABEL[p.task_type] || p.task_type) + '</span><span class="badge">v' + esc(p.version) + '</span></div>'
      + '<div class="card-meta">更新于 ' + esc((p.updated_at || '').replace('T', ' ').slice(0, 19)) + ' · 出处 ' + (p.citation_ids || []).length + ' 条</div>'
      + '</div><div class="card-actions">'
      + '<button class="ghost small act-open">打开</button>'
      + '<button class="ghost small act-copy">复制</button>'
      + '<button class="ghost small danger act-del">删除</button>'
      + '</div></div></div>');
    card.querySelector('.act-open').onclick = function () { openPlanDetail(p.id); };
    card.querySelector('.act-copy').onclick = function () { copyPlan(p.id); };
    card.querySelector('.act-del').onclick = async function () {
      if (!confirm('删除方案「' + p.title + '」？')) return;
      try { await api('DELETE', '/api/v1/plans/' + p.id); toast('已删除'); await loadPlans(); }
      catch (e) { toast(e.message, true); }
    };
    box.appendChild(card);
  });
}

async function openPlanDetail(pid) {
  try {
    var p = await api('GET', '/api/v1/plans/' + pid);
    var cites = [];
    try { cites = (await api('GET', '/api/v1/plans/' + pid + '/citations')).items || []; } catch (e) {}
    var citeHtml = cites.map(function (c) {
      return '<div class="item-row"><div class="t">' + esc(c.material_title || '') + '</div>'
        + '<div class="d">' + esc(c.excerpt_snapshot || '') + '</div>'
        + '<div class="card-meta">' + esc([c.source_file, c.source_page ? '第 ' + c.source_page + ' 页' : '', c.jump_url ? '可跳转链接' : ''].filter(Boolean).join(' · ')) + '</div></div>';
    }).join('');
    openModal(p.title, ''
      + '<div class="hint">' + esc(TASK_LABEL[p.task_type] || p.task_type) + ' · v' + esc(p.version) + ' · 更新于 ' + esc((p.updated_at || '').slice(0, 19).replace('T', ' ')) + '</div>'
      + '<div class="pre" style="max-height:none;white-space:pre-wrap">' + esc(p.body) + '</div>'
      + (p.body_private_note ? '<div class="notice">私人备注（仅自己可见）：' + esc(p.body_private_note) + '</div>' : '')
      + (p.rationale ? '<div class="notice">选择理由（仅自己可见）：' + esc(p.rationale) + '</div>' : '')
      + '<div class="small-title">出处</div>' + (citeHtml || '<div class="hint">无</div>')
      + '<div class="row"><button class="primary" id="pd-copy">复制公开版</button></div>');
    byId('pd-copy').onclick = function () { copyPlan(pid); };
  } catch (e) { toast(e.message, true); }
}

function legacyCopy(text) {
  try {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:-1000px;left:0;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    var ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return !!ok;
  } catch (e) { return false; }
}

async function copyPlan(pid) {
  try {
    var out = await api('POST', '/api/v1/plans/' + pid + '/copy');
    var text = (out.title ? out.title + '\n\n' : '') + (out.text || '');
    var ok = false;
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); ok = true; } catch (e) { ok = false; }
    }
    if (!ok) ok = legacyCopy(text);
    openModal('复制内容（公开版，已去掉私人备注与选择理由）',
      '<div class="pre" style="max-height:none;white-space:pre-wrap" id="copy-text">' + esc(text) + '</div>'
      + '<div class="hint">' + (ok ? '已复制到剪贴板。' : '浏览器不让自动复制，请手动全选上面的文字。') + '</div>');
    if (!ok) {
      var node = byId('copy-text');
      var range = document.createRange(); range.selectNodeContents(node);
      var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
    }
  } catch (e) { toast(e.message, true); }
}

/* ---------- 启动 ---------- */
async function boot() {
  await loadWho();
  await loadModels();
  await loadTopics();
  await loadMaterials();
  await loadPlans();
}

function switchTab(name) {
  var tabs = document.querySelectorAll('.tab');
  for (var i = 0; i < tabs.length; i++) {
    tabs[i].classList.toggle('active', tabs[i].getAttribute('data-tab') === name);
  }
  ['materials', 'chat', 'plans'].forEach(function (t) {
    byId('tab-' + t).classList.toggle('hidden', t !== name);
  });
  if (name === 'plans') loadPlans();
  if (name === 'materials') { loadMaterials(); loadTopics(); }
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
}

function bindTabs() {
  var tabs = document.querySelectorAll('.tab');
  for (var i = 0; i < tabs.length; i++) {
    tabs[i].onclick = function (ev) {
      switchTab(ev.currentTarget.getAttribute('data-tab'));
    };
  }
}

function bindMain() {
  byId('mat-form').onsubmit = submitMaterial;
  byId('mat-file').onchange = onFilePicked;

byId('mat-dl').onclick = function () {
  if (!window.__lastMd) return;
  var blob = new Blob([window.__lastMd], { type: 'text/markdown;charset=utf-8' });
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (window.__lastMdName || '材料') + '.md';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
};
  byId('mat-reset').onclick = resetMaterialForm;
  byId('mat-refresh').onclick = function () { loadMaterials(); loadTopics(); };
  byId('mat-q').onkeydown = function (e) { if (e.key === 'Enter') loadMaterials(); };
  byId('mat-topic').onchange = loadMaterials;
  byId('mat-send').onclick = function () {
    if (!state.selMaterials.length) { toast('先在列表里勾选材料', true); return; }
    var tabs = document.querySelectorAll('.tab');
    tabs[1].click();
    byId('goal').focus();
    toast('已带入 ' + state.selMaterials.length + ' 条材料');
  };
  byId('task-type').onchange = setTemplates;
  byId('btn-start').onclick = startConv;
  byId('btn-confirm').onclick = confirmMaterials;
  byId('btn-generate').onclick = generate;
  byId('btn-turn').onclick = sendTurn;
  byId('btn-reset-conv').onclick = function () {
    state.conv = null; state.candidates = []; state.gens = {};
    byId('conv-box').classList.add('hidden');
    byId('results').innerHTML = '';
    renderConvState();
    toast('已清空当前对话');
  };
  byId('btn-clear-results').onclick = function () { byId('results').innerHTML = ''; };
  byId('plan-refresh').onclick = loadPlans;
  byId('plan-q').oninput = renderPlans;
  byId('modal-close').onclick = closeModal;
  byId('modal').onclick = function (e) { if (e.target === byId('modal')) closeModal(); };
}

function init() {
  setTemplates();
  bindTabs();
  bindAuth();
  bindMain();
  renderConvState();
  var hash = (location.hash || '').replace('#', '');
  if (hash === 'chat' || hash === 'plans' || hash === 'materials') switchTab(hash);
  if ((location.search || '').indexOf('demo=1') >= 0) setTimeout(runDemo, 600);
  boot().catch(function (e) {
    byId('who').textContent = '连不上后端：' + e.message;
    toast('连不上后端：' + e.message, true);
  });
}

async function runDemo() {
  try {
    toast('演示模式：自动走一遍流程…');
    var f = byId('mat-form');
    f.title.value = '高数课件 第3章 极限';
    f.topic.value = '高数';
    f.excerpt.value = '极限的四则运算法则：若 lim f = A，lim g = B，则 lim(f+g) = A+B，lim(f·g) = A·B。第 8 页例题：先判断极限存在，再计算极限值。';
    f.source_kind.value = 'courseware';
    f.source_file.value = '高数课件.pdf';
    f.source_page_start.value = '8';
    f.source_page_end.value = '12';
    f.note.value = '老师强调会考';
    await submitMaterial({ preventDefault: function () {} });
    await loadMaterials();
    if (state.materials.length) {
      state.selMaterials = [state.materials[0].id];
      renderMaterials();
    }
    byId('task-type').value = 'review';
    setTemplates();
    byId('conditions').value = JSON.stringify({ duration_minutes: 15 }, null, 2);
    byId('goal').value = '帮我用 15 分钟复习极限，先讲例题';
    if (location.hash !== '#plans') switchTab('chat');
    await startConv();
    await confirmMaterials();
    await generate();
    var gens = [];
    for (var k = 0; k < 40; k++) {
      await new Promise(function (r) { setTimeout(r, 500); });
      var list = await api('GET', '/api/v1/conversations/' + state.conv.id + '/generations');
      gens = (list && (list.items || list)) || [];
      if (gens.length && (gens[0].status === 'succeeded' || gens[0].status === 'blocked')) break;
    }
    var saved = false;
    if (gens.length && gens[0].id) {
      try {
        var full = await api('GET', '/api/v1/generations/' + gens[0].id);
        await api('POST', '/api/v1/plans', {
          conversation_id: state.conv.id, generation_id: gens[0].id,
          title: '复习方案 · 15 分钟极限', body: resultToText((full && full.result) || {}),
          body_private_note: '例题那步还是有点绕', rationale: '按课件第 8 页顺序排',
          confirmed_conditions: state.conv.conditions || {}
        });
        await loadPlans();
        saved = true;
      } catch (e) { window.__demoErr = (e && e.message) || String(e); }
    }
    toast('演示：gen=' + (gens[0] ? gens[0].status : 'none') + (saved ? ' · 已保存' : ' · 未保存') + (window.__demoErr ? ' · ' + window.__demoErr : ''));
  } catch (e) { toast('演示失败：' + e.message, true); }
}

document.addEventListener('DOMContentLoaded', init);
