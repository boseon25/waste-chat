import {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Button} from '@astryxdesign/core/Button';
import {IconButton} from '@astryxdesign/core/IconButton';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {ArrowLeft, ChevronLeft, ChevronRight, Download, FileText, FolderOpen, Layers3, Plus, ScanText, Trash2} from 'lucide-react';
import {UITheme} from './theme.jsx';
import './finish.css';

async function requestPdf(action, doc, signal) {
  const response = await fetch('/api/finish/' + action, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    cache: 'no-store',
    body: JSON.stringify({pdf: doc.pdf, page: doc.page, placements: doc.placements}),
    signal
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.error === 'string' ? body.error : `PDF 요청을 처리할 수 없습니다. (${response.status})`);
  }
  return response;
}

function readPdf(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(new Error('선택한 파일을 읽을 수 없습니다.'));
    reader.readAsDataURL(file);
  });
}

function FinishEditor() {
  const [doc, setDoc] = useState(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState({text: 'PDF를 열면 빈칸을 선택할 수 있습니다.'});
  const [x, setX] = useState('');
  const [y, setY] = useState('');
  const [size, setSize] = useState('10');
  const [text, setText] = useState('');
  const [dirty, setDirty] = useState(false);
  const fileInput = useRef(null);
  const stage = useRef(null);
  const inputText = useRef(null);
  const active = useRef(null);
  const previewUrl = useRef('');

  useEffect(() => () => {
    active.current?.abort();
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
  }, []);

  useEffect(() => {
    if (!dirty && !text.trim()) return;
    function guard(event) { event.preventDefault(); event.returnValue = ''; }
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty, text]);

  function message(text, error = false) { setStatus({text, error}); }
  function startJob(label) {
    active.current?.abort();
    const job = new AbortController();
    active.current = job;
    setBusy(true);
    message(label);
    return job;
  }
  function finishJob(job) {
    if (active.current === job) {
      active.current = null;
      setBusy(false);
    }
  }
  function failJob(job, error) {
    if (active.current === job && !job.signal.aborted) {
      message(error instanceof TypeError ? '로컬 서버에 연결할 수 없습니다. 작업실을 실행한 뒤 다시 시도하세요.' : error.message, true);
    }
  }
  async function preview(next, reason, success, job = startJob('페이지 미리보기를 만드는 중입니다…')) {
    let url;
    try {
      const response = await requestPdf('preview', next, job.signal);
      const total = Number(response.headers.get('X-Pdf-Pages'));
      const width = Number(response.headers.get('X-Pdf-Width'));
      const height = Number(response.headers.get('X-Pdf-Height'));
      if (!(total > next.page && width > 0 && height > 0)) throw new Error('페이지 크기를 읽을 수 없습니다.');
      const png = await response.blob();
      if (!png.type.startsWith('image/png')) throw new Error('미리보기 이미지를 읽을 수 없습니다.');
      url = URL.createObjectURL(png);
      const image = new Image();
      image.src = url;
      await image.decode();
      if (active.current !== job || job.signal.aborted) return;
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
      previewUrl.current = url;
      url = null;
      setDoc({...next, total, width, height, image: previewUrl.current});
      const pageChanged = reason === 'open' || next.page !== doc?.page;
      if (pageChanged) stage.current.scrollTo(0, 0);
      if (pageChanged || reason === 'add') { setX(''); setY(''); }
      if (reason === 'open' || reason === 'add') setText('');
      if (reason === 'open') { setSize('10'); setDirty(false); }
      if (reason === 'add' || reason === 'remove') setDirty(true);
      message(success);
    } catch (error) {
      failJob(job, error);
    } finally {
      if (url) URL.revokeObjectURL(url);
      finishJob(job);
    }
  }
  async function openFile(event) {
    const file = event.target.files[0];
    event.target.value = '';
    if (!file || active.current) return;
    if (file.size > 4 * 1024 * 1024) return message('4 MiB 이하의 PDF를 선택하세요.', true);
    if (!file.size) return message('선택한 파일이 비어 있습니다.', true);
    if ((dirty || text.trim()) && !window.confirm('새 PDF를 열면 현재 화면의 추가 기입 내용이 지워집니다. 새 파일을 열까요?')) return;
    const job = startJob('PDF를 여는 중입니다…');
    try {
      const pdf = await readPdf(file);
      if (active.current !== job || job.signal.aborted) return;
      await preview({pdf, name: file.name, page: 0, placements: []}, 'open', '빈칸의 왼쪽 위를 선택한 후 글자를 입력하세요.', job);
    } catch (error) {
      failJob(job, error);
      finishJob(job);
    }
  }
  function pageBy(delta) {
    if (!doc || active.current) return;
    const page = doc.page + delta;
    if (page >= 0 && page < doc.total) preview({...doc, page}, 'page', '선택한 페이지를 표시했습니다.');
  }
  function choosePoint(event) {
    if (!doc || active.current) return;
    const box = event.currentTarget.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const point = (offset, pixels, points) => Math.min(points - 0.1, Math.max(0, offset / pixels * points)).toFixed(1);
    setX(point(event.clientX - box.left, box.width, doc.width));
    setY(point(event.clientY - box.top, box.height, doc.height));
    message(`${doc.page + 1}쪽의 기입 위치를 선택했습니다.`);
  }
  function add(event) {
    event.preventDefault();
    if (!doc || active.current || !event.currentTarget.reportValidity()) return;
    if (doc.placements.length >= 100) return message('문구는 최대 100개까지 추가할 수 있습니다.', true);
    if (Number(x) >= doc.width || Number(y) >= doc.height) return message('페이지 안쪽의 좌표를 입력하세요.', true);
    if (!text.trim()) { message('추가할 글자를 입력하세요.', true); inputText.current.focus(); return; }
    const placements = [...doc.placements, {page: doc.page, x: Number(x), y: Number(y), size: Number(size), text}];
    preview({...doc, placements}, 'add', '추가한 글자를 미리보기에 반영했습니다.');
  }
  function remove(index) {
    if (!doc || active.current) return;
    const placements = doc.placements.filter((_, i) => i !== index);
    preview({...doc, page: doc.placements[index].page, placements}, 'remove', '삭제한 글자를 미리보기에 반영했습니다.');
  }
  async function save() {
    if (!doc?.placements.length || active.current) return;
    if (text.trim() && !window.confirm('입력 중인 글자는 아직 추가되지 않았습니다. 추가한 글자만 저장할까요?')) return;
    const job = startJob('PDF 사본을 만드는 중입니다…');
    try {
      const response = await requestPdf('export', doc, job.signal);
      const pdf = await response.blob();
      if (!pdf.type.startsWith('application/pdf')) throw new Error('PDF 사본을 만들 수 없습니다.');
      if (active.current !== job || job.signal.aborted) return;
      const url = URL.createObjectURL(pdf);
      const link = document.createElement('a');
      link.href = url;
      link.download = doc.name.replace(/\.pdf$/i, '') + '_추가기입.pdf';
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      setDirty(false);
      message(link.download + ' 다운로드를 시작했습니다.');
    } catch (error) {
      failJob(job, error);
    } finally { finishJob(job); }
  }
  const locked = !doc || busy;
  const entries = doc?.placements ?? [];
  const markerVisible = doc && x !== '' && y !== '' && Number(x) >= 0 && Number(x) < doc.width && Number(y) >= 0 && Number(y) < doc.height;

  return <div className="finish">
    <header className="finish-head">
      <div className="finish-brand"><span className="finish-logo"><Layers3 size={20} strokeWidth={1.8}/></span>서식 작업실</div>
      <Button href="/" className="finish-back" label="작업실로 돌아가기" icon={<ArrowLeft size={15}/>} variant="ghost"/>
    </header>
    <main className="finish-main">
      <div className="finish-intro">
        <div className="finish-document">
          <h1>PDF 추가 기입</h1>
          <span className="finish-file" id="fileName" title={doc?.name}>{doc?.name ?? '파일 선택 전'}</span>
        </div>
        <input id="file" ref={fileInput} type="file" hidden accept=".pdf,application/pdf" onChange={openFile} disabled={busy} aria-label="작성할 PDF 파일 선택"/>
        {doc && <Button label="PDF 변경" className="finish-file-btn" variant="secondary" icon={<FolderOpen size={15}/>} onClick={() => fileInput.current.click()} isDisabled={busy}/>}
      </div>
      <div className="finish-grid">
        <section className="finish-panel finish-preview" aria-labelledby="preview-heading">
          <div className="finish-panel-head"><h2 id="preview-heading">페이지 미리보기</h2>
            <nav className="finish-pages" aria-label="페이지 이동">
              <IconButton id="prev" size="sm" label="이전 페이지" icon={<ChevronLeft size={17}/>} onClick={() => pageBy(-1)} isDisabled={locked || doc.page === 0}/>
              <span id="pageNo">{doc ? `${doc.page + 1} / ${doc.total}` : '– / –'}</span>
              <IconButton id="next" size="sm" label="다음 페이지" icon={<ChevronRight size={17}/>} onClick={() => pageBy(1)} isDisabled={locked || doc.page + 1 >= doc.total}/>
            </nav>
          </div>
          <div className="finish-stage" id="stage" ref={stage}>
            {doc ? <div className="finish-sheet" id="sheet" aria-busy={busy}>
              <img id="image" src={doc.image} alt={`${doc.page + 1}쪽 PDF 미리보기`} draggable={false} onClick={choosePoint}/>
              <span id="marker" className="finish-marker" hidden={!markerVisible} style={markerVisible ? {left: `${Number(x)/doc.width*100}%`, top: `${Number(y)/doc.height*100}%`} : undefined} aria-hidden="true"/>
            </div> : <div id="empty" className="finish-placeholder">
              <div className="finish-paper-slot">
                <div className="finish-paper-illustration" aria-hidden="true"><FileText size={34} strokeWidth={1.2}/></div>
                <EmptyState title="원본 PDF 열기" description="PDF · 최대 4 MiB · 64쪽 이하" isCompact
                  actions={<Button label="PDF 선택" className="finish-open" icon={<FolderOpen size={16}/>} variant="primary" onClick={() => fileInput.current.click()} isDisabled={busy}/>}/>
              </div>
            </div>}
          </div>
          <p className={'finish-status' + (status.error ? ' error' : '')} id="status" role="status" aria-live="polite">{status.text}</p>
        </section>
        <aside className="finish-tools" aria-label="추가 기입 도구">
          <section className="finish-panel">
            <div className="finish-editor-head"><h2><ScanText size={18} aria-hidden="true"/>글자 넣기</h2><p>미리보기에서 글자의 시작 위치를 누르거나 아래에 좌표를 입력하세요.</p></div>
            <form id="entryForm" onSubmit={add}><fieldset className="finish-fields" id="editor" disabled={locked}>
              <div className="finish-coords">
                <label htmlFor="x">왼쪽 (pt)<input id="x" type="number" min="0" max={doc?.width} step="any" inputMode="decimal" value={x} onChange={e => setX(e.target.value)} placeholder="0" required/></label>
                <label htmlFor="y">위쪽 (pt)<input id="y" type="number" min="0" max={doc?.height} step="any" inputMode="decimal" value={y} onChange={e => setY(e.target.value)} placeholder="0" required/></label>
                <label htmlFor="size">글자 크기 (pt)<input id="size" type="number" min="6" max="36" step="any" value={size} onChange={e => setSize(e.target.value)} required/></label>
              </div>
              <label htmlFor="entryText">추가할 글자 <span className="finish-limit">{text.length} / 500자</span></label>
              <textarea id="entryText" ref={inputText} value={text} onChange={e => setText(e.target.value)} rows={3} maxLength={500} placeholder="빈칸에 넣을 내용 또는 ✓" required/>
              <Button label="현재 페이지에 추가" className="finish-accent" icon={<Plus size={16}/>} width="100%" type="submit" isDisabled={locked}/>
            </fieldset></form>
          </section>
          <section className="finish-panel">
            <div className="finish-panel-head"><h2>추가한 글자</h2><span className="finish-count" id="count">{entries.length} / 100개</span></div>
            {entries.length === 0 && <p className="finish-list-empty" id="listEmpty">추가한 글자가 없습니다.</p>}
            <ul className="finish-items" id="items">{entries.map((item, i) => <li key={i}>
              <div><small>{item.page + 1}쪽 · {item.size} pt</small><span>{item.text}</span></div>
              <IconButton label={`${i + 1}번째 추가한 글자 삭제`} icon={<Trash2 size={15}/>} size="sm" variant="ghost" onClick={() => remove(i)} isDisabled={busy}/>
            </li>)}</ul>
          </section>
          <div className="finish-save"><Button id="save" label="PDF 사본 저장" variant="primary" className="finish-primary" width="100%" icon={<Download size={16}/>} isDisabled={locked || !entries.length} onClick={save}/>
            <small>원본 파일명 뒤에 ‘_추가기입’이 붙습니다.</small>
            <details className="finish-advice"><summary>기입할 수 있는 서류</summary><p>신청서에서 작성자가 기입하는 빈칸에 사용하세요. 증명서·분석결과서·성적서는 발급기관의 서명과 수치가 담긴 원본을 보관하세요.</p></details>
          </div>
        </aside>
      </div>
    </main>
  </div>;
}

createRoot(document.getElementById('finish-root')).render(<UITheme><FinishEditor/></UITheme>);
