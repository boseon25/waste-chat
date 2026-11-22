import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { createPortal } from 'react-dom';
import { AppShell } from '@astryxdesign/core/AppShell';
import { SideNav, SideNavItem, SideNavSection } from '@astryxdesign/core/SideNav';
import { Button } from '@astryxdesign/core/Button';
import { ProgressBar } from '@astryxdesign/core/ProgressBar';
import { SegmentedControl, SegmentedControlItem } from '@astryxdesign/core/SegmentedControl';
import {
  ArrowDownToLine, ArrowUp, ArrowUpRight, BadgeCheck, ChevronDown, ChevronLeft, ChevronRight,
  CircleHelp, ClipboardList, ExternalLink, FilePenLine, FileText, Files, FolderOpen, Layers3,
  ListChecks, Menu, MessageSquare, NotebookPen, Pin, Route, ScanLine, Settings2, SquarePen, X,
} from 'lucide-react';
import { UITheme } from './theme.jsx';
import { Attachments } from './Attachments.jsx';
import { ChatQuote } from './ChatQuote.jsx';
import { IntakeImport, IntakeStatus } from './IntakeSource.jsx';
import { ValueOffers } from './ValueOffers.jsx';
import { OriginalHitTargets, OriginalInspector, OriginalToolbar, openCellPicker, selectOriginalCell } from './OriginalPlacement.jsx';
import { startWorkspace } from '../../web/workspace.js';
import '../../web/style.css';
import './workspace.css';

const initialView = {
  ready: false, canFilter: false, route: '전체 경로',
  stageId: '', stageOptions: [], documents: [], activeId: '', activeStageLabel: '',
  mobile: 'doc', tab: 'preview', title: '문서 불러오는 중', description: '', previewMode: 'draft',
  kind: '', filled: 0, total: 0, hasMemo: false, busy: false, canSend: false,
  page: 0, pageCount: 0, pageBusy: false, caseTitle: '', dirty: false,
  fieldOptions: [], pinnedKey: '', fieldHelp: '', fieldExample: '', fieldValue: '',
  fieldSource: '', fieldOffers: [], fieldDeferred: false, hotspots: [],
  originalPoints: [], originalSelected: null, originalReady: false, originalPick: false, originalHref: '',
  originalCells: [], originalMode: 'manual', originalFieldOffers: [], intakeSummary: null,
};

function DocumentNav({ view, actions, onClose }) {
  const activeRef = useRef(null);
  useEffect(() => {
    const button = activeRef.current;
    if (!button?.getClientRects().length) return;
    const scrolling = button.closest('.rail-scroll')?.parentElement;
    if (!scrolling) return;
    const rect = button.getBoundingClientRect();
    const bounds = scrolling.getBoundingClientRect();
    if (rect.top < bounds.top || rect.bottom > bounds.bottom) button.scrollIntoView({block:'nearest'});
  }, [view.activeId]);

  const choose = id => {
    actions.current?.chooseDoc(id);
    actions.current?.showMobile('doc');
    onClose?.();
  };

  return (
    <SideNav id="documentNav" className="case-rail" aria-label="문서 목록"
      header={<div className="rail-brand"><span className="logo-tile"><Layers3 /></span><span>서식 작업실</span>
        {onClose && <Button id="closeNav" variant="ghost" label="문서 목록 닫기" isIconOnly icon={<X />} onClick={onClose} />}</div>}
      topContent={<div className="rail-filter"><label htmlFor="stage">업무 단계</label>
          <div className="rail-select"><select id="stage" value={view.stageId} disabled={!view.ready} onChange={e => actions.current?.chooseStage(e.target.value)}>
            <option value="">전체 단계</option>
            {view.stageOptions.filter(s => s.id).map(s => <option value={s.id} key={s.id}>{s.label}</option>)}
          </select><ChevronDown /></div>
        </div>}
      footer={<button type="button" className="route-control" id="contextsBtn" disabled={!view.canFilter}
        aria-label={'업무 경로 선택 · ' + view.route}
        onClick={() => {onClose?.(); actions.current?.openContexts();}}>
        <Route /><span><small>업무 경로</small><strong id="routeLabel">{view.route}</strong></span><Settings2 />
      </button>}>
      <div className="rail-scroll">
        {view.stageOptions.map(stage => {
          const docs = view.documents.filter(d => d.stage === stage.id);
          if (!docs.length) return null;
          return <SideNavSection className="rail-section" key={stage.id} title={stage.label} endContent={<span className="section-count">{docs.length}</span>}>
            {docs.map(d => {
              const selected = view.activeId === d.id;
              const Glyph = d.kind === '법정서식' ? ClipboardList : /증빙|발급/.test(d.kind) ? BadgeCheck : FileText;
              return <SideNavItem key={d.id} className="rail-doc" data-doc-id={d.id} ref={selected ? activeRef : undefined}
                title={d.title} label={d.title} icon={<Glyph size={15} />} isSelected={selected} onClick={() => choose(d.id)} />;
            })}
          </SideNavSection>;
        })}
      </div>
    </SideNav>
  );
}

