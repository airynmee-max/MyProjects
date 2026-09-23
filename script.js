const toggle = document.getElementById('motion');
const canvas = document.getElementById('universe');
const stage = document.querySelector('.motion-stage');
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
let paused = reduce;
let frame = 0;
let travel = 0;
let mx = 0, my = 0;
const gl = canvas.getContext('webgl', {antialias:true, alpha:false, powerPreference:'low-power'});
function updateMotion(){
  document.body.classList.toggle('paused',paused);
  toggle.setAttribute('aria-pressed',String(paused));
  toggle.innerHTML = paused ? 'Resume motion <span aria-hidden="true">▷</span>' : 'Pause motion <span aria-hidden="true">Ⅱ</span>';
}
if(reduce) toggle.hidden=true;
updateMotion();
toggle.addEventListener('click',()=>{paused=!paused;updateMotion();});
document.getElementById('year').textContent=new Date().getFullYear();
if(gl){
  const vertex=`attribute vec3 aPosition; attribute float aSize; attribute vec3 aColor;
  uniform float uTime; uniform float uTravel; uniform vec2 uMouse; uniform float uAspect;
  varying vec3 vColor; varying float vAlpha;
  void main(){
    vec3 p=aPosition;
    float angle=uTime*.055 + uTravel*.8;
    float ca=cos(angle), sa=sin(angle);
    p.xy=mat2(ca,-sa,sa,ca)*p.xy;
    p.x+=uMouse.x*.22*(1.+p.z*.15); p.y+=uMouse.y*.18*(1.+p.z*.15);
    p.z+=sin(uTime*.42+p.x*2.+p.y*2.)*.14;
    float depth=5.7-p.z+uTravel*.8;
    vec2 pos=p.xy/depth*3.0;
    pos.x/=uAspect;
    gl_Position=vec4(pos,0.,1.);
    gl_PointSize=min(11.,aSize*(6.5/depth));
    vColor=aColor; vAlpha=clamp(1.1-depth*.10,.24,.95);
  }`;
  const fragment=`precision mediump float; varying vec3 vColor; varying float vAlpha;
  void main(){float d=length(gl_PointCoord-.5); float halo=smoothstep(.5,.02,d); float core=smoothstep(.18,.01,d); float a=(halo*.38+core*.85)*vAlpha; gl_FragColor=vec4(vColor*a,a);}`;
  function shader(type,source){const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s));return s;}
  try{
    const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,vertex));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(program);gl.useProgram(program);
    const count=innerWidth<700?1500:3200, stride=7, data=new Float32Array(count*stride);
    for(let i=0;i<count;i++){
      let a=Math.random()*Math.PI*2, ring=Math.pow(Math.random(),.56)*2.75+.38, scatter=Math.random();
      let x=Math.cos(a)*ring,y=Math.sin(a)*ring*.72,z=(Math.random()-.5)*4.5;
      if(scatter<.18){x=(Math.random()-.5)*6.2;y=(Math.random()-.5)*3.9;}
      let k=i*stride;data[k]=x;data[k+1]=y;data[k+2]=z;data[k+3]=Math.random()*5+2;
      const hue=Math.random();data[k+4]=hue<.69?.48:.75;data[k+5]=hue<.69?.94:.84;data[k+6]=hue<.69?.82:1;
    }
    const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,data,gl.STATIC_DRAW);
    for(const [name,size,offset] of [['aPosition',3,0],['aSize',1,3],['aColor',3,4]]){const loc=gl.getAttribLocation(program,name);gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,size,gl.FLOAT,false,stride*4,offset*4);}
    const uniforms={time:gl.getUniformLocation(program,'uTime'),travel:gl.getUniformLocation(program,'uTravel'),mouse:gl.getUniformLocation(program,'uMouse'),aspect:gl.getUniformLocation(program,'uAspect')};
    gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE);gl.clearColor(.02,.065,.085,1);
    function resize(){const dpr=Math.min(devicePixelRatio||1,1.7);canvas.width=stage.clientWidth*dpr;canvas.height=stage.clientHeight*dpr;gl.viewport(0,0,canvas.width,canvas.height);}
    new ResizeObserver(resize).observe(stage);resize();
    stage.addEventListener('pointermove',e=>{const r=stage.getBoundingClientRect();mx=(e.clientX-r.left)/r.width*2-1;my=1-(e.clientY-r.top)/r.height*2;});
    stage.addEventListener('pointerleave',()=>{mx=0;my=0;});
    addEventListener('scroll',()=>{const r=stage.getBoundingClientRect();travel=Math.max(-1,Math.min(1,-r.top/Math.max(r.height,1)));},{passive:true});
    let last=performance.now(),elapsed=0;
    function render(now){const delta=Math.min((now-last)/1000,.05);last=now;if(!paused)elapsed+=delta;gl.clear(gl.COLOR_BUFFER_BIT);gl.uniform1f(uniforms.time,elapsed);gl.uniform1f(uniforms.travel,reduce?0:travel);gl.uniform2f(uniforms.mouse,reduce?0:mx,reduce?0:my);gl.uniform1f(uniforms.aspect,canvas.width/canvas.height);gl.drawArrays(gl.POINTS,0,count);frame=requestAnimationFrame(render);}
    frame=requestAnimationFrame(render);
    canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();cancelAnimationFrame(frame);stage.classList.add('fallback');});
  }catch(e){stage.classList.add('fallback');}
}else{stage.classList.add('fallback');}
