const {test} = require('node:test');
const assert = require('node:assert/strict');
const {specs, actorsFor, pointAt} = require('../../public/js/playbook.js');

test('each concept preserves a complete lineup and finite staged geometry', () => {
  assert.equal(Object.keys(specs).length,6);
  for(const [key,spec] of Object.entries(specs)) {
    const actors=actorsFor(spec);
    assert.equal(actors.filter(a=>a.team==='O').length,11,key);
    assert.equal(actors.filter(a=>a.team==='D').length,11,key);
    assert.equal(new Set(actors.map(a=>a.id)).size,22,key);
    for(const actor of [...actors,{points:spec.ball}]) {
      assert.equal(actor.points.length,4,key);
      for(const [x,y] of actor.points) assert.ok(Number.isFinite(x)&&Number.isFinite(y)&&x>=35&&x<=625&&y>=70&&y<=435,key);
    }
    assert.equal(spec.notes.length,4,key);
    assert.match(spec.source,/^https:\/\/(blogs\.usafootball\.com|www\.buccaneers\.com)\//);
  }
});
test('scrubbing clamps to endpoints and never jumps at a stage boundary', () => {
  const points=[[0,0],[2,10],[8,20],[12,30]];
  assert.deepEqual(pointAt(points,-2),points[0]);
  assert.deepEqual(pointAt(points,8),points[3]);
  assert.deepEqual(pointAt(points,1.5),[5,15]);
  for(const p of [1,2]) assert.ok(Math.abs(pointAt(points,p-1e-8)[0]-pointAt(points,p+1e-8)[0])<1e-5);
});
test('screen catch stays behind scrimmage until blockers release', () => {
  const spec=specs.spacing_screen;
  assert.ok(spec.ball[2][1]>292);
  for(const id of ['RG','RT']) assert.ok(actorsFor(spec).find(a=>a.id===id).points[2][1]>292);
});
test('fire zone illustrates five rushers and six coverage players', () => {
  const defense=actorsFor(specs.zone_blitz).filter(a=>a.team==='D');
  assert.equal(defense.filter(a=>a.points[3][1]>292).length,5);
  assert.equal(defense.filter(a=>a.points[3][1]<292).length,6);
});
test('mesh crossers remain on distinct levels through the key read', () => {
  const actors=actorsFor(specs.mesh);
  const a=actors.find(a=>a.id==='H'), b=actors.find(a=>a.id==='Y');
  assert.ok(Math.abs(a.points[2][1]-b.points[2][1])>=20);
});

test('controls respect reduced motion, scrubbing, replay and offscreen pause', () => {
  const vm = require('node:vm');
  const fs = require('node:fs');
  class Element {
    constructor(tag) { this.tag=tag; this.children=[]; this.dataset={}; this.style={}; this.attrs={}; this.events={}; this.isConnected=true; }
    append(...nodes) { this.children.push(...nodes); }
    setAttribute(k,v) { this.attrs[k]=v; }
    addEventListener(k,f) { this.events[k]=f; }
    fire(k) { this.events[k](); }
    dispatchEvent() {}
    getTotalLength() { return 100; }
    querySelector() { return this.image; }
  }
  const figure=new Element('figure');
  figure.dataset={playbook:'inside_zone',playbookTitle:'Test zone'};
  figure.image=new Element('img');
  const document={hidden:false,events:{},querySelectorAll:()=>[figure],
    createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t),
    addEventListener(k,f) { this.events[k]=f; }};
  const motion={matches:true,addEventListener(_,f) { this.change=f; }};
  const frames=new Map(); let nextId=0, observer;
  const context={CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},document,window:{matchMedia:()=>motion},module:{exports:{}},
    requestAnimationFrame:f=>{frames.set(++nextId,f);return nextId;},
    cancelAnimationFrame:id=>frames.delete(id),
    IntersectionObserver:class { constructor(f) { observer=f; } observe() {} }};
  vm.runInNewContext(fs.readFileSync(require.resolve('../../public/js/playbook.js'),'utf8'),context);
  const panel=figure.children[0], controls=panel.children.find(e=>e.className==='playbook__controls');
  const [play,replay,prev,next]=controls.children;
  const slider=panel.children.find(e=>e.className==='playbook__scrub').children[0];
  assert.equal(play.disabled,true);
  next.fire('click'); assert.equal(slider.value,'1'); assert.equal(frames.size,0);
  replay.fire('click'); assert.equal(slider.value,'0'); assert.equal(frames.size,0);
  motion.matches=false; motion.change(); play.fire('click'); assert.equal(frames.size,1);
  observer([{isIntersecting:false}]); assert.equal(frames.size,0); assert.equal(play.attrs['aria-pressed'],'false');
  observer([{isIntersecting:true}]); assert.equal(frames.size,0);
  play.fire('click'); slider.value='2.5'; slider.fire('input');
  assert.equal(frames.size,0); assert.equal(slider.value,'2.5');
  prev.fire('click'); assert.equal(slider.value,'2');
  next.fire('click'); assert.equal(slider.value,'3'); assert.equal(next.disabled,true);
  replay.fire('click'); assert.equal(slider.value,'0'); assert.equal(frames.size,1);
  document.hidden=true; document.events.visibilitychange(); assert.equal(frames.size,0);
  context.module.exports.init(); assert.equal(figure.children.length,1);
});
