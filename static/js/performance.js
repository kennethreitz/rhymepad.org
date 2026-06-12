// performance.js — the booth: synthesized beats + the rap engine.
// Carved verbatim from core; the rhyme analysis conducts the voice.
import { editor, render, analysis, karaokeSpan, setKaraoke } from './core.js';

/* ============================================================
   BEATS — Web Audio synthesized drum patterns, adjustable tempo.
   No external samples; everything is generated.
============================================================ */
// bass: [step, semitone offset, length in 16ths]
const PATTERNS = {
  'Boom Bap':   {bpm:90,  kick:[0,6,8],        snare:[4,12], hat:[0,2,4,6,8,10,12,14], swing:0.12,
                 bass:[[0,0,2],[6,0,1],[8,0,2],[14,3,1]]},
  'Trap':       {bpm:140, kick:[0,3,7,8,11],   snare:[8],    hat:[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15], swing:0,
                 clap:true, bass:[[0,0,3],[7,-2,1],[8,0,3],[11,3,2]]},
  'Lo-Fi':      {bpm:75,  kick:[0,8],          snare:[4,12], hat:[2,6,10,14], swing:0.18,
                 ohat:[10], bass:[[0,0,3],[8,5,3],[12,3,2]]},
  'Drill':      {bpm:142, kick:[0,3,6,10,11],  snare:[4,12,13], hat:[0,2,4,6,8,10,12,14], swing:0.05,
                 bass:[[0,0,2],[3,1,2],[6,-2,2],[10,0,1],[11,3,2]]},
  'Spoken/Jazz':{bpm:96,  kick:[0,10],         snare:[4,12], hat:[0,3,6,9,12,15], swing:0.2,
                 ohat:[14], bass:[[0,0,2],[4,7,2],[8,5,2],[12,3,2]]},
  'West Coast': {bpm:94,  kick:[0,6,8,14],     snare:[4,12], hat:[0,2,4,6,8,10,12,14], swing:0.08,
                 bass:[[0,0,2],[6,0,1],[8,-4,2],[14,0,1]]},
  'Halftime':   {bpm:74,  kick:[0,10],         snare:[8],    hat:[0,2,4,6,8,10,12,14], swing:0.1,
                 clap:true, ohat:[6], bass:[[0,0,6],[10,-2,4]]},
  'Dembow':     {bpm:96,  kick:[0,4,8,12],     snare:[3,6,11,14], hat:[0,2,4,6,8,10,12,14], swing:0,
                 bass:[[0,0,3],[6,0,1],[8,0,3],[14,0,1]]},
  'G-Funk':     {bpm:92,  kick:[0,4,7,8],      snare:[4,12], hat:[0,2,4,6,8,10,12,14], swing:0.1,
                 ohat:[2,10], bass:[[0,0,3],[4,-5,2],[8,0,2],[12,-2,3]]},
  'Memphis':    {bpm:134, kick:[0,2,8,10,13],  snare:[4,12], hat:[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15], swing:0,
                 bass:[[0,0,2],[2,0,1],[8,-2,2],[13,1,2]]},
  'Jersey Club':{bpm:138, kick:[0,3,6,8,11,14],snare:[4,12], hat:[2,6,10,14], swing:0,
                 clap:true, bass:[[0,0,2],[6,0,1],[8,5,2],[14,3,1]]},
  'Grime':      {bpm:140, kick:[0,6,10],       snare:[8],    hat:[0,4,8,12], swing:0,
                 ohat:[14], bass:[[0,0,3],[6,-4,2],[10,2,3]]},
  'Electro':    {bpm:108, kick:[0,4,8,12],     snare:[4,12], hat:[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15], swing:0,
                 clap:true, bass:[[0,0,1],[4,0,1],[8,7,1],[12,5,1]]},
  'Afrobeats':  {bpm:104, kick:[0,3,8,11],     snare:[4,7,12], hat:[2,5,6,10,13,14], swing:0.06,
                 bass:[[0,0,2],[3,5,2],[8,3,2],[11,0,2]]},
  'Cypher':     {bpm:98,  kick:[0,7,8,10],     snare:[4,12], hat:[0,2,4,5,6,8,10,12,14,15], swing:0.14,
                 bass:[[0,0,2],[7,3,1],[8,0,2],[10,-2,1],[12,5,2]]},
  'Double Time':{bpm:160, kick:[0,6,8,14],     snare:[4,12], hat:[0,2,4,6,8,10,12,14], swing:0,
                 bass:[[0,0,3],[6,0,1],[8,-2,3],[14,0,1]]},
  'Four-Floor': {bpm:122, kick:[0,4,8,12],     snare:[4,12], hat:[2,6,10,14], swing:0,
                 ohat:[2,6,10,14], bass:[[0,0,2],[4,0,2],[8,5,2],[12,7,2]]},
  'Bounce':     {bpm:100, kick:[0,3,6,8,11],   snare:[4,12], hat:[0,2,4,6,8,10,12,14], swing:0.08,
                 clap:true, bass:[[0,0,2],[3,0,1],[6,5,1],[8,0,2],[11,3,2]]}
};

