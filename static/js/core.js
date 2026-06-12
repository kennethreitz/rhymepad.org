import { stopRap } from './performance.js';

/* ============================================================
   ANALYSIS — real phonetic rhyme detection lives in the Python
   backend (CMU pronouncing dictionary). We POST the draft and
   get back colored token spans:
     end rhymes      -> strong tint
     internal rhymes -> soft tint
     gray tint       -> an ending no rhyme answers yet
============================================================ */
const editor = document.getElementById('editor');
const highlight = document.getElementById('highlight');
const stresslayer = document.getElementById('stresslayer');
const gutter = document.getElementById('gutter');

const stressToggle = document.getElementById('stressToggle');
stressToggle.checked = false;
stressToggle.addEventListener('change', ()=>{
  document.querySelector('.editor-shell').classList.toggle('rhythm', stressToggle.checked);
  render();
});
const allitToggle = document.getElementById('allitToggle');
allitToggle.checked = false;
allitToggle.addEventListener('change', render);
const rhymeToggle = document.getElementById('rhymeToggle');
rhymeToggle.checked = true;
rhymeToggle.addEventListener('change', ()=>{
  if(!rhymeToggle.checked && ghost){ ghost = null; closeGhostMenu(); }
  render();
});
const schemeReadout = document.getElementById('schemeReadout');
const COLORS = 16;  // matches --r0..--r15 above and COLORS in app.py

function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function debounce(fn, ms){ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; }

let analysis = null;      // last server response
let karaokeSpan = null;   // {l, s, e} — the word being rapped right now
function setKaraoke(v){ karaokeSpan = v; }
let backendOk = true;
let focusAllit = null;    // alliteration group under the caret
let activeFam = null;     // the rhyme family currently emphasized (hover or caret)
let hoverGid = null;      // family under the mouse (hover preview)
function gidAtPoint(x, y){
  let off = null;
  if(document.caretPositionFromPoint){
    const c = document.caretPositionFromPoint(x, y);
    if(c && c.offsetNode) off = c.offset;
  }else if(document.caretRangeFromPoint){
    const r = document.caretRangeFromPoint(x, y);
    if(r) off = r.startOffset;
  }
  if(off == null) return null;
  const before = editor.value.slice(0, off);
  const ln = before.split('\n').length - 1;
  const col = off - (before.lastIndexOf('\n') + 1);
  let best = null;
  (analysis ? analysis.tokens : []).forEach(t=>{
    if(t.l === ln && !t.ph && t.s <= col && col < t.e){
      if(!best || (t.e - t.s) < (best.e - best.s)) best = t;
    }
  });
  return best ? best.g : null;
}

function caretGid(){
  if(!analysis || editor.selectionStart !== editor.selectionEnd) return null;
  const pos = editor.selectionStart;
  const before = editor.value.slice(0, pos);
  const ln = before.split('\n').length - 1;
  const col = pos - (before.lastIndexOf('\n') + 1);
  const line = editor.value.split('\n')[ln];
  if(!analysis.lines || analysis.lines[ln] !== line) return null;
  let best = null;
  analysis.tokens.forEach(t=>{
    if(t.l === ln && t.s <= col && col < t.e){
      if(!best || (t.e - t.s) < (best.e - best.s)) best = t;
    }
  });
  return best ? best.g : null;
}

function caretAllit(){
  if(!analysis || !analysis.allit || !allitToggle.checked) return null;
  if(editor.selectionStart !== editor.selectionEnd) return null;
  const pos = editor.selectionStart;
  const before = editor.value.slice(0, pos);
  const ln = before.split('\n').length - 1;
  const col = pos - (before.lastIndexOf('\n') + 1);
  const line = editor.value.split('\n')[ln];
  if(!analysis.lines || analysis.lines[ln] !== line) return null;
  const hit = analysis.allit.find(t=>t.l===ln && t.s<=col && col<t.e);
  return hit ? hit.g : null;
}

function updateSpotlight(){
  focusAllit = caretAllit();
  setEmphasis(caretGid());
}
let analyzeSeq = 0;       // guards against out-of-order responses

const SAMPLE_TEXT =
`I keep the cadence tucked beneath my tongue tonight
the city hums in amber under fading light
I trace a melody that never quite takes flight
and let the silence answer everything I write

I put my orange
four-inch
door hinge
in storage,
and ate porridge with George`;

/* ---------- drafts — multiple docs in localStorage, tabbed ---------- */
const DOCS_KEY = 'rhymepad.docs';
const docKey = id => 'rhymepad.doc.' + id;
const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

function loadDocs(){
  try{
    const s = JSON.parse(localStorage.getItem(DOCS_KEY));
    if(s && s.docs && s.docs.length && s.docs.some(d=>d.id===s.current)) return s;
  }catch(e){}
  // migrate the single-draft era; a brand-new visitor starts with the
  // sample so the colors are visible before they type a word
  const id = newId();
  const text = localStorage.getItem('rhymepad.draft') ?? SAMPLE_TEXT;
  localStorage.setItem(docKey(id), text);
  const s = {docs: [{id, title: titleOf(text)}], current: id};
  localStorage.setItem(DOCS_KEY, JSON.stringify(s));
  return s;
}
function titleOf(text){
  const l = (text || '').split('\n').find(l=>l.trim());
  if(!l) return 'Untitled';
  const t = l.trim();
  return t.length > 26 ? t.slice(0, 25) + '…' : t;
}
function saveDocs(){
  try{ localStorage.setItem(DOCS_KEY, JSON.stringify(docsState)); }catch(e){}
}
let docsState = loadDocs();
editor.value = localStorage.getItem(docKey(docsState.current)) || '';

function persist(){
  try{
    localStorage.setItem(docKey(docsState.current), editor.value);
    const doc = docsState.docs.find(d=>d.id===docsState.current);
    const title = titleOf(editor.value);
    if(doc && doc.title !== title){
      doc.title = title;
      const el = draftsBar.querySelector('.dtab.active .dtitle');
      if(el) el.textContent = title;
    }
    document.title = (title && title !== 'Untitled')
      ? title + ' · RhymePad' : 'RhymePad — rhyme scheme analyzer & writing pad for poets and rappers';
    saveDocs();
  }catch(e){ /* storage full/blocked */ }
}

const draftsBar = document.getElementById('draftsBar');
function renderTabs(){
  draftsBar.innerHTML = '';
  docsState.docs.forEach(doc=>{
    const tab = document.createElement('div');
    tab.className = 'dtab' + (doc.id === docsState.current ? ' active' : '');
    const title = document.createElement('span');
    title.className = 'dtitle';
    title.textContent = doc.title || 'Untitled';
    tab.appendChild(title);
    if(doc.id === docsState.current && docsState.docs.length > 1){
      const x = document.createElement('span');
      x.className = 'x'; x.textContent = '×'; x.title = 'Delete this draft';
      x.addEventListener('click', e=>{ e.stopPropagation(); deleteDoc(doc.id); });
      tab.appendChild(x);
    }
    tab.addEventListener('click', ()=>{ if(doc.id !== docsState.current) openDoc(doc.id); });
    draftsBar.appendChild(tab);
  });
  const plus = document.createElement('div');
  plus.className = 'dtab new'; plus.textContent = '+'; plus.title = 'New draft';
  plus.addEventListener('click', newDoc);
  draftsBar.appendChild(plus);
}
function openDoc(id){
  // remember where we were in the draft we're leaving
  const cur = docsState.docs.find(d=>d.id===docsState.current);
  if(cur){ cur.sel = editor.selectionStart; cur.scroll = editor.scrollTop; }
  docsState.current = id;
  saveDocs();
  editor.value = localStorage.getItem(docKey(id)) || '';
  analysis = null;
  // scorched earth on every transient layer — no paint from the old
  // draft can survive the transition
  if(typeof stopRap === 'function') stopRap();
  if(typeof closeGhostMenu === 'function') closeGhostMenu();
  if(typeof ghost !== 'undefined') ghost = null;
  if(typeof karaokeSpan !== 'undefined') karaokeSpan = null;
  if(typeof activeFam !== 'undefined' && activeFam !== null) setEmphasis(null);
  highlight.innerHTML = '';
  stresslayer.innerHTML = '';
  gutter.innerHTML = '';
  renderTabs();
  render(); analyze(); editor.focus();
  if(typeof linkSoon === 'function') linkSoon();
  if(typeof syncSampleChip === 'function') syncSampleChip();
  const doc = docsState.docs.find(d=>d.id===id);
  if(doc && doc.sel != null){
    editor.setSelectionRange(doc.sel, doc.sel);
    editor.scrollTop = doc.scroll || 0;
    highlight.scrollTop = editor.scrollTop;
  }
}
function newDoc(){
  const id = newId();
  docsState.docs.push({id, title: 'Untitled'});
  localStorage.setItem(docKey(id), '');
  openDoc(id);
}
function deleteDoc(id){
  const doc = docsState.docs.find(d=>d.id===id);
  const text = localStorage.getItem(docKey(id)) || '';
  if(text.trim() && !confirm(`Delete “${doc.title}”?`)) return;
  try{ if(text.trim()) localStorage.setItem('rhymepad.trash', text); }catch(e){}
  localStorage.removeItem(docKey(id));
  docsState.docs = docsState.docs.filter(d=>d.id!==id);
  if(!docsState.docs.length){
    const nid = newId();
    docsState.docs = [{id: nid, title: 'Untitled'}];
    localStorage.setItem(docKey(nid), '');
  }
  openDoc(docsState.docs[0].id);
}
renderTabs();

