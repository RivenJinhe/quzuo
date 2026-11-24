const fs = require('fs');
const P = 'F:/DSH工作区/design/session-baee0d2c-15f5-4df9-820f-2c2b943df03a/entry.html';
const src = fs.readFileSync(P, 'utf8');
const out = {};
out.bytes = Buffer.byteLength(src, 'utf8');
out.lines = src.split('\n').length;

// script 段提取
const m = src.match(/<script>([\s\S]*?)<\/script>/);
const js = m ? m[1] : '';
const html = src.replace(/<script>[\s\S]*?<\/script>/, '');

// 语法
try { new Function(js); out.syntaxOk = true; } catch (e) { out.syntaxOk = false; out.syntaxErr = e.message; }

// 已定义函数
const defs = new Set();
let mm;
const reFn = /function\s+([A-Za-z_$][\w$]*)\s*\(/g;
while ((mm = reFn.exec(js))) defs.add(mm[1]);
const reVar = /var\s+([A-Za-z_$][\w$]*)\s*=\s*function/g;
while ((mm = reVar.exec(js))) defs.add(mm[1]);

// HTML 中的 onclick/oninput/onchange 调用
const calls = new Set();
const reAttr = /on(?:click|input|change|keydown)="([^"]*)"/g;
while ((mm = reAttr.exec(html))) {
  const body = mm[1];
  const reCall = /([A-Za-z_$][\w$]*)\s*\(/g;
  let c;
  while ((c = reCall.exec(body))) calls.add(c[1]);
}
const builtin = new Set(['toast','go','event','document','this','Number','String','alert','confirm','prompt']);
const missingFns = [...calls].filter(f => !defs.has(f) && !builtin.has(f) && f !== 'if' && f !== 'for');

// HTML 里出现的 id
const ids = new Set();
const reId = /id="([^"]+)"/g;
while ((mm = reId.exec(src))) ids.add(mm[1]);

// JS 里 $('xxx') 和 getElementById('xxx')
const refIds = new Set();
const reGet = /\$\('([A-Za-z0-9_\-]+)'\)/g;
while ((mm = reGet.exec(js))) refIds.add(mm[1]);
const reGid = /getElementById\('([A-Za-z0-9_\-]+)'\)/g;
while ((mm = reGid.exec(js))) refIds.add(mm[1]);
// querySelector('#id')
const reQs = /querySelector(?:All)?\('#([A-Za-z0-9_\-]+)/g;
while ((mm = reQs.exec(js))) refIds.add(mm[1]);

const dynamic = new Set(['pop']);  // 运行时可能由模板生成的
const missingIds = [...refIds].filter(i => !ids.has(i) && !dynamic.has(i));

// section 平衡
out.sections = (src.match(/<section/g)||[]).length;
out.sectionClose = (src.match(/<\/section>/g)||[]).length;
out.divOpen = (src.match(/<div[\s>]/g)||[]).length;
out.divClose = (src.match(/<\/div>/g)||[]).length;

// 屏幕
out.screens = [...src.matchAll(/<section class="screen[^"]*" id="([^"]+)"/g)].map(x=>x[1]);
out.activeScreens = [...src.matchAll(/<section class="screen active[^"]*" id="([^"]+)"/g)].map(x=>x[1]);

// CSS 类存在性抽查
const cssBlock = (src.match(/<style>([\s\S]*?)<\/style>/)||[])[1] || '';
function hasCls(c) { return new RegExp('\\.' + c.replace(/[-]/g,'\\-') + '[\\s,{:>.\\[]').test(cssBlock); }
out.css = {};
['mat','picked','check','mat-more','mat-title','mat-excerpt','mat-foot','badge','meta-dot','plan','plan-top',
 'plan-foot','plan-actions','msg-user','role','ai','acard','cand','switch','off','t','r','item','idx','h','mins','must',
 'd','cite-link','tl','gen-title','checks','ck','pass','warn','block','notice','bad','skel','pop','danger','chip','n',
 'selectbar','show','toast','err','result-body','steps','step','now','done','dropzone','md','empty','empty-ic','seg',
 'btn','primary','quiet','sm','eyebrow','mono','spacer','row','input','select','textarea','field','nav-item','count','tabbar','blk','title','pad'
].forEach(c => { if (!hasCls(c)) out.css[c] = 'MISSING'; });

out.noticeWarnClass = hasCls('warn');
out.missingFns = missingFns;
out.missingIds = missingIds;
out.undefinedCss = Object.keys(out.css);
out.hasOldTrialText = src.includes('试用身份');
out.hasDemoCalc = src.includes('判敛') || src.includes('微积分');
console.log(JSON.stringify(out, null, 1));