let actx=null, masterGain=null, playing=false, schedTimer=null, current='Boom Bap', step=0, nextTime=0;
let userTempo = 90;

const beatGrid = document.getElementById('beatGrid');
Object.entries(PATTERNS).forEach(([name,p])=>{
  const d=document.createElement('div'); d.className='beat'; d.dataset.name=name;
  d.innerHTML = `<div class="name">${name}</div><div class="bpm">${p.bpm} BPM feel</div>`;
  d.addEventListener('click', ()=>{
    current=name;
    document.getElementById('tempo').value = p.bpm;
    setTempo(p.bpm);
    if(!playing) startBeat();
    document.querySelectorAll('.beat').forEach(b=>b.classList.toggle('playing', b.dataset.name===name));
  });
  beatGrid.appendChild(d);
});

const tempoEl = document.getElementById('tempo'), tempoVal=document.getElementById('tempoVal');
function setTempo(v){ userTempo=+v; tempoEl.value=v; tempoVal.textContent = v+' BPM'; }
tempoEl.addEventListener('input', e=> setTempo(e.target.value));

function ensureCtx(){
  if(!actx){
    actx = new (window.AudioContext||window.webkitAudioContext)();
    masterGain = actx.createGain();
    masterGain.connect(actx.destination);
    setVol(volEl.value);
  }
}
const volEl = document.getElementById('vol'), volVal = document.getElementById('volVal');
function setVol(v){
  volVal.textContent = v + '%';
  if(masterGain) masterGain.gain.value = Math.pow(v / 100, 2);  // perceptual
}
volEl.addEventListener('input', e=> setVol(e.target.value));

function kick(t){
  const o=actx.createOscillator(), g=actx.createGain();
  o.frequency.setValueAtTime(150,t); o.frequency.exponentialRampToValueAtTime(50,t+0.12);
  g.gain.setValueAtTime(0.9,t); g.gain.exponentialRampToValueAtTime(0.001,t+0.18);
  o.connect(g).connect(masterGain); o.start(t); o.stop(t+0.2);
}
function snare(t){
  const noise=actx.createBufferSource();
  const buf=actx.createBuffer(1, actx.sampleRate*0.2, actx.sampleRate);
  const d=buf.getChannelData(0); for(let i=0;i<d.length;i++) d[i]=Math.random()*2-1;
  noise.buffer=buf;
  const f=actx.createBiquadFilter(); f.type='highpass'; f.frequency.value=1500;
  const g=actx.createGain(); g.gain.setValueAtTime(0.6,t); g.gain.exponentialRampToValueAtTime(0.001,t+0.18);
  noise.connect(f).connect(g).connect(masterGain); noise.start(t); noise.stop(t+0.2);
}
function hat(t){
  const noise=actx.createBufferSource();
  const buf=actx.createBuffer(1, actx.sampleRate*0.05, actx.sampleRate);
  const d=buf.getChannelData(0); for(let i=0;i<d.length;i++) d[i]=Math.random()*2-1;
  noise.buffer=buf;
  const f=actx.createBiquadFilter(); f.type='highpass'; f.frequency.value=7000;
  const g=actx.createGain();
  g.gain.setValueAtTime(0.2 + Math.random()*0.12, t);  // humanized
  g.gain.exponentialRampToValueAtTime(0.001,t+0.05);
  noise.connect(f).connect(g).connect(masterGain); noise.start(t); noise.stop(t+0.06);
}
function ohat(t){
  const noise=actx.createBufferSource();
  const buf=actx.createBuffer(1, actx.sampleRate*0.3, actx.sampleRate);
  const d=buf.getChannelData(0); for(let i=0;i<d.length;i++) d[i]=Math.random()*2-1;
  noise.buffer=buf;
  const f=actx.createBiquadFilter(); f.type='highpass'; f.frequency.value=6000;
  const g=actx.createGain(); g.gain.setValueAtTime(0.16,t); g.gain.exponentialRampToValueAtTime(0.001,t+0.28);
  noise.connect(f).connect(g).connect(masterGain); noise.start(t); noise.stop(t+0.3);
}
function clap(t){
  for(let k=0;k<3;k++){
    const n=actx.createBufferSource();
    const buf=actx.createBuffer(1, actx.sampleRate*0.08, actx.sampleRate);
    const d=buf.getChannelData(0); for(let i=0;i<d.length;i++) d[i]=Math.random()*2-1;
    n.buffer=buf;
    const f=actx.createBiquadFilter(); f.type='bandpass'; f.frequency.value=1500; f.Q.value=1.2;
    const g=actx.createGain(); const tt=t+k*0.012;
    g.gain.setValueAtTime(0.3,tt); g.gain.exponentialRampToValueAtTime(0.001,tt+0.09);
    n.connect(f).connect(g).connect(masterGain); n.start(tt); n.stop(tt+0.12);
  }
}
function bass(t, semi, lenSteps, secPer16){
  const o=actx.createOscillator(), g=actx.createGain();
  o.type='sine';
  const f0 = 55 * Math.pow(2, semi/12);
  o.frequency.setValueAtTime(f0*2, t);
  o.frequency.exponentialRampToValueAtTime(f0, t+0.035);
  const len = Math.max(0.12, lenSteps * secPer16 * 0.95);
  g.gain.setValueAtTime(0.5, t);
  g.gain.exponentialRampToValueAtTime(0.001, t+len);
  o.connect(g).connect(masterGain); o.start(t); o.stop(t+len+0.05);
}

