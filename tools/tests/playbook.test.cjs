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
