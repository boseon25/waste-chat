// Original-page positions are stored in displayed PDF points, measured from the top left.
// Pixel fractions are used only by the overlay, so resizing a page never moves written text.
export function originalAvailable(doc) {
  return !!doc?.file && !doc.fields.length && doc.blank_url === '/api/blank/' + doc.id;
}

export function pointForView(mark, cells) {
  if (!mark) return null;
  const cell = mark.cellKey && cells.find(c => c.key === mark.cellKey);
  return {...mark, x:mark.x / mark.width, y:mark.y / mark.height, cellLabel:cell?.label, help:cell?.help};
}

// Old case files identify a phrase only by its upper-left anchor. Reuse it if unambiguous.
export function marksInCell(points, cell) {
  const registered = points.find(p => p.cellKey === cell.key);
  if (registered) return [registered];
  const [left,top,right,bottom] = cell.box;
  return points.filter(p => !p.cellKey && p.page === cell.page &&
    p.x >= left && p.x < right && p.y >= top && p.y < bottom);
}

export function newPoint(page, x, y, width, height) {
  return {id:crypto.randomUUID(), page, x:x * width, y:y * height, width, height, size:10, text:''};
}

export function placementPayload(points) {
  return points.map(({page,x,y,size,text,cellKey}) => ({page,x,y,size,text,...(cellKey ? {cellKey} : {})}));
}

const inRange = (value, min, max) => Number.isFinite(value) && value >= min && value <= max;

export function readOriginalPoints(raw, docs) {
  const result = Object.create(null);
  if (raw === undefined) return result;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw Error('원본 PDF 문구 목록을 확인하세요.');
  for (const [id, points] of Object.entries(raw)) {
    if (!originalAvailable(docs.get(id)) || !Array.isArray(points) || points.length > 100)
      throw Error('원본 PDF와 쪽별 문구 목록을 확인하세요.');
    const ids = new Set();
    const usedCells = new Set();
    const cells = docs.get(id).original_cells || [];
    result[id] = points.map(p => {
      if (!p || typeof p.id !== 'string' || !/^[a-z0-9-]{1,64}$/i.test(p.id) || ids.has(p.id) ||
          !Number.isInteger(p.page) || !inRange(p.page, 0, 63) ||
          !inRange(p.width,1,4000) || !inRange(p.height,1,4000) ||
          !inRange(p.x,0,p.width) || !inRange(p.y,0,p.height) || !inRange(p.size,6,36) ||
          typeof p.text !== 'string' || !p.text.trim() || p.text.length > 500) throw Error('원본 PDF에 저장된 위치와 문구 형식을 확인하세요.');
      ids.add(p.id);
      const {id,page,x,y,width,height,size,text}=p;
      if (p.cellKey === undefined) return {id,page,x,y,width,height,size,text};
      const cell = cells.find(c => c.key === p.cellKey);
      if (!cell || page !== cell.page || usedCells.has(cell.key))
        throw Error('저장된 원본 서식의 칸을 현재 서식에서 한 곳으로 확인할 수 없습니다.');
      usedCells.add(cell.key);
      const source = typeof p.source === 'string' ? p.source.slice(0,5000) : '';
      return {id,page,x:cell.write[0],y:cell.write[1],width,height,size,text,cellKey:cell.key,...(source ? {source} : {})};
    });
  }
  return result;
}
