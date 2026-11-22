import {CaseFields} from './case_fields.js';
import {parseIntakeFile, restoreIntakeSource} from './intake_source.js';
import {intakeOffers} from './intake_mapping.js';
import {createAttachmentStore, readCaseAttachments} from './attachments.js';
import {mountFieldPage} from './field_picker.js';
import {marksInCell, newPoint, originalAvailable, placementPayload, pointForView, readOriginalPoints} from './original_placements.js';

// Owns the document values, draft history, PDF requests and file import/export.
// React receives only the status it needs to render the workspace controls.
export function startWorkspace(updateView) {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, txt) => { const n = document.createElement(tag); if(cls)n.className=cls; if(txt!==undefined)n.textContent=txt; return n; };
  const obj = () => Object.create(null);
  const clean = (s, limit=5000) => typeof s==='string' ? s.slice(0,limit) : '';
  const docs = new Map();
  const photos=createAttachmentStore();
  let state=new CaseFields(), pinned=obj();
  let intakeSource=null, stagedIntakes=[], intakeFileNo=0, caseFileNo=0;
  const hasSecret = str => typeof str==='string'&&(/tgpt_sk_[A-Za-z0-9_-]{8,}|\bsk-[a-zA-Z0-9_-]{20,}/i.test(str)||($('key').value.trim().length>=8&&str.includes($('key').value.trim())));
  let cfg, doc, stage='', contexts=[], histories=obj(), notes=obj(), focus=obj(), drafts=obj();
  let caseTitle='', dirty=false, epoch=0, busy=new Map(), fieldInputs=new Map(), tab='preview', noticeTimer;
  let pdfNo=0, pdfTimer, pdfCtl, pdfUrl='', composing=false;
  let pageIndex=0, pageCount=0, previewSource=null, imageUrl='', imageCtl=null;
  let originalPoints=obj(), position=null, movingPoint=false, pageSize=null, cachedCopy=null;
  let originalMode='manual', cellSelectionNo=0, documentVisit=0, ambiguousCell=null, cleanOriginalBytes='';
  const MAX_PREVIEW_BYTES=4*1024*1024;
  const MAX_TURNS=120, FILE_TAG='waste-form-workspace', MAX_FILE=16*1024*1024;
  const safeName = s => (s||'폐기물_서식').replace(/[<>:"/\\|?*\u0000-\u001f]/g,'_').trim().slice(0,90)||'폐기물_서식';
  function announce(msg){$('notice').textContent=msg;$('notice').hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>{$('notice').hidden=true},3200);}
  function setDirty(value=true){
    dirty=value;
    updateView({dirty,caseTitle});
  }
  function scoped(d){
    const values=obj();
    for(const {key} of d.fields){
      const value=state.value(d.id,key);
      if(value)values[key]=value;
    }
    return values;
  }
  function setField(d,key,value,source,trace=null){
    state.set(d.id,key,value,source,trace);
    if(doc?.id!==d.id)return;
    const input=fieldInputs.get(key);
    if(input && input.value!==value){
      input.value=value;
      input.dispatchEvent(new Event('updatechoices'));
    }
  }
  function selectedField(){return doc?.fields.find(f=>f.key===pinned[doc.id]);}
  function sourceOffers(d,key){
    if(!intakeSource)return [];
    const skipped=state.at(d.id,key)?.skipped;
    return intakeOffers(intakeSource,d,key).filter(offer=>skipped?.[offer.origin]!==offer.value);
  }
  function offersForField(d,key){
    if(state.value(d.id,key).trim())return [];
    const compatible=offer=>{
      if(!offer.paired)return true;
      const paired=state.value(d.id,offer.paired.key).trim();
      return !paired || paired===offer.paired.value;
    };
    const direct=sourceOffers(d,key).filter(compatible);
    // A linked record is authoritative for its imported paths; approved snapshots in other
    // documents must not revive a declined, superseded or no-longer-applicable candidate.
    const prior=state.offers(docs,d.id,key).filter(offer=>compatible(offer) && !(intakeSource && offer.intakeTrace));
    return [...direct,...prior];
  }
  function fieldView(){
    if(!doc)return;
    const f=selectedField();
    updateView({
      fieldOptions:doc.fields.map(({key,label})=>({key,label,filled:!!state.value(doc.id,key).trim(),deferred:state.pending(doc.id,key),offers:!!offersForField(doc,key).length})),
      pinnedKey:f?.key||'',fieldHelp:f?.help||'',fieldExample:f?.example||'',
      fieldValue:f?state.value(doc.id,f.key):'',fieldSource:f?state.at(doc.id,f.key)?.source||'':'',
      fieldOffers:f?offersForField(doc,f.key):[],fieldDeferred:f?state.pending(doc.id,f.key):false,
    });
  }
  function photoView(){
    updateView({attachments:photos.forDoc(doc.id),attachmentFields:doc.fields.map(({key,label})=>({key,label})),attachmentEpoch:epoch});
  }
  function checkPhotoScope(id,stamp,key){
    if(doc?.id!==id || stamp!==epoch)throw Error('문서가 바뀌었습니다. 현재 문서에서 다시 시도하세요.');
    if(key && !doc.fields.some(f=>f.key===key))throw Error('현재 서류에서 선택할 수 있는 칸을 확인하세요.');
  }
  function listQuotes(){
    const result=[];
    const order=[doc,...Array.from(docs.values()).filter(d=>d.id!==doc.id)];
    for(const d of order){
      const history=histories[d.id]||[];
      for(let i=history.length-1;i>=0;i--){
        const turn=history[i];
        if(turn.role==='user')result.push({id:d.id+':'+i+':'+turn.text,title:d.title,text:turn.text});
      }
    }
    return result;
  }
  function applyQuote(id,stamp,key,sourceId,raw){
    if(id!==doc?.id || epoch!==stamp || pinned[id]!==key || state.value(id,key).trim())throw Error('현재 서류와 빈칸을 다시 확인하세요.');
    const field=selectedField(), quote=listQuotes().find(row=>row.id===sourceId);
    if(!field || !quote || !raw || !quote.text.includes(raw) || hasSecret(raw))throw Error('원래 메시지에서 그대로 가져올 문구를 선택하세요.');
    setField(doc,key,raw,'이전 대화 · '+quote.title);
    addTurn(id,{role:'assistant',text:quote.title+'의 대화에서 선택한 문구를 반영했습니다.',labels:[field.label]});
    count();setDirty();renderChat();schedulePDF(80,true);
  }
  function changedPhotos(){setDirty();photoView();}
  function pinField(key,locate=true){
    if(!doc || (key && !doc.fields.some(f=>f.key===key)))return;
    const previous=pinned[doc.id]||'';
    if(key!==previous){
      drafts[draftKey(doc)]=$('message').value;
      pinned[doc.id]=key;
      $('message').value=drafts[draftKey(doc)]||'';
    }
    $('message').placeholder=key?'이 칸의 값이나 질문을 입력하세요…':doc.fields.length?'항목명=값 입력…':'찾고 싶은 서류를 입력하세요…';
    fieldView();syncComposer();
    if(locate && key && previewSource?.regions && !previewSource.regions.some(r=>r.key===key && r.page===pageIndex)){
      const match=previewSource.regions.find(r=>r.key===key);
      if(match)showPage(match.page);
    }
  }
  function draftKey(d){return d.id+':'+(pinned[d.id] || (originalAvailable(d) && d===doc && position ? position.cellKey || position.id : ''));}
  function askField(){
    const f=selectedField();if(!f)return;
    const help=(f.help||('확인된 '+f.label+' 값을 적어 주세요.'))+(f.example?'\n입력 예시  '+f.example:'');
    addTurn(doc.id,{role:'assistant',text:'「'+f.label+'」\n'+help});
    showMobile('chat');setDirty();renderChat();
  }
  function deferField(){
    const f=selectedField();if(!f||state.value(doc.id,f.key).trim())return;
    const pending=!state.pending(doc.id,f.key);
    state.setPending(doc.id,f.key,pending);setDirty();count();renderChat();
    announce(pending?'나중에 확인할 항목으로 표시했습니다.':'나중에 확인 표시를 해제했습니다.');
  }
  function reuse(id,accept){
    const f=selectedField();if(!f)return;
    const offer=offersForField(doc,f.key).find(o=>o.id===id);
    if(!offer){fieldView();return;}
    if(accept){
      const trace=offer.origin.startsWith('intake:') ? {origin:offer.origin,source:offer.source,paired:offer.paired} : offer.intakeTrace;
      setField(doc,f.key,offer.value,'승인 후 복사 · '+offer.source,trace);
      addTurn(doc.id,{role:'assistant',text:offer.source+'에서 확인한 값을 이 서류에 반영했습니다.',labels:[f.label]});
      schedulePDF(80,true);
    }else state.dismiss(doc.id,f.key,offer);
    setDirty();count();renderChat();
  }

  function routeDocs(){if(!contexts.length)return cfg.documents;const ids=new Set(cfg.contexts.filter(c=>contexts.includes(c.id)||c.id==='common').flatMap(c=>c.docs||[]));return cfg.documents.filter(d=>ids.has(d.id));}
  function nav(preferred){
    const pool=routeDocs();
    const stages=cfg.stages.filter(s=>pool.some(d=>d.stage===s.id));
    if(!stages.some(s=>s.id===stage))stage='';
    const filtered=pool.filter(d=>!stage||d.stage===stage);
    const target=filtered.find(d=>d.id===preferred)||filtered[0];
    if(!target)return;
    updateView({
      ready:true,canFilter:!!cfg.contexts.length,
      route:contexts.length?'선택 경로 '+contexts.length+'개':'전체 경로',
      caseTitle,dirty,stageId:stage,stageOptions:stages,
      documents:filtered.map(({id,title,stage,kind})=>({id,title,stage,kind})),
    });
    selectDoc(target);
  }
  function chooseStage(id){
    if(!cfg || (id && !cfg.stages.some(s=>s.id===id)))return;
    stage=id;
    nav(doc?.id);
  }
  function chooseDoc(id){
    if(!cfg)return;
    const candidate=routeDocs().find(d=>d.id===id && (!stage||stage===d.stage));
    if(candidate && candidate.id!==doc?.id)selectDoc(candidate);
  }
  function firstMissing(d){return d.fields.find(f=>!state.value(d.id,f.key).trim() && !state.pending(d.id,f.key));}
  function previewMode(d){
    if(d.fields.length)return 'draft';
    return d.file && d.blank_url ? 'original' : 'guide';
  }
  function offersForCell(d,cell,points=originalPoints[d.id]||[]){
    if(!cell || marksInCell(points,cell).length)return [];
    return sourceOffers(d,cell.key);
  }
  function originalView(){
    const available=originalAvailable(doc);
    const cells=available?(doc.original_cells||[]):[];
    const points=available?(originalPoints[doc.id]||[]):[];
    updateView({
      originalHref:available?doc.blank_url:'',
      originalReady:available && !!pageSize,
      originalMode,
      originalFieldOffers:available ? offersForCell(doc,cells.find(c=>c.key===position?.cellKey),points) : [],
      originalCells:cells.map(cell=>({
        key:cell.key,label:cell.label,page:cell.page,help:cell.help,
        filled:marksInCell(points,cell).length>0,offers:!!offersForCell(doc,cell,points).length,
        box:cell.page===pageIndex && pageSize
          ? cell.box.map((n,i)=>n/(i%2 ? pageSize.height : pageSize.width)) : null,
      })),
      originalPoints:points.map(p=>pointForView(p,cells)),
      originalSelected:available?pointForView(position,cells):null,
      originalMoving:available && movingPoint,
      originalPick:available && originalMode==='manual',
    });
  }
  function changePosition(next, move=false){
    const oldKey=draftKey(doc);
    cellSelectionNo++;ambiguousCell=null;position=next;movingPoint=move;
    if(oldKey!==draftKey(doc)){
      drafts[oldKey]=$('message').value;$('message').value=drafts[draftKey(doc)]||'';
    }
    const cell=next?.cellKey && doc.original_cells?.find(c=>c.key===next.cellKey);
    $('message').placeholder=cell?cell.label+' 값이나 질문을 보내세요…':next?'선택한 위치에 넣을 문구나 질문을 보내세요…':'원본 작성 방법을 물어보세요…';
    originalView();syncComposer();
  }
  function originalPickMode(){
    if(!originalAvailable(doc))return;
    originalMode='manual';changePosition(null);showTab('preview');showMobile('doc');
  }
  function useOriginalCells(){
    if(!originalAvailable(doc)||!doc.original_cells?.length)return;
    originalMode='cells';changePosition(null);showTab('preview');showMobile('doc');
  }
  async function chooseOriginalCell(key, chosenId=null){
    const d=doc, cell=d?.original_cells?.find(c=>c.key===key);
    if(!originalAvailable(d)||!cell||!previewSource)return false;
    originalMode='cells';
    if(pageIndex!==cell.page||!pageSize){
      changePosition(null);
      const token=cellSelectionNo;
      await showPage(cell.page);
      if(doc!==d||token!==cellSelectionNo||pageIndex!==cell.page||!pageSize)return false;
    }
    const points=originalPoints[d.id]||[];
    const matches=marksInCell(points,cell);
    const chosen=chosenId && matches.find(m=>m.id===chosenId);
    if(matches.length>1 && !chosen){
      originalMode='manual';changePosition(null);ambiguousCell=cell;
      showMobile('chat');announce('이 칸에 예전 문구가 여러 개 있습니다. ‘배치한 문구’에서 이 칸에 쓸 문구를 고르세요.');
      return false;
    }
    if(!matches.length && points.length>=100){
      announce('한 원본에 최대 100개 문구를 배치할 수 있습니다.');
      return false;
    }
    const {width,height}=pageSize;
    const mark=chosen||matches[0]||newPoint(pageIndex,0,0,width,height);
    changePosition({...mark,x:cell.write[0],y:cell.write[1],width,height,cellKey:key});
    showMobile('chat');
    return true;
  }
  function askOriginalCell(){
    const cell=doc?.original_cells?.find(c=>c.key===position?.cellKey);
    if(!cell)return;
    addTurn(doc.id,{role:'assistant',text:'「'+cell.label+'」 '+cell.help});
    setDirty();showMobile('chat');renderChat();
  }
  function chooseOriginalPoint(x,y){
    if(!originalAvailable(doc)||!pageSize||![x,y].every(n=>Number.isFinite(n)&&n>=0&&n<=1))return;
    if(originalMode!=='manual')return;
    const points=originalPoints[doc.id]||[];
    if(!movingPoint && points.length>=100){announce('한 원본에 최대 100개 문구를 배치할 수 있습니다.');return;}
    const {width,height}=pageSize;
    const next=movingPoint && position
      ? {...position,page:pageIndex,x:x*width,y:y*height,width,height}
      : newPoint(pageIndex,x,y,width,height);
    changePosition(next);
    showMobile('chat');
  }
  function selectOriginalPoint(id){
    if(!originalAvailable(doc))return;
    const mark=originalPoints[doc.id]?.find(mark=>mark.id===id);
    if(!mark)return;
    if(mark.cellKey)return chooseOriginalCell(mark.cellKey);
    if(ambiguousCell && marksInCell([mark],ambiguousCell).length)
      return chooseOriginalCell(ambiguousCell.key,mark.id);
    originalMode='manual';changePosition({...mark});
    if(mark.page!==pageIndex)showPage(mark.page);
  }
  function moveOriginalPoint(){
    if(!originalAvailable(doc)||!position||position.cellKey)return;
    changePosition({...position},true);showMobile('doc');
  }
  function cancelOriginalPoint(){if(originalAvailable(doc))changePosition(null);}
  function setOriginalSize(size){
    const number=Number(size);
    if(position&&!position.cellKey&&Number.isFinite(number)&&number>=6&&number<=36)changePosition({...position,size:number},movingPoint);
  }
  async function writeCopy(d, encoded, points, signal){
    const r=await fetch('/api/finish/export',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({doc:d.id,pdf:encoded,placements:placementPayload(points)}),signal});
    if(!r.ok)throw Error(await responseError(r));
    if(!r.headers.get('Content-Type')?.includes('application/pdf'))throw Error('작성 사본 PDF를 받지 못했습니다.');
    return r.blob();
  }
  function captureCellTarget(d,point){
    if(!point?.cellKey||!cleanOriginalBytes||d!==doc)return null;
    const cell=d.original_cells?.find(c=>c.key===point.cellKey);
    if(!cell)return null;
    return {doc:d,cell,point,token:epoch,visit:documentVisit,encoded:cleanOriginalBytes,
      previous:marksInCell(originalPoints[d.id]||[],cell)};
  }
  function updatedPoints(points, mark){
    if(points.some(p=>p.id===mark.id))return points.map(p=>p.id===mark.id?mark:p);
    if(points.length>=100)throw Error('한 원본에 최대 100개 문구를 배치할 수 있습니다.');
    return [...points,mark];
  }
  function checkedCellPoints(target){
    if(target.doc!==doc || target.token!==epoch || target.visit!==documentVisit)
      throw Error('작업 또는 문서가 바뀌어 문구를 반영하지 않았습니다.');
    if(target.sourceLink && intakeSource!==target.sourceLink)throw Error('연결된 기초정보가 바뀌었습니다. 새 후보의 출처를 확인하세요.');
    const points=originalPoints[doc.id];
    const current=marksInCell(points||[],target.cell);
    if(current.length!==target.previous.length || current.some((p,i)=>p!==target.previous[i]))
      throw Error('응답을 기다리는 동안 이 칸의 값이 바뀌었습니다. 현재 값을 확인하고 다시 보내세요.');
    return points;
  }
  async function applyOriginalText(raw, captured=null, approvedSource=null){
    const text=typeof raw==='string'?raw.trim().normalize('NFC'):'';
    if(!text||text.length>500||hasSecret(text))throw Error('키를 제외한 1~500자의 문구를 입력하세요.');
    const d=doc, selected=captured?.point||position, token=epoch, source=previewSource;
    const target=captured||captureCellTarget(d,selected);
    const mark={...selected,text};
    if(approvedSource)mark.source=approvedSource;
    else if(text!==selected?.text)delete mark.source;
    if(target){
      // A submitted named value belongs to that cell even if the user is already typing in another.
      let before=checkedCellPoints(target);
      for(;;){
        const points=updatedPoints(before||[],mark);
        const blob=await writeCopy(d,target.encoded,points);
        const latest=checkedCellPoints(target);
        if(latest!==before){before=latest;continue;} // another cell changed during PDF export
        originalPoints[d.id]=points;cachedCopy={points,blob};
        if(position===selected || position?.cellKey===target.cell.key)changePosition({...mark});
        else originalView();
        break;
      }
    }else{
      if(!originalAvailable(d)||!selected||!source?.originalEncoded||!pageSize||selected.page!==pageIndex||movingPoint)
        throw Error('PDF에서 문구를 입력할 위치를 다시 선택하세요.');
      const before=originalPoints[d.id], points=updatedPoints(before||[],mark);
      const blob=await writeCopy(d,source.originalEncoded,points);
      if(epoch!==token||doc!==d||selected!==position||source!==previewSource||originalPoints[d.id]!==before)
        throw Error('입력할 위치나 문서가 바뀌었습니다. 현재 위치를 확인한 뒤 다시 입력하세요.');
      originalPoints[d.id]=points;cachedCopy={points,blob};changePosition({...mark});
    }
    setDirty();schedulePDF(0,true);return true;
  }
  async function applyOriginalOffer(id){
    const d=doc, target=captureCellTarget(d,position);
    if(!target || target.previous.length)throw Error('값을 가져올 빈칸을 다시 선택하세요.');
    const offer=offersForCell(d,target.cell).find(o=>o.id===id);
    if(!offer)throw Error('현재 칸과 연결된 기초정보를 다시 확인하세요.');
    target.sourceLink=intakeSource;
    await applyOriginalText(offer.value,target,'승인 후 복사 · '+offer.source);
    if(target.token!==epoch)return false;
    addTurn(d.id,{role:'assistant',text:offer.source+'에서 확인한 값을 반영했습니다.',labels:[target.cell.label]});
    if(d===doc)renderChat();
    return true;
  }
  function dismissOriginalOffer(id){
    const cell=doc?.original_cells?.find(c=>c.key===position?.cellKey);
    if(!cell)return;
    const offer=offersForCell(doc,cell).find(o=>o.id===id);
    if(!offer)return;
    state.dismiss(doc.id,cell.key,offer);
    originalView();setDirty();
  }
  function removeOriginalPoint(){
    if(!originalAvailable(doc)||!position)return;
    const points=originalPoints[doc.id]||[];
    const filtered=points.filter(p=>p.id!==position.id);
    changePosition(null);
    if(filtered.length===points.length)return;
    if(filtered.length)originalPoints[doc.id]=filtered;
    else delete originalPoints[doc.id];
    cachedCopy=null;originalView();setDirty();schedulePDF(0,true);
  }

  function originalLink(d){
    if(!d.blank_url||typeof d.blank_url!=='string')return null;
    try{
      const url=new URL(d.blank_url,location.origin);const local=url.origin===location.origin;
      if(!local&&url.protocol!=='https:')return null;
      const a=el('a','btn',local?'동봉 원본 PDF 내려받기':'공식 사이트 열기');a.href=url.href;
      if(local)a.download=safeName(d.title)+'_원본.pdf';else{a.target='_blank';a.rel='noopener noreferrer';}return a;
    }catch{return null;}
  }
  function canFinish(d){
    const localBlank=d.blank_url&&new URL(d.blank_url,location.origin).origin===location.origin;
    return ['자체양식','법정서식'].includes(d.kind)&&(localBlank||(!d.blank_url&&d.fields.length));
  }
  function externalView(d){
    const box=$('external');box.replaceChildren();
    const title=el('h3','',d.kind||'외부 자료');const copy=el('p','',d.description||'담당 기관이나 해당 시스템에서 원본을 확인하고 확보하세요.');
    if(canFinish(d))copy.append(document.createTextNode(' 동봉된 원본을 내려받은 뒤 ‘나머지 칸 직접 배치’에서 필요한 칸을 작성할 수 있습니다.'));
    const memo=el('button','btn','파일·접수 메모');memo.type='button';memo.addEventListener('click',openMemo);
    box.append(title,copy);const link=originalLink(d);if(link)box.append(link);box.append(memo);
  }
  function selectDoc(d){
    const different=doc?.id!==d.id;
    if(different){
      documentVisit++;cellSelectionNo++;ambiguousCell=null;cleanOriginalBytes='';pageIndex=0;pageSize=null;position=null;movingPoint=false;
      originalMode=d.original_cells?.length?'cells':'manual';
    }
    doc=d;originalView();
    updateView({activeId:d.id,activeStageLabel:cfg.stages.find(s=>s.id===d.stage)?.label||'',title:d.title,description:d.description||'',kind:d.kind||'',previewMode:previewMode(d),hasMemo:!!notes[d.id]});
    $('finishLink').hidden=!canFinish(d);$('finishLink').title='작성 중인 PDF 또는 동봉된 원본을 먼저 내려받아 새 탭에서 여세요.';
    photoView();$('message').value=drafts[draftKey(d)]||'';$('message').placeholder=pinned[d.id]?'이 칸의 값이나 질문을 입력하세요…':d.fields.length?'항목명=값 입력…':originalAvailable(d)?'원본 작성 방법을 물어보세요…':'찾고 싶은 서류를 입력하세요…';
    
    if(different||!d.fields.length)tab='preview';
    count();buildFields();renderChat();showTab(tab);schedulePDF(0,!different);
  }
  function count(){
    const n=doc.fields.filter(f=>state.value(doc.id,f.key).trim()).length;
    fieldView();
    updateView({filled:n,total:doc.fields.length});
  }
  function buildFields(){
    fieldInputs.clear();const parent=$('fieldsView');parent.replaceChildren();if(!doc.fields.length)return;
    const p=el('p','fields-intro','입력한 내용은 이 서류에 반영됩니다. 다른 서류의 값을 쓰려면 칸을 선택하고 출처를 확인해 주세요.');
    const list=el('div','fields-list');parent.append(p,list);
    for(const [i,f] of doc.fields.entries()){
      const group=el('div','field');
      const long=f.multiline||/주소|장소|내역|내용|사유|계획|목적|방법|특이|기타|요약|비고/.test(f.label);
      if(long)group.classList.add('wide');
      const label=el('label','',f.label);const input=el(long?'textarea':'input');
      if(long)input.rows=2;else input.type='text';
      input.id='field-'+i;label.htmlFor=input.id;input.value=state.value(doc.id,f.key);input.maxLength=5000;input.autocomplete='off';
      input.addEventListener('focus',()=>{focus[doc.id]=f.key;pinField(f.key,false);});
      input.addEventListener('input',()=>{if(hasSecret(input.value)){input.value=state.value(doc.id,f.key);announce('API 키는 AI 설정에만 입력하세요.');return;}setField(doc,f.key,input.value,'직접 입력');setDirty();count();if(!histories[doc.id]?.length)renderChat();schedulePDF(450,true);});
      fieldInputs.set(f.key,input);group.append(label,input);
      if(Array.isArray(f.choices)&&f.choices.length){
        const multi=f.key==='attachment_types';const row=el('div','choice-row');
        if(multi)group.append(el('p','field-help','실제로 확보해 첨부할 자료만 선택하세요. 여러 개를 고를 수 있습니다.'));
        row.setAttribute('role','group');row.setAttribute('aria-label',f.label+' 선택지');
        for(const choice of f.choices){
          const b=el('button','choice',choice);b.type='button';
          const sync=()=>{const active=multi?input.value.split(/[,;，、\n]/).map(x=>x.trim()).includes(choice):input.value===choice;b.setAttribute('aria-pressed',String(active));};
          b.addEventListener('click',()=>{
            if(multi){const items=input.value.split(/[,;，、\n]/).map(x=>x.trim()).filter(Boolean);input.value=(items.includes(choice)?items.filter(x=>x!==choice):[...items,choice]).join(', ');}
            else input.value=input.value===choice?'':choice;
            input.dispatchEvent(new Event('input',{bubbles:true}));focus[doc.id]=f.key;
          });
          input.addEventListener('input',sync);input.addEventListener('updatechoices',sync);sync();row.append(b);
        }
        group.append(row);
      }
      list.append(group);
    }
    const link=originalLink(doc);if(link){link.style.marginTop='18px';parent.append(link);}
  }
  function showTab(which){
    tab=which==='fields'&&doc?.fields.length?'fields':'preview';
    updateView({tab});
  }
  function showMobile(which){
    updateView({mobile:which});
  }
  function clearPageImage(){
    if(imageUrl)URL.revokeObjectURL(imageUrl);
    imageUrl='';
    $('frameSlot').replaceChildren();
  }
  function clearPdf(keepImage){
    imageCtl?.abort();imageCtl=null;
    previewSource=null;pageCount=0;pageSize=null;originalView();
    if(!keepImage)clearPageImage();
    updateView(keepImage?{pageBusy:true,hotspots:[]}:{pageCount:0,pageBusy:false,hotspots:[]});
    if(pdfUrl)URL.revokeObjectURL(pdfUrl);
    pdfUrl='';
    for(const id of ['openPdf','downloadPdf']){$(id).removeAttribute('href');$(id).hidden=true;}
  }
  function schedulePDF(delay, sameDocument=false){
    const seq=++pdfNo;clearTimeout(pdfTimer);pdfCtl?.abort();pdfCtl=null;
    const mode=previewMode(doc);
    const guidanceOnly=mode==='guide';
    const keepImage=Boolean(sameDocument && !guidanceOnly && imageUrl);
    clearPdf(keepImage);
    $('external').hidden=!guidanceOnly;$('frameSlot').hidden=guidanceOnly;
    if(guidanceOnly){$('pdfStatus').textContent='';externalView(doc);return;}
    $('pdfStatus').textContent=mode==='original'?'원본 PDF를 불러오는 중…':keepImage?'입력 내용 반영 중…':'PDF를 준비하고 있습니다…';
    const d=doc,values=scoped(d);pdfTimer=setTimeout(()=>renderPDF(d,values,mode,seq),delay);
  }
  async function responseError(r){
    let data;try{data=await r.json();}catch{return '서버 응답 오류 ('+r.status+')';}
    return clean(data.error||data.detail||data.message,1400)||'서버 응답 오류 ('+r.status+')';
  }
  function encodePdf(blob){
    return new Promise((resolve,reject)=>{
      const reader=new FileReader();
      reader.onerror=()=>reject(Error('PDF를 미리보기용으로 읽을 수 없습니다.'));
      reader.onload=()=>resolve(String(reader.result).split(',')[1]);
      reader.readAsDataURL(blob);
    });
  }
  function pdfFallback(message, retry){
    const slot=$('frameSlot');
    clearPageImage();
    if(navigator.pdfViewerEnabled){
      const frame=el('iframe');
      frame.title=doc.title+' PDF 미리보기';
      frame.src=pdfUrl+'#view=FitH';
      slot.append(frame);
    }
    const status=$('pdfStatus');
    status.textContent=message+' 위의 PDF 열기 또는 PDF 저장으로 원본을 확인할 수 있습니다.';
    if(retry){
      const button=el('button','btn','미리보기 다시 시도');
      button.onclick=retry;
      status.append(button);
    }
    updateView({pageCount:0,pageBusy:false,hotspots:[]});
  }
  async function showPage(index, retryFirst=false){
    const source=previewSource;
    if(!source)return;
    imageCtl?.abort();
    const ctl=new AbortController();
    imageCtl=ctl;
    pageSize=null;originalView();
    if(originalAvailable(doc)&&position&&position.page!==index)changePosition(null);
    updateView({pageBusy:true});
    $('pdfStatus').textContent=(index+1)+'쪽을 불러오고 있습니다…';
    try{
      const response=await fetch('/api/finish/preview',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({pdf:source.encoded,page:index}),
        signal:ctl.signal,
      });
      if(ctl.signal.aborted||previewSource!==source)return;
      if(!response.ok){
        if(retryFirst&&index>0&&response.status===400)return showPage(0);
        throw Error(await responseError(response));
      }
      if(!response.headers.get('Content-Type')?.startsWith('image/png'))throw Error('쪽 이미지를 받지 못했습니다.');
      const total=Number(response.headers.get('X-Pdf-Pages'));
      if(!Number.isInteger(total)||total<=index)throw Error('전체 쪽 수를 확인할 수 없습니다.');
      const image=await response.blob();
      if(ctl.signal.aborted||previewSource!==source)return;
      const url=URL.createObjectURL(image);
      const img=el('img','pdf-page');
      img.id='previewImage';img.src=url;
      img.alt=source.title+' '+(index+1)+'쪽 (전체 '+total+'쪽)';
      try{
        await img.decode();
        if(ctl.signal.aborted||previewSource!==source){URL.revokeObjectURL(url);return;}
      }catch(error){
        URL.revokeObjectURL(url);
        throw error;
      }
      const slot=$('frameSlot');
      const scrollTop=imageUrl && pageIndex===index ? slot.scrollTop : 0;
      if(imageUrl)URL.revokeObjectURL(imageUrl);
      imageUrl=url;
      slot.replaceChildren(mountFieldPage(img));slot.scrollTop=scrollTop;
      pageIndex=index;pageCount=total;
      const width=Number(response.headers.get('X-Pdf-Width')),height=Number(response.headers.get('X-Pdf-Height'));
      pageSize=width>0 && height>0 ? {width,height}:null;originalView();
      $('pdfStatus').textContent='';
      updateView({page:index,pageCount:total,pageBusy:false,hotspots:(source.regions||[]).filter(r=>r.page===index)});
    }catch(e){
      if(ctl.signal.aborted||previewSource!==source)return;
      pdfFallback('쪽 미리보기를 열지 못했습니다. '+friendly(e),()=>showPage(index,true));
    }
  }
  function turnPage(delta){
    const index=pageIndex+delta;
    if(index>=0&&index<pageCount){cellSelectionNo++;showPage(index);}
  }
  async function renderPDF(d,values,mode,seq){
    const ctl=new AbortController();pdfCtl=ctl;
    const original=mode==='original';
    try{
      const r=original
        ? await fetch(d.blank_url,{signal:ctl.signal})
        : await fetch('/api/pdf',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({doc:d.id,values,include_fields:true}),signal:ctl.signal});
      if(!r.ok)throw Error(await responseError(r));
      let blob, encoded, originalEncoded, regions=[];
      if(original){
        if(!r.headers.get('Content-Type')?.includes('application/pdf'))throw Error('원본 PDF를 읽을 수 없습니다.');
        blob=await r.blob();
        if(originalAvailable(d) && blob.size<=MAX_PREVIEW_BYTES){
          originalEncoded=await encodePdf(blob);
          if(seq!==pdfNo)return;
          cleanOriginalBytes=originalEncoded;
          const points=originalPoints[d.id];
          if(points?.length){
            blob=cachedCopy?.points===points?cachedCopy.blob:await writeCopy(d,originalEncoded,points,ctl.signal);
            if(seq!==pdfNo)return;
            cachedCopy={points,blob};
          }else encoded=originalEncoded;
        }
      }else{
        const result=await r.json();
        if(typeof result.pdf!=='string'||!Array.isArray(result.regions))throw Error('작성본과 입력칸 위치를 읽을 수 없습니다.');
        encoded=result.pdf;regions=result.regions;
        blob=new Blob([Uint8Array.from(atob(encoded),ch=>ch.charCodeAt(0))],{type:'application/pdf'});
      }
      if(seq!==pdfNo)return;
      const url=URL.createObjectURL(blob);pdfUrl=url;
      $('openPdf').href=url;$('downloadPdf').href=url;$('downloadPdf').download=safeName(d.title)+(original?(originalPoints[d.id]?.length?'_작성사본.pdf':'_원본.pdf'):'.pdf');
      $('openPdf').hidden=false;$('downloadPdf').hidden=false;
      if(blob.size>MAX_PREVIEW_BYTES){
        pdfFallback('4 MiB를 초과하는 PDF는 쪽 이미지를 표시할 수 없습니다.');
        return;
      }
      try{
        encoded??=await encodePdf(blob);
        if(seq!==pdfNo)return;
        previewSource={encoded,originalEncoded,title:d.title,regions};
        await showPage(pageIndex,true);
      }catch(e){
        if(seq===pdfNo)pdfFallback(friendly(e),()=>schedulePDF(0));
      }
    }catch(e){
      if(seq!==pdfNo||ctl.signal.aborted)return;
      clearPageImage();updateView({pageCount:0,pageBusy:false,hotspots:[]});
      const status=$('pdfStatus');status.textContent=(original?'원본 PDF를 불러오지 못했습니다. ':'PDF를 만들지 못했습니다. ')+friendly(e);
      const retry=el('button','btn','다시 시도');retry.onclick=()=>schedulePDF(0);status.append(retry);
    }
  }
  function friendly(e){return e instanceof TypeError&&/fetch|network|load/i.test(e.message)?'로컬 서버 연결을 확인하세요.':clean(e.message||String(e),1400);}
  function focusField(key){
    showMobile('doc');
    showTab('fields');
    requestAnimationFrame(()=>{
      const input=fieldInputs.get(key);
      input?.focus({preventScroll:true});
      input?.scrollIntoView({block:'nearest'});
    });
  }
  function turnNode(t){
    const item=el('div','turn '+t.role);
    const head=el('span','speaker',t.role==='user'?'나':'작성 대화');
    item.append(head,el('div','bubble',t.text));
    if(t.labels?.length){
      const links=el('div','changed');
      for(const label of t.labels){
        const field=doc.fields.find(f=>f.label===label);
        const originalCell=!field && doc.original_cells?.find(cell=>cell.label===label);
        if(!field&&!originalCell)continue;
        const button=el('button','changed-field',label);
        button.type='button';
        button.setAttribute('aria-label',label+' 입력란 열기');
        button.onclick=()=>field?focusField(field.key):chooseOriginalCell(originalCell.key);
        links.append(button);
      }
      item.append(links);
    }
    return item;
  }
  function startChat(d){
    const block=el('div','chat-start');
    if(!d.fields.length){
      if(d.original_cells?.length){
        block.append(el('h3','','어느 칸을 작성할까요?'),el('p','','원본의 칸을 누르거나 목록에서 고른 뒤, 여기에 값만 보내세요.'));
        return block;
      }
      const original=previewMode(d)==='original';
      block.append(el('h3','',original?'원본의 어느 위치에 작성할까요?':'관련 자료를 확인하세요.'));
      block.append(el('p','',originalAvailable(d)?'원본에서 문구를 넣을 위치를 선택하고 여기에 값을 보내세요. 작성 사본으로 저장할 수 있습니다.':'확인한 자료의 보관 위치와 접수 번호를 메모에 남길 수 있습니다.'));
      return block;
    }
    const company=d.id==='intake' && d.fields.find(f=>f.key==='generator_name' && !state.value(d.id,f.key) && !state.pending(d.id,f.key));
    const field=company||firstMissing(d);
    if(!field){
      const waiting=d.fields.filter(f=>state.pending(d.id,f.key)).length;
      block.append(el('h3','',waiting?'확인이 필요한 칸이 있습니다.':'입력한 내용을 검토하세요.'),el('p','',waiting?'칸 목록에서 나중에 확인으로 표시한 항목을 다시 선택하세요.':'서류에서 이름과 수량을 확인한 뒤 작성본을 저장하세요.'));
      return block;
    }
    block.append(el('h3','',company?'어느 업체에서 배출하나요?':field.label+'부터 작성할까요?'));
    block.append(el('p','','아래 항목을 눌러 값을 입력하면 서류에 반영됩니다.'));
    const button=el('button','chat-prompt',field.label+' 입력');
    button.type='button';
    button.onclick=()=>{
      pinField(field.key);
      $('message').focus();
    };
    block.append(button);
    return block;
  }
  function renderChat(){
    const parent=$('turns');
    const history=histories[doc.id]||[];
    parent.replaceChildren();
    if(history.length)parent.append(...history.map(turnNode));
    else parent.append(startChat(doc));
    if(busy.has(doc.id))parent.append(turnNode({role:'pending',text:'입력 내용을 확인하고 있습니다…'}));
    parent.scrollTop=parent.scrollHeight;syncComposer();
  }
  function syncComposer(){
    const waiting=busy.has(doc?.id);
    updateView({hasQuoteHistory:Object.values(histories).some(turns=>turns.some(t=>t.role==='user')),busy:waiting,canSend:!!doc&&!waiting&&!!$('message').value.trim()});
  }
  function addTurn(id,t){const xs=histories[id]||(histories[id]=[]);xs.push(t);if(xs.length>MAX_TURNS)xs.splice(0,xs.length-MAX_TURNS);}
  async function send(prompt){
    const d=doc, msg=prompt||$('message').value.trim();if(!d||!msg||busy.has(d.id))return;
    if(hasSecret(msg)){announce('API 키는 AI 설정에만 입력하세요.');return;}
    const sent=scoped(d),req={doc:d.id,message:msg,values:sent,contexts:[...contexts]};
    const k=$('key').value.trim(),m=$('model').value.trim();if(k)req.key=k;if(m)req.model=m;
    const active=focus[d.id]||firstMissing(d)?.key;if(active)req.focus=active;
    if(pinned[d.id]){req.pinned_field=pinned[d.id];req.focus=pinned[d.id];}
    const targetPoint=originalAvailable(d)?position:null;
    const targetCell=captureCellTarget(d,targetPoint);
    if(targetPoint&&!movingPoint){
      req.original_position=true;
      if(targetPoint.cellKey)req.original_cell=targetPoint.cellKey;
    }
    const controller=new AbortController(), token=epoch;busy.set(d.id,controller);addTurn(d.id,{role:'user',text:msg});
    if(!prompt){$('message').value='';delete drafts[draftKey(d)];}setDirty();renderChat();
    try{
      const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(req),signal:controller.signal});
      if(!r.ok)throw Error(await responseError(r));const res=await r.json();if(token!==epoch)return;
      const labels=[], fields=new Map(d.fields.map(f=>[f.key,f.label]));let stale=false;
      for(const [key,v] of Object.entries(res.updates||{})){
        if(!fields.has(key)||!['string','number'].includes(typeof v))continue;
        if(state.value(d.id,key)!==(sent[key]||'')){stale=true;continue;}
        const value=String(v).slice(0,5000);if(value===state.value(d.id,key))continue;
        setField(d,key,value,'작성 대화');labels.push(fields.get(key));
      }
      if(res.defer_field && res.defer_field===req.pinned_field && !state.value(d.id,res.defer_field).trim())state.setPending(d.id,res.defer_field,true);
      let reply=clean(res.reply,9000)||'입력값을 확인해 주세요.';
      if(req.original_position && typeof res.placement_text==='string'){
        if(doc!==d || (!targetCell && targetPoint!==position)){reply='문서나 위치가 바뀌어 문구를 반영하지 않았습니다. 현재 위치를 확인하고 다시 보내세요.';}
        else {
          await applyOriginalText(res.placement_text,targetCell);if(token!==epoch)return;
          const cell=d.original_cells?.find(c=>c.key===targetPoint.cellKey);
          if(cell){labels.push(cell.label);reply='「'+cell.label+'」 칸에 반영했습니다.';}
        }
      }
      addTurn(d.id,{role:'assistant',text:reply+(stale?'\n응답을 기다리는 동안 직접 수정한 값은 유지했습니다.':''),labels});
      // A follow-up without a label is scoped to the next still-empty field by default.
      focus[d.id]=firstMissing(d)?.key||'';
      if(doc?.id===d.id){count();if(d.fields.length && labels.length)schedulePDF(100,true);}
      else fieldView();
      if(docs.has(res.open_doc)&&res.open_doc!==d.id&&doc?.id===d.id){
        const target=docs.get(res.open_doc),resetFilter=!routeDocs().some(item=>item.id===target.id);
        if(resetFilter)contexts=[];
        stage=target.stage;addTurn(target.id,{role:'assistant',text:reply+(resetFilter?'\n선택 경로 밖의 문서라 전체 경로로 전환했습니다.':'')});
        nav(target.id);announce(resetFilter?'요청한 문서를 열기 위해 전체 경로로 전환했습니다.':'요청한 문서를 열었습니다.');
      }
      setDirty();
    }catch(e){if(token!==epoch||controller.signal.aborted)return;addTurn(d.id,{role:'error',text:'메시지를 처리하지 못했습니다. '+friendly(e)+(originalAvailable(d)?'':'\n입력 항목 탭에서 직접 작성할 수도 있습니다.')});}
    finally{if(token===epoch){busy.delete(d.id);if(doc?.id===d.id)renderChat();}}
  }
  function askNext(){
    const f=firstMissing(doc);
    if(f){
      pinField(f.key);
      const offers=offersForField(doc,f.key);
      const question=offers.length?'이전에 입력한 값이 있습니다. 출처를 확인하고 사용할 값을 선택하세요.':f.help||'이 칸에 기입할 값을 알려주세요.';
      addTurn(doc.id,{role:'assistant',text:'「'+f.label+'」 '+question});
    }else{
      const pending=doc.fields.filter(field=>state.pending(doc.id,field.key)).length;
      addTurn(doc.id,{role:'assistant',text:pending?'나중에 확인할 칸이 '+pending+'개 있습니다. 칸 목록에서 다시 선택해 주세요.':'연결된 입력칸을 모두 채웠습니다. PDF에서 기입 내용을 확인하세요.'});
    }
    setDirty();renderChat();$('message').focus();
  }
  function openDialog(id){const d=$(id);if(!d.open)d.showModal();}
  function openMemo(){if(!doc)return;$('memoTitle').textContent=doc.title;$('memo').value=notes[doc.id]||'';openDialog('memoDlg');}
  function openContexts(){
    const list=$('contextList');list.replaceChildren();
    for(const c of cfg.contexts){
      const label=el('label'),cb=el('input');cb.type='checkbox';cb.value=c.id;cb.checked=c.id==='common'||contexts.includes(c.id);cb.disabled=c.id==='common';
      label.append(cb,document.createTextNode(c.label+(c.id==='common'?' · 항상 표시':'')));list.append(label);
    }
    openDialog('contextsDlg');
  }
  function applyContexts(all){
    contexts=all?[]:Array.from($('contextList').querySelectorAll('input:checked:not(:disabled)'),n=>n.value);
    setDirty();nav(doc?.id);$('contextsDlg').close();
  }
  function intakeSummary(source){
    if(!source)return null;
    const r=source.record;
    const mapped=(docId,key)=>intakeOffers(source,docs.get(docId),key);
    const amount=mapped('intake','quantity_value')[0];
    const periods=mapped('analysis','row1_quantity');
    const omitted=[...(r.missing||[]),...(r.not_applicable||[])];
    const regionPart=key=>!omitted.includes('region')&&!omitted.includes('region.'+key) ? clean(r.region?.[key]) : '';
    const monthly=periods.map(offer=>offer.note || (offer.value+' kg/월'));
    return {
      id:r.case_id, version:r.case_version, name:r.name, synthetic:source.synthetic, published:source.published,
      notice:source.sample_notice, evidenceCount:Array.isArray(r.evidence)?r.evidence.length:0,
      currentLot:amount?.paired?.value ? amount.value+' '+amount.paired.value : '',
      process:mapped('intake','generation_process')[0]?.value||'', region:[regionPart('sido'),regionPart('sigungu')].filter(Boolean).join(' '),
      monthly
    };
  }
  function updateSourceView(){updateView({intakeSummary:intakeSummary(intakeSource)});}
  function cancelIntakeImport(){intakeFileNo++;stagedIntakes=[];}
  async function readIntakeFile(file){
    cancelIntakeImport();caseFileNo++;
    const fileNo=intakeFileNo, token=epoch;
    if(!cfg || !file || typeof file.text!=='function' || file.size>MAX_FILE)throw Error('16 MiB 이하의 기초정보 파일을 선택하세요.');
    const content=await file.text();
    if(hasSecret(content))throw Error('API 키로 보이는 문자열이 있습니다. 키를 제외한 파일을 선택하세요.');
    const sources=parseIntakeFile(content);
    if(fileNo!==intakeFileNo || token!==epoch)throw Error('작업이나 선택한 파일이 바뀌었습니다. 사용할 파일을 다시 고르세요.');
    stagedIntakes=sources;
    return sources.map(source=>({...intakeSummary(source),token:fileNo}));
  }
  function blankCase(){
    discardRuntime();
    state=new CaseFields();originalPoints=obj();photos.replace([]);notes=obj();histories=obj();
    caseTitle='';stage='';contexts=[];doc=null;intakeSource=null;
  }
  function useIntakeCase(fileNo,id){
    if(fileNo!==intakeFileNo)throw Error('선택 목록이 바뀌었습니다. 사용할 파일을 다시 고르세요.');
    const chosen=stagedIntakes.find(s=>s.record.case_id===id);
    if(!chosen)throw Error('이번 작업에서 사용할 건을 하나 선택하세요.');
    const different=intakeSource && (intakeSource.record.case_id!==id || intakeSource.synthetic!==chosen.synthetic);
    if(different){
      if(!confirm('다른 건으로 새 작업을 시작할까요? 현재 서류·사진·대화는 이 탭에서 닫힙니다. 보관하려면 취소 후 작업 파일을 먼저 저장하세요.'))return false;
      blankCase();
    }else if(intakeSource && JSON.stringify(intakeSource)!==JSON.stringify(chosen)){
      if(!confirm('같은 건의 기초정보를 교체할까요? 이미 승인해 기입한 값과 출처는 유지되고 빈칸의 후보가 새 자료로 바뀝니다.'))return false;
    }
    intakeSource=chosen;
    if(!caseTitle)caseTitle=clean(chosen.record.name||id,100);
    if($('caseDlg').open)$('caseName').value=caseTitle;
    cancelIntakeImport();caseFileNo++;
    updateSourceView();setDirty();
    if(!doc)nav('intake');
    else {count();originalView();renderChat();}
    announce('기초정보를 연결했습니다. 작성할 칸에서 후보와 출처를 확인하세요.');
    return true;
  }
  function discardRuntime(){
    cancelIntakeImport();caseFileNo++;epoch++;position=null;movingPoint=false;cachedCopy=null;pageSize=null;for(const c of busy.values())c.abort();busy=new Map();focus=obj();pinned=obj();drafts=obj();
  }
  function resetCase(){
    if(!confirm('현재 작업을 닫고 새 작업을 시작할까요? 저장하지 않은 내용은 사라집니다.'))return;
    blankCase();updateSourceView();setDirty(false);nav();$('caseDlg').close();announce('새 작업을 열었습니다.');
  }
  function exportCase(){
    if(!cfg)return;
    const data={format:FILE_TAG,version:2,title:caseTitle,stage,doc:doc.id,contexts,fieldState:state.exportState(),intakeSource,originalPoints,attachments:photos.all(),notes,histories};
    const json=JSON.stringify(data,null,2);if(hasSecret(json)){$('caseStatus').textContent='키로 보이는 값이 있어 저장하지 않았습니다. 입력 내용과 메모에서 키를 지워 주세요.';return;}
    const blob=new Blob([json],{type:'application/json'});
    if(blob.size>MAX_FILE){$('caseStatus').textContent='작업 파일이 너무 큽니다. 메모나 입력 내용을 줄여 주세요.';return;}
    const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=safeName(caseTitle||'폐기물_서식_작업')+'.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
    setDirty(false);$('caseStatus').textContent='작업 JSON 다운로드를 시작했습니다.';
  }
  const isRecord = v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
  async function importCase(file){
    if(!file)return;const fileNo=++caseFileNo, token=epoch;cancelIntakeImport();try{
      if(file.size>MAX_FILE)throw Error('16MB 이하의 작업 JSON 파일을 선택하세요.');
      const content=await file.text();if(hasSecret(content))throw Error('키로 보이는 값이 포함된 파일입니다. 키를 지운 사본으로 시도하세요.');const v=JSON.parse(content);if(!isRecord(v)||v.format!==FILE_TAG||![1,2].includes(v.version))throw Error('이 작업실에서 저장한 JSON 파일을 선택하세요.');
      const cleanIntake=restoreIntakeSource(v.intakeSource);
      const cleanState=CaseFields.fromFile(v,docs);
      const downloadedPoints=readOriginalPoints(v.originalPoints,docs);
      for(const [id,points] of Object.entries(downloadedPoints)){
        if(!points.length)continue;
        const original=await fetch(docs.get(id).blank_url);
        if(!original.ok)throw Error('원본 PDF를 불러오지 못했습니다.');
        await writeCopy(docs.get(id),await encodePdf(await original.blob()),points);
      }
      const cleanPhotos=await readCaseAttachments(v.attachments,docs);
      const cleanNotes=obj(),cleanHist=obj();
      for(const d of cfg.documents){
        if(isRecord(v.notes)&&typeof v.notes[d.id]==='string')cleanNotes[d.id]=clean(v.notes[d.id],8000);
        const h=isRecord(v.histories)&&Array.isArray(v.histories[d.id])?v.histories[d.id]:[];
        const labels=new Set([...d.fields,...(d.original_cells||[])].map(f=>f.label));
        cleanHist[d.id]=h.slice(-MAX_TURNS).filter(t=>isRecord(t)&&['user','assistant','error'].includes(t.role)&&typeof t.text==='string').map(t=>({role:t.role,text:clean(t.text,12000),labels:Array.isArray(t.labels)?t.labels.filter(l=>labels.has(l)).slice(0,100):[]}));
      }
      if(token!==epoch || fileNo!==caseFileNo)return;
      if((dirty||intakeSource||photos.all().length||Object.keys(originalPoints).length||Object.keys(state.entries).length||Object.keys(state.legacy).length||Object.values(histories).some(h=>h.length))&&!confirm('현재 작업을 불러온 파일로 바꿀까요? 저장하지 않은 내용은 사라집니다.'))return;
      discardRuntime();intakeSource=cleanIntake;updateSourceView();state=cleanState;originalPoints=downloadedPoints;photos.replace(cleanPhotos);notes=cleanNotes;histories=cleanHist;caseTitle=clean(v.title,100);stage=cfg.stages.some(s=>s.id===v.stage)?v.stage:'';
      contexts=cfg.contexts.filter(c=>c.id!=='common'&&Array.isArray(v.contexts)&&v.contexts.includes(c.id)).map(c=>c.id);
      doc=null;nav(docs.has(v.doc)?v.doc:undefined);setDirty(false);$('caseDlg').close();announce('작업을 불러왔습니다.');
    }catch(e){$('caseStatus').textContent='파일을 열지 못했습니다. '+(e instanceof SyntaxError?'유효한 JSON 파일이 아닙니다.':friendly(e));}
    finally{$('importFile').value='';}
  }
  // 거래방에서 연 서류: 업체 정보로 칸을 먼저 채우고, 작성 중임을 알리고, 작성본을 채팅방에 공유한다.
  async function roomLink(room,item){
    const base='/api/p/rooms/'+encodeURIComponent(room)+'/docs/'+encodeURIComponent(item), d=doc, bar=$('roomBar');
    const post=body=>fetch(base+(body.pdf?'/share':''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    let info;
    try{
      const r=await fetch(base+'/prefill',{cache:'no-store'});
      if(!r.ok)throw Error(await responseError(r));
      info=await r.json();
    }catch(e){announce('거래방 정보를 불러오지 못했습니다. '+friendly(e));return;}
    const labels=[];
    for(const f of d.fields){
      const v=info.values?.[f.key];
      if(typeof v==='string'&&v&&!state.value(d.id,f.key).trim()){
        setField(d,f.key,clean(v),clean(info.sources?.[f.key],80)||'거래방 정보');labels.push(f.label);
      }
    }
    caseTitle=clean(info.room_title,100);
    if(labels.length){
      addTurn(d.id,{role:'assistant',text:'가입할 때 입력한 업체 정보와 거래 조건으로 '+labels.length+'개 칸을 먼저 채웠습니다. 내용을 확인하고 필요한 곳을 고쳐 주세요.',labels});
      if(doc===d){count();buildFields();renderChat();schedulePDF(80,true);}
    }
    setDirty(false);
    const status=el('span','room-status'), action=el('button','btn primary',info.shared?'채팅방에 공유':'준비 완료로 표시');
    const others=names=>{status.textContent=Array.isArray(names)&&names.length?names.join(', ')+'에서도 작성 중입니다':'';};
    const beat=async()=>{try{const r=await post({action:'editing',values:scoped(d)});if(r.ok)others((await r.json()).others);}catch{}};
    action.type='button';
    action.onclick=async()=>{
      action.disabled=true;
      try{
        let r;
        if(info.shared){
          if(doc!==d||!pdfUrl)throw Error('이 서류의 작성본이 화면에 표시된 뒤에 공유하세요.');
          const blob=await (await fetch(pdfUrl)).blob();
          r=await post({pdf:await encodePdf(blob),values:scoped(d)});
        }else r=await post({action:'done'});
        if(!r.ok)throw Error(await responseError(r));
        setDirty(false);announce(info.shared?'작성본을 거래방 채팅에 공유했습니다. 거래방 탭에서 확인하세요.':'거래방에 준비 완료로 표시했습니다.');
      }catch(e){announce(friendly(e));}
      action.disabled=false;
    };
    others(info.others);
    bar.replaceChildren(el('strong','',info.title),el('span','room-name',info.room_title+' · '+(info.shared?'함께 작성하는 서류':'우리 업체가 준비하는 서류')),status,action);
    bar.hidden=false;document.body.classList.add('in-room');
    beat();setInterval(beat,15000);
    window.addEventListener('pagehide',()=>navigator.sendBeacon(base,new Blob([JSON.stringify({action:'stop'})],{type:'application/json'})));
  }
  function bind(){
    document.querySelectorAll('dialog .close').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
    $('allContexts').onclick=()=>applyContexts(true);$('applyContexts').onclick=()=>applyContexts(false);
    $('memo').oninput=e=>{notes[doc.id]=e.target.value;updateView({hasMemo:!!e.target.value});setDirty();};
    $('caseName').oninput=e=>{caseTitle=e.target.value;setDirty();};$('saveCase').onclick=exportCase;$('loadCase').onclick=()=>$('importFile').click();$('importFile').onchange=e=>importCase(e.target.files[0]);$('newCase').onclick=resetCase;
    $('message').oninput=()=>{if(doc)drafts[draftKey(doc)]=$('message').value;syncComposer();};
    $('message').oncompositionstart=()=>{composing=true;};$('message').oncompositionend=()=>{composing=false;};
    $('message').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!composing&&!e.isComposing&&e.keyCode!==229){e.preventDefault();send();}};
    $('compose').onsubmit=e=>{e.preventDefault();send();};
  }
  async function init(){
    try{
      const r=await fetch('/api/config',{cache:'no-store'});if(!r.ok)throw Error(await responseError(r));
      const v=await r.json();if(!Array.isArray(v.documents)||!v.documents.length||!Array.isArray(v.stages)||!Array.isArray(v.contexts))throw Error('문서 구성을 읽을 수 없습니다.');
      cfg=v;for(const d of cfg.documents){if(!Array.isArray(d.fields))d.fields=[];docs.set(d.id,d);}
      $('keyHint').textContent=cfg.has_env_key?'서버의 .env 파일에 키가 있어 따로 입력하지 않아도 됩니다. 다른 키를 잠깐 쓰려면 이 칸에 입력하세요.':'입력한 키는 이 탭에서만 유지되며 작업 파일에 저장되지 않습니다.';
      $('model').placeholder=cfg.default_model||'서버 기본 모델';
      const link=new URLSearchParams(location.search);
      nav(docs.has(link.get('doc'))?link.get('doc'):undefined);
      if(link.get('room')&&link.get('item'))roomLink(link.get('room'),link.get('item'));
    }catch(e){updateView({description:'문서 목록을 불러오지 못했습니다. '+friendly(e)});const b=el('button','btn','다시 연결');b.onclick=init;$('pdfStatus').replaceChildren(b);}
  }
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  bind();init();
  return {
    settings:()=>openDialog('settingsDlg'),
    openCase(){
      $('caseName').value=caseTitle;
      $('caseStatus').textContent='';
      openDialog('caseDlg');
    },
    openContexts,openMemo,showTab,showMobile,askNext,turnPage,chooseStage,chooseDoc,pinField,askField,deferField,
    applyOffer:id=>reuse(id,true),
    dismissOffer:id=>reuse(id,false),
    listQuotes,applyQuote,
    readIntakeFile,useIntakeCase,cancelIntakeImport,applyOriginalOffer,dismissOriginalOffer,
    originalPickMode,useOriginalCells,chooseOriginalCell,askOriginalCell,chooseOriginalPoint,selectOriginalPoint,removeOriginalPoint,cancelOriginalPoint,applyOriginalText,moveOriginalPoint,setOriginalSize,
    addPhotos(id,stamp,items,key){
      checkPhotoScope(id,stamp,key);
      const added=photos.add(id,items);
      if(key)for(const item of added)photos.link(id,item.id,key);
      changedPhotos();
    },
    removePhoto(id,stamp,photo){checkPhotoScope(id,stamp);photos.remove(id,photo);changedPhotos();},
    linkPhoto(id,stamp,photo,key){checkPhotoScope(id,stamp,key);photos.link(id,photo,key);changedPhotos();},
    applyPhotoValue(id,stamp,photoId,key,raw){
      checkPhotoScope(id,stamp,key);
      const f=doc.fields.find(field=>field.key===key);
      const photo=photos.forDoc(id).find(item=>item.id===photoId);
      const value=typeof raw==='string'?raw.trim():'';
      if(!photo||!f||!value||value.length>5000||hasSecret(value))throw Error('사진, 입력할 칸과 값을 확인해 주세요.');
      const previous=state.value(id,key);
      if(previous && previous!==value && !confirm('「'+f.label+'」의 기존 값 ‘'+previous+'’을 ‘'+value+'’로 바꿀까요?'))return false;
      photos.link(id,photoId,key);
      setField(doc,key,value,'사진 · '+photo.name);
      pinField(key,false);count();changedPhotos();
      addTurn(id,{role:'assistant',text:photo.name+'에서 확인한 값을 반영했습니다.',labels:[f.label]});
      renderChat();schedulePDF(80,true);return true;
    },
    guide:()=>send('어떤 서류가 필요해?'),
    goFields(){
      if(doc?.fields.length)focusField(firstMissing(doc)?.key||doc.fields[0].key);
    },
  };
}
