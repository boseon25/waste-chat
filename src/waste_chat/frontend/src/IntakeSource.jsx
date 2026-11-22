import {useEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {FileInput} from 'lucide-react';
import './intake-source.css';

function Summary({source, active=false}) {
  const reference = [
    ['발생 공정', source.process],
    ['지역', source.region],
    ['이번 물량', source.currentLot],
    ['월별 발생량', source.monthly?.join(' / ')],
  ].filter(([,value])=>typeof value==='string' && value.length);
  return <div id={active?'activeIntakeSource':'intakeCasePreview'} className="intake-record">
    <div className="intake-record-name">{source.synthetic && <span className="intake-demo">가상 예시</span>}
      <strong>{source.name || source.id}</strong>
    </div>
    <div className="intake-meta">{source.id} · 버전 {source.version}
      {Number.isInteger(source.evidenceCount) && ' · 출처 기록 '+source.evidenceCount+'개'}
    </div>
    {(reference.length>0 || source.notice) && <details key={source.id+':'+source.version} className="intake-reference">
      <summary>기초정보 살펴보기</summary>
      {reference.length>0 && <dl>{reference.map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>}
      {source.notice && <p>{source.notice}</p>}
    </details>}
  </div>;
}

export function IntakeImport({view, actions}) {
  const [records,setRecords] = useState([]);
  const [selected,setSelected] = useState('');
  const [reading,setReading] = useState(false);
  const [status,setStatus] = useState('');
  const file = useRef(null);
  const epoch = useRef(0);
  const host = document.getElementById('intakeImportRoot');
  function clear() {
    epoch.current++;
    setRecords([]); setSelected(''); setReading(false); setStatus('');
    actions.current?.cancelIntakeImport();
  }
  useEffect(() => {
    const dialog = host?.closest('dialog');
    if (!dialog) return;
    dialog.addEventListener('close',clear);
    return () => dialog.removeEventListener('close',clear);
  },[host,actions]);
  async function readFile(event) {
    const inputFile=event.currentTarget.files[0];
    event.currentTarget.value='';
    if (!inputFile) return;
    clear();
    const ticket=epoch.current;
    setReading(true);
    try {
      const rows=await actions.current.readIntakeFile(inputFile);
      if (epoch.current!==ticket) return;
      setRecords(rows);
      requestAnimationFrame(()=>document.getElementById('intakeCaseSelect')?.focus());
    } catch (error) {
      if (epoch.current===ticket) setStatus(error.message || '기초정보 파일을 확인하세요.');
    } finally {
      if (epoch.current===ticket) setReading(false);
    }
  }
  const picked=records.find(row=>row.id===selected);
  function apply() {
    if (!picked) return;
    try {
      if (!actions.current.useIntakeCase(picked.token,picked.id)) return;
      clear();
      setStatus('기초정보를 연결했습니다. 서류에서 칸을 선택한 뒤 가져올 값을 확인하세요.');
    } catch (error) {
      setStatus(error.message || '선택한 기초정보를 연결하지 못했습니다.');
    }
  }
  if (!host) return null;
  return createPortal(<section className="intake-import" aria-label="기초정보 연결">
    {view.intakeSummary && !records.length && <Summary active source={view.intakeSummary} />}
    <button id="intakeImportButton" className="btn" type="button" disabled={!view.ready||reading} onClick={()=>file.current?.click()}>
      <FileInput size={15} aria-hidden="true"/>{reading?'기초정보 읽는 중…':'기초정보 불러오기'}</button>
    <input id="intakeSourceFile" ref={file} type="file" hidden aria-label="기초정보 파일" accept=".json,.md,.markdown,application/json,text/markdown" onChange={readFile}/>
    {!records.length && !view.intakeSummary && !status && <p className="intake-explainer">JSON 또는 마크다운에서 폐기물 한 건을 선택해 서류 작성에 사용합니다.</p>}
    {records.length>0 && <div className="intake-select">
      <label htmlFor="intakeCaseSelect">연결할 폐기물</label>
      <select id="intakeCaseSelect" value={selected} onChange={e=>{setSelected(e.target.value);setStatus('');}}>
        <option value="">한 건을 선택하세요</option>
        {records.map(r=><option key={r.id} value={r.id}>{r.name||r.id} · {r.id} · v{r.version}{r.synthetic?' · 가상 예시':''}</option>)}
      </select>
      {picked && <Summary source={picked}/>}
      <div className="case-actions"><button id="applyIntakeCase" type="button" className="btn primary" disabled={!picked} onClick={apply}>기초정보 연결</button>
        <button type="button" className="btn" onClick={clear}>취소</button></div>
    </div>}
    <p id="intakeImportStatus" role="status" aria-live="polite">{status}</p>
  </section>,host);
}

export function IntakeStatus({source,onOpen}) {
  if (!source) return null;
  return <button type="button" id="intakeStatusLink" className="intake-status-link"
    title={source.id+' · 버전 '+source.version} onClick={onOpen}>
    {source.synthetic?'가상 예시':'기초정보'} · {source.name||source.id}
  </button>;
}
