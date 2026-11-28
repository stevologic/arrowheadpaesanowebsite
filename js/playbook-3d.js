/* Optional, single-context Three.js view of the same schematic coordinates. */
const ArrowheadPlaybook3D = (() => {
  'use strict';
  const script = typeof document === 'undefined' ? null : document.currentScript;
  const moduleURL = script?.dataset.threeModule;
  const toWorld = ([x,y]) => [(x-330)/10, 0, (y-235)/10];
  function makeView(T, spec, board) {
    const renderer=new T.WebGLRenderer({antialias:false,alpha:false,powerPreference:'low-power'});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,1.5));
    renderer.setClearColor(0x0b2318);
    const scene=new T.Scene(), camera=new T.OrthographicCamera(-45,45,32, -32,.1,300);
    camera.position.set(0,90,55);camera.lookAt(0,0,0);camera.zoom=1.3;camera.updateProjectionMatrix();
    scene.add(new T.HemisphereLight(0xffffff,0x34512e,2));
    const light=new T.DirectionalLight(0xffffff,2);light.position.set(-20,60,40);scene.add(light);
    const field=new T.Mesh(new T.PlaneGeometry(66,47),new T.MeshStandardMaterial({color:0x164e2e}));field.rotation.x=-Math.PI/2;scene.add(field);
    function line(points,color) {
      const geometry=new T.BufferGeometry().setFromPoints(points.map(p=>new T.Vector3(...p)));
      const object=new T.Line(geometry,new T.LineBasicMaterial({color}));scene.add(object);return object;
    }
    for(let y=80;y<460;y+=40)line([[-30,.03,(y-235)/10],[30,.03,(y-235)/10]],0x4e785b);
    line([[-30,.06,5.7],[30,.06,5.7]],0xffcf53);
    const actors=board.actorsFor(spec), tokens=[], routes=[];
    for(const actor of actors) {
      const group=new T.Group(), color=actor.team==='O'?0xb91536:0x326dba;
      group.add(new T.Mesh(new T.CylinderGeometry(1.25,1.25,1.5,12),new T.MeshStandardMaterial({color})));
      const label=document.createElement('canvas');label.width=128;label.height=64;
      const ctx=label.getContext('2d');ctx.fillStyle='#ffffff';ctx.font='bold 48px sans-serif';ctx.textAlign='center';ctx.fillText(actor.id,64,48);
      const sprite=new T.Sprite(new T.SpriteMaterial({map:new T.CanvasTexture(label),depthTest:false}));sprite.scale.set(6,3,1);sprite.position.y=3.2;group.add(sprite);
      if((spec.highlight||[]).includes(actor.id)) {const ring=new T.Mesh(new T.TorusGeometry(1.6,.13,6,24),new T.MeshBasicMaterial({color:0xffcf53}));ring.rotation.x=Math.PI/2;group.add(ring);}
      scene.add(group);tokens.push({actor,group});
      if(actor.points.some(p=>p.join()!==actor.points[0].join()))routes.push(line(actor.points.map(p=>{const w=toWorld(p);w[1]=.12;return w;}),actor.team==='D'?0x9bc7ff: /^(LT|LG|C|RG|RT)$/.test(actor.id)?0xffcf53:0xffffff));
    }
    const ball=new T.Mesh(new T.SphereGeometry(.65,10,8),new T.MeshStandardMaterial({color:0xffcf53}));ball.scale.set(1.4,.7,.7);scene.add(ball);
    let progress=0;
    function update(value) {
      progress=value;tokens.forEach(({actor,group})=>{const [x,,z]=toWorld(board.pointAt(actor.points,value));group.position.set(x,1,z);});
      const [x,,z]=toWorld(board.pointAt(spec.ball,value));ball.position.set(x,2,z);
      routes.forEach(r=>r.geometry.setDrawRange(0,Math.max(1,Math.ceil(4*value/3))));renderer.render(scene,camera);
    }
    function resize(width) {renderer.setSize(Math.max(1,width),Math.max(1,width*470/660),false);update(progress);}
    function dispose() {
      const geometries=new Set(), materials=new Set(), textures=new Set();
      scene.traverse(o=>{if(o.geometry)geometries.add(o.geometry);for(const m of [o.material].flat().filter(Boolean)){materials.add(m);if(m.map)textures.add(m.map);}});
      textures.forEach(x=>x.dispose());geometries.forEach(x=>x.dispose());materials.forEach(x=>x.dispose());renderer.dispose();renderer.forceContextLoss();renderer.domElement.remove();
    }
    return {canvas:renderer.domElement,update,resize,dispose};
  }
  function init(root=document, options={}) {
    const board=options.board||ArrowheadPlaybook;
    const load=options.load||(()=>import(moduleURL));
    const factory=options.factory||makeView;
    const motion=window.matchMedia('(prefers-reduced-motion: reduce)');
    let active=null, serial=0, imported;
    function close(message='2D schematic view.') {
      serial++;
      if(!active)return;
      const old=active;active=null;old.el.removeEventListener('playbookframe',old.onFrame);old.resize?.disconnect();
      old.svg.removeAttribute('hidden');old.button.textContent='View in 3D';old.button.setAttribute('aria-pressed','false');old.note.textContent=message;delete old.el.dataset.threeActive;old.view.dispose();
    }
    const observer=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{for(const e of entries)if(active?.el===e.target&&!e.isIntersecting)close('2D restored while the play is offscreen.');},{threshold:.1}):null;
    root.querySelectorAll('[data-playbook-ready]:not([data-three-bound])').forEach(el=>{
      const spec=board.specs[el.dataset.playbook];if(!spec)return;
      el.dataset.threeBound='true';
      const button=document.createElement('button');button.type='button';button.textContent='View in 3D';button.setAttribute('aria-label','Toggle 3D schematic: '+el.dataset.playbookTitle);button.setAttribute('aria-pressed','false');
      const note=document.createElement('p');note.className='playbook__3d-note';note.setAttribute('role','status');
      el.querySelector('.playbook__controls').append(button);el.querySelector('.playbook').append(note);
      function setMotion(){button.disabled=motion.matches;if(motion.matches){close();note.textContent='Reduced motion: use the 2D stage controls.';}}
      motion.addEventListener('change',setMotion);setMotion();observer?.observe(el);
      button.addEventListener('click',async()=>{
        if(active?.el===el){close();return;} close();if(motion.matches)return;
        const token=++serial;button.disabled=true;note.textContent='Loading optional 3D view. The 2D board remains available.';
        try {
          imported=imported||load();const T=await imported;if(token!==serial||!el.isConnected){note.textContent='2D schematic remains available.';return;}
          const view=factory(T,spec,board), svg=el.querySelector('.playbook__field');
          view.canvas.className='playbook__3d';view.canvas.setAttribute('role','img');view.canvas.setAttribute('aria-label',el.dataset.playbookTitle+' — 3D schematic, not tracked game film');
          svg.after(view.canvas);svg.setAttribute('hidden','');el.dataset.threeActive='true';
          const onFrame=e=>view.update(e.detail.progress);el.addEventListener('playbookframe',onFrame);
          view.canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();if(active?.el===el)close('3D graphics became unavailable. The 2D board and controls remain available.');});
          const resize=typeof ResizeObserver==='function'?new ResizeObserver(()=>view.resize(el.clientWidth)):null;
          active={el,svg,button,note,view,onFrame,resize};resize?.observe(el);view.resize(el.clientWidth);view.update(Number(el.dataset.playbookProgress)||0);
          button.textContent='Return to 2D';button.setAttribute('aria-pressed','true');note.textContent='3D schematic. Use the same play, pause and stage controls.';
        } catch {if(token===serial){close();note.textContent='3D is unavailable here. The 2D board and controls remain available.';}}
        finally {button.disabled=motion.matches;}
      });
    });
    document.addEventListener('visibilitychange',()=>{if(document.hidden)close('2D restored while this tab is hidden.');});
    return {close};
  }
  return {init,makeView,toWorld};
})();
if(typeof module!=='undefined'&&module.exports)module.exports=ArrowheadPlaybook3D;
if(typeof document!=='undefined')ArrowheadPlaybook3D.init();
