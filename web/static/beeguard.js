"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const date = value => value ? new Date(value).toLocaleString("pt-BR", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "—";
const clock = value => value ? new Date(value).toLocaleTimeString("pt-BR") : "—";
const statusNames = {usual:"Dentro da referência",changed:"Alteração detectada",calibrating:"Formando referência",quality_review:"Revisar qualidade do áudio",historical:"Coleta anterior à referência"};
const labelNames = {healthy:"Saudável · condição observada",sick:"Doente · condição observada",uncertain:"Condição não confirmada"};
const state = {hives:[],hive:null,cycle:null,cycles:[],records:[],offset:0,total:0,version:0,seen:null,mic:false,stream:null,recorder:null,timer:null,pending:null,hiveSearch:"",showArchived:false};
try { state.hive = localStorage.getItem("beeguard-hive"); } catch {}
let toastTimer,refreshing=0;
function toast(message) { $("toast").textContent=message; $("toast").hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$("toast").hidden=true,5000); }
async function api(path, options={}) {
  const response = await fetch(path,{...options,cache:"no-store",signal:AbortSignal.timeout(20000)});
  const data = await response.json();
  if(!response.ok) throw Error(data.error || "Não foi possível concluir a solicitação.");
  return data;
}
const post = (path,data={}) => api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
const patch = (path,data={}) => api(path,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
function currentCycle(){return state.cycles.find(c=>c.id===state.cycle);}
function activeCycle(){return state.hives.find(h=>h.id===state.hive)?.active_cycle;}
function errorBanner(message){$("error-banner").hidden=!message;$("error-banner").textContent=message || "";}
function chart(){
  const cycle=currentCycle(), chronological=[...state.records].reverse();
  const points=chronological.filter(r=>r.comparison.score!==null);
  const threshold=cycle?.threshold ?? 45;
  let markup="";
  for(let level=0;level<=100;level+=25){let y=190-level*1.6;markup+='<line x1="35" y1="'+y+'" x2="790" y2="'+y+'" stroke="#edf0e7"/><text x="2" y="'+(y+4)+'" fill="#a0a894" font-size="10">'+level+'</text>';}
  const ty=190-threshold*1.6;
  markup+='<line x1="35" y1="'+ty+'" x2="790" y2="'+ty+'" stroke="#c4b89a" stroke-dasharray="5 5"/><text x="790" y="'+(ty-7)+'" text-anchor="end" fill="#9a8c6a" font-size="9">Limite '+threshold+'%</text>';
  if(points.length){
    const start=new Date(chronological[0].captured_at).getTime(),end=new Date(chronological.at(-1).captured_at).getTime();
    const coords=points.map(r=>[40+(new Date(r.captured_at).getTime()-start)/Math.max(end-start,1)*740,190-r.comparison.score*1.6]);
    markup+='<polyline points="'+coords.map(p=>p.join(",")).join(" ")+'" stroke="#d49b3a" fill="none" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>';
    for(const [x,y] of coords)markup+='<circle cx="'+x+'" cy="'+y+'" r="3" fill="#d49b3a"/>';
  } else markup+='<text x="410" y="115" text-anchor="middle" fill="#929c85" font-size="13">As comparações aparecem após formar a referência.</text>';
  $("change-chart").innerHTML=markup;
  $("chart-start").textContent=date(chronological[0]?.captured_at);
  $("chart-end").textContent=date(chronological.at(-1)?.captured_at);
  const latest=state.records[0],base=cycle?.baseline?.spectrum,actual=latest?.features?.spectrum;
  let spectrum='<line x1="20" y1="120" x2="790" y2="120" stroke="#e5eadd"/>';
  const peak=Math.max(...(base || [0]),...(actual || [0]),0.01);
  for(const [values,color] of [[base,"#66805b"],[actual,"#d49b3a"]]){
    if(values)spectrum+='<polyline points="'+values.map((v,i)=>(22+i/31*760)+","+(117-v/peak*95)).join(" ")+'" fill="none" stroke="'+color+'" stroke-width="2.5" opacity=".85"/>';
  }
  if(!actual)spectrum+='<text x="400" y="65" text-anchor="middle" fill="#929c85" font-size="12">Aguardando o primeiro áudio do ciclo.</text>';
  spectrum+='<text x="20" y="140" fill="#9ba48e" font-size="9">40 Hz</text><text x="790" y="140" text-anchor="end" fill="#9ba48e" font-size="9">8.000 Hz · escala logarítmica</text>';
  $("spectrum-chart").innerHTML=spectrum;
}
function renderHives(){
  const hives=state.hives;
  const query=state.hiveSearch.trim().toLocaleLowerCase("pt-BR");
  const visible=hives.filter(h=>!query || h.name.toLocaleLowerCase("pt-BR").includes(query) || h.id.toLocaleLowerCase("pt-BR").includes(query));
  const active=hives.filter(h=>!h.archived_at);
  $("stat-hives").textContent=active.length;
  $("stat-cycles").textContent=active.filter(h=>h.active_cycle).length;
  $("stat-recordings").textContent=active.reduce((n,h)=>n+h.recording_count,0).toLocaleString("pt-BR");
  $("hive-count").textContent=String(hives.length).padStart(2,"0");
  $("toggle-archived").textContent=state.showArchived?"Ocultar arquivadas":"Ver arquivadas";
  const cards=visible.length ? visible.map(h=>'<button class="hive-card '+(h.id===state.hive?"selected":"")+'" data-hive="'+esc(h.id)+'" aria-pressed="'+(h.id===state.hive)+'"><div class="hive-top"><span>'+esc(h.id)+'</span><span><i class="status-dot '+(h.online?"online":"")+'"></i>'+(h.archived_at?"Arquivada":h.online?"Recebendo":"Sem envio recente")+'</span></div><h3>'+esc(h.name)+'</h3><p>'+esc(h.species||"Esp\u00e9cie n\u00e3o informada")+' / '+esc(h.location||"Local n\u00e3o informado")+'</p><div class="hive-bottom"><span>'+h.cycle_count+' ciclos / '+h.recording_count+' coletas</span><span>'+(h.unread_alerts?h.unread_alerts+" alerta(s)":"Abrir")+'</span></div><small>Última gravação: '+esc(date(h.latest?.captured_at)||"nenhuma")+'</small></button>').join("") : '<div class="empty">'+(query?"Nenhuma colmeia corresponde à busca.":"Seu apiário começa aqui. Cadastre a primeira colmeia.")+'</div>';
  const list=$("hive-list");
  if(cards!==list.renderedCards){list.innerHTML=cards;list.renderedCards=cards;}
}
function renderDetail(){
  const hive=state.hives.find(h=>h.id===state.hive),cycle=currentCycle(),latest=state.records[0];
  if(state.mic && cycle?.ended_at)stopMic();
  $("selected-code").textContent=hive ? "COLMEIA / "+hive.id : "ESCOLHA UMA COLMEIA";
  $("selected-name").textContent=hive?.name || "Comece pelo seu apiário";
  $("selected-location").textContent=hive ? (hive.location || "Localização não informada") : "Cadastre uma colmeia para iniciar o acompanhamento.";
  const badge=$("hive-status");
  badge.className="badge";
  badge.textContent=!hive?"Sem coleta":hive.archived_at?"Colmeia arquivada":!cycle?"Sem ciclo":cycle.ended_at?"Ciclo encerrado":!hive.online?"Sem gravação recente":latest?.status==="changed"?"Alteração detectada":"Recebendo áudio";
  if(latest?.status==="changed"&&!cycle?.ended_at)badge.classList.add("warning");
  $("archive-hive").disabled=!hive;
  $("edit-hive").disabled=!hive;
  $("archive-hive").textContent=hive?.archived_at?"Restaurar colmeia":"Arquivar colmeia";
  $("hive-dependencies").textContent=hive?"Histórico preservado: "+hive.cycle_count+" ciclo(s), "+hive.recording_count+" gravação(oes).":"";
  $("delete-hive").disabled=!hive;
  $("new-cycle").disabled=!hive || !!hive.archived_at || !!activeCycle();
  $("end-cycle").disabled=!cycle || !!cycle.ended_at;
  $("cycle-select").disabled=!state.cycles.length;
  $("cycle-select").innerHTML=state.cycles.length ? state.cycles.map(c=>'<option value="'+c.id+'"'+(c.id===state.cycle?" selected":"")+'>'+esc(c.name)+(c.ended_at?" · encerrado":" · em andamento")+'</option>').join("") : '<option>Nenhum ciclo iniciado</option>';
  $("cycle-info").textContent=cycle ? (cycle.ended_at?"Encerrado em "+date(cycle.ended_at)+". Histórico preservado.":cycle.baseline?"Referência formada com "+cycle.baseline_count+" áudios. Alerta a partir de "+cycle.threshold+"% em "+cycle.consecutive_samples+" coletas consecutivas.":"Formando referência: "+cycle.baseline_count+"/"+cycle.calibration_samples+" áudios válidos. Mantenha o microfone na mesma posição.") : "Cada ciclo guarda sua própria referência e seu histórico.";
  $("calibration-progress").style.width=cycle ? Math.min(100,cycle.baseline_count/cycle.calibration_samples*100)+"%" : "0%";
  $("collector-setup").hidden=!cycle || !!cycle.ended_at;
  $("device-setup").textContent=hive ? 'ESP32 por Wi-Fi: use o firmware BeeGuardWiFi com servidor '+location.origin+' e código da colmeia '+hive.id+'. Ao ligar, a placa grava enquanto houver um ciclo ativo. Encerrar o ciclo interrompe novas capturas na próxima consulta.' : '';
  $("collector-command").textContent=cycle ? '.\\.venv-web\\Scripts\\python.exe run.py capture_field --session '+cycle.id+' --port COM6 --server '+location.origin : "";
  const score=latest?.comparison?.score;
  $("change-score").textContent=score==null?"—":Math.round(score)+"%";
  $("previous-score").textContent=latest?.comparison?.previous ? "Anterior: "+Math.round(latest.comparison.previous.score)+"% de alteração" : "Aguardando comparação";
  $("frequency").textContent=latest ? Math.round(latest.features.dominant_frequency_hz)+" Hz" : "—";
  $("rms").textContent=latest ? "Amplitude RMS: "+latest.features.rms.toFixed(5) : "Aguardando áudio";
  $("last-time").textContent=clock(latest?.captured_at);
  $("sample-status").textContent=statusNames[latest?.status] || "Sem dados neste ciclo";
  $("new-inspection").disabled=!cycle;
  const canCapture=!!cycle&&!cycle.ended_at&&!state.pending;
  $("microphone").disabled=!canCapture&&!state.mic;
  $("audio-file").disabled=!canCapture || state.mic;
  $("upload-label").style.opacity=(!canCapture||state.mic)?".45":"1";
  $("export").href="/api/export"+(cycle?"?cycle_id="+encodeURIComponent(cycle.id):hive?"?hive_id="+encodeURIComponent(hive.id):"");
  chart();
}
function renderTable(data){
  state.total=data.total;
  $("recording-summary").textContent=currentCycle() ? data.total+" coletas neste ciclo; áudios originais preservados." : "Selecione um ciclo para consultar os áudios.";
  const sourceNames={manual:"Painel/USB",esp32:"ESP32",unknown:"Origem não registrada"};
  $("recording-rows").innerHTML=data.recordings.length ? data.recordings.map(r=>'<tr><td>'+esc(date(r.captured_at))+'</td><td>'+r.features.rms.toFixed(5)+'</td><td>'+Math.round(r.features.dominant_frequency_hz)+' Hz</td><td>'+(r.comparison.score==null?"-":r.comparison.score.toFixed(1)+"%")+'</td><td><span class="badge '+(r.status==="changed"?"warning":r.status==="quality_review"?"error":"")+'">'+esc(statusNames[r.status] || r.status)+'</span></td><td><span class="badge">'+esc(sourceNames[r.source] || sourceNames.unknown)+'</span></td><td><button class="button small" data-listen="'+r.id+'">Ouvir</button> <a href="'+esc(r.spectrogram_url)+'" target="_blank" rel="noopener">Espectrograma</a></td></tr>').join("") : '<tr><td colspan="7" class="empty">Nenhuma coleta neste ciclo. Inicie a captura ou envie um WAV.</td></tr>';
  $("page-info").textContent="Página "+(Math.floor(state.offset/20)+1)+" de "+Math.max(1,Math.ceil(data.total/20));
  $("prev-page").disabled=state.offset===0;
  $("next-page").disabled=state.offset+20>=data.total;
}
function renderAlerts(data){
  $("stat-alerts").textContent=data.unread;
  $("nav-alert-count").textContent=data.unread;
  $("alert-list").innerHTML=data.alerts.length ? data.alerts.map(a=>'<article class="event '+(a.acknowledged_at?"acknowledged":"")+'"><span class="badge warning">'+Math.round(a.score)+'% de alteração</span><p>'+esc(a.message)+'</p><small>'+esc(date(a.created_at))+'</small>'+(a.acknowledged_at?'<small>Revisado em '+esc(date(a.acknowledged_at))+'</small>':'<button class="button small" data-ack="'+a.id+'">Marcar como revisado</button>')+'</article>').join("") : '<div class="empty">Nenhum alerta registrado.<br>As alterações persistentes aparecerão aqui.</div>';
  if(state.seen!==null){
    for(const a of data.alerts){
      if(!state.seen.has(a.id)&&!a.acknowledged_at){
        toast(a.message);
        if("Notification" in window && Notification.permission==="granted"){
          try{new Notification("BioBee · Alteração acústica",{body:a.message,tag:a.id});}catch{}
        }
      }
    }
  }
  state.seen=new Set(data.alerts.map(a=>a.id));
}
async function refresh({detailOnly=false}={}){
  const version=++state.version;
  refreshing=version;
  try{
    if(!detailOnly){
      const [hives,alerts,health]=await Promise.all([api("/api/hives"+(state.showArchived?"?include_archived=true":"")),api("/api/alerts"),api("/health")]);
      if(version!==state.version)return;
      state.hives=hives.hives;
      if(!state.hives.some(h=>h.id===state.hive)){state.hive=state.hives[0]?.id || null;state.cycle=null;state.offset=0;}
      renderHives();renderAlerts(alerts);
      $("connection").className="connection online";
      $("connection").innerHTML="<i></i> Conectado · "+esc(health.backend);
    }
    errorBanner("");
    if(!state.hive){state.cycles=[];state.records=[];renderDetail();renderTable({recordings:[],total:0});$("inspection-list").innerHTML='<div class="empty">As observações de campo aparecerão aqui.</div>';return;}
    const hive=state.hive;
    const cycles=await api("/api/hives/"+encodeURIComponent(hive)+"/cycles");
    if(version!==state.version||hive!==state.hive)return;
    state.cycles=cycles.cycles;
    if(!state.cycles.some(c=>c.id===state.cycle)){state.cycle=state.cycles[0]?.id || null;state.offset=0;}
    if(!state.cycle){state.records=[];renderDetail();renderTable({recordings:[],total:0});$("inspection-list").innerHTML='<div class="empty">Inicie um ciclo para registrar inspeções.</div>';return;}
    const cycle=state.cycle,query="?cycle_id="+encodeURIComponent(cycle);
    const [page,inspections]=await Promise.all([api("/api/recordings"+query+"&limit=20&offset="+state.offset+"&recent_limit=120"),api("/api/inspections"+query)]);
    if(version!==state.version||cycle!==state.cycle)return;
    state.records=page.recent_recordings || page.recordings;
    renderDetail();renderTable(page);
    $("inspection-list").innerHTML=inspections.inspections.length ? inspections.inspections.map(i=>'<article class="event"><span class="badge '+(i.label==="sick"?"warning":"")+'">'+esc(labelNames[i.label])+'</span><p>'+esc(i.notes)+'</p><small>'+esc(i.observer)+" · "+esc(date(i.observed_at))+'</small></article>').join("") : '<div class="empty">Como está a colmeia?<br>Registre uma inspeção para relacionar som e observação.</div>';
  }catch(error){
    if(version!==state.version)return;
    $("connection").className="connection";
    $("connection").innerHTML="<i></i> Conexão interrompida";
    errorBanner(error.message+" O painel tentará reconectar automaticamente.");
  }finally{
    if(refreshing===version)refreshing=0;
  }
}
function showDialog(title,content,kind,submitLabel="Salvar"){
  $("dialog-content").innerHTML='<div class="dialog-heading"><h2>'+esc(title)+'</h2><button type="button" data-close aria-label="Fechar">×</button></div><form data-kind="'+kind+'">'+content+'<p class="form-error" role="alert"></p><div class="form-actions"><button type="button" class="button" data-close>Cancelar</button><button class="button primary" type="submit">'+esc(submitLabel)+'</button></div></form>';
  $("dialog").showModal();
}
function localInput(value=new Date()){const d=new Date(value);return new Date(d-d.getTimezoneOffset()*60000).toISOString().slice(0,19);}
$("new-hive").onclick=()=>showDialog("Cadastrar colmeia",'<label>Nome da colmeia<input name="name" maxlength="120" placeholder="Ex.: Jataí do jardim" required></label><div class="form-grid"><label>Código único<input name="id" maxlength="64" pattern="[A-Za-z0-9_-]+" placeholder="Ex.: BG01" required></label><label>Espécie<input name="species" maxlength="120" placeholder="Ex.: Jataí"></label></div><label>Localização<input name="location" maxlength="160" placeholder="Ex.: Horta da escola"></label><label>Observações<textarea name="notes" maxlength="2000"></textarea></label>',"hive");
$("edit-hive").onclick=()=>{const hive=state.hives.find(h=>h.id===state.hive);if(!hive)return;showDialog("Editar colmeia",'<label>Nome<input name="name" maxlength="120" value="'+esc(hive.name)+'" required></label><div class="form-grid"><label>Espécie<input name="species" maxlength="120" value="'+esc(hive.species)+'"></label><label>Localização<input name="location" maxlength="160" value="'+esc(hive.location)+'"></label></div><label>Observações<textarea name="notes" maxlength="2000">'+esc(hive.notes)+'</textarea></label>',"hive-edit");};
$("new-cycle").onclick=()=>showDialog("Iniciar ciclo",'<p class="muted">A referência é formada com os primeiros áudios válidos. Escolha um período que você tenha observado e mantenha a posição e o ganho do microfone.</p><label>Nome do ciclo<input name="name" maxlength="120" placeholder="Ex.: Observação de novembro" required></label><div class="form-grid"><label>Áudios para formar referência<input type="number" name="calibration_samples" min="3" max="60" value="6" required></label><label>Limite de alteração (%)<input type="number" name="threshold" min="5" max="100" value="45" required></label></div><label>Coletas alteradas consecutivas para alertar<input type="number" name="consecutive_samples" min="1" max="20" value="2" required></label>',"cycle");
$("new-inspection").onclick=()=>{const c=currentCycle();showDialog("Registrar inspeção",'<p class="muted">Registre o que foi observado na colmeia. Esta informação rotula os áudios próximos no CSV e não é um diagnóstico produzido pelo sistema.</p><label>Condição observada<select name="label"><option value="uncertain">Condição não confirmada</option><option value="healthy">Saudável</option><option value="sick">Doente</option></select></label><label>Quem realizou a inspeção<input name="observer" maxlength="100" required></label><label>Data e hora da observação<input type="datetime-local" step="1" name="observed_at" value="'+localInput(c.ended_at || new Date())+'" required></label><label>O que foi observado?<textarea name="notes" maxlength="2000" required></textarea></label>',"inspection");};
$("end-cycle").onclick=()=>showDialog("Encerrar ciclo",'<p>Os áudios e comparações serão preservados. Pare a captura antes de encerrar este ciclo.</p>',"end");
$("dialog").addEventListener("click",e=>{if(e.target.closest("[data-close]"))$("dialog").close();});
$("dialog").addEventListener("close",()=>{
  const key=$("dialog-content").querySelector('[name="delete_key"]');
  if(key)key.value="";
});
$("dialog").addEventListener("submit",async e=>{
  e.preventDefault();
  const form=e.target,data=Object.fromEntries(new FormData(form)),submit=form.querySelector('[type="submit"]');
  submit.disabled=true;
  try{
    if(form.dataset.kind==="hive-delete"){
      if(data.confirm_id!==data.hive_id)throw Error("Digite o identificador exato da colmeia.");
      const hiveId=data.hive_id;
      delete data.hive_id;
      await post("/api/hives/"+encodeURIComponent(hiveId)+"/delete",data);
      if(state.hive===hiveId){stopMic();state.hive=null;state.cycle=null;state.offset=0;}
      document.querySelector(".hive-menu").open=false;
      $("dialog").close();
      toast("Colmeia e histórico excluídos.");
      await refresh();
      return;
    }
    if(form.dataset.kind==="hive"){await post("/api/hives",data);state.hive=data.id;state.cycle=null;}
    if(form.dataset.kind==="hive-edit"){await patch("/api/hives/"+encodeURIComponent(state.hive),data);}
    if(form.dataset.kind==="cycle"){for(const key of ["calibration_samples","threshold","consecutive_samples"])data[key]=Number(data[key]);const result=await post("/api/hives/"+encodeURIComponent(state.hive)+"/cycles",data);state.cycle=result.cycle.id;state.offset=0;}
    if(form.dataset.kind==="inspection"){data.cycle_id=state.cycle;data.observed_at=new Date(data.observed_at).toISOString();await post("/api/inspections",data);}
    if(form.dataset.kind==="end"){stopMic();await post("/api/cycles/"+state.cycle+"/end");}
    $("dialog").close();toast("Registro salvo.");await refresh();
  }catch(error){form.querySelector(".form-error").textContent=error.message;}finally{submit.disabled=false;}
});
$("hive-search").addEventListener("input",e=>{state.hiveSearch=e.target.value;renderHives();});
$("toggle-archived").onclick=()=>{state.showArchived=!state.showArchived;refresh();};
$("archive-hive").onclick=async()=>{
  const hive=state.hives.find(h=>h.id===state.hive);
  if(!hive)return;
  const archived=!hive.archived_at;
  const action=archived?"arquivar":"restaurar";
  if(!window.confirm("Confirma "+action+" a colmeia "+hive.name+" ("+hive.id+")? O histórico será preservado."))return;
  try{
    await post("/api/hives/"+encodeURIComponent(hive.id)+"/archive",{archived});
    document.querySelector(".hive-menu").open=false;
    if(archived&&!state.showArchived){state.hive=null;state.cycle=null;}
    toast(archived?"Colmeia arquivada; histórico preservado.":"Colmeia restaurada.");
    await refresh();
  }catch(error){toast(error.message);}
};
$("delete-hive").onclick=()=>{
  const hive=state.hives.find(h=>h.id===state.hive);
  if(!hive)return;
  showDialog("Excluir colmeia",
    '<p>Esta ação apaga permanentemente '+esc(hive.name)+' e todo o histórico, incluindo gravações, ciclos, alertas e inspeções.</p>'+
    '<input type="hidden" name="hive_id" value="'+esc(hive.id)+'">'+
    '<label>Digite '+esc(hive.id)+' para confirmar<input name="confirm_id" maxlength="64" autocomplete="off" required></label>'+
    '<label>Chave administrativa<input type="password" name="delete_key" autocomplete="off" required></label>',
    "hive-delete","Excluir definitivamente");
};
$("hive-list").onclick=e=>{
  const card=e.target.closest("[data-hive]");
  if(!card)return;
  if(card.dataset.hive!==state.hive){
    stopMic();
    state.hive=card.dataset.hive;
    state.cycle=null;
    state.cycles=[];
    state.records=[];
    state.offset=0;
    try{localStorage.setItem("beeguard-hive",state.hive);}catch{}
    renderHives();
    renderDetail();
    renderTable({recordings:[],total:0});
    $("recording-summary").textContent="Carregando histórico da colmeia…";
    $("recording-rows").innerHTML='<tr><td colspan="7" class="empty">Carregando gravações…</td></tr>';
    $("inspection-list").innerHTML='<div class="empty">Carregando inspeções…</div>';
    refresh({detailOnly:true});
  }
  $("selected-name").focus({preventScroll:true});
  $("selected-name").scrollIntoView({block:"start"});
};
$("cycle-select").onchange=e=>{stopMic();state.cycle=e.target.value;state.offset=0;refresh();};
$("prev-page").onclick=()=>{state.offset=Math.max(0,state.offset-20);refresh();};
$("next-page").onclick=()=>{state.offset+=20;refresh();};
$("alert-list").onclick=async e=>{const button=e.target.closest("[data-ack]");if(!button)return;button.disabled=true;try{await post("/api/alerts/"+button.dataset.ack+"/acknowledge");await refresh();}catch(error){toast(error.message);button.disabled=false;}};
$("recording-rows").onclick=e=>{const button=e.target.closest("[data-listen]");if(!button)return;const audio=document.createElement("audio");audio.controls=true;audio.src="/api/recordings/"+button.dataset.listen+"/audio";button.replaceWith(audio);audio.play().catch(()=>{});};
$("enable-notifications").onclick=async()=>{
  if(!("Notification" in window)){toast("Este navegador não oferece notificações. Os alertas continuam disponíveis no painel.");return;}
  try{const permission=await Notification.requestPermission();$("enable-notifications").textContent=permission==="granted"?"Notificações ativas":"Notificações bloqueadas";toast(permission==="granted"?"Novos alertas serão notificados com o painel aberto.":"Você pode liberar notificações nas configurações do navegador.");}catch(error){toast(error.message);}
};
$("copy-command").onclick=async()=>{try{await navigator.clipboard.writeText($("collector-command").textContent);toast("Comando copiado. Ajuste COM6 para a porta do seu ESP32.");}catch{toast("Selecione e copie o comando exibido.");}};
if("Notification" in window&&Notification.permission==="granted")$("enable-notifications").textContent="Notificações ativas";


function pendingVisible(){
  $("pending-audio").hidden=!state.pending;
  if(state.pending){
    if($("save-audio").dataset.blobUrl)URL.revokeObjectURL($("save-audio").dataset.blobUrl);
    const url=URL.createObjectURL(state.pending.blob);
    $("save-audio").href=url;$("save-audio").dataset.blobUrl=url;
  }else if($("save-audio").dataset.blobUrl){
    URL.revokeObjectURL($("save-audio").dataset.blobUrl);
    delete $("save-audio").dataset.blobUrl;
  }
  renderDetail();
}
async function sendPending(){
  const pending=state.pending;
  if(!pending)return;
  $("retry-audio").disabled=true;
  $("capture-status").textContent="Enviando áudio de "+pending.hive+"…";
  try{
    const headers={"Content-Type":"audio/wav","X-Hive-Id":pending.hive,"X-Cycle-Id":pending.cycle,"X-Recording-Id":pending.id,"X-Captured-At":pending.captured};
    const result=await api("/api/audio",{method:"POST",headers,body:pending.blob});
    if(result.id!==pending.id||result.storage!=="ok")throw Error("O servidor não confirmou o armazenamento.");
    state.pending=null;
    pendingVisible();
    $("capture-status").textContent="Áudio salvo · "+(statusNames[result.acoustic_status]||"Coleta confirmada")+".";
    await refresh();
  }catch(error){
    $("capture-status").textContent="Envio não confirmado: "+error.message+" Reenvie ou salve o WAV antes de fechar a página.";
    pendingVisible();
    throw error;
  }finally{$("retry-audio").disabled=false;}
}
async function queueAudio(blob,context){
  if(state.pending)throw Error("Reenvie a coleta pendente antes de iniciar outra.");
  state.pending={id:crypto.randomUUID(),hive:context.hive,cycle:context.cycle,captured:context.captured,blob};
  pendingVisible();
  await sendPending();
}
$("retry-audio").onclick=()=>sendPending().catch(error=>toast(error.message));
$("discard-audio").onclick=()=>{state.pending=null;pendingVisible();$("capture-status").textContent="Pendência descartada. Você pode enviar outra coleta.";};
$("audio-file").onchange=async e=>{
  const file=e.target.files[0],cycle=currentCycle();
  if(!file||!cycle||cycle.ended_at)return;
  try{await queueAudio(file,{hive:state.hive,cycle:cycle.id,captured:new Date().toISOString()});toast("Coleta armazenada.");}
  catch(error){toast(error.message);}
  finally{e.target.value="";}
};
function stopMic(){
  state.mic=false;clearTimeout(state.timer);
  if(state.recorder&&state.recorder.state!=="inactive")state.recorder.stop();
  state.stream?.getTracks().forEach(track=>track.stop());
  state.stream=null;
  $("microphone").textContent="● Iniciar microfone";
}
const RECORD_SECONDS=30;
function recordBlock(stream){
  return new Promise((resolve,reject)=>{
    const chunks=[],recorder=new MediaRecorder(stream);
    state.recorder=recorder;
    recorder.ondataavailable=event=>{if(event.data.size)chunks.push(event.data);};
    recorder.onerror=()=>reject(Error("Falha na gravação do microfone."));
    recorder.onstop=()=>resolve(new Blob(chunks,{type:recorder.mimeType}));
    recorder.start();
    state.timer=setTimeout(()=>{if(recorder.state!=="inactive")recorder.stop();},RECORD_SECONDS*1000+500);
  });
}
async function pcmWave(blob){
  const AudioEngine=window.AudioContext||window.webkitAudioContext;
  const engine=new AudioEngine();
  let source;
  try{source=await engine.decodeAudioData(await blob.arrayBuffer());}finally{await engine.close();}
  if(source.duration<RECORD_SECONDS)throw Error("A captura precisa ter ao menos "+RECORD_SECONDS+" segundos de áudio.");
  const offline=new OfflineAudioContext(1,RECORD_SECONDS*16000,16000);
  const node=offline.createBufferSource();node.buffer=source;node.connect(offline.destination);node.start(0);
  const rendered=await offline.startRendering(),samples=rendered.getChannelData(0);
  const result=new ArrayBuffer(44+samples.length*2),view=new DataView(result);
  function word(offset,value){for(let i=0;i<value.length;i++)view.setUint8(offset+i,value.charCodeAt(i));}
  word(0,"RIFF");view.setUint32(4,36+samples.length*2,true);word(8,"WAVE");word(12,"fmt ");
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);word(36,"data");view.setUint32(40,samples.length*2,true);
  for(let i=0;i<samples.length;i++){const value=Math.max(-1,Math.min(1,samples[i]));view.setInt16(44+i*2,value<0?value*32768:value*32767,true);}
  return new Blob([result],{type:"audio/wav"});
}
async function micLoop(context){
  while(state.mic){
    const captured=new Date().toISOString();
    $("capture-status").textContent="Ouvindo "+context.hive+"… Um bloco de "+RECORD_SECONDS+" segundos será enviado automaticamente.";
    const blob=await recordBlock(context.stream);
    if(!state.mic)break;
    const wav=await pcmWave(blob);
    if(!state.mic)break;
    await queueAudio(wav,{...context,captured});
  }
}
$("microphone").onclick=async()=>{
  if(state.mic){stopMic();$("capture-status").textContent="Microfone parado. Coletas confirmadas foram preservadas.";renderDetail();return;}
  if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){toast("Microfone indisponível. Use HTTPS ou localhost e um navegador compatível.");return;}
  try{
    const cycle=currentCycle();if(!cycle||cycle.ended_at)return;
    const hive=state.hive,cycleId=cycle.id;
    const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
    if(state.hive!==hive||state.cycle!==cycleId){stream.getTracks().forEach(t=>t.stop());return;}
    state.stream=stream;state.mic=true;$("microphone").textContent="■ Parar microfone";renderDetail();
    await micLoop({hive,cycle:cycleId,stream});
  }catch(error){stopMic();toast(error.message);if(!state.pending)$("capture-status").textContent=error.message;renderDetail();}
};
document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});
window.addEventListener("online",()=>refresh());
window.addEventListener("beforeunload",e=>{stopMic();if(state.pending){e.preventDefault();e.returnValue="";}});
document.querySelectorAll(".nav-item").forEach(link=>link.addEventListener("click",()=>{document.querySelectorAll(".nav-item").forEach(item=>item.classList.remove("active"));link.classList.add("active");}));
(async function poll(){if(!refreshing)await refresh();setTimeout(poll,3000);})();