async function analyze(){
  const text = editor.value;
  const seq = ++analyzeSeq;
  try{
    const r = await fetch('/api/analyze', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text})
    });
    if(seq !== analyzeSeq) return;  // a newer request superseded us
    analysis = await r.json();
    backendOk = true;
    if(typeof ghostSoon === 'function') ghostSoon();  // meter is fresh: re-rank
  }catch(e){
    if(seq !== analyzeSeq) return;
    backendOk = false;
  }
  render();
}
const analyzeSoon = debounce(analyze, 180);

function render(){
  persist();
  const lines = editor.value.split('\n');
  const tokByLine = {};
  const allitByLine = {};
  const openByLine = {};
  const nearByLine = {};
  const groupInfo = {};
  if(analysis){
    analysis.groups.forEach(g=>{ groupInfo[g.id] = g; });
    analysis.tokens.forEach(t=>{ (tokByLine[t.l] ||= []).push(t); });
    if(analysis.allit) analysis.allit.forEach(t=>{ (allitByLine[t.l] ||= []).push(t); });
    if(analysis.open) analysis.open.forEach(t=>{ (openByLine[t.l] ||= []).push(t); });
    if(analysis.near) analysis.near.forEach(t=>{ (nearByLine[t.l] ||= []).push(t); });
  }
  const colorOf = t => `var(--r${groupInfo[t.g].color % COLORS})`;
  let html = '';
  lines.forEach((line, i)=>{
    // apply spans where the line still matches what the server analyzed;
    // on an edited line, keep highlights over the unchanged prefix so
    // typing at the end of a line doesn't blank it
    const fresh = analysis && analysis.lines[i] === line;
    let raw = [];
    if(fresh){
      raw = tokByLine[i] || [];
    }else if(analysis && typeof analysis.lines[i] === 'string'){
      const old = analysis.lines[i];
      let cp = 0;
      const n = Math.min(old.length, line.length);
      while(cp < n && old[cp] === line[cp]) cp++;
      raw = (tokByLine[i] || []).filter(t=>t.e <= cp);
    }
    const toks = rhymeToggle.checked ? raw.filter(t=>groupInfo[t.g]) : [];
    const words = toks.filter(t=>!t.ph);
    const phrases = toks.filter(t=>t.ph);
    const als = (allitToggle.checked && fresh) ? (allitByLine[i] || []) : [];
    const opens = [];
    const kj = (karaokeSpan && karaokeSpan.l === i) ? karaokeSpan : null;
    const nears = (rhymeToggle.checked && fresh) ? (nearByLine[i] || []) : [];
    let h = '';
    if(!toks.length && !als.length && !opens.length){
      h = esc(line);
    }else{
      const cuts = new Set([0, line.length]);
      toks.forEach(t=>{ cuts.add(t.s); cuts.add(t.e); if(t.rs != null) cuts.add(t.rs); });
      als.forEach(t=>{ cuts.add(t.s); cuts.add(t.e); });
      opens.forEach(t=>{ cuts.add(t.s); cuts.add(t.e); });
      nears.forEach(t=>{ cuts.add(t.s); cuts.add(t.e); });
      if(kj){ cuts.add(kj.s); cuts.add(kj.e); }
      const pts = [...cuts].sort((a,b)=>a-b);
      for(let k = 0; k < pts.length - 1; k++){
        const a = pts[k], b = pts[k+1];
        const text = esc(line.slice(a, b));
        if(!text) continue;
        const w = words.find(t=>t.s <= a && b <= t.e);
        const p = phrases.find(t=>t.s <= a && b <= t.e);
        const al = als.find(t=>t.s <= a && b <= t.e);
        const op = opens.find(t=>t.s <= a && b <= t.e);
        const nr = nears.find(t=>t.s <= a && b <= t.e);
        const kk = kj && kj.s <= a && b <= kj.e;
        if(!w && !p && !al && !op && !nr && !kk){ h += text; continue; }
        // whole word fills dimly; the rhyming part gets a bright underline.
        // gray = an ending still waiting for its answer
        let style = '';
        let tk = '', dg = '';
        const shadows = [];
        if(w || p){
          const t = w || p;
          let alpha = !t.ph ? (t.end ? 34 : 19) : (t.end ? 24 : 14);
          const str = t.str != null ? t.str : ((groupInfo[t.g] && groupInfo[t.g].strength) || 1);
          alpha = Math.round(alpha * (0.4 + 0.6 * str));  // brightness = this word's rhyme strength
          dg = ` data-g="${t.g}"`;
          style += `background:color-mix(in srgb, ${colorOf(t)} ${alpha}%, transparent);`;
          if(w){ tk = ` data-tk="${i}:${w.s}"`; if(w.rs == null || a >= w.rs) tk += ` data-tl="${i}:${w.s}"`; }
          // the whole rhyming word takes a quiet tint of the family color
          const mix = Math.round(28 + 32 * str);
          style += `color:color-mix(in srgb, ${colorOf(t)} ${mix}%, var(--ink));`;
        }
        if(al) shadows.push(`inset 0 -2px 0 0 color-mix(in srgb, var(--r${al.g % COLORS}) 75%, transparent)`);
        if(nr) style += 'text-decoration:underline dotted color-mix(in srgb, var(--accent-2) 60%, transparent);text-underline-offset:3px;';
        if(shadows.length) style += `box-shadow:${shadows.join(',')};`;
        if(kk) style += 'background:var(--accent);color:#1a120c;font-weight:600;';
        h += `<span class="hseg"${tk}${dg} style="${style}">${text}</span>`;
      }
    }
    const lcls = 'lmark' + (/^\s*#/.test(line) ? ' hdr' : /^\s*\[/.test(line) ? ' anno' : '');
    if(ghost && ghost.line === i && ghost.base === line){
      const gc = ghost.cands[ghost.sel] || {};
      const disp = gc.comp
        ? ghost.text.slice(ghost.partial.length)
        : (line.endsWith(' ') ? '' : ' ') + ghost.text;
      h += `<span class="ghost">${esc(disp)}` +
        `${gc.fit ? '<span class="ghost-fit"> ✦</span>' : ''}</span><span class="ghost-key">tab</span>`;
    }
    html += (line ? `<span class="${lcls}" data-l="${i}">${h}</span>` : '') + '\n';
  });
  highlight.innerHTML = html;
  if(typeof syncSampleChip === 'function') syncSampleChip();
  if(activeFam != null)
    highlight.querySelectorAll(`.hseg[data-g="${activeFam}"]`).forEach(s=>{
      s.classList.add('lit');
      const lm = s.closest('.lmark'); if(lm) lm.classList.add('litline');
    });
  renderStress(lines);
  syncLayerHeights();
  renderGutter();
  highlight.scrollTop = editor.scrollTop;
  highlight.scrollLeft = editor.scrollLeft;
  buildReadout();
}

function spineFor(memberLines, color, cls){
  const marks = [...highlight.querySelectorAll('.lmark')]
    .filter(m=>memberLines.has(+m.dataset.l));
  if(marks.length < 2) return;
  const off = editor.scrollTop;
  let top = Infinity, bot = -Infinity;
  const centers = [];
  marks.forEach(m=>{
    const t = m.offsetTop - off, h2 = m.offsetHeight;
    top = Math.min(top, t); bot = Math.max(bot, t + h2);
    centers.push(t + h2 / 2);
  });
  const br = document.createElement('div');
  br.className = 'bracket' + (cls ? ' ' + cls : '');
  br.style.top = (top + 5) + 'px';
  br.style.height = (bot - top - 10) + 'px';
  br.style.color = `color-mix(in srgb, ${color} 32%, transparent)`;
  gutter.appendChild(br);
  centers.forEach(c=>{
    const tk = document.createElement('div');
    tk.className = 'tick' + (cls ? ' ' + cls : '');
    tk.style.top = (c - 1.25) + 'px';
    tk.style.background = `color-mix(in srgb, ${color} 45%, transparent)`;
    gutter.appendChild(tk);
  });
}

function renderGutter(){
  gutter.innerHTML = '';
  if(!analysis) return;
  if(activeFam !== null && rhymeToggle.checked){
    const g = analysis.groups.find(x=>x.id === activeFam);
    if(g){
      const ls = new Set();
      analysis.tokens.forEach(t=>{ if(t.g === activeFam) ls.add(t.l); });
      spineFor(ls, `var(--r${g.color % COLORS})`, '');
    }
  }
  if(focusAllit !== null){
    const ls = new Set();
    analysis.allit.forEach(t=>{ if(t.g === focusAllit) ls.add(t.l); });
    spineFor(ls, `var(--r${focusAllit % COLORS})`, 'allit');
  }
}


function syncLayerHeights(){
  [highlight, stresslayer].forEach(l=>{
    let sp = l.querySelector('.lspacer');
    if(!sp){
      sp = document.createElement('div');
      sp.className = 'lspacer';
      l.appendChild(sp);
    }
    sp.style.height = '0px';
    const diff = editor.scrollHeight - l.scrollHeight;
    if(diff > 0) sp.style.height = diff + 'px';
  });
}

function cadenceColors(){
  // exact stress-contour matches (5+ syllables) form a flow family
  const map = {};
  if(!analysis || !analysis.meter) return map;
  const byPat = {};
  analysis.meter.forEach(m=>{
    if(!m.stress || m.stress.length < 5) return;
    // flexible monosyllables (x) read as stressed in delivery, so
    // "x01" and "101" are the same flow
    const key = m.stress.replace(/x/g, '1');
    (byPat[key] ||= []).push(m.l);
  });
  let fid = 0;
  Object.keys(byPat).sort().forEach(pat=>{
    const lns = byPat[pat];
    if(lns.length >= 2){ lns.forEach(l=>{ map[l] = fid % COLORS; }); fid++; }
  });
  return map;
}

function renderStress(lines){
  if(!stressToggle.checked){ stresslayer.innerHTML = ''; return; }
  const byLine = {};
  if(analysis && analysis.stress) analysis.stress.forEach(s=>{ (byLine[s.l] ||= []).push(s); });
  const cmap = cadenceColors();
  let html = '';
  lines.forEach((line, i)=>{
    const fresh = analysis && analysis.lines[i] === line;
    let raw = [];
    if(fresh){ raw = byLine[i] || []; }
    else if(analysis && typeof analysis.lines[i] === 'string'){
      const old = analysis.lines[i]; let cp = 0;
      const n = Math.min(old.length, line.length);
      while(cp < n && old[cp] === line[cp]) cp++;
      raw = (byLine[i] || []).filter(s=>s.e <= cp);
    }
    const spans = raw.slice().sort((a,b)=>a.s-b.s);
    let pos = 0, h2 = '';
    spans.forEach(s=>{
      if(s.s < pos) return;
      h2 += esc(line.slice(pos, s.s));
      const dots = [...s.st].map(c=> c === '0' ? '\u25CB' : '\u25CF').join('');
      const col = cmap[i] != null ? ` style="color:var(--r${cmap[i]})"` : '';
      h2 += `<span class="sw">${esc(line.slice(s.s, s.e))}<span class="sd"${col}>${dots}</span></span>`;
      pos = s.e;
    });
    h2 += esc(line.slice(pos));
    html += h2 + '\n';
  });
  stresslayer.innerHTML = html;
  stresslayer.scrollTop = editor.scrollTop;
  stresslayer.scrollLeft = editor.scrollLeft;
}

function caretLine(){
  return editor.value.slice(0, editor.selectionStart).split('\n').length - 1;
}
function caretStanza(){
  if(!analysis || !analysis.stanzas.length) return null;
  const ln = caretLine();
  return analysis.stanzas.find(s=>s.lines.includes(ln))
      || analysis.stanzas.findLast(s=>s.lines[0] <= ln)
      || analysis.stanzas[0];
}

function buildReadout(){
  // the stats bar is gone — this element only ever speaks up when the
  // engine is unreachable (and hosts the transient "saved" flash)
  schemeReadout.innerHTML = backendOk ? ''
    : '<span class="offline">backend offline — run: uv run uvicorn app:app</span>';
}

const sampleChip = document.getElementById('sampleChip');
const SAMPLE = `# The Tinted Verse

I paint the placement of creation in elation,
a constellation forming in the patience of the nation.

cold smoke, gold rope
a slow boat, a low note
broke, awoke, I wrote the whole quote

the shimmer in the river is a glimmer and a flicker,
the whisper of the scripture is the rigor in the mixture.`;
function syncSampleChip(){ sampleChip.hidden = !!editor.value.trim() || editor.readOnly; }
sampleChip.addEventListener('click', ()=>{
  editor.value = SAMPLE;
  localStorage.setItem(docKey(docsState.current), SAMPLE);
  syncSampleChip();
  render(); analyze(); editor.focus();
});
syncSampleChip();

editor.addEventListener('input', ()=>{
  if(ghost){
    const cur = editor.value.split('\n')[ghost.line];
    if(cur !== ghost.base){ ghost = null; closeGhostMenu(); }
  }
  render(); analyzeSoon(); ghostSoon(); linkSoon();
  syncSampleChip();
});
editor.addEventListener('scroll', ()=>{ highlight.scrollTop = stresslayer.scrollTop = editor.scrollTop; highlight.scrollLeft = stresslayer.scrollLeft = editor.scrollLeft; renderGutter(); });
const editorShell = document.querySelector('.editor-shell');

/* ---- toggle legends: hover a pill, learn the layer ------------------ */
const LEGENDS = {
  rhymeToggle: `
    <h4>Rhyme</h4>
    <p><b style="color:var(--ink)">Same color = same sound.</b> Detection is
    phonetic, not spelling — yes, <i>orange</i> rhymes with <i>door hinge</i>.
    16 hues, assigned so neighboring families never share:</p>
    <div class="lg-pal">${Array.from({length:16},(_,i)=>`<span style="background:var(--r${i})"></span>`).join('')}</div>
    <p><b style="color:var(--ink)">Brightness = strength.</b> Perfect rhymes
    blaze, slant rhymes sit back; within a family the anchors out-glow the
    loose attachments.</p>
    <div class="lg-key">
      <span class="chip" style="background:color-mix(in srgb,var(--r1) 34%,transparent);color:color-mix(in srgb,var(--r1) 55%,var(--ink));box-shadow:inset 0 -1.5px 0 0 color-mix(in srgb,var(--r1) 42%,transparent)">tonight</span>
      <i>perfect rhyme — the underline marks the exact rhyming part</i>
      <span class="chip" style="background:color-mix(in srgb,var(--r1) 14%,transparent);color:color-mix(in srgb,var(--r1) 32%,var(--ink))">mine</span>
      <i>slant rhyme — fainter fill, weaker grip</i>
      <span class="chip" style="text-decoration:underline dotted color-mix(in srgb,var(--accent-2) 70%,transparent);text-underline-offset:3px">bond</span>
      <i>near-miss — one sound from rhyming; a small edit locks it</i>
    </div>
    <p><b style="color:var(--ink)">Hover any word</b> to light its whole
    family. The philosophy throughout: color only what a listener would
    actually hear.</p>`,
  allitToggle: `
    <h4>Alliteration</h4>
    <p>Words sharing a <b style="color:var(--ink)">head sound</b> get an
    underline — fills belong to rhyme (tail sounds), underlines to
    alliteration (front sounds), so the two never collide.</p>
    <div class="lg-demo">
      <span style="box-shadow:inset 0 -2px 0 0 var(--r2)">sipping</span>
      <span style="box-shadow:inset 0 -2px 0 0 var(--r2)">cider</span>
      <span style="box-shadow:inset 0 -2px 0 0 var(--r2)">slow</span>
    </div>
    <p>Runs need three shared onsets — or a tight side-by-side pair
    of bigger words (<i>sordid solutions</i>).</p>`,
  stressToggle: `
    <h4>Rhythm</h4>
    <p>Sheet music for your flow: a dot under every syllable —
    <b style="color:var(--ink)">● stressed, ○ unstressed</b>.</p>
    <div class="lg-demo">placement of creation<br>
    <span style="font-size:9px;letter-spacing:4px">●○&nbsp;&nbsp;○&nbsp;&nbsp;○●○</span></div>
    <p>Lines whose stress patterns echo each other share a dot color —
    a cadence family. Scan the dots to see where your bars match.</p>`,
  suggestToggle: `
    <h4>Suggest</h4>
    <p>Pause at the end of the line you're writing — a real pause, it
    won't interrupt a breath between words — and a quiet ghost offers a
    landing word that answers the stanza's open ending. It knows your
    song: words that echo what you're writing about lead, marked
    <b style="color:var(--accent)">✦</b>, and it keeps the grammar of
    your line. Start typing a word and it narrows to completions.</p>
    <p><kbd>Tab</kbd> opens the candidates (or summons the ghost when
    there isn't one) — syllable dots show which fits the bar, <b>~</b>
    marks slant. <kbd>Enter</kbd> lands it. <kbd>Esc</kbd> means no for
    this line, and it listens. It never types for you; it only
    offers.</p>`,
  exploreToggle: `
    <h4>Explore</h4>
    <p>Hover, for fingers: the pad goes read-only so a tap lights up the
    rhyme family under it — and the keyboard stays out of the way.</p>`,
};
const legendEl = document.createElement('div');
legendEl.id = 'legend';
legendEl.hidden = true;
document.body.appendChild(legendEl);
let legendTimer = 0;
if(window.matchMedia('(hover: hover)').matches){
  document.querySelectorAll('.mtoggle').forEach(lab=>{
    const input = lab.querySelector('input');
    const html = input && LEGENDS[input.id];
    if(!html) return;
    lab.addEventListener('mouseenter', ()=>{
      legendTimer = setTimeout(()=>{
        legendEl.innerHTML = html;
        legendEl.hidden = false;
        legendEl.style.visibility = 'hidden';
        const r = lab.getBoundingClientRect();
        const w = legendEl.offsetWidth, hh = legendEl.offsetHeight;
        let x = Math.min(Math.max(8, r.left + r.width / 2 - w / 2),
                         innerWidth - w - 8);
        let y = r.top - hh - 10;
        if(y < 8) y = r.bottom + 10;
        legendEl.style.left = x + 'px';
        legendEl.style.top = y + 'px';
        legendEl.style.visibility = '';
      }, 350);
    });
    lab.addEventListener('mouseleave', ()=>{
      clearTimeout(legendTimer);
      legendEl.hidden = true;
    });
  });
}
function setEmphasis(g){
  if(!rhymeToggle.checked) g = null;  // no rhyme layer, no focus
  if(g === activeFam) return;
  activeFam = g;
  editorShell.classList.toggle('focusing', g !== null);
  highlight.querySelectorAll('.hseg.lit').forEach(s=>s.classList.remove('lit'));
  highlight.querySelectorAll('.lmark.litline').forEach(s=>s.classList.remove('litline'));
  if(g !== null)
    highlight.querySelectorAll(`.hseg[data-g="${g}"]`).forEach(s=>{
      s.classList.add('lit');
      const lm = s.closest('.lmark'); if(lm) lm.classList.add('litline');
    });
  renderGutter();
  buildReadout();
}
let hoverRAF = 0;
editor.addEventListener('mousemove', e=>{
  if(hoverRAF) return;
  hoverRAF = requestAnimationFrame(()=>{
    hoverRAF = 0;
    const g = gidAtPoint(e.clientX, e.clientY);
    setEmphasis(g != null ? g : caretGid());
  });
});
editor.addEventListener('mouseleave', ()=> setEmphasis(caretGid()));
editor.addEventListener('keyup', ()=>{ updateSpotlight(); buildReadout(); ghostSoon(); });
editor.addEventListener('click', ()=>{
  if(editor.readOnly) return;  // explore mode: pointerdown already handled it
  updateSpotlight(); buildReadout(); ghostSoon();
});

/* ---- explore mode: hover for fingers ---------------------------------
   On touch there's no hover, and tapping the pad summons the keyboard.
   Explore makes the draft read-only so a tap does what the mouse does:
   light up the rhyme family under it, keyboard nowhere in sight. */
const exploreToggle = document.getElementById('exploreToggle');
exploreToggle.addEventListener('change', ()=>{
  editor.readOnly = exploreToggle.checked;
  if(exploreToggle.checked) editor.blur();
  else setEmphasis(caretGid());
});
editor.addEventListener('pointerdown', e=>{
  if(!editor.readOnly) return;
  e.preventDefault();  // no focus, no keyboard — scrolling still works
  setEmphasis(gidAtPoint(e.clientX, e.clientY));
});

// ---- ghost rhyme completion: the pad suggests your landing word ----
let ghost = null;            // {line, base, text}
let ghostDismissed = '';     // line|base the writer waved off with Esc
const ghostCache = {};       // target word -> ranked candidate list
const knownWord = {};        // fragment -> is it a real word (zipf gate)
const suggestToggle = document.getElementById('suggestToggle');
suggestToggle.checked = localStorage.getItem('rhymepad.suggest') !== '0';
suggestToggle.addEventListener('change', ()=>{
  localStorage.setItem('rhymepad.suggest', suggestToggle.checked ? '1' : '0');
  if(!suggestToggle.checked && ghost){ ghost = null; render(); }
});

function caretLineCol(){
  const pos = editor.selectionStart;
  const before = editor.value.slice(0, pos);
  const ln = before.split('\n').length - 1;
  return {ln, col: pos - (before.lastIndexOf('\n') + 1)};
}

function lastWordOf(s){
  const m = s.match(/[A-Za-z']+(?=[^A-Za-z']*$)/);
  return m ? m[0].toLowerCase() : null;
}

