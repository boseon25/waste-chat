import { useState } from 'react';
import { Crosshair, List, Move, Pin, ScanLine, Trash2 } from 'lucide-react';
import {ValueOffers} from './ValueOffers.jsx';
import './original-placement.css';

const percent = n => Math.round(n * 1000) / 10;

function enterChat(actions) {
  actions.current?.showMobile('chat');
  requestAnimationFrame(() => document.getElementById('message')?.focus({preventScroll:true}));
}

function choose(actions, x, y) {
  actions.current?.chooseOriginalPoint(x, y);
  enterChat(actions);
}

export async function selectOriginalCell(actions, key) {
  if (!key) return;
  if (await actions.current?.chooseOriginalCell(key)) enterChat(actions);
}

export function openCellPicker(actions) {
  actions.current?.showMobile('chat');
  requestAnimationFrame(() => document.getElementById('originalCellPicker')?.focus({preventScroll:true}));
}

export function OriginalToolbar({ view, actions }) {
  const [coords, setCoords] = useState(false);
  const hasCells = view.originalCells.length > 0;
  const cellsMode = hasCells && view.originalMode === 'cells';
  const ready = view.originalReady && !view.pageBusy;
  function selectCoords(event) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    choose(actions, Number(values.get('x')) / 100, Number(values.get('y')) / 100);
    setCoords(false);
  }
  return <div className="original-tools">
    <div className="original-actions">
      {hasCells && <div className="original-modes" role="group" aria-label="원본 입력 방식">
        <button id="originalCellsMode" type="button" aria-pressed={cellsMode}
          onClick={() => {actions.current?.useOriginalCells(); setCoords(false);}}>
          <ScanLine aria-hidden="true" />칸 선택
        </button>
        <button id="originalManualMode" type="button" aria-pressed={!cellsMode}
          onClick={() => {actions.current?.originalPickMode(); setCoords(false);}}>
          <Crosshair aria-hidden="true" />자유 배치
        </button>
      </div>}
      {cellsMode ? <button type="button" id="chooseOriginalCellFromList" disabled={!ready} onClick={() => openCellPicker(actions)}>
        <List aria-hidden="true" />목록에서 칸 찾기
      </button> : <>
        <button id="startOriginalPlacement" type="button" disabled={!ready} aria-pressed={Boolean(view.originalPick)}
          onClick={() => { actions.current?.originalPickMode(); setCoords(false); }}>
          <Pin aria-hidden="true" />원본에 문구 배치
        </button>
        <button type="button" id="toggleOriginalCoords" disabled={!ready} aria-expanded={coords}
          onClick={() => {if(!coords && !view.originalPick) actions.current?.originalPickMode(); setCoords(!coords);}}>
          <Crosshair aria-hidden="true" />좌표로 선택
        </button>
      </>}
      {view.originalPoints.length > 0 && view.originalHref && <a id="downloadCleanOriginal" href={view.originalHref} download={view.title+'_원본.pdf'}>원본 PDF 저장</a>}
    </div>
    {cellsMode && <p>{view.originalCells.some(c => c.page === view.page)
      ? '작성할 칸을 누르세요.' : '이 쪽에는 지정된 입력 칸이 없습니다. 문구를 넣으려면 자유 배치를 선택하세요.'}</p>}
    {!cellsMode && view.originalPick && <p>{view.originalMoving ? '문구를 옮길 위치를 누르세요.' : '문구가 시작될 위치를 누르세요.'}</p>}
    {!cellsMode && coords && <form className="original-coords" onSubmit={selectCoords}>
      <label>왼쪽에서 (%)<input autoFocus name="x" type="number" min="0" max="100" step="0.1" required defaultValue="20" /></label>
      <label>위에서 (%)<input name="y" type="number" min="0" max="100" step="0.1" required defaultValue="25" /></label>
      <button id="confirmOriginalCoords" type="submit" disabled={!ready}>위치 지정</button>
    </form>}
  </div>;
}