function scheduler(){
  const p=PATTERNS[current];
  const secPer16 = (60/userTempo)/4;
  while(nextTime < actx.currentTime + 0.1){
    const s = step % 16;
    const bar = Math.floor(step / 16);
    const swing = (s%2===1) ? secPer16 * (p.swing||0) : 0;
    const t = nextTime + swing;
    // fills: a two-hit pickup closes every 4th bar; a full snare roll
    // (with the hats dropped out) closes every 8th
    const bigFill = bar % 8 === 7 && s >= 12;
    const lilFill = bar % 4 === 3 && (s === 14 || s === 15);
    if(p.kick.includes(s) && !bigFill) kick(t);
    if((p.snare.includes(s) && !bigFill) || bigFill || lilFill){
      snare(t); if(p.clap && p.snare.includes(s)) clap(t);
    }
    if(p.hat.includes(s) && !bigFill) hat(t);
    if(p.ohat && p.ohat.includes(s) && !bigFill) ohat(t);
    if(p.bass) p.bass.forEach(([bs, semi, len])=>{ if(bs === s) bass(t, semi, len, secPer16); });
    if(s % 4 === 0){
      const beat = s / 4;
      setTimeout(()=>flashBeat(beat), Math.max(0, (t - actx.currentTime) * 1000));
    }
    nextTime += secPer16;
    step++;
  }
  schedTimer = setTimeout(scheduler, 25);
}
const beatInd = document.getElementById('beatInd');
function flashBeat(n){
  if(!playing) return;
  [...beatInd.children].forEach((d, i)=> d.classList.toggle('on', i === n));
}
const taps = [];
document.getElementById('tapBtn').addEventListener('click', ()=>{
  const now = performance.now();
  if(taps.length && now - taps[taps.length - 1] > 2000) taps.length = 0;
  taps.push(now);
  if(taps.length >= 2){
    const iv = (taps[taps.length - 1] - taps[0]) / (taps.length - 1);
    setTempo(Math.max(60, Math.min(180, Math.round(60000 / iv))));
  }
});

function startBeat(){
  ensureCtx(); if(actx.state==='suspended') actx.resume();
  if(playing) return;
  playing=true; step=0; nextTime=actx.currentTime+0.05; scheduler();
  document.querySelectorAll('.beat').forEach(b=>b.classList.toggle('playing', b.dataset.name===current));
  document.getElementById('playBeat').textContent='Playing…';
}
function stopBeat(){
  playing=false; clearTimeout(schedTimer);
  [...beatInd.children].forEach(d=>d.classList.remove('on'));
  document.querySelectorAll('.beat').forEach(b=>b.classList.remove('playing'));
  document.getElementById('playBeat').textContent='Play';
}
document.getElementById('playBeat').addEventListener('click', startBeat);
document.getElementById('stopBeat').addEventListener('click', ()=>{ stopBeat(); stopRap(); });

