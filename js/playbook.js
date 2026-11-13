/* Illustrative teaching board. Geometry extends the repository's 660x470
 * diagrams; the four frames are representative, never tracked game film. */
const ArrowheadPlaybook = (() => {
  'use strict';
  const phases = ['Pre-snap', 'Snap & develop', 'Key read', 'Illustrated finish'];
  const base = [
    ['LT','O',244,310],['LG','O',287,310],['C','O',330,310],['RG','O',373,310],['RT','O',416,310],
    ['QB','O',330,365],['RB','O',365,405],['Y','O',460,332],['X','O',74,310],['H','O',164,328],['Z','O',586,310],
    ['E1','D',240,270],['T1','D',300,270],['T2','D',360,270],['E2','D',420,270],
    ['W','D',245,225],['M','D',330,215],['S','D',415,225],['C1','D',78,270],['C2','D',582,270],
    ['F','D',200,140],['SS','D',460,140]
  ];
  const usa = 'https://blogs.usafootball.com/blog/';
  const specs = {
    inside_zone: {
      source: usa+'6952/adding-the-wildcat-package-inside-zone-variations', sourceLabel:'USA Football · zone blocking',
      notes: ['The back is behind the quarterback; the offensive line is below the gold line of scrimmage.',
        'The line steps together. The back presses the interior track while the quarterback presents the ball.',
        'Read the interior fit. This example shows the back taking the crease to the right of the center.',
        'The back gets vertical behind the blocks. This is a teaching finish, not a predicted gain.'],
      moves: {
        LT:[[250,292],[253,282],[260,274]], LG:[[294,292],[297,281],[302,273]],
        C:[[338,292],[342,281],[350,274]], RG:[[381,292],[390,280],[395,266]], RT:[[424,292],[431,280],[438,267]],
        QB:[[330,380],[348,359],[329,367]], RB:[[352,379],[348,323],[389,245]], Y:[[460,291],[465,280],[469,268]],
        T1:[[306,281],[308,275],[312,265]],T2:[[368,281],[367,273],[373,266]],
        M:[[340,230],[361,244],[369,254]],S:[[411,238],[405,257],[404,268]]
      }, ball:[[330,310],[330,372],[348,323],[389,245]], highlight:['RB','M']
    },
    play_action_boot: {
      source:'https://www.buccaneers.com/news/bucs-baker-mayfield-qb-art-bootleg-career', sourceLabel:'Buccaneers · bootleg concept',
      notes:['A run look gives the quarterback a reason to move the launch point. The right flat is the illustrated outlet.',
        'The back flows left; the quarterback sells the fake before coming out to the right.',
        'Watch the edge. Here it squeezes inside, so the quarterback has room to find the flat.',
        'The ball goes to the short outlet. If the edge stays home, this pictured answer may not be there.'],
      moves:{QB:[[305,380],[424,365],[500,340]],RB:[[330,370],[290,316],[260,260]],
        Y:[[474,287],[507,263],[550,244]], Z:[[586,252],[586,155],[560,178]], X:[[92,265],[116,190],[142,135]],
        E2:[[405,290],[413,311],[450,330]],M:[[307,236],[286,255],[307,266]],C2:[[570,235],[550,190],[548,211]],
        LT:[[238,302],[235,297],[231,295]],LG:[[282,302],[277,297],[274,295]],C:[[329,302],[324,299],[324,298]],
        RG:[[377,302],[380,300],[384,299]],RT:[[420,303],[423,302],[430,302]]},
      ball:[[330,310],[305,380],[424,365],[550,244]],highlight:['QB','E2','Y']
    },
    mesh: {
      source:usa+'5812/how-andy-reid-s-shallow-cross-package-could-work-for-you',sourceLabel:'USA Football · Reid’s crossing concepts',
      notes:['Two shallow crossers start from opposite sides. Each has a different level through the middle.',
        'The crossers stem inside while the outside routes stretch or settle. The quarterback takes the drop.',
        'Read the trailing underneath defenders. Keep the two crossing paths separated; this is not a designed collision.',
        'This example throws to the crosser exiting right. Against zone, a receiver may settle in space instead.'],
      moves:{H:[[225,267],[314,244],[493,230]],Y:[[423,261],[349,224],[181,212]],
        X:[[81,257],[95,199],[71,148]],Z:[[577,260],[548,218],[548,215]],RB:[[396,382],[423,339],[442,323]],
        QB:[[330,381],[329,373],[331,368]],W:[[210,280],[291,263],[470,250]],S:[[439,276],[371,244],[205,235]],
        C1:[[81,259],[96,218],[84,177]],C2:[[578,258],[566,232],[565,234]]},
      ball:[[330,310],[330,381],[329,373],[493,230]],highlight:['H','Y','W','S']
    },
    spacing_screen: {
      source:usa+'4577/why-screen-plays-can-work-so-well-in-the-nfl',sourceLabel:'USA Football · screen timing',
      notes:['The back starts behind the pocket. Two right-side linemen are the illustrated lead blockers.',
        'The quarterback drops and invites the rush. The back delays before slipping out behind the line.',
        'The pass reaches the back behind the line of scrimmage. The lead blockers have not crossed it yet.',
        'After the catch, the back follows the released blockers into space. A defender can still beat the block.'],
      moves:{QB:[[330,390],[325,395],[324,390]],RB:[[391,390],[468,344],[535,276]],
        RG:[[378,323],[452,315],[501,277]],RT:[[425,323],[489,316],[554,274]],
        X:[[76,263],[78,235],[78,231]],Z:[[584,262],[578,235],[574,231]],
        E2:[[420,315],[386,352],[405,349]],T2:[[358,310],[342,345],[359,357]],
        S:[[427,244],[455,278],[479,290]],C2:[[566,261],[552,257],[555,265]]},
      ball:[[330,310],[330,390],[468,344],[535,276]],highlight:['RB','RG','RT','S']
    },
    cover_two: {
      source:usa+'7073/eliminate-big-plays-with-cover-2-read',sourceLabel:'USA Football · two-high coverage teaching',
      notes:['Two safeties protect the deep halves. Two corners and three linebackers form the five underneath defenders.',
        'The safeties gain depth while the underneath defenders read the releases. Four linemen rush.',
        'The quarterback sees the deep help and takes the short throw shown here. Underneath defenders must rally.',
        'The corner closes toward the catch while the safety stays over the top. A tackle is not guaranteed by the call.'],
      moves:{F:[[204,126],[199,110],[190,103]],SS:[[458,126],[464,110],[470,103]],
        C1:[[91,257],[110,248],[137,255]],C2:[[572,257],[561,244],[552,240]],
        W:[[234,211],[228,204],[204,229]],M:[[330,203],[332,188],[312,192]],S:[[423,208],[430,194],[438,201]],
        E1:[[243,298],[252,319],[265,337]],T1:[[301,296],[306,315],[308,334]],T2:[[360,295],[355,315],[350,332]],E2:[[416,295],[406,312],[395,326]],
        QB:[[330,382],[330,383],[330,380]],RB:[[276,373],[165,315],[126,282]],X:[[83,267],[101,204],[127,165]],Z:[[575,255],[565,185],[549,143]]},
      ball:[[330,310],[330,382],[330,383],[126,282]],highlight:['F','SS','C1','RB'],
      zones:[[175,120,110,56,'Deep half'],[485,120,110,56,'Deep half'],[92,229,45,33,'Flat'],[235,212,49,36,'Hook'],[330,198,44,35,'Middle'],[431,212,49,36,'Hook'],[568,229,45,33,'Flat']]
    },
    zone_blitz: {
      source:usa+'6996/learn-how-manny-diaz-s-fire-zone-s-helped-miami-lead-the-country-in-tfl-s',sourceLabel:'USA Football · fire-zone pressure',
      notes:['This illustrated fire zone sends five and keeps three deep plus three underneath. Watch the right end and the two outside linebackers.',
        'Both outside linebackers add to the rush; the right defensive end drops. The secondary starts its rotation.',
        'The dropping end, middle linebacker and rotating safety replace the underneath coverage. The quarterback looks for a short outlet.',
        'The ball goes underneath as the dropping end rallies. This example shows a coverage exchange, not a promised sack or takeaway.'],
      moves:{W:[[262,266],[277,308],[290,346]],S:[[398,266],[386,308],[373,347]],
        E1:[[245,295],[256,323],[280,355]],T1:[[306,295],[308,326],[312,348]],T2:[[355,295],[350,323],[340,348]],
        E2:[[451,241],[463,244],[466,279]],M:[[329,214],[329,214],[348,238]],
        F:[[270,120],[330,108],[330,96]],SS:[[479,185],[483,222],[480,254]],
        C1:[[114,209],[136,157],[142,121]],C2:[[550,209],[544,154],[547,121]],
        QB:[[330,386],[328,393],[325,386]],RB:[[400,382],[449,349],[469,325]],X:[[85,260],[105,209],[138,180]],Z:[[578,260],[560,205],[543,175]]},
      ball:[[330,310],[330,386],[328,393],[469,325]],highlight:['W','S','E2','SS'],
      zones:[[140,118,82,48,'Deep 1/3'],[330,102,86,48,'Deep 1/3'],[548,118,78,48,'Deep 1/3'],[333,219,53,38,'Middle'],[464,258,46,34,'End drops'],[507,218,45,34,'Safety']]
    }
  };

  function pointAt(points, progress) {
    const p = Math.max(0, Math.min(3, Number(progress) || 0));
    const index = Math.min(2, Math.floor(p)), mix = p - index;
    return points[index].map((value, i) => value + (points[index + 1][i] - value) * mix);
  }
  function actorsFor(spec) {
    return base.map(([id, team, x, y]) => ({id, team, points:[[x,y], ...(spec.moves[id] || [[x,y],[x,y],[x,y]])]}));
  }
  function svgNode(tag, attributes = {}, text) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attributes).forEach(([key,value]) => element.setAttribute(key,String(value)));
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function html(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  const controllers = new Set();
  function enhance(figure) {
    const spec = specs[figure.dataset.playbook], original = figure.querySelector('img');
    if (!spec || !original || figure.dataset.playbookReady) return;
    const title = figure.dataset.playbookTitle || figure.dataset.playbook;
    const panel = html('div','playbook');
    const badge = html('p','playbook__badge','Illustrative concept · not game film');
    const svg = svgNode('svg',{viewBox:'0 0 660 470',role:'img','aria-label':`${title}: ${spec.notes[0]}`,class:'playbook__field'});
    svg.append(svgNode('rect',{width:660,height:470,fill:'#123f27'}));
    for (let y=80;y<460;y+=40) svg.append(svgNode('line',{x1:35,y1:y,x2:625,y2:y,stroke:'#326044','stroke-width':1}));
    svg.append(svgNode('line',{x1:35,y1:292,x2:625,y2:292,stroke:'#ffcf53','stroke-width':2,'stroke-dasharray':'5 5'}));
    svg.append(svgNode('text',{x:625,y:283,'text-anchor':'end',fill:'#ffcf53','font-size':13},'Line of scrimmage'));
    svg.append(svgNode('text',{x:25,y:30,fill:'#fff','font-size':16},'Offense attacks ↑'));
    (spec.zones || []).forEach(([cx,cy,rx,ry,label]) => {
      svg.append(svgNode('ellipse',{cx,cy,rx,ry,fill:'#8cb9ed','fill-opacity':'.07',stroke:'#9bbce5','stroke-dasharray':'5 5'}));
      svg.append(svgNode('text',{x:cx,y:cy-ry+13,'text-anchor':'middle',fill:'#dceaff','font-size':12},label));
    });
    const actors = actorsFor(spec), paths = [], marks = [];
    actors.forEach(actor => {
      const moved = actor.points.some(p => p[0]!==actor.points[0][0] || p[1]!==actor.points[0][1]);
      if (moved) {
        const color = actor.team==='D' ? '#9bc7ff' : /^(LT|LG|C|RG|RT)$/.test(actor.id) ? '#ffcf53' : '#fff';
        const path = svgNode('path',{d:actor.points.map((p,i)=>`${i?'L':'M'} ${p.join(' ')}`).join(' '),fill:'none',stroke:color,'stroke-width':2.2,'stroke-dasharray':'5 4',opacity:'.72'});
        svg.append(path); paths.push(path);
      }
    });
    actors.forEach(actor => {
      const group = svgNode('g',{'aria-hidden':'true'}), hi=(spec.highlight || []).includes(actor.id);
      if (hi) group.append(svgNode('circle',{r:19,fill:'none',stroke:'#ffcf53','stroke-width':2,opacity:'.8'}));
      if (actor.team==='O') group.append(svgNode('circle',{r:15,fill:actor.id==='QB'?'#181818':'#b91536',stroke:'#fff','stroke-width':1.5}));
      else {
        group.append(svgNode('circle',{r:15,fill:'#0b2340',stroke:'#9bc7ff','stroke-width':1.5}));
        group.append(svgNode('path',{d:'M -6 -6 L 6 6 M 6 -6 L -6 6',stroke:'#b8d8ff','stroke-width':2}));
      }
      group.append(svgNode('text',{x:0,y:actor.team==='O'?5:-19,'text-anchor':'middle',fill:'#fff','font-size':14,'font-weight':'bold'},actor.id));
      svg.append(group); marks.push({group,actor});
    });
    const ball=svgNode('g',{'aria-hidden':'true'});
    ball.append(svgNode('ellipse',{rx:9,ry:6,fill:'#ffcf53',stroke:'#171717','stroke-width':2}));
    ball.append(svgNode('path',{d:'M -4 0 L 4 0',stroke:'#171717','stroke-width':1.5}));
    svg.append(ball);
    const controls=html('div','playbook__controls');
    const play=html('button','', 'Play'), replay=html('button','','Replay'), prev=html('button','','← Step'), next=html('button','','Step →');
    [play,replay,prev,next].forEach(b=>{b.type='button'; controls.append(b);});
    play.setAttribute('aria-label',`Play or pause ${title}`);play.setAttribute('aria-pressed','false');
    replay.setAttribute('aria-label',`Replay ${title}`);prev.setAttribute('aria-label',`Previous stage: ${title}`);next.setAttribute('aria-label',`Next stage: ${title}`);
    const label=html('label','playbook__scrub','Play progression '), scrub=html('input');
    scrub.type='range';scrub.min='0';scrub.max='3';scrub.step='0.01';scrub.value='0';scrub.setAttribute('aria-label',`Scrub ${title}`); label.append(scrub);
    const status=html('p','playbook__status');status.setAttribute('aria-live','polite');status.setAttribute('aria-atomic','true');
    const legend=html('p','playbook__legend','O = offense · × = defense · gold oval = ball. White trails show routes, gold trails blocking tracks, blue trails defensive movement. Rings mark the key read.');
    const note=html('p','playbook__motion-note');
    const source=html('p','playbook__source');
    const link=html('a','',spec.sourceLabel); link.href=spec.source;link.target='_blank';link.rel='noopener';source.append('Teaching reference: ',link,'. Representative spacing; not to scale.');
    const fallback=html('details','playbook__static'), summary=html('summary','','Full static diagram');fallback.append(summary);
    const help=html("details","playbook__help"), helpTitle=html("summary","","Diagram key & teaching source");help.append(helpTitle,legend,note,source);
    panel.append(badge,svg,controls,label,status,help,fallback);
    figure.append(panel);fallback.append(original);figure.dataset.playbookReady='true';
    const lengths=paths.map(path=>path.getTotalLength());
    let progress=0, frame=null, last=null, announced=-1, visible=true;
    const motion=window.matchMedia('(prefers-reduced-motion: reduce)');
    function render(value) {
      progress=Math.max(0,Math.min(3,value));scrub.value=String(progress);
      figure.dataset.playbookProgress=String(progress);
      figure.dispatchEvent(new CustomEvent("playbookframe",{detail:{progress}}));
      const phase=Math.min(3,Math.floor(progress+0.000001));
      marks.forEach(({group,actor})=>group.setAttribute('transform',`translate(${pointAt(actor.points,progress).join(' ')})`));
      ball.setAttribute('transform',`translate(${pointAt(spec.ball,progress).join(' ')})`);
      paths.forEach((path,i)=>{path.style.strokeDasharray=String(lengths[i]);path.style.strokeDashoffset=String(lengths[i]*(1-progress/3));});
      prev.disabled=progress===0;next.disabled=progress===3;
      scrub.setAttribute('aria-valuetext',`${phases[phase]}, ${Math.round(progress/3*100)} percent`);
      if (announced!==phase) {status.textContent=`${phase+1}/4 · ${phases[phase]} — ${spec.notes[phase]}`;svg.setAttribute('aria-label',`${title}: ${spec.notes[phase]}`);announced=phase;}
    }
    function pause() {if(frame!==null) cancelAnimationFrame(frame);frame=null;last=null;play.textContent='Play';play.setAttribute('aria-pressed','false');panel.dataset.playing='false';}
    function tick(now) {
      if (!visible || document.hidden || motion.matches || !panel.isConnected) {pause();return;}
      if(last!==null) render(Math.min(3,progress+(now-last)/3000));last=now;
      if(progress>=3) pause();else frame=requestAnimationFrame(tick);
    }
    function start() {
      if(motion.matches || !visible) return;
      controllers.forEach(c=>c.pause());if(progress>=3) render(0);
      last=null;play.textContent='Pause';play.setAttribute('aria-pressed','true');panel.dataset.playing='true';frame=requestAnimationFrame(tick);
    }
    play.addEventListener('click',()=>frame===null?start():pause());
    replay.addEventListener('click',()=>{pause();render(0);if(!motion.matches) start();});
    prev.addEventListener('click',()=>{pause();render(Math.max(0,Math.ceil(progress-0.000001)-1));});
    next.addEventListener('click',()=>{pause();render(Math.min(3,Math.floor(progress+0.000001)+1));});
    scrub.addEventListener('input',()=>{pause();render(Number(scrub.value));});
    function setMotion() {pause();play.disabled=motion.matches;note.textContent=motion.matches?'Reduced motion is on. Step or scrub to explore each static stage.':'Press Play to animate. Step buttons and the slider also work with a keyboard.';}
    motion.addEventListener('change',setMotion);
    document.addEventListener('visibilitychange',()=>{if(document.hidden) pause();});
    if(typeof IntersectionObserver==='function') new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(!visible) pause();},{threshold:0.1}).observe(figure);
    controllers.add({pause});setMotion();render(0);
  }
  function init(root=document) {root.querySelectorAll('[data-playbook]:not([data-playbook-ready])').forEach(enhance);}
  return {specs,base,phases,pointAt,actorsFor,init};
})();
if(typeof module!=='undefined' && module.exports) module.exports=ArrowheadPlaybook;
if(typeof document!=='undefined') ArrowheadPlaybook.init();