export function OriginalHitTargets({ view, actions }) {
  if (!view.originalReady || view.pageBusy) return null;
  const selected = view.originalSelected;
  if (view.originalMode === 'cells' && view.originalCells.length) return view.originalCells
    .filter(c => c.page === view.page && c.box)
    .map(cell => {
      const [left, top, right, bottom] = cell.box;
      return <button type="button" key={cell.key} className="pdf-hotspot original-cell"
        data-original-cell={cell.key} aria-pressed={selected?.cellKey === cell.key}
        aria-label={cell.label + ' 선택'} title={cell.label}
        style={{left:left*100+'%',top:top*100+'%',width:(right-left)*100+'%',height:(bottom-top)*100+'%'}}
        onClick={() => selectOriginalCell(actions,cell.key)} />;
    });
  const saved = view.originalPoints.filter(p => p.page === view.page);
  const selectionHere = selected && selected.page === view.page;
  return <>
    {view.originalPick && <button className="original-paper-target" id="originalPaperTarget" type="button" tabIndex={-1}
      aria-label="PDF 문구 배치 위치 선택" onClick={e => {
        const box = e.currentTarget.getBoundingClientRect();
        if (!box.width || !box.height) return;
        const x = e.detail === 0 ? .25 : (e.clientX - box.left) / box.width;
        const y = e.detail === 0 ? .25 : (e.clientY - box.top) / box.height;
        choose(actions, Math.max(0,Math.min(x,1)), Math.max(0,Math.min(y,1)));
      }} />}
    {saved.map((p, i) => <button key={p.id} type="button" className="original-mark" data-placement-id={p.id}
      aria-label={(p.page + 1) + '쪽 문구 ' + (i + 1) + ' 수정 · ' + p.text} title={p.cellLabel || p.text} aria-pressed={selected?.id === p.id}
      style={{left:percent(p.x)+'%',top:percent(p.y)+'%'}}
      onClick={() => {actions.current?.selectOriginalPoint(p.id); enterChat(actions);}}><span /></button>)}
    {selectionHere && <span id="originalAnchor" className="original-anchor" style={{left:percent(selected.x)+'%',top:percent(selected.y)+'%'}} aria-hidden="true" />}
  </>;
}