/* ---- Rap it: speechSynthesis flows the draft over the beat ----------
   One lyric line per bar. Each line's speech rate is fitted from its
   syllable count so it lands inside its bar — sparse bars drawl,
   dense bars hustle. It will absolutely sound like a robot. That is
   the charm. */
let rapTimers = [];
let rapping = false;

const rapBtn = document.getElementById('rapBtn');

function rapVoice(){
  const vs = speechSynthesis.getVoices().filter(v=>v.lang.startsWith('en'));
  return vs.find(v=>/Aaron|Daniel|Samantha|Google US/i.test(v.name)) || vs[0] || null;
}
function hypeVoice(main){
  // the hype man is the same voice as the MC — a different voice was
  // too jarring; the pitch offset alone reads as the second presence
  return main;
}
let voiceCal = 0;  // measured syllables/sec at rate 1, per voice
let voiceLat = 0;  // measured speak()->sound latency, per voice (sec)
function calibrateVoice(voice){
  const key = 'rhymepad.cal.' + (voice ? voice.name : 'default');
  const lkey = 'rhymepad.lat.' + (voice ? voice.name : 'default');
  const saved = parseFloat(localStorage.getItem(key));
  voiceLat = parseFloat(localStorage.getItem(lkey)) || 0;
  if(saved > 1 && saved < 12){
    voiceCal = saved;
    if(voiceLat) return;  // both measured — nothing to do
  }
  // a silent 12-syllable phrase, timed — every platform speaks at its
  // own pace, so we measure instead of guessing. The speak->onstart
  // gap is the engine's spin-up; we pre-subtract it from every cue.
  const u = new SpeechSynthesisUtterance('one two three four five six seven');
  if(voice) u.voice = voice;
  u.volume = 0; u.rate = 1;
  const tSpeak = performance.now();
  let t0 = 0;
  u.onstart = ()=>{
    t0 = performance.now();
    const lat = (t0 - tSpeak) / 1000;
    if(lat > 0.005 && lat < 2){
      voiceLat = lat;
      localStorage.setItem(lkey, lat.toFixed(3));
    }
  };
  u.onend = ()=>{
    if(!t0) return;
    const sec = (performance.now() - t0) / 1000;
    if(sec > 0.5 && sec < 8){
      voiceCal = 8 / sec;
      localStorage.setItem(key, voiceCal.toFixed(2));
    }
  };
  speechSynthesis.speak(u);
}

function stopRap(){
  rapping = false;
  rapTimers.forEach(clearTimeout); rapTimers = [];
  try{ speechSynthesis.cancel(); }catch(e){}
  rapBtn.innerHTML = 'Rap it <span class="beta">beta</span>';
  if(karaokeSpan){ setKaraoke(null); render(); }
}

