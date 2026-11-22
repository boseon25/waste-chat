import {useCallback, useEffect, useRef, useState} from 'react';
import {Button} from '@astryxdesign/core/Button';
import {Camera, Check, ImagePlus, Paperclip, Trash2, X, ZoomIn, ZoomOut} from 'lucide-react';
import {MAX_PHOTOS, preparePhoto} from '../../web/attachments.js';
import './attachments.css';

export function Attachments({view, actions}) {
  const photos = view.attachments || [];
  const fields = view.attachmentFields || [];
  const [selected, setSelected] = useState('');
  const [value, setValue] = useState('');
  const [zoom, setZoom] = useState(false);
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState('');
  const dialog = useRef(null);
  const file = useRef(null);
  const host = useRef(null);
  const ticket = useRef(0);
  const reading = useRef(0);
  const current = useRef(null);
  current.current = {id: view.activeId, epoch: view.attachmentEpoch, ready: view.ready, pinnedKey: view.pinnedKey || ''};
  const selectedPhoto = photos.find(p => p.id === selected) || photos[0];
  const selectedKey = selectedPhoto?.fieldKey || '';

  useEffect(() => {
    ticket.current++;
    reading.current = 0;
    setPending(false);
    if (dialog.current?.open) dialog.current.close();
    setSelected('');
    setStatus('');
    setValue('');
    setZoom(false);
  }, [view.activeId, view.attachmentEpoch]);
  function openDialog() {
    if (!dialog.current.open) dialog.current.showModal();
  }
  function choosePhoto(id) {
    setSelected(id);
    setValue('');
    setZoom(false);
    setStatus('');
  }
  const addFiles = useCallback(async list => {
    const target = current.current;
    if (!target.ready || !list.length) return;
    openDialog();
    if (list.length > MAX_PHOTOS) { setStatus('작업 파일에 사진을 최대 8장까지 첨부할 수 있습니다.'); return; }
    if (reading.current) { setStatus('앞서 선택한 사진을 처리하고 있습니다.'); return; }
    const seq = ++ticket.current;
    setPending(true);
    reading.current = seq;
    setStatus('사진을 읽고 있습니다…');
    try {
      const items = [];
      for (const item of list) {
        items.push(await preparePhoto(item));
        if (seq !== ticket.current) return;
      }
      await actions.current.addPhotos(target.id, target.epoch, items, target.pinnedKey);
      if (seq !== ticket.current) return;
      setSelected(items[0].id);
      setValue('');
      setZoom(false);
      setStatus(items.length + '장을 이 문서에 첨부했습니다.');
    } catch (error) {
      if (seq === ticket.current) setStatus(error.message || '사진을 첨부할 수 없습니다.');
    } finally {
      if (seq === ticket.current) setPending(false);
      if (reading.current === seq) reading.current = 0;
    }
  }, [actions]);
  useEffect(() => {
    const chat = host.current?.closest('.chat-panel') || document.getElementById('compose')?.parentElement;
    if (!chat) return;
    function paste(event) {
      const data = event.clipboardData;
      if (!data || !current.current.ready) return;
      let images = [...(data.items || [])].filter(i => i.kind === 'file' && i.type.startsWith('image/')).map(i => i.getAsFile()).filter(Boolean);
      if (!images.length) images = [...(data.files || [])].filter(f => f.type.startsWith('image/'));
      if (!images.length) return;
      event.preventDefault();
      addFiles(images);
    }
    chat.addEventListener('paste', paste);
    return () => chat.removeEventListener('paste', paste);
  }, [addFiles]);
  function run(action) {
    try { action(); } catch (error) { setStatus(error.message || '첨부 내용을 변경할 수 없습니다.'); }
  }
  function changeField(key) {
    if (!selectedPhoto) return;
    run(() => {
      actions.current.linkPhoto(view.activeId, view.attachmentEpoch, selectedPhoto.id, key);
      setValue('');
      setStatus(key ? '사진을 선택한 항목에 연결했습니다.' : '문서 전체의 첨부로 변경했습니다.');
    });
  }
  function apply(event) {
    event.preventDefault();
    if (!selectedPhoto || !selectedKey || !value.trim()) return;
    run(() => {
      const result = actions.current.applyPhotoValue(view.activeId, view.attachmentEpoch, selectedPhoto.id, selectedKey, value.trim());
      if (result === false) return;
      setValue('');
      setStatus('확인한 값을 문서에 반영했습니다.');
    });
  }
  function remove() {
    if (!selectedPhoto) return;
    run(() => {
      actions.current.removePhoto(view.activeId, view.attachmentEpoch, selectedPhoto.id);
      choosePhoto(photos.find(p => p.id !== selectedPhoto.id)?.id || '');
      setStatus('사진을 문서의 첨부 목록에서 삭제했습니다.');
    });
  }

  return <div className="photo-attachments" ref={host}>
    <Button id="attachPhoto" label={'사진 첨부' + (photos.length ? ' · ' + photos.length : '')} variant="ghost" size="sm"
      icon={<Paperclip size={15}/>} isDisabled={!view.ready} aria-haspopup="dialog" onClick={() => {setStatus(''); openDialog();}}/>
    <dialog className="photo-dialog" ref={dialog} id="photoDlg" aria-labelledby="photoHeading">
      <header className="photo-header"><div><Camera size={19}/><h2 id="photoHeading">사진 첨부</h2></div>
        <Button id="closePhotos" label="사진 첨부 닫기" isIconOnly size="sm" variant="ghost" icon={<X size={17}/>} onClick={() => dialog.current.close()}/></header>
      <p className="photo-document">{view.title}</p>
      <div className="photo-actions">
        <Button label="사진 선택" icon={<ImagePlus size={16}/>} isDisabled={pending} variant="secondary" onClick={() => file.current.click()}/>
        <input hidden multiple id="attachmentFile" ref={file} type="file" accept="image/jpeg,image/png,image/webp" aria-label="첨부 사진 파일 선택"
          onChange={e => {const list = [...e.target.files]; e.target.value = ''; addFiles(list);}}/>
        <span>채팅창이나 이 창에 캡처를 붙여넣을 수 있습니다.</span>
      </div>
      {photos.length > 0 && <div className="photo-review">
        <div className="photo-visual">
          <label className="photo-selector">첨부 사진 <select value={selectedPhoto.id} onChange={e => choosePhoto(e.target.value)}>
            {photos.map((p, i) => <option key={p.id} value={p.id}>{i + 1}. {p.name}</option>)}
          </select></label>
          <div className={'photo-canvas' + (zoom ? ' zoomed' : '')} tabIndex={0} aria-label="첨부 사진 미리보기">
            <img id="photoImage" src={selectedPhoto.dataUrl} alt={selectedPhoto.name} style={zoom ? {width: selectedPhoto.width} : undefined}/>
          </div>
          <div className="photo-toolbar">
            <Button label={zoom ? '화면에 맞추기' : '100% 크기로 보기'} size="sm" variant="ghost" icon={zoom ? <ZoomOut size={15}/> : <ZoomIn size={15}/>} onClick={() => setZoom(!zoom)}/>
            <Button id="deletePhoto" label="사진 삭제" size="sm" variant="ghost" icon={<Trash2 size={14}/>} onClick={remove}/>
          </div>
        </div>
        <form className="photo-fields" onSubmit={apply}>
          {fields.length > 0 ? <>
            <h3>사진에서 값 옮기기</h3>
            <label htmlFor="attachField">연결할 입력 항목</label>
            <select id="attachField" value={selectedKey} onChange={e => changeField(e.target.value)}>
              <option value="">문서 전체에 첨부</option>{fields.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
            </select>
            <label htmlFor="attachValue">사진에서 확인한 값</label>
            <textarea id="attachValue" rows={4} maxLength={5000} value={value} onChange={e => setValue(e.target.value)} placeholder="사진을 보면서 값을 입력하세요."/>
            <Button id="applyPhoto" label="입력값 반영" type="submit" variant="primary" icon={<Check size={15}/>} isDisabled={!selectedKey || !value.trim()}/>
          </> : <p>이 문서에 첨부한 사진을 작업 파일에 함께 보관합니다.</p>}
        </form>
      </div>}
      <p className="photo-status" id="photoStatus" role="status" aria-live="polite">{status}</p>
      <footer className="photo-info"><span>JPG · PNG · WebP · 한 장 12 MiB 이하 · 작업당 최대 8장</span>
        <span>사진은 브라우저에서 축소해 작업 JSON에 보관합니다. 원본은 별도로 보관하세요.</span></footer>
    </dialog>
  </div>;
}