// the target: the nearest UNANSWERED ending in this stanza — answer
// the scheme, not blindly the previous line (in ABAB, land the B).
// Fallback when nothing's open: the previous lyric line's ending.
function ghostTarget(lines, ln){
  const openLines = new Set();
  if(analysis) (analysis.open || []).forEach(o=>{
    if(analysis.lines[o.l] === lines[o.l]) openLines.add(o.l);
  });
  let target = null, tline = -1;
  for(let j = ln - 1; j >= 0; j--){
    const prev = lines[j];
    if(!prev.trim()) break;                 // stanza boundary
    if(/^\s*[#\[]/.test(prev)) continue;  // annotations don't end lines
    if(tline < 0){ tline = j; target = lastWordOf(prev); }  // fallback
    if(openLines.has(j)){ tline = j; target = lastWordOf(prev); break; }
  }
  return {target, tline};
}

async function loadGhostCands(target){
  if(target in ghostCache) return;
  ghostCache[target] = null;  // in flight
  try{
    // draft-aware: candidates that echo the song carry a fit
    const r = await fetch('/api/suggest', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({word: target, text: editor.value})});
    const d = await r.json();
    const shape = (w, near, multi)=>({word: w.word.toLowerCase(), syl: w.syl || 1,
      z: w.z || 0, fit: w.fit || null, fitn: w.fitn || 0, pos: w.pos || '', near, multi});
    ghostCache[target] = (d.words || []).map(w=>shape(w, false, false))
      .concat(((d.multis || []).filter(w=>w.word)).map(w=>shape(w, false, true)))
      .concat((d.near || []).map(w=>shape(w, true, false)));
  }catch(e){ delete ghostCache[target]; }
}

// follows-cache: context -> words that commonly come next, for ranking
// candidates that read like language. The trigram tier sees idioms the
// bigram can't: "from time to _time_", not "to _my_".
const followsCache = {};
async function loadFollows(key, prev, prev2){
  if(key in followsCache) return;
  followsCache[key] = null;  // in flight
  try{
    const q = `prev=${encodeURIComponent(prev)}` + (prev2 ? `&prev2=${encodeURIComponent(prev2)}` : '');
    const d = await (await fetch(`/api/follows?${q}`)).json();
    followsCache[key] = {bi: new Set(d.words || []), tri: new Set(d.tri || [])};
  }catch(e){ delete followsCache[key]; }
}

// short fragments that are real words — appends allowed without asking
const SHORT_OK = new Set(('a i ah an am as be by do go ha he hi if in is it ma me ' +
  'my no of oh on or so to uh up us we ya yo').split(' '));

// grammar: after a determiner this slot ENDS the line, so it wants a
// noun — an adjective-only word ("my own…") dangles, a verb-only word
// ("the ignite") is nonsense. After a modal or "to" it wants a verb.
const DET_PREV = new Set(('the a an my your his her its our their this that these ' +
  'those some no every each another').split(' '));
const VERB_PREV = new Set(("to will would can could should must might shall don't " +
  "doesn't didn't won't can't couldn't wouldn't shouldn't gonna wanna gotta lemme").split(' '));
function grammarTier(c, prev){
  if(!prev) return 0;
  const pos = c.pos || '';
  if(DET_PREV.has(prev))
    return pos.includes('n') ? 0 : !pos || pos.includes('a') ? 1 : 2;
  if(VERB_PREV.has(prev)) return !pos ? 1 : (pos.includes('v') ? 0 : 2);
  return 0;
}

let lastType = 0;        // when the writer last touched a key
let forceGhost = false;  // Tab asked for the ghost explicitly
let idleTimer = null;
const GHOST_IDLE = 800;  // a real pause, not a breath between words

async function computeGhost(){
  const clear = ()=>{ if(ghost){ ghost = null; closeGhostMenu(); render(); } };
  if(!suggestToggle.checked || !rhymeToggle.checked || editor.readOnly) return clear();
  if(editor.selectionStart !== editor.selectionEnd) return clear();
  const lines = editor.value.split('\n');
  const {ln, col} = caretLineCol();
  const line = lines[ln] || '';
  if(col !== line.length || /^\s*[#\[]/.test(line)) return clear();
  if(!line.trim()){
    // a fresh line: warm the cache now so the pause costs nothing
    const t = ghostTarget(lines, ln);
    if(t.target) loadGhostCands(t.target);
    return clear();
  }
  const {target, tline} = ghostTarget(lines, ln);
  if(!target) return clear();
  // Esc means no for this line — stay quiet however much they type;
  // Tab can always ask again
  if(!forceGhost && ghostDismissed === ln + '|' + target) return clear();
  if(!(target in ghostCache)){
    await loadGhostCands(target);
    return computeGhost();  // the caret may have moved while we fetched
  }
  const cands = ghostCache[target];
  if(cands === null) return;  // in flight elsewhere — hold, don't flicker
  if(!cands.length) return clear();
  const cur = lastWordOf(line);
  if(cur && (cur === target || cands.some(c=>c.word === cur))) return clear();  // already lands
  // a trailing fragment turns matching candidates into COMPLETIONS:
  // type "fi" and the ghost narrows to fire, inserting just the rest
  const partial = /[A-Za-z']$/.test(line) ? cur : null;
  // skip words already spent as line endings in this draft
  const spent = new Set(lines.map(l=>lastWordOf(l)).filter(Boolean));
  let avail = cands.filter(c=>!spent.has(c.word.split(' ').pop()) && c.word !== cur)
    .map(c=>({...c, comp: !!(partial && c.word.startsWith(partial)
                             && c.word.length > partial.length)}));
  if(!avail.length) return clear();
  // a fragment nothing completes: only append after it if it's a real
  // word — never bolt a suggestion onto half of one ("brai throw")
  if(partial && !avail.some(c=>c.comp)){
    if(partial.length <= 2){
      if(!SHORT_OK.has(partial)) return clear();
    }else{
      if(!(partial in knownWord)){
        try{
          const d = await (await fetch(`/api/zipf?word=${encodeURIComponent(partial)}`)).json();
          knownWord[partial] = (d.zipf || 0) >= 2.3;
        }catch(e){ return; }
        return computeGhost();  // the caret may have moved while we fetched
      }
      if(knownWord[partial] === null) return;  // in flight — hold
      if(!knownWord[partial]) return clear();
    }
  }
  // what comes right before the slot? phrase-rank against it
  const ws = (line.match(/[A-Za-z']+/g) || []).map(w=>w.toLowerCase());
  const at = ws.length - (partial ? 2 : 1);
  const prevWord = at >= 0 ? ws[at] : null;
  const prev2 = at >= 1 ? ws[at - 1] : null;
  const fkey = prevWord ? (prev2 ? prev2 + ' ' : '') + prevWord : null;
  if(fkey && !(fkey in followsCache)){
    await loadFollows(fkey, prevWord, prev2);
    return computeGhost();
  }
  const follows = (fkey && followsCache[fkey]) || {bi: new Set(), tri: new Set()};
  // rank: completions of what's being typed lead, then the more draft
  // words a candidate echoes the higher it sits, the bar breaks ties
  // (when the meter is fresh), slant back-fills
  const fresh = i => analysis && analysis.lines[i] === lines[i] && analysis.meter && analysis.meter[i];
  let gap = 0;
  if(tline >= 0 && fresh(tline) && fresh(ln))
    gap = analysis.meter[tline].syl - analysis.meter[ln].syl;
  // a completion's typed vowels are already counted in this line's bar
  const pv = partial ? (partial.match(/[aeiouy]+/g) || []).length : 0;
  // tier order is the product: completions of what's typed, then
  // grammar (the slot's part of speech), then idiom-completers
  // (trigram — sharp), then song echoes, then rhyme quality. The loose
  // bigram only breaks ties BELOW rhyme quality — "the guy" must never
  // beat a perfect rhyme ("the light").
  avail = avail.map((c, i)=>({c, i,
      p: c.comp ? 0 : 1,
      g: grammarTier(c, prevWord),
      t: follows.tri.has(c.word.split(' ')[0]) ? 0 : 1,
      f: -(c.fitn || 0),
      n: c.near ? 1 : 0,
      b: follows.bi.has(c.word.split(' ')[0]) ? 0 : 1,
      m: gap >= 1 ? Math.abs((c.comp ? c.syl - pv : c.syl) - gap) : 0}))
    .sort((a, b)=>a.p - b.p || a.g - b.g || a.t - b.t || a.n - b.n || a.f - b.f
                || a.b - b.b || a.m - b.m || a.i - b.i)
    .map(x=>x.c);
  avail = avail.slice(0, 8);
  // patience: a brand-new append ghost waits for a real pause — but a
  // completion of what's mid-keystroke helps NOW, and Tab means now
  if(!avail[0].comp && !ghost && !forceGhost){
    const wait = GHOST_IDLE - (Date.now() - lastType);
    if(wait > 0){
      clearTimeout(idleTimer);
      idleTimer = setTimeout(computeGhost, wait + 20);
      return;
    }
  }
  const sig = avail.map(c=>c.word + (c.comp ? '+' : '')).join();
  if(!ghost || ghost.line !== ln || ghost.base !== line || ghost.sig !== sig){
    // steady hands: if the word we're already showing is still a live
    // candidate, keep showing it — don't fidget over rank shuffles
    let sel = 0;
    if(ghost && ghost.line === ln && ghost.text){
      const j = avail.findIndex(c=>c.word === ghost.text);
      if(j > 0) sel = j;
    }
    ghost = {line: ln, base: line, text: avail[sel].word, cands: avail, sel,
             target, partial, sig};
    render();
  }
}

// ---- the Tab dropdown: browse the candidates at the caret ----
let ghostMenu = null;
function closeGhostMenu(){ if(ghostMenu){ ghostMenu.remove(); ghostMenu = null; } }
function acceptGhost(word){
  const pos = editor.selectionStart;
  const line = editor.value.split('\n')[ghost.line] || '';
  const c = ghost.cands.find(x=>x.word === word);
  const insert = c && c.comp
    ? word.slice(ghost.partial.length)            // finish the word
    : (line.endsWith(' ') ? '' : ' ') + word;     // land a new one
  editor.setRangeText(insert, pos, pos, 'end');
  ghost = null; closeGhostMenu();
  render(); analyzeSoon(); buildReadout();
  editor.focus();
}
function openGhostMenu(){
  closeGhostMenu();
  if(!ghost) return;
  ghostMenu = document.createElement('div');
  ghostMenu.className = 'ghost-menu';
  ghost.cands.forEach((c, i)=>{
    const it = document.createElement('div');
    it.className = 'gm-item' + (i === ghost.sel ? ' sel' : '') + (c.near ? ' near' : '');
    if(c.fit) it.title = `echoes \u201c${c.fit}\u201d in your draft`;
    it.innerHTML = `<span>${esc(c.word)}${c.fit ? '<span class="gm-fit"> \u2726</span>' : ''}</span>` +
      `<span class="gm-syl">${'\u00b7'.repeat(Math.min(c.syl, 6))}</span>`;
    it.addEventListener('mousedown', e=>e.preventDefault());  // keep editor focus
    it.addEventListener('click', ()=>acceptGhost(c.word));
    it.addEventListener('mouseenter', ()=>{ ghost.sel = i; ghost.text = c.word; render(); paintGhostMenu(); });
    ghostMenu.appendChild(it);
  });
  editorShell.appendChild(ghostMenu);
  positionGhostMenu();
}
function paintGhostMenu(){
  if(!ghostMenu) return;
  [...ghostMenu.children].forEach((el, i)=>el.classList.toggle('sel', i === ghost.sel));
}
function positionGhostMenu(){
  if(!ghostMenu) return;
  const g = highlight.querySelector('.ghost');
  if(!g){ closeGhostMenu(); return; }
  const sr = editorShell.getBoundingClientRect(), gr = g.getBoundingClientRect();
  let left = gr.left - sr.left, top = gr.bottom - sr.top + 6;
  left = Math.min(left, sr.width - ghostMenu.offsetWidth - 10);
  if(top + ghostMenu.offsetHeight > sr.height - 8)
    top = gr.top - sr.top - ghostMenu.offsetHeight - 6;  // flip above
  ghostMenu.style.left = Math.max(8, left) + 'px';
  ghostMenu.style.top = Math.max(8, top) + 'px';
}
const ghostSoon = debounce(computeGhost, 150);

editor.addEventListener('keydown', e=>{
  if(ghostMenu && ghost){
    if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){
      e.preventDefault();
      const n = ghost.cands.length;
      ghost.sel = (ghost.sel + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
      ghost.text = ghost.cands[ghost.sel].word;
      render(); paintGhostMenu(); positionGhostMenu();
      return;
    }
    if(e.key === 'Enter' || e.key === 'Tab'){
      e.preventDefault(); acceptGhost(ghost.cands[ghost.sel].word); return;
    }
    if(e.key === 'Escape'){ e.preventDefault(); closeGhostMenu(); return; }
    closeGhostMenu();  // any other key: back to writing
    return;
  }
  if(e.key === 'Tab' && ghost){
    const {ln, col} = caretLineCol();
    const line = editor.value.split('\n')[ln] || '';
    if(ln === ghost.line && col === line.length && line === ghost.base){
      e.preventDefault();
      openGhostMenu();
      return;
    }
  }
  if(e.key === 'Tab' && !ghost && suggestToggle.checked && !editor.readOnly){
    // no ghost on screen? Tab asks for one — consent works both ways
    const {ln, col} = caretLineCol();
    const line = editor.value.split('\n')[ln] || '';
    if(col === line.length && line.trim() && !/^\s*[#\[]/.test(line)){
      e.preventDefault();
      ghostDismissed = '';
      forceGhost = true;
      computeGhost().then(()=>{ forceGhost = false; if(ghost) openGhostMenu(); });
      return;
    }
  }
  if(e.key === 'Escape' && ghost){
    ghostDismissed = ghost.line + '|' + (ghost.target || '');
    ghost = null; render();
  }
});
editor.addEventListener('input', ()=>{ lastType = Date.now(); });
editor.addEventListener('scroll', closeGhostMenu);
editor.addEventListener('blur', ()=>setTimeout(closeGhostMenu, 150));

/* ---------- double-click (or touch-select) a word -> look it up ---------- */
function lookupSelection(){
  const sel = editor.value.slice(editor.selectionStart, editor.selectionEnd)
    .trim().replace(/[^A-Za-z']/g,'');
  if(sel && !sel.includes(' ')){
    tab('lookup');
    lookupFor(sel);
  }
}
editor.addEventListener('dblclick', lookupSelection);
if(window.matchMedia('(pointer: coarse)').matches){
  const _lh = document.getElementById('lookupHint');
  if(_lh) _lh.innerHTML = 'tip: <b>select any word</b> in your draft to look it up here';
  // no double-click on touch — long-press word selection does the job
  editor.addEventListener('select', debounce(()=>{
    const len = editor.selectionEnd - editor.selectionStart;
    if(len > 0 && len <= 30) lookupSelection();
  }, 400));
}

/* ---------- insert at cursor ---------- */
/* ============================================================
   WORD ENTRIES — one lookup renders the whole entry: phonetic
   readout, describes, rhymes (+near), synonyms. All of it comes
   from our engine — no third-party calls.
============================================================ */
const lookupInput = document.getElementById('lookupInput');
const resultsBox = document.getElementById('lookupResults');
const lookupScroll = document.getElementById('lookupScroll');
const histBar = document.getElementById('histBar');
const defBox = document.getElementById('defBox');
let curWord = null, lookupSeq = 0, entry = null;
let subMode = 'explore';
const subSeg = document.getElementById('subSeg');
subSeg.addEventListener('click', e=>{
  const b = e.target.closest('button'); if(!b) return;
  subMode = b.dataset.sub;
  [...subSeg.children].forEach(c=>c.classList.toggle('active', c.dataset.sub === subMode));
  paintSections();
});
const wordHistory = [];

lookupInput.addEventListener('focus', ()=> lookupInput.select());
lookupInput.addEventListener('keydown', e=>{ if(e.key === 'Enter') doLookup(); });
lookupInput.addEventListener('input', debounce(()=>{
  const w = lookupInput.value.trim();
  if(w.length >= 2 && /^[A-Za-z' -]+\??$/.test(w)) doLookup();
}, 400));

function lookupFor(word){
  const hint = document.getElementById('lookupHint');
  if(hint) hint.remove();  // the tip has done its job
  lookupInput.value = word;
  doLookup();
}

function renderHistory(){
  const past = wordHistory.filter(w=>w !== curWord).slice(-5).reverse();
  histBar.innerHTML = past.length
    ? '↩ ' + past.map(w=>`<span class="hist" data-w="${esc(w)}">${esc(w)}</span>`).join(' · ')
    : '';
  histBar.querySelectorAll('.hist').forEach(el=>
    el.addEventListener('click', ()=> lookupFor(el.dataset.w)));
}

async function doLookup(){
  const word = lookupInput.value.trim().replace(/\?$/, '').toLowerCase();
  if(!word || word === curWord) return;
  lookupScroll.scrollTop = 0;
  if(curWord){
    const i = wordHistory.indexOf(curWord);
    if(i >= 0) wordHistory.splice(i, 1);
    wordHistory.push(curWord);
    if(wordHistory.length > 8) wordHistory.shift();
  }
  curWord = word;
  renderHistory();
  const seq = ++lookupSeq;
  defBox.innerHTML = `<div class="defhead"><b>${esc(word)}</b></div>` +
    `<div class="muted defphon" id="defPhon">…</div>`;
  resultsBox.innerHTML = '<p class="muted">gathering…</p>';
  // rhymes go draft-aware when there's a draft to be aware of
  const rhymeReq = editor.value.trim()
    ? fetch('/api/suggest', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({word, text: editor.value})})
    : fetch(`/api/lookup?word=${encodeURIComponent(word)}&mode=rhyme`);
  const [info, rhyme, syn, desc, trig] = await Promise.all([
    fetch(`/api/word?word=${encodeURIComponent(word)}`).then(r=>r.json()).catch(()=>null),
    rhymeReq.then(r=>r.json()).catch(()=>null),
    fetch(`/api/lookup?word=${encodeURIComponent(word)}&mode=syn`).then(r=>r.json()).catch(()=>null),
    fetch(`/api/lookup?word=${encodeURIComponent(word)}&mode=desc`).then(r=>r.json()).catch(()=>null),
    fetch(`/api/lookup?word=${encodeURIComponent(word)}&mode=trig`).then(r=>r.json()).catch(()=>null),
  ]);
  if(seq !== lookupSeq) return;
  const el = defBox.querySelector('#defPhon');
  if(el){
    if(info && info.known){
      const dots = [...info.stress].map(s=>s==='1' ? '●' : '○').join('');
      // the rime is a suffix of the phones — color it in place
      const ph = info.phones.toLowerCase(), rime = (info.rime || '').toLowerCase();
      const phHtml = rime && ph.endsWith(rime)
        ? esc(ph.slice(0, ph.length - rime.length)) + `<i>${esc(rime)}</i>`
        : esc(ph);
      el.innerHTML = `/${phHtml}/ · ${info.syl} syl ${dots}` +
        ((info.homophones || []).length ? `<br>sounds like: ${info.homophones.map(esc).join(', ')}` : '');
    }else{
      el.textContent = 'not in the pronunciation dictionary';
    }
  }
  const defs = (info && info.defs) || [];
  if(defs.length){
    const POS = {noun:'n.', verb:'v.', adj:'adj.', adv:'adv.', intj:'interj.',
                 prep:'prep.', conj:'conj.', pron:'pron.', det:'det.', num:'num.'};
    const of = info.def_of ? `<div class="def"><i>→</i> ${esc(info.def_of)}</div>` : '';
    defBox.insertAdjacentHTML('beforeend', '<div class="defs">' + of +
      defs.map(d=>`<div class="def"><i>${POS[d.pos] || esc(d.pos)}</i> ${esc(d.gloss)}</div>`).join('') +
      '</div>');
  }
  entry = {word, rhyme, syn, desc, trig};
  paintSections();
}

function rarity(z){
  if(z == null) return '';
  return z >= 4.6 ? ' common' : (z < 3.4 ? ' rare' : '');
}
function chipHtml(items, cls){
  // items: strings or {word, z, chime}; words already in the draft get
  // a tick, words that also rhyme with the entry chime in gold
  const inDraft = new Set((editor.value.toLowerCase().match(/[a-z']+/g)) || []);
  return '<div class="results">' +
    items.map(d=>{
      const w = d.word || d, r = rarity(d.z);
      const used = inDraft.has(w.toLowerCase()) ? ' indraft' : '';
      const ch = d.chime === 'perfect' ? ' chime' : (d.chime === 'near' ? ' chime soft' : '');
      const fit = d.fit ? ' fits' : '';
      const tip = d.fit ? ` title="echoes “${esc(d.fit)}” in your draft"` : '';
      return `<span class="chip${cls ? ' ' + cls : ''}${r}${used}${ch}${fit}" data-w="${esc(w)}"${tip}>${esc(w)}</span>`;
    }).join('') + '</div>';
}

function paintSections(){
  const e = entry;
  if(!e) return;
  let h = '';
  if(subMode === 'explore'){
    // the meaning side: what replaces it, what it summons, what describes it
    if(e.syn && e.syn.known && e.syn.sections.length){
      h += `<div class="res-label">synonyms</div>`;
      e.syn.sections.forEach(s=>{
        if(s.label !== 'synonyms') h += `<div class="res-label sub">${esc(s.label)}</div>`;
        h += chipHtml(s.words, s.label === 'synonyms' ? '' : 'near');
      });
    }
    if(e.trig && e.trig.known && e.trig.words.length){
      h += `<div class="res-label">associations</div>` + chipHtml(e.trig.words);
    }
    if(e.desc && e.desc.known && e.desc.words.length){
      h += `<div class="res-label">describes</div>` + chipHtml(e.desc.words);
    }
  }else{
    // the sound side: rhymes, near, multis
    if(e.rhyme && e.rhyme.known && (e.rhyme.words.length || (e.rhyme.near || []).length)){
      const bySyl = {};
      e.rhyme.words.forEach(d=>{ (bySyl[d.syl || 0] ||= []).push(d); });
      const onWord = e.rhyme.rhyme_on ? ` — on “${esc(e.rhyme.rhyme_on)}”` : '';
      h += `<div class="res-label">rhymes${onWord}</div>`;
      Object.keys(bySyl).sort((a,b)=>a-b).forEach(k=>{
        h += `<div class="res-label sub">${k == 0 ? '?' : k} syl</div>` + chipHtml(bySyl[k]);
      });
      const near = e.rhyme.near || [];
      if(near.length) h += `<div class="res-label sub">near</div>` + chipHtml(near, 'near');
      const multis = e.rhyme.multis || [];
      if(multis.length) h += `<div class="res-label sub">multis</div>` + chipHtml(multis);
    }
  }
  resultsBox.innerHTML = h || `<p class="muted">nothing here for “${esc(e.word)}”.</p>`;
  lookupScroll.scrollTop = 0;
  resultsBox.querySelectorAll('.chip').forEach(c=>
    c.addEventListener('click', ()=> lookupFor(c.dataset.w)));
}

/* ============================================================
   EXPORT IMAGE — draw the draft with its rhyme colors to a PNG,
   entirely client-side.
============================================================ */
document.getElementById('exportBtn').addEventListener('click', async ()=>{
  if(!editor.value.trim()) return;
  await document.fonts.ready;
  const lines = editor.value.split('\n');
  const css = getComputedStyle(document.documentElement);
  const palette = Array.from({length: COLORS}, (_, i)=>css.getPropertyValue(`--r${i}`).trim());
  const ink = css.getPropertyValue('--ink').trim();
  const bg = css.getPropertyValue('--bg').trim();
  const rhythm = stressToggle.checked && analysis && analysis.stress;
  const S = 2, FS = 16, LH = FS * (rhythm ? 2.35 : 1.9), PAD = 40;
  const font = FS + "px 'Spline Sans Mono', monospace";

  const probe = document.createElement('canvas').getContext('2d');
  probe.font = font;
  const w = Math.ceil(Math.max(220, ...lines.map(l=>probe.measureText(l).width)) + PAD * 2);
  const h = Math.ceil(lines.length * LH + PAD * 2 + 18);
  const canvas = document.createElement('canvas');
  canvas.width = w * S; canvas.height = h * S;
  const x = canvas.getContext('2d');
  x.scale(S, S);
  x.fillStyle = bg; x.fillRect(0, 0, w, h);
  x.font = font; x.textBaseline = 'middle';

  const hexRgb = hex =>{ hex = hex.replace('#',''); if(hex.length===3) hex=hex.split('').map(c=>c+c).join('');
    return [parseInt(hex.slice(0,2),16),parseInt(hex.slice(2,4),16),parseInt(hex.slice(4,6),16)]; };
  const inkRgb = hexRgb(ink), dimRgb = hexRgb(css.getPropertyValue('--ink-dim').trim());
  const mix = (a,b,t)=>`rgb(${a.map((v,i)=>Math.round(v+(b[i]-v)*t)).join(',')})`;
  const groupInfo = {}, tokByLine = {}, openByLine = {}, nearByLine = {};
  if(analysis){
    analysis.groups.forEach(g=>{ groupInfo[g.id] = g; });
    analysis.tokens.forEach(t=>{ (tokByLine[t.l] ||= []).push(t); });
    if(analysis.open) analysis.open.forEach(t=>{ (openByLine[t.l] ||= []).push(t); });
    if(analysis.near) analysis.near.forEach(t=>{ (nearByLine[t.l] ||= []).push(t); });
  }
  const xat = (line, c)=> PAD + x.measureText(line.slice(0, c)).width;
  lines.forEach((line, i)=>{
    const y = PAD + i * LH + LH / 2;
    const fresh = analysis && analysis.lines[i] === line;
    const on = rhymeToggle.checked && fresh;
    const toks = (on ? (tokByLine[i] || []) : []).filter(t=>groupInfo[t.g]);
    const words = toks.filter(t=>!t.ph), phrases = toks.filter(t=>t.ph);
    const nears = on ? (nearByLine[i] || []) : [];
    const palHex = t => palette[groupInfo[t.g].color % COLORS];

    // 1) fills — one rounded rect per token, opacity scaled by strength
    const fillTok = (t, end0)=>{
      let base = !t.ph ? (t.end ? 34 : 19) : (t.end ? 24 : 14);
      const str = t.str != null ? t.str : (groupInfo[t.g].strength || 1);
      x.globalAlpha = (base * (0.4 + 0.6*str)) / 100;
      x.fillStyle = palHex(t);
      x.beginPath();
      x.roundRect(xat(line,t.s) - 2, y - FS*0.72, xat(line,t.e)-xat(line,t.s) + 4, FS*1.42, 4);
      x.fill(); x.globalAlpha = 1;
    };
    phrases.forEach(t=>fillTok(t)); words.forEach(t=>fillTok(t));

    // 2) base text (ink, or dim for headers/annotations)
    const isHdr = /^\s*#/.test(line), isAnno = /^\s*\[/.test(line);
    x.font = isHdr ? '700 ' + font : font;
    x.fillStyle = isHdr ? '#8a7d6c' : isAnno ? '#6a5f52' : ink;
    x.fillText(line, PAD, y);
    x.font = font;

    // 3) tint each rhyming word in its family color, over the ink
    words.forEach(t=>{
      const str = t.str != null ? t.str : (groupInfo[t.g].strength || 1);
      x.fillStyle = mix(inkRgb, hexRgb(palHex(t)), 0.28 + 0.32*str);
      x.fillText(line.slice(t.s, t.e), xat(line,t.s), y);
    });

    // 4) sub-word tail underline (rs..e) per word
    words.forEach(t=>{
      if(t.rs == null) return;
      const str = t.str != null ? t.str : 1;
      x.globalAlpha = 0.42 * str; x.fillStyle = palHex(t);
      x.fillRect(xat(line,t.rs), y + FS*0.62, xat(line,t.e)-xat(line,t.rs), 1.5);
      x.globalAlpha = 1;
    });
    // alliteration underline
    if(allitToggle.checked && fresh){
      (analysis.allit || []).filter(t=>t.l===i).forEach(t=>{
        x.globalAlpha = 0.75; x.fillStyle = palette[t.g % COLORS];
        x.fillRect(xat(line,t.s) - 1, y + FS*0.66, xat(line,t.e)-xat(line,t.s) + 2, 2);
        x.globalAlpha = 1;
      });
    }
    // near-miss: dotted gold underline
    nears.forEach(t=>{
      x.fillStyle = mix(inkRgb, hexRgb(css.getPropertyValue('--accent-2').trim()), 0.6);
      let px = xat(line,t.s), end = xat(line,t.e);
      while(px < end){ x.fillRect(px, y + FS*0.62, 1.6, 1.6); px += 3.5; }
    });

    if(rhythm && fresh){
      const spans = (analysis.stress.filter(s=>s.l===i)).sort((a,b)=>a.s-b.s);
      x.font = "8px 'Spline Sans Mono', monospace";
      x.textAlign = 'center';
      spans.forEach(s=>{
        // measure with the body font for positions
        x.font = font;
        const left = PAD + x.measureText(line.slice(0, s.s)).width;
        const wpx = x.measureText(line.slice(s.s, s.e)).width;
        x.font = "9px 'Spline Sans Mono', monospace";
        const dots = [...s.st].map(c=> c === '0' ? '○' : '●').join(' ');
        x.fillText(dots, left + wpx / 2, y + FS * 0.95);
      });
      x.textAlign = 'left';
      x.font = font;
    }
  });
  x.fillStyle = 'rgba(167,154,137,0.55)';
  x.font = "11px 'Spline Sans Mono', monospace";
  x.textAlign = 'right';
  x.fillText('rhymepad.org', w - 16, h - 16);

  canvas.toBlob(blob=>{
    const doc = docsState.docs.find(d=>d.id===docsState.current);
    const name = ((doc && doc.title && doc.title !== 'Untitled') ? doc.title : 'rhymepad')
      .replace(/[^\w\- ]+/g, '').trim() || 'rhymepad';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name + '.png';
    a.click();
    setTimeout(()=>URL.revokeObjectURL(a.href), 5000);
    flash('exportBtn', 'Exported \u2713');
  });
});

/* ============================================================
   SMART PASTE — typographic quotes break phonetic tokenization
   (don’t -> don + t), and lyrics sites ship cruft lines. Pasted
   text gets normalized and de-crufted on the way in.
============================================================ */
const SECTION_RE = /^\[[^\]]{1,48}\]$/;
function extractLyricBlock(t){
  // a whole-page Genius dump: lyrics live between the first [Section]
  // header and the Embed/About/Comments footer
  const lines = t.split('\n');
  const first = lines.findIndex(l=>SECTION_RE.test(l.trim()));
  if(first < 0) return null;
  let end = -1;
  for(let i = first; i < lines.length; i++){
    const s = lines[i].trim();
    if(/^\d*\s*Embed$/.test(s) || /^About$/.test(s) || /^Comments$/.test(s)){ end = i; break; }
  }
  const head = lines.slice(0, first).join('\n');
  const pageish = /Contributors|Translations|Lyrics/i.test(head);
  if(end < 0 && !pageish) return null;  // just lyrics with [tags] — leave alone
  let block = lines.slice(first, end < 0 ? lines.length : end);
  // recommendation inserts run from "You might also like" to the next [Section]
  const out = [];
  let skipping = false;
  for(const l of block){
    const s = l.trim();
    if(/^You might also like/i.test(s)){ skipping = true; continue; }
    if(skipping){
      if(SECTION_RE.test(s)) skipping = false;
      else continue;
    }
    out.push(l);
  }
  while(out.length && /^\d*$/.test(out[out.length - 1].trim())) out.pop();
  return out.join('\n').trim() + '\n';
}
function extractAZBlock(t){
  // AZLyrics-style page: quoted-title headers up top, a "Thanks to…"
  // credits footer below — the lyrics are what's between
  const lines = t.split('\n');
  const headRe = [/^AZLyrics/i, /^"[^"]+" lyrics$/i, /^.{1,60} Lyrics$/, /^"[^"]+"$/];
  let start = -1;
  for(let i = 0; i < Math.min(lines.length, 10); i++){
    const s = lines[i].trim();
    if(s && headRe.some(r=>r.test(s))) start = i;
  }
  if(start < 0) return null;
  const endRe = [/^Thanks to .+ for (adding|correcting)/i, /^Writer\(s\):/i,
                 /^Lyrics licensed by/i, /^You May Also Like$/i, /^album:/i,
                 /^Submit Lyrics$/i];
  let end = -1;
  for(let i = start + 1; i < lines.length; i++){
    if(endRe.some(r=>r.test(lines[i].trim()))){ end = i; break; }
  }
  if(end < 0) return null;  // no footer evidence — not a page dump
  const block = lines.slice(start + 1, end).join('\n').trim();
  return block ? block + '\n' : null;
}
function extractGoogleBlock(t){
  // Google's knowledge panel: a standalone "Lyrics" header, the verse,
  // then a Source:/Songwriters: footer — search-result noise follows
  const lines = t.split('\n');
  let start = -1;
  for(let i = 0; i < lines.length; i++){
    if(lines[i].trim() === 'Lyrics'){ start = i; break; }
  }
  if(start < 0) return null;
  while(start + 1 < lines.length &&
        (lines[start + 1].trim() === 'Lyrics' || !lines[start + 1].trim())) start++;
  let end = -1;
  for(let i = start + 1; i < lines.length; i++){
    if(/^(Source:\s|Songwriters?:)/i.test(lines[i].trim())){ end = i; break; }
  }
  if(end < 0) return null;
  const block = lines.slice(start + 1, end).join('\n').trim();
  return block ? block + '\n' : null;
}
function cleanPaste(t){
  t = t.replace(/\r\n?/g, '\n')
       .replace(/[\u2028\u2029\u0085\u000B\u000C]/g, '\n')  // Google's lyrics box
       .replace(/[\u2018\u2019\u02BC]/g, "'")
       .replace(/[\u201C\u201D]/g, '"')
       .replace(/\u00A0/g, ' ');
  const extracted = extractLyricBlock(t) || extractGoogleBlock(t) || extractAZBlock(t);
  if(extracted) return extracted;
  const out = [];
  t.split('\n').forEach((l, i)=>{
    const s = l.trim();
    if(i < 4 && /^\d+\s*Contributors/i.test(s)) return;
    if(i < 4 && /Lyrics$/.test(s) && s.length < 60) return;
    if(/^Translations$/i.test(s)) return;
    if(/^You might also like/i.test(s)) return;
    if(/^Source:\s/i.test(s)) return;
    if(/^Songwriters?:/i.test(s)) return;
    if(/^Musixmatch$/i.test(s)) return;
    if(/^\d*\s*Embed$/.test(s)) return;
    out.push(l);
  });
  return out.join('\n');
}
editor.addEventListener('paste', e=>{
  const text = e.clipboardData && e.clipboardData.getData('text/plain');
  if(!text) return;
  const cleaned = cleanPaste(text);
  if(cleaned === text) return;
  e.preventDefault();
  // execCommand keeps the native undo stack intact (setRangeText doesn't)
  editor.focus();
  if(!document.execCommand('insertText', false, cleaned)){
    editor.setRangeText(cleaned, editor.selectionStart, editor.selectionEnd, 'end');
  }
  render(); analyzeSoon();
});
lookupInput.addEventListener('paste', e=>{
  const text = e.clipboardData && e.clipboardData.getData('text/plain');
  if(text && /[\u2018\u2019]/.test(text)){
    e.preventDefault();
    lookupInput.focus();
    const fixed = text.replace(/[\u2018\u2019]/g, "'");
    if(!document.execCommand('insertText', false, fixed)){
      lookupInput.setRangeText(fixed, lookupInput.selectionStart, lookupInput.selectionEnd, 'end');
    }
  }
});

/* ============================================================
   FILES IN, FILES OUT — drafts shouldn't be hostage to localStorage
============================================================ */
// ---- share links: the draft itself, gzipped into the URL fragment ----
async function gzB64(str){
  const cs = new CompressionStream('gzip');
  const blob = new Blob([new TextEncoder().encode(str)]);
  const buf = await new Response(blob.stream().pipeThrough(cs)).arrayBuffer();
  return btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
}
async function gunzB64(b64){
  const bin = atob(b64.replace(/-/g,'+').replace(/_/g,'/'));
  const bytes = Uint8Array.from(bin, c=>c.charCodeAt(0));
  const ds = new DecompressionStream('gzip');
  const buf = await new Response(new Blob([bytes]).stream().pipeThrough(ds)).arrayBuffer();
  return new TextDecoder().decode(buf);
}
async function draftLink(){
  const doc = docsState.docs.find(d=>d.id===docsState.current);
  const payload = JSON.stringify({t: doc ? doc.title : 'Untitled', x: editor.value});
  const enc = await gzB64(payload);
  if(enc.length > 7000) return null;  // past sane URL territory
  return location.origin + '/?d=' + enc;
}
// the link is encoded eagerly in the background, so the click handlers
// stay synchronous — an await before share()/writeText() voids the
// user gesture and the browser refuses the share sheet
let linkCache = null;
const linkSoon = debounce(async ()=>{
  linkCache = editor.value.trim() ? await draftLink() : null;
}, 400);
linkSoon();

const linkBtn = document.getElementById('linkBtn');
linkBtn.addEventListener('click', ()=>{
  if(!editor.value.trim()) return;
  if(!linkCache){ flash('linkBtn', 'Too long for a link'); return; }
  navigator.clipboard.writeText(linkCache)
    .then(()=>flash('linkBtn', 'Link copied \u2713'))
    .catch(()=>flash('linkBtn', 'Copy failed'));
});

const shareBtn = document.getElementById('shareBtn');
if(navigator.share){
  shareBtn.hidden = false;
  shareBtn.addEventListener('click', ()=>{
    if(!editor.value.trim()) return;
    const doc = docsState.docs.find(d=>d.id===docsState.current);
    navigator.share({
      title: (doc && doc.title !== 'Untitled') ? doc.title : 'RhymePad draft',
      text: editor.value,
      ...(linkCache ? {url: linkCache} : {}),
    }).catch(()=>{ /* user closed the sheet */ });
  });
}

// an incoming shared link (?d= — or #d= from older links) becomes a
// fresh draft, never clobbering yours
(async function importShared(){
  const d = new URLSearchParams(location.search).get('d')
         || (location.hash.startsWith('#d=') ? location.hash.slice(3) : null);
  if(!d) return;
  try{
    const {t, x} = JSON.parse(await gunzB64(d));
    if(typeof x !== 'string' || !x.trim()) return;
    newDoc();
    const doc = docsState.docs.find(d=>d.id===docsState.current);
    doc.title = (t && t !== 'Untitled') ? t : 'Shared draft';
    editor.value = x;
    localStorage.setItem(docKey(docsState.current), x);
    saveDocs(); renderTabs();
    render(); analyzeSoon();
  }catch(e){ /* malformed link — ignore */ }
  history.replaceState(null, '', location.pathname);
})();
// drop a .txt (or any text file) anywhere -> it becomes a new draft
document.addEventListener('dragover', e=>{ e.preventDefault(); });
document.addEventListener('drop', async e=>{
  e.preventDefault();
  const files = [...(e.dataTransfer?.files || [])].slice(0, 8);
  for(const f of files){
    if(f.size > 1024 * 1024) continue;
    const text = cleanPaste(await f.text());
    if(!text.trim()) continue;
    const id = newId();
    docsState.docs.push({id, title: titleOf(text)});
    localStorage.setItem(docKey(id), text);
    openDoc(id);
  }
});

/* ============================================================
   COPY / CLEAR / SAMPLE
============================================================ */
document.getElementById('copyBtn').addEventListener('click', async ()=>{
  try{ await navigator.clipboard.writeText(editor.value); flash('copyBtn','Copied!'); }
  catch{ editor.select(); document.execCommand('copy'); flash('copyBtn','Copied!'); }
});
function flash(id,msg){
  const b=document.getElementById(id); const o=b.textContent; b.textContent=msg;
  setTimeout(()=>b.textContent=o,1100);
}

const tab = (name)=>{
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active', t.dataset.tab===name));
  document.getElementById('tab-lookup').style.display = name==='lookup'?'flex':'none';
  document.getElementById('tab-beats').style.display  = name==='beats'?'block':'none';
};
document.querySelectorAll('.tab').forEach(t=> t.addEventListener('click', ()=>tab(t.dataset.tab)));

document.addEventListener('keydown', e=>{
  if(!(e.metaKey || e.ctrlKey)) return;
  if(e.key === 's'){
    e.preventDefault();  // it's already saved — say so
    const prev = schemeReadout.innerHTML;
    schemeReadout.innerHTML = 'saved ✓';
    setTimeout(buildReadout, 900);
  }else if(e.key === 'k'){
    e.preventDefault();
    tab('lookup');
    lookupInput.focus();
  }
});
if(window.matchMedia('(pointer: fine)').matches) editor.focus();

render();
analyze();


export { editor, render, analysis, karaokeSpan, setKaraoke };