function startRap(){
  if(rapping){ stopRap(); return; }
  let lines = editor.value.split('\n')
    .map((raw, i)=>({raw: raw.trim(), i}))
    .filter(l=>l.raw && !/^[#\[]/.test(l.raw));
  // a selection raps just the selected lines; otherwise the caret is
  // the starting point (parked at the very start/end = whole draft)
  if(editor.selectionStart !== editor.selectionEnd){
    const a = editor.value.slice(0, editor.selectionStart).split('\n').length - 1;
    const b = editor.value.slice(0, editor.selectionEnd).split('\n').length - 1;
    const sel = lines.filter(l=>l.i >= a && l.i <= b);
    if(sel.length) lines = sel;
  }else if(editor.selectionStart > 0 && editor.selectionStart < editor.value.length){
    const c = editor.value.slice(0, editor.selectionStart).split('\n').length - 1;
    const from = lines.filter(l=>l.i >= c);
    if(from.length && from.length < lines.length) lines = from;
  }
  if(!lines.length) return;
  const bpm = userTempo || 90;
  const barSec = (60 / bpm) * 4;
  // come in on the one: a one-bar count-in on a cold start, otherwise
  // the next downbeat (padded so there's always at least half a bar)
  let lead;
  let coldStart = !playing;
  if(coldStart){
    startBeat();
    lead = barSec + 0.05;
  }else{
    const secPer16 = (60 / bpm) / 4;
    lead = (nextTime - actx.currentTime) + (((16 - (step % 16)) % 16) * secPer16);
    if(lead < barSec * 0.5) lead += barSec;
  }
  rapping = true;
  rapBtn.innerHTML = 'Stop rap';
  const editorLines = editor.value.split('\n');
  const voice = rapVoice();
  const hype = hypeVoice(voice);
  const calibrating = !voiceCal && coldStart;
  if(calibrating){
    calibrateVoice(voice);   // silent; runs once per voice, ever
    lead += barSec;          // give the measurement its own bar
  }
  // section awareness: [Chorus]/[Hook] lines perform hotter
  let _sec = '';
  const sectionOf = {};
  editorLines.forEach((ln, i)=>{
    const m = ln.match(/^\s*\[([^\]]+)/);
    if(m) _sec = m[1].toLowerCase();
    sectionOf[i] = _sec;
  });
  const sylOf = l=>{
    const m = analysis && analysis.lines[l.i] === editor.value.split('\n')[l.i]
      && analysis.meter && analysis.meter.find(x=>x.l===l.i);
    if(m) return m.syl;
    return Math.max(2, (l.raw.match(/[aeiouy]+/gi) || []).length);  // rough
  };
  // flow planning: each line claims one bar or two — whichever lets the
  // voice stay nearest its natural rate. Dense lines go halftime; sparse
  // lines just breathe out the rest of their bar.
  const SYL_PER_SEC = voiceCal || 4.3;  // measured per voice when we can
  const endGroup = {};
  if(analysis){
    analysis.tokens.forEach(tk=>{
      if(analysis.lines[tk.l] !== editorLines[tk.l]) return;
      if(tk.end) endGroup[tk.l] = tk.g;
    });
  }
  let t = 0, prevG = null;
  const plan = lines.map((l, k)=>{
    const syl = sylOf(l);
    const ideal = syl / SYL_PER_SEC;               // seconds at rate 1
    let bars = 1, bestDist = Infinity;
    for(const b of [1, 2]){
      const usable = b * barSec * 7 / 8;           // the breath is real time
      const r = ideal / usable;
      // overspeed is penalized: when torn, rap leans back, never sprints
      const d = Math.abs(Math.log(r)) * (r > 1 ? 1.35 : 1);
      if(d < bestDist){ bestDist = d; bars = b; }
    }
    const at = t;
    t += bars * barSec;
    const endG = endGroup[l.i] != null ? endGroup[l.i] : null;
    const entry = {l, at, syl, slot: bars * barSec, k, endG, prevEndG: prevG};
    prevG = endG;
    return entry;
  });

  const say = (text, at, rate, pitch, lineIdx, charMap, vol, v2)=>{
    // some voices announce "capital I" — lowercase every standalone I
    // (sounds identical), normalize curly quotes, and lowercase any
    // lone-capital utterance
    text = text.replace(/[\u2018\u2019]/g, "'").replace(/[\u201C\u201D]/g, '')
               .replace(/\bI\b/g, 'i');
    if(/^[A-Z][^A-Za-z]*$/.test(text.trim())) text = text.toLowerCase();
    rapTimers.push(setTimeout(()=>{
      if(!rapping) return;
      const u = new SpeechSynthesisUtterance(text);
      const vv = v2 || voice;
      if(vv) u.voice = vv;
      u.rate = rate; u.pitch = pitch;
      if(vol != null) u.volume = vol;
      if(lineIdx != null && charMap){
        u.onboundary = e=>{
          if(e.name && e.name !== 'word') return;
          if(!rapping) return;
          const rest = text.slice(e.charIndex);
          const m = rest.match(/[A-Za-z'\u2019-]+/);
          if(!m) return;
          const a = e.charIndex + m.index, b = a + m[0].length;
          if(charMap[a] == null || charMap[b - 1] == null) return;
          setKaraoke({l: lineIdx, s: charMap[a], e: charMap[b - 1] + 1});
          render();
        };
      }
      speechSynthesis.speak(u);
    }, Math.max(0, (at - voiceLat) * 1000)));
  };

  plan.forEach(p=>{ p.at += lead; });
  plan.forEach(p=>{
    // breath chunks: split at punctuation, onsets quantized to the 8th-
    // note grid, the bar's last 8th left empty to breathe, and the final
    // chunk of every line dropping pitch — the cadence
    // chunk the ORIGINAL line and keep a stripped-char -> editor-column
    // map per chunk, so speech boundary events can light the live word
    const lineText = editorLines[p.l.i] || '';
    const base = lineText.length - lineText.trimStart().length;
    const rawChunks = p.l.raw.split(/(?<=[,;:.!?\u2014\u2013])\s+/)
      .map(s=>s.trim()).filter(Boolean);
    let cursor = 0;
    const chunks = rawChunks.map(rc=>{
      const off = p.l.raw.indexOf(rc, cursor);
      cursor = off + rc.length;
      let text = '', map = [], hypeText = '', depth = 0;
      for(let q = 0; q < rc.length; q++){
        const c = rc[q];
        if(c === '('){ depth++; continue; }
        if(c === ')'){ depth = Math.max(0, depth - 1); hypeText += ' '; continue; }
        if(depth > 0){ hypeText += c; continue; }  // the hype man's lines
        if('[]*_#"'.includes(c)) continue;
        map.push(base + off + q);
        text += c;
      }
      return {text: text.trim(), map, line: p.l.i,
              hype: hypeText.trim()};
    }).filter(c=>c.text || c.hype);
    if(!chunks.length) return;

    const inChorus = /chorus|hook/.test(sectionOf[p.l.i] || '');
    const GRID = barSec / 8;
    const lastLine = p.k === plan.length - 1;

    // clusters only APPORTION the line's true syllable count across
    // chunks — silent e's can't inflate the speed that way
    const cs = chunks.map(c=>({
      c, w: Math.max(1, ((c.text || c.hype).match(/[aeiouy]+/gi) || []).length)}));
    const wtotal = cs.reduce((a, b)=>a + b.w, 0);
    const usable = p.slot * 7 / 8;  // the breath is real time
    // the energy arc: pitch climbs gently through each 4-bar phrase;
    // chorus/hook sections perform a step hotter
    const basePitch = 0.88 + (p.k % 4) * 0.018 + (inChorus ? 0.06 : 0);
    // couplet cadence from the engine's own rhyme families: the first
    // line of a rhymed pair stays suspended, its answer resolves low
    let cadence = 0.78;
    const resolving = p.endG != null && p.prevEndG != null && p.endG === p.prevEndG;
    if(p.endG != null && p.prevEndG != null)
      cadence = resolving ? 0.7 : 0.86;
    if(lastLine) cadence = 0.66;  // stick the landing
    let off = 0;
    cs.forEach((ch, j)=>{
      const isLast = j === cs.length - 1;
      const share = usable * ch.w / wtotal;
      const at = p.at + Math.round(off / GRID) * GRID;  // lock to the 8th grid
      const rate = Math.min(1.3, Math.max(0.7,
        chSylRate(p.syl, ch.w, wtotal, share) * (lastLine ? 0.9 : 1)));
      // declination: punch the bar's entry, settle chunk by chunk into
      // the cadence — the natural downhill of a spoken line
      const pitch = isLast ? cadence
        : Math.max(0.78, basePitch + 0.015 - j * 0.01);
      if(ch.c.text)
        say(ch.c.text, at, rate, pitch, ch.c.line, ch.c.map);
      // the hype man answers with the line's (parenthetical) — in the
      // gap after the main vocal, or owning the slot on a full-paren line
      if(ch.c.hype)
        say(ch.c.hype, ch.c.text ? at + share * 0.85 : at, 1.05, 0.85,
            null, null, 0.75, hype);
      off += share;
    });
  });
  if(coldStart){  // the count-in — after the calibration bar, if any
    const yoAt = (calibrating ? barSec : 0) + 0.4;
    say('yo', yoAt, 1, 0.85, null, null, 0.6, hype);
    say('yo', yoAt + barSec / 2, 1, 0.85, null, null, 0.6, hype);
  }
  rapTimers.push(setTimeout(stopRap, (lead + t + barSec * 0.5) * 1000));

  function chSylRate(syl, w, wt, share){ return (syl * w / wt) / SYL_PER_SEC / share; }
}
rapBtn.addEventListener('click', startRap);
if('speechSynthesis' in window) speechSynthesis.getVoices();  // warm the list
else rapBtn.hidden = true;

export { stopRap, startBeat, stopBeat };