function TextEditor({ point, cellLabel, actions }) {
  const [text, setText] = useState(point.text || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function apply(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError('');
    try { await actions.current?.applyOriginalText(text); }
    catch (err) { setError(err instanceof Error ? err.message : '문구와 배치 위치를 확인해 주세요.'); }
    finally { setBusy(false); }
  }
  return <details className="original-editor"><summary>문구 직접 편집</summary>
    <form onSubmit={apply}>
      <label>{cellLabel ? cellLabel + ' 입력값' : '이 위치에 넣을 문구'}<textarea id="originalText" rows="2" maxLength={500} value={text} onChange={e=>setText(e.target.value)} /></label>
      <button id="applyOriginalText" type="submit" disabled={busy || !text.trim()}>{busy ? '반영 중…' : '사본에 반영'}</button>
      {error && <p role="alert">{error}</p>}
    </form>
  </details>;
}

function ConfirmPosition({point, actions}) {
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  async function confirm() {
    setBusy(true); setError('');
    try { await actions.current?.applyOriginalText(point.text); }
    catch (err) { setError(err instanceof Error ? err.message : '문구의 위치와 크기를 확인해 주세요.'); }
    finally { setBusy(false); }
  }
  return <div className="original-save-position">
    <button type="button" id="confirmOriginalPosition" disabled={busy} onClick={confirm}>{busy ? '반영 중…' : '위치·크기 반영'}</button>
    {error && <p role="alert">{error}</p>}
  </div>;
}

export function OriginalInspector({ view, actions }) {
  const p = view.originalSelected;
  const marks = view.originalPoints;
  const cellsMode = view.originalMode === 'cells' && view.originalCells.length > 0;
  if (!cellsMode && !p && !marks.length) return null;
  const saved = marks.find(m=>m.id === p?.id);
  const repositioned = saved && ['x','y','page','size'].some(k => p[k] !== saved[k]);
  if (cellsMode) {
    const cell = view.originalCells.find(c=>c.key === p?.cellKey);
    return <section className="original-inspector cell-inspector" aria-label="원본 서식의 입력 칸">
      <label className="original-mark-list" htmlFor="originalCellPicker">작성할 칸
        <select id="originalCellPicker" disabled={!view.originalReady || view.pageBusy} value={cell?.key || ''} onChange={e=>selectOriginalCell(actions, e.target.value)}>
          <option value="" disabled>항목 선택…</option>
          {view.originalCells.map(c=><option key={c.key} value={c.key}>
            {c.page > 0 || view.pageCount > 1 ? (c.page + 1) + '쪽 · ' : ''}{c.label}{c.filled ? ' · 입력됨' : c.offers ? ' · 가져올 값 있음' : ''}
          </option>)}
        </select>
      </label>
      {cell && p && <>
        <h3 id="originalSelectedLocation" className="original-location"><Pin aria-hidden="true" />{cell.label}</h3>
        {cell.help && <p id="originalCellHelp" className="original-chat-hint">{cell.help}</p>}
        {p.text && <div className={'cell-value' + (p.source ? ' with-source' : '')}><span>현재 값</span><strong>{p.text}</strong>
          {p.source && <small className="offer-source">출처 · {p.source}</small>}</div>}
        <ValueOffers id="originalFieldOffers" key={view.activeId+':'+p.cellKey} offers={view.originalFieldOffers}
          apply={id=>actions.current?.applyOriginalOffer(id)} dismiss={id=>actions.current?.dismissOriginalOffer(id)}/>
        <div className="original-mark-actions">
          {saved && <button type="button" id="deleteOriginalPoint" onClick={()=>actions.current?.removeOriginalPoint()}><Trash2 aria-hidden="true" />값 지우기</button>}
        </div>
        <TextEditor key={[view.activeId,p.id,p.cellKey,p.text].join('|')} point={p} cellLabel={cell.label} actions={actions} />
      </>}
    </section>;
  }
  return <section className="original-inspector" aria-label="원본 서식에 배치할 문구">
    {marks.length > 0 && <label className="original-mark-list">배치한 문구
      <select id="originalMarkList" value={saved ? p.id : ''} onChange={e=>e.target.value && actions.current?.selectOriginalPoint(e.target.value)}>
        <option value="">문구 선택…</option>
        {marks.map(m=><option key={m.id} value={m.id}>{m.page+1}쪽 · {m.cellLabel ? m.cellLabel+' · ' : ''}{m.text.slice(0,48)}</option>)}
      </select>
    </label>}
    {p && <><div id="originalSelectedLocation" className="original-location"><Pin aria-hidden="true" />
      <strong>{p.page+1}쪽 선택 위치</strong>
      <small>왼쪽 {percent(p.x)}% · 위 {percent(p.y)}%</small>
    </div>
    <p className="original-chat-hint">아래 채팅에 이 위치에 넣을 문구를 보내세요.</p>
    <div className="original-mark-actions">
      <button type="button" id="moveOriginalPoint" disabled={view.pageBusy} onClick={()=>{
        actions.current?.moveOriginalPoint(); actions.current?.showMobile('doc');
      }}><Move aria-hidden="true" />위치 이동</button>
      <label>글자 크기<select id="originalSize" value={p.size} onChange={e=>actions.current?.setOriginalSize(Number(e.target.value))}>
        {Array.from({length:31},(_,i)=>i+6).map(n=><option key={n} value={n}>{n} pt</option>)}
      </select></label>
      {saved && <button type="button" id="deleteOriginalPoint" onClick={()=>actions.current?.removeOriginalPoint()}><Trash2 aria-hidden="true" />삭제</button>}
      <button type="button" onClick={()=>actions.current?.cancelOriginalPoint()}>선택 해제</button>
    </div>
    {repositioned && !view.originalMoving && <ConfirmPosition key={[p.id,p.page,p.x,p.y,p.size].join('|')} point={p} actions={actions} />}
    <TextEditor key={[view.activeId,p.id,p.page,p.x,p.y,p.text].join('|')} point={p} actions={actions} />
    </>}
  </section>;
}
