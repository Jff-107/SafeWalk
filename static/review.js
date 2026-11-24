(() => {
  const image=document.getElementById('review-frame'), native=document.getElementById('review-native'), canvas=document.getElementById('review-chart'), ctx=canvas.getContext('2d');
  const seek=document.getElementById('review-seek'), playButton=document.getElementById('play-video');
  let rows=[],duration=0,position=0,frameUrl='',playing=false,requestId=0;
  const series={peatones_frame:'#315ee8',vehiculos_frame:'#078ca7',riesgo_alto_frame:'#c73b48'};
  const fmt=t=>`${String(Math.floor(t/60)).padStart(2,'0')}:${String(Math.floor(t%60)).padStart(2,'0')}`;
  function draw(){
    const width=canvas.parentElement.clientWidth,height=300,scale=devicePixelRatio||1;
    canvas.width=width*scale;canvas.height=height*scale;canvas.style.height=`${height}px`;ctx.setTransform(scale,0,0,scale,0,0);ctx.clearRect(0,0,width,height);
    const left=42,right=width-16,top=18,bottom=height-30,max=Math.max(4,...rows.flatMap(r=>[r.peatones_frame,r.vehiculos_frame,r.riesgo_alto_frame]));
    for(let i=0;i<=4;i++){const y=bottom-(bottom-top)*i/4;ctx.strokeStyle='#e7ecf2';ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();}
    for(const [key,color] of Object.entries(series)){ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();rows.forEach((r,i)=>{const x=left+(right-left)*r.segundo/Math.max(duration,1),y=bottom-(bottom-top)*r[key]/max;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}
    const x=left+(right-left)*Math.min(position,duration)/Math.max(duration,1);ctx.strokeStyle='#182230';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke();ctx.setLineDash([]);
  }
  function updateMetrics(){let row=rows[0];for(const item of rows){if(item.segundo<=position)row=item;else break;}if(row)for(const [id,key] of [['r-people','peatones_frame'],['r-vehicles','vehiculos_frame'],['r-medium','riesgo_medio_frame'],['r-high','riesgo_alto_frame'],['r-pcross','peatones_cruzaron'],['r-vcross','vehiculos_cruzaron']])document.getElementById(id).textContent=row[key]??0;document.getElementById('review-time').textContent=`${fmt(position)} / ${fmt(duration)}`;if(seek)seek.value=position;draw();}
  async function showFrame(){if(native||!frameUrl)return;const id=++requestId;try{const response=await fetch(`${frameUrl}?t=${position.toFixed(2)}&v=${id}`,{cache:'no-store'});if(!response.ok||response.status===204)return;const blob=await response.blob();if(id!==requestId)return;const url=URL.createObjectURL(blob),old=image.dataset.objectUrl;image.src=url;image.dataset.objectUrl=url;if(old)URL.revokeObjectURL(old);}catch(_){}}
  function setPosition(value){position=Math.max(0,Math.min(duration,Number(value)||0));updateMetrics();if(native)native.currentTime=position;else showFrame();}
  async function tick(){if(!playing)return;setPosition(position+.2);if(position>=duration){playing=false;playButton.textContent='▶ Reproducir';return;}setTimeout(tick,200);}
  fetch(`/api/reportes/sesion/${window.SAFEWALK_REVIEW_ID}`,{cache:'no-store'}).then(async r=>{if(!r.ok)throw Error('No se encontró este reporte.');return r.json();}).then(data=>{
    rows=data.muestras;document.getElementById('review-title').textContent=`${data.sesion.nombre} · ${data.sesion.fuente} · ${data.sesion.inicio}`;
    if(data.video){duration=data.video.duration||0;frameUrl=data.video.frame_url;if(seek)seek.max=duration;showFrame();}else{(native||image).parentElement.outerHTML='<div class="notice warning">Este reporte no tiene un video guardado. Los reportes antiguos solo tienen sus datos.</div>';}
    updateMetrics();
  }).catch(e=>document.getElementById('review-title').textContent=e.message);
  if(native){
    native.addEventListener('timeupdate',()=>{position=native.currentTime;updateMetrics();});
    native.addEventListener('loadedmetadata',()=>{duration=native.duration||duration;updateMetrics();});
  }else{
  playButton.onclick=()=>{playing=!playing;playButton.textContent=playing?'❚❚ Pausar':'▶ Reproducir';if(playing)tick();};
  document.getElementById('back-5').onclick=()=>setPosition(position-5);document.getElementById('forward-5').onclick=()=>setPosition(position+5);
  document.getElementById('stop-video').onclick=()=>{playing=false;playButton.textContent='▶ Reproducir';setPosition(0);};
  seek.oninput=()=>setPosition(seek.value);
  }
  canvas.onclick=event=>{if(!duration)return;const rect=canvas.getBoundingClientRect();setPosition((event.clientX-rect.left)/rect.width*duration);};
  new ResizeObserver(draw).observe(canvas.parentElement);
})();
