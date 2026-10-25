const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function harness(failed=false) {
  class El {
    constructor(){this.dataset={};this.attrs={};this.events={};this.children=[];this.isConnected=true;this.clientWidth=380;}
    append(el){this.children.push(el);} setAttribute(k,v){this.attrs[k]=v;} removeAttribute(k){delete this.attrs[k];}
    addEventListener(k,f){this.events[k]=f;} removeEventListener(k){delete this.events[k];} after(){}
    querySelector(k){return this.parts[k];}
  }
  const doc={events:{},querySelectorAll:()=>[],createElement:()=>new El(),addEventListener(k,f){this.events[k]=f;}};
  const motion={matches:false,listeners:[],addEventListener(_,f){this.listeners.push(f);}};
  let imports=0,active=0,peak=0,disposed=0,lastView;
  const context={document:doc,window:{matchMedia:()=>motion},module:{exports:{}},ArrowheadPlaybook:{},IntersectionObserver:class{constructor(f){this.callback=f;}observe(){}},ResizeObserver:class{observe(){}disconnect(){}}};
  vm.runInNewContext(fs.readFileSync(require.resolve('../../public/js/playbook-3d.js'),'utf8'),context);
  const cards=[new El(),new El()];for(const c of cards){c.dataset={playbook:'inside_zone',playbookTitle:'Example'};c.parts={'.playbook__controls':new El(),'.playbook':new El(),'.playbook__field':new El()};}
  const root={querySelectorAll:()=>cards};
  const api=context.module.exports;
  api.init(root,{board:{specs:{inside_zone:{}}},load:async()=>{imports++;if(failed)throw Error('unavailable');return {};},factory:()=>{active++;peak=Math.max(peak,active);lastView={canvas:new El(),update:p=>{lastView.progress=p;},resize:()=>{},dispose:()=>{active--;disposed++;}};return lastView;}});
  return {cards,motion,doc,button:i=>cards[i].parts['.playbook__controls'].children[0],state:()=>({imports,active,peak,disposed,lastView})};
}
test('Three.js stays lazy and only one context survives switching or loss',async()=>{
  const h=harness();assert.equal(h.state().imports,0);
  await h.button(0).events.click();assert.equal(h.state().active,1);assert.equal(h.cards[0].parts['.playbook__field'].attrs.hidden,'');
  h.cards[0].events.playbookframe({detail:{progress:2}});assert.equal(h.state().lastView.progress,2);
  await h.button(1).events.click();assert.equal(h.state().active,1);assert.equal(h.state().peak,1);assert.equal(h.state().disposed,1);
  h.state().lastView.canvas.events.webglcontextlost({preventDefault(){}});assert.equal(h.state().active,0);
  assert.equal(h.cards[1].parts['.playbook__field'].attrs.hidden,undefined);
  h.motion.matches=true;h.motion.listeners.forEach(f=>f());assert.equal(h.button(0).disabled,true);
});
test('unavailable WebGL/module leaves 2D and accessible controls',async()=>{
  const h=harness(true);await h.button(0).events.click();assert.equal(h.state().active,0);
  assert.equal(h.cards[0].parts['.playbook__field'].attrs.hidden,undefined);assert.equal(h.button(0).disabled,false);
});