function TopBar({ view, actions, compact, openNav }) {
  return <header className="work-topbar">
    <div className="crumbs">
      {compact && <Button id="openNav" className="open-nav" variant="ghost" label="문서 목록 열기" aria-haspopup="dialog" aria-controls="navDrawer" isIconOnly icon={<Menu />} onClick={openNav} />}
      <FolderOpen className="crumb-icon" />
      <span className="crumb-case" title={view.caseTitle}>{view.caseTitle || '작업공간'}</span>
      <ChevronRight className="crumb-sep" />
      <span className="crumb-current">{view.activeStageLabel || '문서'}</span>
    </div>
    <div className="toolbar-actions">
      <Button id="settingsBtn" className="settings-action" variant="ghost" label="AI 설정" icon={<Settings2 />} onClick={() => actions.current?.settings()} />
      <Button id="caseBtn" className="case-action" variant="secondary" label="작업 파일" icon={<FolderOpen />} isDisabled={!view.ready} onClick={() => actions.current?.openCase()} />
    </div>
  </header>;
}

function DocumentHeading({ view }) {
  return <header className="document-heading">
    <div className="heading-copy">
      {view.kind && <div className="doc-kind"><FileText />{view.kind}</div>}
      <h1 id="docTitle">{view.title}</h1>
      <p id="docDesc">{view.description}</p>
    </div>
    <a id="downloadPdf" className="download-pdf" hidden><ArrowDownToLine /><span>{view.previewMode === 'original' ? (view.originalPoints.length ? '작성 사본 PDF 저장' : '원본 PDF 저장') : 'PDF 저장'}</span></a>
  </header>;
}

function FieldInspector({ view, actions }) {
  if (!view.total) return null;
  const field = view.fieldOptions.find(item => item.key === view.pinnedKey);
  const reusable = view.fieldOptions.filter(item => !item.filled && item.offers);
  return <section className="field-context" id="fieldContext" aria-label="작성 항목 선택">
    <label htmlFor="fieldPicker"><Pin size={13} />작성할 항목</label>
    <div className="field-select">
      <select id="fieldPicker" value={field?.key || ''} onChange={e => actions.current?.pinField(e.target.value)}>
        <option value="">항목 선택…</option>
        {view.fieldOptions.map(item => {
          const status = item.filled ? '입력됨' : item.deferred ? '나중에 확인' : item.offers ? '가져올 값 있음' : '';
          return <option value={item.key} key={item.key}>{item.label}{status && ' · ' + status}</option>;
        })}
      </select><ChevronDown aria-hidden="true" />
    </div>
    {!field && reusable.length > 0 && <details className="reusable-fields" key={view.activeId}>
      <summary>가져올 값이 있는 항목 {reusable.length}개</summary>
      <div>{reusable.map(item => <button type="button" key={item.key} onClick={() => actions.current?.pinField(item.key)}>{item.label}</button>)}</div>
    </details>}
    {field && <div className="field-detail" key={field.key}>
      <p id="fieldHelpText">{view.fieldHelp || field.help || '이 항목에 적을 내용이 궁금하면 아래에서 작성 방법을 물어보세요.'}</p>
      {(view.fieldExample || field.example) && <p className="field-example">예시 · {view.fieldExample || field.example}</p>}
      {view.fieldValue && <div className="pinned-value"><span>현재 입력값</span><strong>{view.fieldValue}</strong>
        {view.fieldSource && <small>출처 · {view.fieldSource}</small>}</div>}
      <div className="field-tools">
        <Button id="askField" size="sm" variant="ghost" label="무엇을 적나요?" icon={<CircleHelp />} isDisabled={view.busy} onClick={() => actions.current?.askField()} />
        <Button id="deferField" size="sm" variant="ghost" label={view.fieldDeferred ? '보류 해제' : '나중에 확인'} isDisabled={!!view.fieldValue} onClick={() => actions.current?.deferField()} />
      </div>
      <ValueOffers offers={view.fieldOffers} apply={id=>actions.current?.applyOffer(id)} dismiss={id=>actions.current?.dismissOffer(id)}/>
      <ChatQuote view={view} actions={actions} />
    </div>}
  </section>;
}

