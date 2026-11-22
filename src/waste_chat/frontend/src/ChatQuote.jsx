import {useEffect, useRef, useState} from 'react';
import './chatquote.css';

export function ChatQuote({view, actions}) {
  const dialog = useRef(null);
  const excerpt = useRef(null);
  const [rows, setRows] = useState([]);
  const [needle, setNeedle] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const selection = rows.find(r => r.id === selectedId);
  const results = rows.filter(r => (r.title+' '+r.text).includes(needle.trim())).slice(0,40);
  useEffect(() => { dialog.current?.close(); },[view.activeId,view.pinnedKey,view.attachmentEpoch]);
  if (!view.pinnedKey || view.fieldValue?.trim() || !view.hasQuoteHistory) return null;
  function choose(row) { setSelectedId(row?.id || ''); setValue(''); setError(''); }
  function open() {
    const recent = actions.current.listQuotes();
    setRows(recent);setNeedle('');choose(recent[0]);dialog.current.showModal();
  }
  function copySelection() {
    const el=excerpt.current;
    if (el && el.selectionEnd>el.selectionStart) setValue(el.value.slice(el.selectionStart,el.selectionEnd));
  }
  function apply() {
    try {
      actions.current.applyQuote(view.activeId,view.attachmentEpoch,view.pinnedKey,selection.id,value.trim());
      dialog.current?.close();
    } catch (e) { setError(e.message); }
  }
  return <>
    <button type="button" className="quote-open" onClick={open}>이전 대화에서 문구 가져오기</button>
    <dialog ref={dialog} id="quoteDialog" aria-labelledby="quoteTitle" className="quote-dialog">
      <div className="quote-head"><h2 id="quoteTitle">이전 대화에서 가져오기</h2><button type="button" onClick={()=>dialog.current.close()} aria-label="이전 대화 닫기">닫기</button></div>
      <p>이 작업에서 내가 보낸 메시지를 찾고, 현재 칸에 넣을 문구만 선택하세요.</p>
      <label>메시지 검색<input type="search" value={needle} onChange={e=>{
        const q=e.target.value;setNeedle(q);choose(rows.find(r=>(r.title+' '+r.text).includes(q.trim())));
      }}/></label>
      <label>원본 메시지<select value={selectedId} onChange={e=>choose(rows.find(r=>r.id===e.target.value))}>
        {!results.length && <option value="">검색 결과 없음</option>}
        {results.map(row=><option key={row.id} value={row.id}>{row.title} · {row.text.slice(0,55)}</option>)}
      </select></label>
      {selection && <>
        <textarea ref={excerpt} aria-label="원문에서 옮길 부분 선택" rows={3} readOnly value={selection.text} onSelect={copySelection}/>
        <label>가져올 문구<input value={value} onChange={e=>setValue(e.target.value)} placeholder="원문에서 선택하거나, 보이는 문구를 그대로 입력하세요."/></label>
        <small>대상 칸 · {view.fieldOptions.find(f=>f.key===view.pinnedKey)?.label}</small>
      </>}
      <div role="status">{error}</div>
      <button type="button" className="quote-apply" disabled={!value.trim() || !selection?.text.includes(value.trim())} onClick={apply}>이 문구를 칸에 반영</button>
    </dialog>
  </>;
}
