const report = JSON.parse(document.querySelector('#results').textContent);
const $ = id => document.getElementById(id);
const source = $('source'), enhanced = $('enhanced'), seek = $('seek');
let current, switching = 0;
const format = value => Number(value).toFixed(2);
$('run-status').textContent = report.passed ? 'ALL LOCAL CHECKS PASSED' : 'CHECKS NEED ATTENTION';
$('environment').textContent = `${report.model} · ${report.device.toUpperCase()}\nPyTorch ${report.torch} / PyAV ${report.pyav}`;
$('scope').textContent = report.scope;
$('date').textContent = report.created_at.slice(0, 10);
$('temporal').textContent = `Temporal trim routing: ${report.temporal_routing.passed ? 'passed' : 'failed'} using an identity engine substitute. Real SeedVR2 inference: not run. Visual quality still needs human review.`;
$('motion-status').textContent = report.motion.passed ? '7 / 7 FRAMES PRESERVED' : 'MOTION CHECK FAILED';
$('motion-status').classList.toggle('failed', !report.motion.passed);
function pause() { source.pause(); enhanced.pause(); $('play').textContent = 'Play study'; }
function position() {
  if (!current || !Number.isFinite(enhanced.duration)) return;
  seek.max = enhanced.duration;
  seek.value = enhanced.currentTime;
  $('time').textContent = `${format(enhanced.currentTime)} / ${format(enhanced.duration)} s`;
  const wanted = enhanced.currentTime + current.source_start;
  if (!source.seeking && !enhanced.seeking && Math.abs(source.currentTime - wanted) > .02) source.currentTime = wanted;
}
function timeline(item) {
  const start = item.source_start;
  const times = item.input.timestamps.filter(t => t >= start - .0001).slice(0, item.frames).map(t => t - start);
  const total = Math.max(...times, ...item.enhanced.timestamps, .01);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = $('timing'); svg.replaceChildren();
  for (const [label, values, y, color] of [['SOURCE', times, 27, '#73816b'], ['OUTPUT', item.enhanced.timestamps, 66, '#a84d2e']]) {
    const text = document.createElementNS(ns, 'text'); text.textContent = label; text.setAttribute('x', '0'); text.setAttribute('y', y+4); text.setAttribute('font-size', '10'); text.setAttribute('font-family','monospace'); text.setAttribute('fill',color); svg.append(text);
    for (const time of values) {
      const line = document.createElementNS(ns,'line'); const x = 95 + time / total * 885;
      for (const [key,value] of Object.entries({x1:x,x2:x,y1:y-10,y2:y+10,stroke:color,'stroke-width':2})) line.setAttribute(key,value);
      svg.append(line);
    }
  }
}
function select(index) {
  pause(); switching++; current = report.cases[index];
  $('playback-status').textContent = '';
  $('study-number').textContent = `STUDY ${String(index+1).padStart(2,'0')} / ${String(report.cases.length).padStart(2,'0')}`;
  $('study-title').textContent = current.title; $('study-description').textContent = current.description;
  $('study-status').textContent = current.passed ? 'CHECKS PASSED' : 'CHECKS FAILED'; $('study-status').classList.toggle('failed',!current.passed);
  for (const [video, url, poster, size, metadata] of [[source,current.source,current.source_poster,'source-size',current.input.probe],[enhanced,current.output,current.output_poster,'output-size',current.enhanced.probe]]) {
    video.src = url; video.poster = poster; video.load(); $(size).textContent = `${metadata.width} × ${metadata.height}`;
  }
  $('measurements').replaceChildren();
  for (const [label,value] of [['Frames',current.frames],['Largest timing shift',`${current.max_timestamp_shift_ms} ms`],['Local elapsed',`${format(current.elapsed_seconds)} s`],['Audio',current.enhanced.audio.length ? 'Retained' : 'No source audio']]) {
    const div = document.createElement('div'), span = document.createElement('span'), strong = document.createElement('strong'); span.textContent = label; strong.textContent = value; div.append(span,strong); $('measurements').append(div);
  }
  $('download').href = current.output;
  $('checks').textContent = Object.entries(current.checks).map(([key,pass]) => `${pass ? '✓' : '×'} ${key.replace('_',' ')}`).join('  /  ');
  document.querySelectorAll('#cases button').forEach((button,i) => button.setAttribute('aria-selected',String(i===index)));
  seek.value=0; timeline(current);
}
report.cases.forEach((item,index) => {
  const button = document.createElement('button'), number = document.createElement('span');
  number.textContent = `0${index+1} / ${item.passed ? 'PASS' : 'FAIL'}`;
  button.append(number,document.createTextNode(item.title)); button.addEventListener('click',()=>select(index)); $('cases').append(button);
});
$('play').addEventListener('click',async()=>{
  if (!enhanced.paused) { pause(); return; }
  const generation = switching;
  if (enhanced.ended) enhanced.currentTime = 0;
  const ready = video => video.readyState >= 1 ? Promise.resolve() : new Promise((resolve,reject) => {
    video.addEventListener('loadedmetadata',resolve,{once:true}); video.addEventListener('error',reject,{once:true});
  });
  try {
    await Promise.all([ready(source),ready(enhanced)]);
    if (generation !== switching) return;
    const target = enhanced.currentTime + current.source_start;
    if (source.seeking || Math.abs(source.currentTime-target)>.005) {
      await new Promise(resolve=>{source.addEventListener('seeked',resolve,{once:true});source.currentTime=target;});
    }
    if (Math.abs(source.currentTime-target)>.05) throw new Error('Source seeking unavailable');
    if (generation !== switching) return;
    await Promise.all([source.play(),enhanced.play()]);
    if (generation !== switching) return; $('play').textContent = 'Pause study';
  }
  catch { if (generation === switching) { pause(); $('playback-status').textContent = 'Comparison playback needs video seeking. Open this report from disk, or open the downloaded clips in a local video player.'; } }
});
seek.addEventListener('input',()=>{ enhanced.currentTime=Number(seek.value); source.currentTime=Number(seek.value)+current.source_start; });
$('audio').addEventListener('click',()=>{ enhanced.muted=!enhanced.muted; $('audio').textContent=enhanced.muted?'Sound off':'Sound on'; $('audio').setAttribute('aria-pressed',String(!enhanced.muted)); });
enhanced.addEventListener('timeupdate',position); enhanced.addEventListener('loadedmetadata',position); enhanced.addEventListener('ended',pause);
function syncPlayback() { if (!enhanced.paused) position(); requestAnimationFrame(syncPlayback); }
requestAnimationFrame(syncPlayback);
source.addEventListener('loadedmetadata',()=>{source.currentTime=current.source_start;});
document.addEventListener('visibilitychange',()=>{if(document.hidden) pause();});
select(0);
