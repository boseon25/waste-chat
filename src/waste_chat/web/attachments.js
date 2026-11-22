// Evidence stays in the user's browser and in the case JSON that they download.
export const MAX_PHOTOS = 8;
const MAX_UPLOAD = 12 * 1024 * 1024;
const MAX_PIXELS = 32_000_000;
const MAX_EDGE = 2200;
const MAX_JPEG = 800 * 1024;
const PREFIX = 'data:image/jpeg;base64,';
const MAX_BASE64 = Math.ceil(MAX_JPEG / 3) * 4;

const fail = text => { throw new Error(text); };
function safeName(name) {
  const plain = String(name || '').replace(/[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/g, '').split(/[\\/]/).pop();
  return plain.trim().slice(0, 160) || '붙여넣은 이미지';
}
function jpegDimensions(bytes) {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes[0] !== 255 || bytes[1] !== 216) return null;
  let i = 2;
  while (i + 4 <= bytes.length && bytes[i] === 255) {
    while (bytes[i] === 255) i++;
    const marker = bytes[i++];
    if (marker === 218 || marker === 217 || i + 2 > bytes.length) break;
    const len = v.getUint16(i);
    if (len < 2 || i + len > bytes.length) break;
    if (len >= 8 && [192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker)) {
      return [v.getUint16(i + 5), v.getUint16(i + 3)];
    }
    i += len;
  }
  return null;
}

function imageHeader(bytes) {
  if (bytes.length < 32) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = i => String.fromCharCode(...bytes.subarray(i, i + 4));
  if (view.getUint32(0) === 0x89504e47 && view.getUint32(4) === 0x0d0a1a0a && tag(12) === 'IHDR') {
    return ['image/png', view.getUint32(16), view.getUint32(20)];
  }
  if (tag(0) === 'RIFF' && tag(8) === 'WEBP') {
    const type = tag(12);
    if (type === 'VP8X' && !(bytes[20] & 2)) {
      const u24 = i => bytes[i] + (bytes[i + 1] << 8) + (bytes[i + 2] << 16);
      return ['image/webp', 1 + u24(24), 1 + u24(27)];
    }
    if (type === 'VP8 ' && bytes[23] === 157 && bytes[24] === 1 && bytes[25] === 42) {
      return ['image/webp', view.getUint16(26, true) & 0x3fff, view.getUint16(28, true) & 0x3fff];
    }
    if (type === 'VP8L' && bytes[20] === 47) {
      const dimensions = view.getUint32(21, true);
      return ['image/webp', 1 + (dimensions & 0x3fff), 1 + ((dimensions >>> 14) & 0x3fff)];
    }
  }
  const jpeg = jpegDimensions(bytes);
  return jpeg && ['image/jpeg', ...jpeg];
}
async function withImage(blob, use) {
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.src = url;
    try { await image.decode(); } catch { fail('이미지 내용을 읽을 수 없습니다. 다른 사진을 선택하세요.'); }
    return await use(image);
  } finally { URL.revokeObjectURL(url); }
}
function dataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('사진을 저장용으로 변환할 수 없습니다.'));
    reader.readAsDataURL(blob);
  });
}
export async function preparePhoto(file) {
  if (!(file instanceof Blob) || !file.size || file.size > MAX_UPLOAD) fail('사진 한 장은 12 MiB 이하여야 합니다.');
  const bytes = new Uint8Array(await file.arrayBuffer());
  const header = imageHeader(bytes);
  if (!header) fail('JPG, PNG 또는 정지 WebP 이미지를 선택하세요.');
  const [mime, w, h] = header;
  if (!(w > 0 && h > 0 && w * h <= MAX_PIXELS)) fail('3,200만 화소 이하의 이미지를 선택하세요.');
  return withImage(new Blob([bytes], {type: mime}), async image => {
    const sourceW = image.naturalWidth, sourceH = image.naturalHeight;
    if (!(sourceW > 0 && sourceH > 0 && sourceW * sourceH <= MAX_PIXELS)) fail('지원하지 않는 이미지 크기입니다.');
    const canvas = document.createElement('canvas');
    try {
      let ratio = Math.min(1, MAX_EDGE / Math.max(sourceW, sourceH));
      for (let i = 0; i < 5; i++) {
        canvas.width = Math.max(1, Math.round(sourceW * ratio));
        canvas.height = Math.max(1, Math.round(sourceH * ratio));
        const ctx = canvas.getContext('2d', {alpha: false});
        if (!ctx) fail('이 브라우저에서는 이미지를 열 수 없습니다.');
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
        const jpeg = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .88));
        if (!jpeg || jpeg.type !== 'image/jpeg') fail('이미지를 JPEG로 변환할 수 없습니다.');
        if (jpeg.size <= MAX_JPEG) {
          return {id: crypto.randomUUID(), name: safeName(file.name), width: canvas.width, height: canvas.height, dataUrl: await dataUrl(jpeg)};
        }
        ratio *= .78;
      }
      fail('축소한 이미지가 너무 큽니다. 필요한 부분을 잘라 첨부하세요.');
    } finally { canvas.width = canvas.height = 0; }
  });
}