function PdfFieldLayer({ view, actions, visible }) {
  const [layer, setLayer] = useState(null);
  useLayoutEffect(() => {
    const slot = document.getElementById('frameSlot');
    if (!slot) return;
    const sync = () => setLayer(slot.querySelector('#pdfHotspots'));
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(slot, { childList: true });
    return () => observer.disconnect();
  }, []);
  if (view.previewMode === 'original' && layer) return createPortal(<OriginalHitTargets view={view} actions={actions} />, layer);
  if (!visible || !layer || view.previewMode !== 'draft' || view.pageBusy || !view.hotspots.length) return null;
  const fields = new Map(view.fieldOptions.map(field => [field.key, field]));
  function choose(key) {
    actions.current?.pinField(key);
    if (matchMedia('(max-width:760px)').matches) {
      actions.current?.showMobile('chat');
      requestAnimationFrame(() => document.getElementById('fieldPicker')?.focus({preventScroll:true}));
    }
  }
  return createPortal(view.hotspots.map((region, index) => {
    const field = fields.get(region.key);
    const width = region.width ?? region.w;
    const height = region.height ?? region.h;
    if (!field || ![region.x, region.y, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return null;
    const label = field.label + (region.choice ? ' · ' + region.choice : '');
    const bounds = {left: region.x * 100 + '%', top: region.y * 100 + '%', width: width * 100 + '%', height: height * 100 + '%'};
    return <button type="button" key={region.key + '-' + index} className="pdf-hotspot"
      style={bounds} data-field-key={region.key} aria-label={label + ' 항목 선택'} title={label}
      aria-pressed={view.pinnedKey === region.key} onClick={() => choose(region.key)} />;
  }), layer);
}

function ChatPanel({ view, actions }) {
  const cellsMode = view.previewMode === 'original' && view.originalMode === 'cells' && view.originalCells.length > 0;
  const ordered = view.originalCells;
  const index = ordered.findIndex(cell => cell.key === view.originalSelected?.cellKey);
  const nextCell = [...ordered.slice(index+1), ...ordered.slice(0,Math.max(0,index))].find(cell => !cell.filled);
  return <section className={view.pinnedKey || view.originalSelected ? "chat-panel has-pin" : "chat-panel"} aria-labelledby="chatTitle">
    <div className="inspector-heading"><MessageSquare /><h2 id="chatTitle">작성 대화</h2></div>
    <FieldInspector view={view} actions={actions} />
    {view.previewMode === 'original' && <OriginalInspector view={view} actions={actions} />}
    <div id="turns" className="turns" role="log" aria-live="polite" aria-relevant="additions text" />
    <div className="compose-area">
      <div className="quick-actions">
        <Button id="guideBtn" variant="ghost" size="sm" icon={<CircleHelp />} label="필요 서류" isDisabled={!view.ready || view.busy} onClick={() => actions.current?.guide()} />
        {cellsMode ? <><Button id="goOriginalCell" variant="ghost" size="sm" icon={<Pin />} label="칸 선택" isDisabled={!view.originalReady || view.pageBusy} onClick={() => openCellPicker(actions)} />
          <Button id="nextOriginalCell" variant="ghost" size="sm" icon={<ListChecks />} label="다음 빈칸" isDisabled={!nextCell || view.busy || view.pageBusy}
            onClick={() => selectOriginalCell(actions,nextCell?.key)} /></> : view.previewMode === 'original'
          ? <Button id="goOriginalPoint" variant="ghost" size="sm" icon={<Pin />} label="입력 위치 선택" isDisabled={!view.originalReady || view.pageBusy}
              onClick={() => {actions.current?.originalPickMode(); actions.current?.showMobile('doc'); requestAnimationFrame(() => document.getElementById('startOriginalPlacement')?.focus({preventScroll:true}));}} />
          : <><Button id="nextBtn" variant="ghost" size="sm" icon={<ListChecks />} label="다음 항목" isDisabled={!view.total || view.busy} onClick={() => actions.current?.askNext()} />
            <Button id="goFields" variant="ghost" size="sm" icon={<SquarePen />} label="직접 입력" isDisabled={!view.total} onClick={() => actions.current?.goFields()} /></>}
      </div>
      <Attachments view={view} actions={actions} />
      <form id="compose">
        <label className="sr-only" htmlFor="message">도우미에게 보낼 내용</label>
        <textarea id="message" rows="2" maxLength="5000" placeholder="작성할 내용을 입력하세요…" disabled={!view.ready} />
        <div className="compose-foot"><span>Enter 전송 · Shift+Enter 줄바꿈</span>
          <Button id="sendBtn" type="submit" variant="primary" label="메시지 보내기" icon={<ArrowUp />} isIconOnly isDisabled={!view.canSend} />
        </div>
      </form>
    </div>
  </section>;
}

function DocumentPanel({ view, actions }) {
  const [showHotspots, setShowHotspots] = useState(true);
  const hasCopy = view.previewMode === 'original' && view.originalPoints.length > 0;
  const previewLabel = { draft: '미리보기', original: hasCopy ? '작성 사본 미리보기' : '원본 미리보기', guide: '자료 안내' }[view.previewMode];
  return <section className="doc-panel" aria-labelledby="docTitle">
    <div className="view-nav">
      <SegmentedControl className="doc-mode" label="문서 보기 방식" value={view.tab} onChange={tab => actions.current?.showTab(tab)}>
        <SegmentedControlItem id="previewTab" value="preview" label={previewLabel} icon={<FileText />} />
        {view.previewMode !== 'original' && <SegmentedControlItem id="fieldsTab" value="fields" label="입력 항목" icon={<FilePenLine />} isDisabled={!view.total} />}
      </SegmentedControl>
      <div className="field-summary" id="fieldCount">
        {view.total ? <><span><strong>{view.filled}</strong> / {view.total}개 입력</span><ProgressBar className="field-progress" label="서식 입력 진행률" isLabelHidden value={view.filled} max={view.total} variant="neutral" /></> : view.ready && (view.previewMode === 'original' ? (hasCopy ? '배치한 문구 '+view.originalPoints.length+'개' : '원본 서식') : '외부 자료')}
      </div>
    </div>
    <div id="previewView" className="preview-view" hidden={view.tab !== 'preview'}>
      <div id="pdfStatus" className="viewer-status" role="status">미리보기를 준비하고 있습니다.</div>
      {view.total > 0 && <div className="field-map-tools">
        {view.hotspots.length > 0 && <button id="toggleHotspots" type="button" aria-pressed={showHotspots} onClick={() => setShowHotspots(v => !v)}>
          <ScanLine aria-hidden="true" /><span>입력 칸 표시</span>
        </button>}
        <button type="button" id="chooseField" onClick={() => {
          actions.current?.showMobile('chat');
          requestAnimationFrame(() => document.getElementById('fieldPicker')?.focus({preventScroll:true}));
        }}><Pin aria-hidden="true" /><span>목록에서 항목 선택</span></button>
      </div>}
      {view.previewMode === 'original' && <OriginalToolbar key={view.activeId+':'+view.page} view={view} actions={actions} />}
      <div id="frameSlot" className="frame-slot" />
      <PdfFieldLayer view={view} actions={actions} visible={showHotspots} />
      <div id="external" className="external" hidden />
      <div className="page-nav">
        {view.pageCount > 0 && <div className="page-controls" role="group" aria-label="PDF 쪽 이동">
          <Button id="prevPage" variant="ghost" size="sm" label="이전 쪽" isIconOnly icon={<ChevronLeft />} isDisabled={view.pageBusy || view.page === 0} onClick={() => actions.current?.turnPage(-1)} />
          <span id="pageNumber" aria-live="polite">{view.page + 1} / {view.pageCount}쪽</span>
          <Button id="nextPage" variant="ghost" size="sm" label="다음 쪽" isIconOnly icon={<ChevronRight />} isDisabled={view.pageBusy || view.page + 1 >= view.pageCount} onClick={() => actions.current?.turnPage(1)} />
        </div>}
        <a id="openPdf" className="open-pdf" target="_blank" rel="noopener" aria-label="새 탭에서 PDF 열기" hidden><ExternalLink /><span>새 탭에서 열기</span></a>
      </div>
    </div>
    <div id="fieldsView" className="fields-view" hidden={view.tab !== 'fields'} />
    <footer className="doc-foot">
      <a id="finishLink" href="/finish" target="_blank" rel="noopener" hidden><NotebookPen /><span>나머지 칸 직접 배치</span><ArrowUpRight /></a>
      <Button id="memoBtn" variant="ghost" size="sm" label={view.hasMemo ? '파일·접수 메모 ●' : '파일·접수 메모'} icon={<FilePenLine />} isDisabled={!view.ready} onClick={() => actions.current?.openMemo()} />
    </footer>
  </section>;
}

function Workspace() {
  const [view, setView] = useState(initialView);
  const actions = useRef(null);
  const drawer = useRef(null);
  const compactQuery = '(max-width: 1050px)';
  const [compact, setCompact] = useState(() => matchMedia(compactQuery).matches);
  useEffect(() => {
    const query = matchMedia(compactQuery);
    const change = () => { if (!query.matches) drawer.current?.close(); setCompact(query.matches); };
    query.addEventListener('change', change);
    return () => query.removeEventListener('change', change);
  }, []);
  useLayoutEffect(() => {
    actions.current = startWorkspace(patch => setView(previous => ({ ...previous, ...patch })));
  }, []);
  return <AppShell className="work-shell" variant="section" height="fill" contentPadding={0} mobileNav={false}
    sideNav={<div className="rail-dock">{!compact && <DocumentNav view={view} actions={actions} />}</div>}>
    <div className="work-column">
      <TopBar view={view} actions={actions} compact={compact} openNav={() => drawer.current?.showModal()} />
      <DocumentHeading view={view} />
      <div className="mobile-switch">
        <SegmentedControl label="작업 영역" value={view.mobile} onChange={section => actions.current?.showMobile(section)}>
          <SegmentedControlItem id="showDoc" value="doc" label="문서 검토" icon={<Files />} />
          <SegmentedControlItem id="showChat" value="chat" label="작성 대화" icon={<MessageSquare />} />
        </SegmentedControl>
      </div>
      <div className="panels" data-mobile={view.mobile}>
        <DocumentPanel view={view} actions={actions} /><ChatPanel view={view} actions={actions} />
      </div>
      <IntakeImport view={view} actions={actions} />
      <footer className="work-status"><IntakeStatus source={view.intakeSummary} onOpen={()=>actions.current?.openCase()} /><span className={view.dirty ? 'save-dot dirty' : 'save-dot'} />{view.dirty ? '저장하지 않은 변경 사항' : '작업 내용은 이 탭에서만 유지됩니다.'}</footer>
      {compact && <dialog className="nav-drawer" id="navDrawer" aria-label="문서 목록" ref={drawer} onClick={e => {if (e.target === drawer.current) drawer.current.close();}}>
        <DocumentNav view={view} actions={actions} onClose={() => drawer.current?.close()} />
      </dialog>}
    </div>
  </AppShell>;
}

createRoot(document.getElementById('workspaceRoot')).render(<UITheme><Workspace /></UITheme>);