function rawJpeg(item) {
  if (typeof item.dataUrl !== 'string' || !item.dataUrl.startsWith(PREFIX) || item.dataUrl.length > MAX_BASE64 + PREFIX.length) {
    fail('작업 파일에 지원하지 않는 첨부 이미지가 있습니다.');
  }
  const base64 = item.dataUrl.slice(PREFIX.length);
  if (!base64 || base64.length % 4 || !/^[A-Za-z0-9+/]+={0,2}$/.test(base64)) fail('첨부 이미지의 저장 형식이 손상되었습니다.');
  const raw = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
  const dim = jpegDimensions(raw);
  if (raw.length > MAX_JPEG || raw.at(-2) !== 255 || raw.at(-1) !== 217 || !dim || Math.min(...dim) <= 0 || Math.max(...dim) > MAX_EDGE) {
    fail('첨부 이미지의 크기 또는 파일 형식을 확인하세요.');
  }
  return {raw, dim};
}

// Case JSON v1 files from before the attachment feature omit this property.
export async function readCaseAttachments(value, docs) {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > MAX_PHOTOS) fail('작업 파일의 사진 목록을 확인하세요.');
  const seen = new Set(), result = [];
  for (const item of value) {
    const document = docs.get(item?.docId);
    if (!document || !item || typeof item !== 'object' || typeof item.id !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(item.id) || seen.has(item.id)) fail('잘못된 첨부 문서 또는 사진 번호가 있습니다.');
    if (typeof item.name !== 'string' || typeof item.fieldKey !== 'string' || (item.fieldKey && !document.fields.some(f => f.key === item.fieldKey))) fail('첨부 사진의 이름과 연결 항목을 확인하세요.');
    const {raw, dim} = rawJpeg(item);
    await withImage(new Blob([raw], {type: 'image/jpeg'}), image => {
      if (image.naturalWidth !== dim[0] || image.naturalHeight !== dim[1]) fail('첨부 사진의 표시 크기를 확인하세요.');
    });
    result.push({id: item.id, docId: document.id, name: safeName(item.name), fieldKey: item.fieldKey, width: dim[0], height: dim[1], dataUrl: item.dataUrl});
    seen.add(item.id);
  }
  return result;
}

export function createAttachmentStore() {
  let photos = [];
  const locate = (docId, id) => {
    const index = photos.findIndex(p => p.docId === docId && p.id === id);
    if (index < 0) fail('현재 문서에서 해당 사진을 찾을 수 없습니다.');
    return index;
  };
  return {
    all: () => photos.slice(),
    forDoc: id => photos.filter(p => p.docId === id),
    replace(validated) { photos = validated.slice(); },
    add(docId, items) {
      if (!Array.isArray(items) || !items.length || !docId) fail('첨부할 사진과 문서를 선택하세요.');
      if (photos.length + items.length > MAX_PHOTOS) fail('작업 파일 하나에 사진을 최대 8장까지 보관할 수 있습니다.');
      const ids = new Set(photos.map(p => p.id));
      const added = items.map(p => {
        if (!p?.id || ids.has(p.id)) fail('첨부 사진 번호가 중복되었습니다.');
        ids.add(p.id);
        return {...p, docId, fieldKey: ''};
      });
      photos = [...photos, ...added];
      return added;
    },
    remove(docId, id) { const i = locate(docId, id); photos = photos.filter((_, index) => index !== i); },
    link(docId, id, fieldKey) { const i = locate(docId, id); photos = photos.map((p, index) => index === i ? {...p, fieldKey} : p); },
  };
}
