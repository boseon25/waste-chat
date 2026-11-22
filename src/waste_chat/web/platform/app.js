// 순환 거래 작업실 화면. 빌드 없이 브라우저가 그대로 읽는다.
// 조건부로 그리는 칸(cond ? 노드 : null)을 replaceChildren·append에 그대로 넘기면 'null' 글자가 찍힌다 → 빈 값은 건너뛴다.
for (const proto of [Element.prototype, DocumentFragment.prototype]) for (const m of ['replaceChildren', 'append', 'prepend']) {
  const orig = proto[m];
  proto[m] = function (...xs) { return orig.apply(this, xs.flat(Infinity).filter(x => x !== null && x !== undefined && x !== false)); };
}
const ICONS = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  inbox: '<path d="M4 13.5 6.5 5h11L20 13.5V19H4z"/><path d="M4 13.5h4.5l1 2.5h5l1-2.5H20"/>',
  chat: '<path d="M20 11.5a7.5 7.5 0 0 1-7.8 7.2H8L4 21v-5.2a7 7 0 0 1-1-4.3A7.8 7.8 0 0 1 11 4.3h1.2A7.6 7.6 0 0 1 20 11.5Z"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/>',
  send: '<path d="M12 19V5.5m-6 6 6-6 6 6"/>',
  clip: '<path d="m20 11-8.2 8.2a5 5 0 0 1-7-7L13 4a3.3 3.3 0 0 1 4.700 4.7l-8 8a1.700 1.700 0 0 1-2.400-2.400L14.500 7"/>',
  check: '<path d="m5 12.5 4.500 4.500L19 7.500"/>',
  side: '<rect x="3.500" y="4.500" width="17" height="15" rx="2.500"/><path d="M9 4.500v15"/>',
  file: '<path d="M6 3.500h8l4.500 4.800v12.200H6zM14 3.500v5h4.500"/>',
  user: '<circle cx="12" cy="8.500" r="3.700"/><path d="M4.500 20a7.500 7.500 0 0 1 15 0"/>',
  loop: '<path d="M7 7h9a4 4 0 0 1 4 4v1M17 17H8a4 4 0 0 1-4-4v-1"/><path d="m14 4 3 3-3 3M10 20l-3-3 3-3"/>',
};
const ROLE = {emitter: '배출업체', hauler: '수집·운반업체', recycler: '재활용·가공업체', disposer: '처분업체'};

const app = document.getElementById('app');
const S = {boot: null, options: null, route: {name: '', id: null}, search: null, room: null, last: 0, tab: 'ai',
  seen: readSeen(), narrow: false, reqTab: 'in', sending: false, sig: {}, invite: {want: 'hauler', data: null}};

function readSeen() { try { return JSON.parse(localStorage.getItem('seen') || '{}'); } catch { return {}; } }
function icon(name) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('class', 'ic'); s.setAttribute('aria-hidden', 'true');
  s.innerHTML = ICONS[name].replace(/(\.\d)00/g, '$1'); return s;
}
function h(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (k in n && typeof v !== 'string') n[k] = v;
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(3)) if (kid !== null && kid !== undefined && kid !== false) n.append(kid);
  return n;
}
async function api(path, body) {
  const r = await fetch('/api/p/' + path, body
    ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {cache: 'no-store'});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = Error(data.error || '요청을 처리하지 못했습니다. (' + r.status + ')'); e.status = r.status; throw e; }
  return data;
}
let toastTimer;
function toast(message) {
  const t = document.getElementById('toast'); t.textContent = message; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3600);
}
const guard = fn => async (...args) => { try { return await fn(...args); } catch (e) { toast(e.message); } };
const km = v => v === null || v === undefined ? '' : v < 0.5 ? '같은 시·군·구' : '약 ' + (v >= 10 ? Math.round(v) : v) + ' km';
const chip = (text, cls) => h('span', {class: 'chip ' + (cls || ''), text});
const needChips = need => h('div', {class: 'needs'}, need.map(n => h('span', {class: 'chip'}, h('span', {class: 'faint', text: n.label}), ' ' + n.value)));
function autosize(area) { area.style.height = 'auto'; area.style.height = Math.min(area.scrollHeight, 180) + 'px'; }

// ── 지도 ──────────────────────────────────────────────────────────────
// OpenStreetMap 타일 서버는 Referer가 없는 요청을 차단한다. 이 페이지의 기본 정책은 no-referrer이므로 타일 요청에만 출처를 보낸다.
const tiles = maxZoom => L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom, referrerPolicy: 'origin', attribution: '© OpenStreetMap contributors'});
function makeMap(box, me, items, onPick) {
  if (!window.L) { box.append(h('p', {class: 'placeholder', text: '지도를 불러오지 못했습니다. 인터넷 연결을 확인하세요.'})); return {focus() {}}; }
  const map = L.map(box, {zoomControl: true, attributionControl: true});
  tiles(17).addTo(map);
  const points = [], markers = new Map();
  const pin = (cls, text) => L.divIcon({className: '', html: '<div class="pin ' + cls + '">' + text + '</div>', iconSize: [26, 26], iconAnchor: [13, 13]});
  if (me && me.lat) { L.marker([me.lat, me.lng], {icon: pin('me', ''), interactive: false}).addTo(map); points.push([me.lat, me.lng]); }
  items.forEach((c, i) => {
    if (!c.lat) return;
    const m = L.marker([c.lat, c.lng], {icon: pin(c.public ? 'pub' : '', i + 1), title: c.name}).addTo(map);
    m.on('click', () => onPick && onPick(c.id));
    markers.set(c.id, {m, i, pub: !!c.public}); points.push([c.lat, c.lng]);
  });
  if (points.length > 1) map.fitBounds(points, {padding: [34, 34], maxZoom: 12});
  else map.setView(points[0] || [36.4, 127.8], points.length ? 11 : 7);
  setTimeout(() => map.invalidateSize(), 60);
  return {
    focus(id) {
      for (const [key, {m, i, pub}] of markers) m.setIcon(pin(key === id ? 'on' : pub ? 'pub' : '', i + 1));
      const hit = markers.get(id); if (hit) map.panTo(hit.m.getLatLng());
    },
  };
}
function resultPanel(results, me, action) {
  const mapBox = h('div', {class: 'map'});
  const cards = h('div', {class: 'cards'});
  let map;
  const pick = id => {
    map.focus(id);
    for (const c of cards.children) c.classList.toggle('on', c.dataset.id === id);
    cards.querySelector('[data-id="' + id + '"]')?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  };
  results.forEach((c, i) => cards.append(h('div', {class: 'card', 'data-id': c.id, onclick: () => pick(c.id)},
    h('span', {class: 'no', text: i + 1}),
    h('div', null,
      h('h3', null, c.name, chip(ROLE[c.match_role] || ROLE[c.roles[0]], 'line'), c.demo && chip('예시', 'warn'), c.public && chip('공공자료 · 미가입', 'accent')),
      h('p', {text: [c.address, km(c.distance_km)].filter(Boolean).join(' · ')}),
      c.why && h('p', {text: c.why}),
      h('div', {class: 'tags'}, c.wastes.slice(0, 5).map(w => chip(w)), c.capacity && chip(c.capacity), !c.capacity && c.vehicles && chip(c.vehicles)),
      c.public && h('p', {class: 'faint', style: 'font-size:12px', text: [c.license, c.phone && '☎ ' + c.phone, '출처 ' + c.source].filter(Boolean).join(' · ')})),
    h('div', {class: 'act'}, action(c)))));
  queueMicrotask(() => { map = makeMap(mapBox, me, results, pick); });
  return [mapBox, cards];
}

// ── 로그인 · 가입 ──────────────────────────────────────────────────────
function profileForm(p, options) {
  p = p || {};
  const state = {roles: new Set(p.roles || []), wastes: new Set(p.wastes || []), permit_wastes: new Set(p.permit_wastes || []), lat: p.lat, lng: p.lng,
    public_key: p.public_key || '', public_name: p.public_key ? p.public_key.split('|')[0].replace(/_$/, '') + ' · ' + (p.public_key.split('|')[1] || '') : ''};
  const inputs = {};
  const field = (name, label, opts = {}) => {
    const el = opts.area ? h('textarea', {class: 'input', rows: 2}) : h('input', {class: 'input', type: 'text'});
    el.value = p[name] || ''; el.placeholder = opts.hint || ''; el.maxLength = opts.max || 160; inputs[name] = el;
    return h('label', {class: 'field' + (opts.full ? ' full' : '')}, h('span', null, label, !opts.required && h('i', {text: '선택'})), el);
  };
  const picks = (set, names, labels, onchange) => h('div', {class: 'picks'}, names.map(name => {
    const b = h('button', {type: 'button', 'aria-pressed': String(set.has(name)), text: labels ? labels[name] : name, onclick: () => {
      set.has(name) ? set.delete(name) : set.add(name); b.setAttribute('aria-pressed', String(set.has(name))); onchange && onchange();
    }});
    return b;
  }));
  const emitterBox = h('div', {class: 'section'}, h('h3', {text: '배출 정보'}), h('p', {text: '거래 상대를 찾을 때와 서류의 발생 공정 칸에 쓰입니다.'}),
    h('div', {class: 'form'}, h('div', {class: 'field'}, h('span', {text: '주로 발생하는 폐기물'}), picks(state.wastes, options.wastes)),
      field('process', '발생 공정', {area: true, hint: '예: 사출 성형 후 스프루·런너, 포장 필름', max: 300})));
  const handlerBox = h('div', {class: 'section'}, h('h3', {text: '허가·처리 정보'}), h('p', {text: '허가증(신고증명서)에 적힌 내용 그대로 입력하세요. 다른 업체가 후보를 고를 때 보게 됩니다.'}),
    h('div', {class: 'form'}, h('div', {class: 'field'}, h('span', {text: '영업대상(취급) 폐기물'}), picks(state.permit_wastes, options.wastes)),
      h('div', {class: 'form-grid'}, field('permit_no', '허가·신고 번호', {required: true}), field('permit_authority', '허가·신고 기관'),
        field('methods', '처리(운반) 방법', {full: true, hint: '예: 분쇄·세척·압출로 재생 펠릿 제조', max: 200}),
        field('capacity', '시설 처리능력', {hint: '예: 40톤/일'}), field('vehicles', '차량', {hint: '예: 암롤 25톤 2대'}))));
  // 공공자료(환경부·한국환경공단 업체현황)의 우리 업체와 연결: 허가 정보를 공공자료 값으로 확인받고 법령 기준 추천에 '거래 요청 가능'으로 나온다
  const linkBox = h('div', {class: 'link-box'});
  const drawLink = (cands) => {
    linkBox.replaceChildren(
      h('div', {class: 'desk-doc-top'}, h('b', {text: '공공자료 업체현황과 연결'}),
        state.public_key ? chip('연결됨 · ' + state.public_name, 'ok') : chip('연결 안 됨', 'warn')),
      h('p', {class: 'muted', style: 'font-size:13px', text: '환경부·한국환경공단 업체현황에서 우리 업체를 고르면, 배출업체가 법령 기준으로 업체를 찾을 때 허가가 확인된 업체로 나오고 바로 거래 요청을 받을 수 있습니다. 공공자료에는 사업자등록번호가 없어 상호·지역·전화번호로 찾습니다.'}),
      h('div', {style: 'display:flex;gap:6px;flex-wrap:wrap'},
        h('button', {class: 'btn small', type: 'button', text: '공공자료에서 우리 업체 찾기', onclick: guard(async () => {
          const out = await api('public-match', {name: inputs.name.value, address: inputs.address.value, phone: inputs.phone.value || inputs.manager_phone.value});
          drawLink(out.candidates);
        })}),
        state.public_key ? h('button', {class: 'btn small ghost', type: 'button', text: '연결 해제', onclick: () => { state.public_key = ''; state.public_name = ''; drawLink(); }}) : null),
      ...(cands ? (cands.length ? cands.map(c => h('div', {class: 'ask-card'},
        h('div', {class: 'desk-doc-top'}, h('b', {text: c.name}), h('small', {class: 'faint', text: c.region}), c.taken ? chip('다른 계정이 연결함', 'bad') : null),
        h('small', {class: 'faint', text: [c.address, c.phone && '☎ ' + c.phone].filter(Boolean).join(' · ')}),
        h('small', {text: [c.licenses.join(', '), c.kinds.slice(0, 4).join(', ')].filter(Boolean).join(' · ')}),
        h('button', {class: 'btn small primary', type: 'button', disabled: c.taken, text: '우리 업체예요', onclick: () => {
          state.public_key = c.key; state.public_name = c.name + ' · ' + c.region; drawLink(); toast('저장을 누르면 연결됩니다.');
        }}))) : [h('p', {class: 'faint', text: '상호가 같은 업체를 찾지 못했습니다. 상호를 허가증과 같게 적었는지 확인하세요.'})]) : []));
  };
  drawLink();
  handlerBox.append(linkBox);
  const sync = () => {
    emitterBox.hidden = !state.roles.has('emitter');
    handlerBox.hidden = ![...state.roles].some(r => r !== 'emitter');
  };
  const mapBox = h('div', {class: 'map small'});
  const where = h('p', {class: 'muted', style: 'font-size:13px', text: state.lat ? '표시한 위치를 사용합니다. 지도를 누르면 옮겨집니다.' : '주소를 입력하면 지도에 위치가 표시됩니다.'});
  queueMicrotask(() => {
    if (!window.L) { mapBox.append(h('p', {class: 'placeholder', text: '지도를 불러오지 못했습니다.'})); return; }
    const map = L.map(mapBox).setView(state.lat ? [state.lat, state.lng] : [36.4, 127.8], state.lat ? 12 : 7);
    tiles(18).addTo(map);
    const ic = L.divIcon({className: '', html: '<div class="pin on" style="width:18px;height:18px"></div>', iconSize: [18, 18], iconAnchor: [9, 9]});
    let marker = state.lat ? L.marker([state.lat, state.lng], {icon: ic}).addTo(map) : null;
    const place = (latlng, note) => {
      state.lat = latlng.lat; state.lng = latlng.lng;
      marker ? marker.setLatLng(latlng) : marker = L.marker(latlng, {icon: ic}).addTo(map);
      where.textContent = note;
    };
    map.on('click', e => place(e.latlng, '직접 표시한 위치를 사용합니다. 다시 누르면 옮겨집니다.'));
    // 주소를 입력하면 그 위치를 찾아 표시한다. 같은 주소는 다시 찾지 않는다.
    let searched = p.lat ? inputs.address.value.trim() : '';
    const find = async () => {
      const address = inputs.address.value.trim();
      if (address.length < 4 || address === searched) return;
      searched = address; where.textContent = '주소 위치를 찾는 중…';
      try {
        const hit = await api('geocode', {address});
        if (inputs.address.value.trim() !== address) return;
        place({lat: hit.lat, lng: hit.lng}, hit.exact ? '주소 위치를 표시했습니다. 다르면 지도를 눌러 옮기세요.'
          : '주소 근처(도로·동 기준)를 표시했습니다. 정확한 자리는 지도를 눌러 옮기세요.');
        map.setView([hit.lat, hit.lng], 15);
      } catch (e) { where.textContent = e.message; }
    };
    let typing;
    inputs.address.addEventListener('input', () => { clearTimeout(typing); typing = setTimeout(find, 1200); });
    inputs.address.addEventListener('change', () => { clearTimeout(typing); find(); });
    inputs.address.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); clearTimeout(typing); find(); } });
    setTimeout(() => map.invalidateSize(), 80);
  });
  const el = h('div', null,
    h('div', {class: 'section'}, h('h3', {text: '업체 유형'}), h('p', {text: '해당하는 유형을 모두 고르세요.'}), picks(state.roles, Object.keys(options.roles), options.roles, sync)),
    h('div', {class: 'section'}, h('h3', {text: '기본 정보'}), h('p', {text: '계약서와 신고 서식의 업체 칸에 그대로 들어갑니다.'}),
      h('div', {class: 'form-grid'}, field('name', '상호', {required: true, max: 80}), field('reg_no', '사업자등록번호', {hint: '000-00-00000', max: 20}),
        field('ceo', '대표자', {max: 40}), field('industry', '업종', {hint: '예: 플라스틱 사출 성형', max: 80}),
        field('phone', '사업장 전화', {max: 30}), h('span'),
        field('manager', '담당자 이름', {required: true, max: 40}), field('manager_phone', '담당자 연락처', {required: true, max: 30}))),
    h('div', {class: 'section'}, h('h3', {text: '사업장 위치'}), h('p', {text: '가까운 거래 상대를 찾고 운반 거리를 계산하는 기준입니다.'}),
      h('div', {class: 'form'}, field('address', '사업장 주소', {required: true, full: true}),
        h('div', {style: 'display:flex;gap:10px;align-items:center;flex-wrap:wrap'}, where), mapBox)),
    emitterBox, handlerBox,
    h('div', {class: 'section'}, h('h3', {text: '소개'}), h('p', {text: '다른 업체에 보여줄 한두 문장입니다.'}), field('intro', '업체 소개', {area: true, max: 400})));
  sync();
  return {el, read: () => ({...Object.fromEntries(Object.entries(inputs).map(([k, v]) => [k, v.value])), roles: [...state.roles],
    wastes: [...state.wastes], permit_wastes: [...state.permit_wastes], lat: state.lat, lng: state.lng, public_key: state.public_key})};
}
function renderAuth(session, mode = 'login') {
  const error = h('p', {class: 'error', role: 'alert'});
  const enter = guard(async (login, password) => { await api('login', {login, password}); location.hash = '#/'; start(); });
  if (mode === 'login') {
    const id = h('input', {class: 'input', autocomplete: 'username'}), pw = h('input', {class: 'input', type: 'password', autocomplete: 'current-password'});
    const submit = async e => { e.preventDefault(); error.textContent = ''; try { await api('login', {login: id.value, password: pw.value}); start(); } catch (x) { error.textContent = x.message; } };
    app.replaceChildren(h('div', {class: 'auth'}, h('div', {class: 'auth-inner'},
      h('h1', {text: '순환 거래 작업실'}), h('p', {text: '폐기물을 내보내는 업체와 받아 쓰는 업체가 만나 거래 조건을 맞추고, 필요한 서류까지 함께 준비합니다.'}),
      h('form', {class: 'form', onsubmit: submit}, h('label', {class: 'field'}, h('span', {text: '아이디'}), id), h('label', {class: 'field'}, h('span', {text: '비밀번호'}), pw),
        error, h('button', {class: 'btn primary', text: '로그인'})),
      h('p', {class: 'switch'}, '처음이신가요? ', h('button', {text: '업체 가입', onclick: () => renderAuth(session, 'signup')})),
      h('div', {class: 'demo-list'}, h('h3', {text: '예시 업체로 둘러보기 — 가상의 자료입니다'}),
        session.demo.map(d => h('button', {onclick: () => enter(d.login, session.demo_password)}, h('span', {text: d.name}), h('span', {text: d.roles.map(r => ROLE[r]).join(' · ')})))))));
    return;
  }
  const id = h('input', {class: 'input', autocomplete: 'username', placeholder: '영문 소문자·숫자 3자 이상'}), pw = h('input', {class: 'input', type: 'password', autocomplete: 'new-password', placeholder: '6자 이상'});
  const form = profileForm(null, session);
  const submit = async e => {
    e.preventDefault(); error.textContent = '';
    try { await api('signup', {login: id.value, password: pw.value, profile: form.read()}); location.hash = '#/'; start(); }
    catch (x) { error.textContent = x.message; error.scrollIntoView({block: 'center'}); }
  };
  app.replaceChildren(h('div', {class: 'auth'}, h('div', {class: 'auth-inner wide'},
    h('h1', {text: '업체 가입'}), h('p', {text: '여기에 적은 업체 기초 정보는 거래 상대를 찾을 때와 이후 서류의 업체 칸을 채울 때 다시 쓰입니다.'}),
    h('form', {onsubmit: submit}, h('div', {class: 'form-grid'}, h('label', {class: 'field'}, h('span', {text: '아이디'}), id), h('label', {class: 'field'}, h('span', {text: '비밀번호'}), pw)),
      form.el, h('div', {class: 'section'}, error, h('button', {class: 'btn primary', text: '가입하고 시작하기'}))),
    h('p', {class: 'switch'}, '이미 가입하셨나요? ', h('button', {text: '로그인', onclick: () => renderAuth(session, 'login')})))));
}

// ── 틀 ────────────────────────────────────────────────────────────────
function renderShell() {
  app.replaceChildren(h('div', {class: 'shell'},
    h('aside', {class: 'sidebar', id: 'sidebar'}),
    h('main', {class: 'main'},
      h('div', {class: 'topbar'}, h('div', {class: 'grow', id: 'top'}), h('div', {id: 'topActions', style: 'display:flex;gap:6px;align-items:center'})),
      h('div', {class: 'body', id: 'body'}))));
  renderSidebar();
}
function renderSidebar() {
  const bar = document.getElementById('sidebar'); if (!bar || !S.boot) return;
  const b = S.boot, r = S.route, p = b.me.profile;
  const pending = b.requests.filter(q => q.incoming && q.status === 'pending').length;
  const item = (ic, label, href, current, extra) => h('button', {class: 'side-item', title: label, 'aria-current': current ? 'page' : null, onclick: () => { location.hash = href; }},
    ic && icon(ic), h('span', {class: 'label', text: label}), extra);
  bar.className = 'sidebar' + (S.narrow ? ' narrow' : '');
  bar.replaceChildren(
    h('div', {class: 'brand'}, h('span', {class: 'label', text: '순환 거래 작업실'}),
      h('button', {class: 'icon-btn', title: '목록 접기', 'aria-label': '목록 접기', onclick: () => { S.narrow = !S.narrow; renderSidebar(); }}, icon('side'))),
    h('button', {class: 'side-item', title: '새 거래 찾기', 'aria-current': (r.name === 'search' && !r.id) || r.name === 'intake' ? 'page' : null,
      onclick: () => { S.intake = null; S.search = null; if (location.hash === '#/' || location.hash === '') route(); else location.hash = '#/'; }},  // 새 거래는 늘 처음부터
      icon('plus'), h('span', {class: 'label', text: '새 거래 찾기'})),
    item('inbox', '요청함', '#/requests', r.name === 'requests', pending ? h('span', {class: 'count', text: pending}) : null),
    h('div', {class: 'side-scroll'},
      b.rooms.length ? h('div', {class: 'side-title', text: '거래방'}) : null,
      h('div', {class: 'side-list'}, b.rooms.map(room => item(null, room.title, '#/r/' + room.id, (r.name === 'room' || r.name === 'doc') && r.id === room.id,
        room.last_id > (S.seen[room.id] || 0) && !((r.name === 'room' || r.name === 'doc') && r.id === room.id) ? h('span', {class: 'unread'}) : null))),
      b.searches.length ? h('div', {class: 'side-title', text: '거래 찾기 기록'}) : null,
      h('div', {class: 'side-list'}, b.searches.map(s => item(null, s.title || '새 대화', '#/s/' + s.id, (r.name === 'search' || r.name === 'prep') && r.id === s.id)))),
    h('button', {class: 'side-item account', title: p.name, onclick: accountMenu},
      h('span', {class: 'avatar', text: (p.name || '?').slice(0, 1)}),
      h('span', {class: 'label'}, h('b', {text: p.name}), h('small', {text: p.roles.map(x => ROLE[x]).join(' · ') + (b.me.demo ? ' · 예시' : '')}))));
}
function accountMenu(e) {
  document.querySelector('.menu')?.remove();
  const rect = e.currentTarget.getBoundingClientRect();
  const close = () => { menu.remove(); document.removeEventListener('pointerdown', outside); };
  const outside = ev => { if (!menu.contains(ev.target)) close(); };
  const menu = h('div', {class: 'menu', style: `left:${rect.left}px;bottom:${innerHeight - rect.top + 6}px`},
    h('button', {text: '업체 정보', onclick: () => { close(); location.hash = '#/profile'; }}),
    h('button', {text: '다른 업체로 전환', onclick: () => { close(); switchDialog(); }}),
    h('button', {text: '로그아웃', onclick: guard(async () => { close(); await api('logout', {}); location.hash = ''; start(); })}));
  document.body.append(menu);
  setTimeout(() => document.addEventListener('pointerdown', outside));
}
async function switchDialog() {
  const o = await api('options');
  const dlg = h('dialog', null, h('h2', {text: '다른 업체로 전환'}),
    h('p', {class: 'muted', style: 'font-size:13.5px', text: '예시 업체로 바로 들어가 상대 업체의 화면을 확인할 수 있습니다. 직접 가입한 업체는 로그아웃 후 다시 로그인하세요.'}),
    h('div', {class: 'demo-list', style: 'margin-top:12px;max-height:50vh;overflow:auto'}, o.demo.map(d => h('button', {onclick: guard(async () => {
      await api('login', {login: d.login, password: o.demo_password}); dlg.close(); location.hash = '#/'; start();
    })}, h('span', {text: d.name}), h('span', {text: d.roles.map(r => ROLE[r]).join(' · ')})))),
    h('div', {class: 'dlg-actions'}, h('button', {class: 'btn', text: '닫기', onclick: () => dlg.close()})));
  dlg.addEventListener('close', () => dlg.remove());
  document.body.append(dlg); dlg.showModal();
}
function setTop(title, ...actions) {
  document.getElementById('top').replaceChildren(h('h1', {text: title}));
  document.getElementById('topActions').replaceChildren(...actions.filter(Boolean));
  document.title = title + ' · 순환 거래 작업실';
}
function composer({placeholder, note, onsend, attach, tray, hasExtra}) {
  // hasExtra: 글자가 없어도 보낼 것이 있는지(첨부 대기 중인 파일 등)
  const area = h('textarea', {rows: 1, placeholder});
  const send = h('button', {class: 'send', type: 'submit', 'aria-label': '보내기', disabled: true}, icon('send'));
  let composing = false;
  const ready = () => !!area.value.trim() || !!(hasExtra && hasExtra());
  const refresh = () => { send.disabled = !ready() || !!send.dataset.busy; };
  const submit = async () => {
    const value = area.value.trim(); if (!ready() || send.dataset.busy) return;
    area.value = ''; autosize(area); send.disabled = true; send.dataset.busy = '1';
    try { await onsend(value); } catch (e) { toast(e.message); area.value = value; autosize(area); }
    delete send.dataset.busy; refresh(); area.focus();
  };
  area.addEventListener('input', () => { autosize(area); refresh(); });
  area.addEventListener('compositionstart', () => { composing = true; });
  area.addEventListener('compositionend', () => { composing = false; });
  area.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !composing && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); submit(); } });
  const el = h('form', {class: 'composer', onsubmit: e => { e.preventDefault(); submit(); }}, tray, area,
    h('div', {class: 'composer-row'}, attach, h('span', {class: 'note', text: note || ''}), send));
  return {el, area, refresh, fill(text) { area.value = text; autosize(area); refresh(); area.focus(); }};
}
const aiNote = () => S.boot.ai.ready ? 'AI 연결됨 · ' + S.boot.ai.model
  : S.boot.ai.problem ? '기본 안내 모드 · API 키가 거절됨 (.env 확인)' : '기본 안내 모드 · .env에 API 키를 넣으면 AI가 문장을 해석합니다';

// ── 거래 찾기 ──────────────────────────────────────────────────────────
function renderSearch() {
  const body = document.getElementById('body'), s = S.search, me = S.boot.me.profile;
  setTop(s?.title || '새 거래 찾기');
  const send = async text => {
    if (S.search) { S.search.turns.push({role: 'user', text}, {role: 'pending', text: S.search.intake ? '입력서·법령 판단·법령 원문을 보고 답하고 있습니다… (10~20초)' : '조건에 맞는 업체를 찾고 있습니다…'}); }
    else S.search = {id: null, title: '', need: [], results: [], turns: [{role: 'user', text}, {role: 'pending', text: '조건에 맞는 업체를 찾고 있습니다…'}]};
    renderSearch();
    try {
      const id = S.search.id;
      S.search = await api('search', {id, message: text});
      if (!id) { S.route = {name: 'search', id: S.search.id}; history.replaceState(null, '', '#/s/' + S.search.id); }
      await refreshBoot();
    } catch (e) { S.search.turns.pop(); S.search.turns.push({role: 'assistant', text: '답변을 받지 못했습니다. ' + e.message}); }
    renderSearch();
  };
  const asking = !!s?.intake;  // 입력서로 시작한 기록: 조건은 그대로, 모르는 것을 묻는 창
  const box = composer({placeholder: asking ? '입력서 내용·법령 판단·업체·서류에 대해 모르는 것을 물어보세요' : '어떤 폐기물이, 어떤 상태로, 얼마나 있나요? 어떤 업체를 찾으세요?',
    note: asking ? 'AI에게 묻기 · 검색 조건은 바뀌지 않아요 (바꾸려면 법령 판단 탭의 ‘사실 고치기’)' : aiNote(), onsend: send});
  if (!s || !s.turns.length) {
    const examples = me.roles.includes('emitter') || me.roles.length === 0
      ? ['PE 포장필름 스크랩이 월 5톤 정도 나옵니다. 오염은 거의 없고 재생 원료로 쓸 재활용업체를 찾고 있어요.',
         '사출 불량품과 런너가 섞인 폐합성수지를 연료로 쓰거나 소각할 수 있는 곳을 알아보고 싶습니다.',
         '폐목재 팔레트가 주 2톤씩 나오는데 우드칩으로 가공하는 업체와 운반업체가 필요합니다.']
      : ['깨끗한 PE·PP 스크랩을 월 20톤 이상 공급받을 배출업체를 찾고 있습니다.',
         '우리 시설로 들어오는 물량을 운반해 줄 수집·운반업체를 찾습니다.'];
    body.replaceChildren(h('div', {class: 'center'}, h('div', {class: 'hero'},
      h('h2', {text: '어떤 거래를 찾고 계신가요?'}),
      h('p', {text: '폐기물의 종류, 상태, 물량과 찾는 상대를 평소 말하듯 적어 주세요. 조건에 맞는 업체를 지도에서 보여 드립니다.'}),
      h('div', {class: 'composer-wrap'}, box.el),
      h('div', {class: 'examples'}, examples.map(text => h('button', {text, onclick: () => box.fill(text)}))))));
    box.area.focus();
    return;
  }
  // 이 기록에서 보낸 요청만 본다(예전 거래방이 있어도 새 거래 요청은 보낼 수 있다)
  const asked = Object.fromEntries((S.boot.requests || []).filter(q => !q.incoming && q.status !== 'declined' && q.search_id === s.id).map(q => [q.other?.id, q]));
  const companyCard = c => h('div', {class: 'co-card' + (c.fit ? '' : ' unfit')},
    h('div', {class: 'desk-doc-top'}, h('b', {text: c.name}), chip(ROLE[c.match_role] || '', 'line'),
      c.kind === 'member' ? chip(c.linked ? '앱 가입 · 공공자료 연결' : '앱 가입', 'ok') : chip('공공자료 · 미가입', 'accent'),
      c.demo ? chip('예시', 'warn') : null, c.fit ? null : chip('영업대상 아님', 'bad')),
    h('small', {class: 'faint', text: [c.address, km(c.distance_km)].filter(Boolean).join(' · ')}),
    c.handles?.length ? h('small', {text: '영업대상: ' + c.handles.join(', ')}) : null,
    h('small', {class: c.fit ? 'faint' : 'warn-note', text: c.note}),
    h('div', {style: 'display:flex;gap:6px;margin-top:4px'},
      c.kind === 'member' ? (asked[c.id]?.status === 'accepted' ? h('a', {class: 'btn small ghost', href: '#/r/' + asked[c.id].room_id, text: '거래방 열기'})
        : asked[c.id]?.status === 'pending' ? chip('답변 대기', 'warn')
        : h('button', {class: 'btn small', type: 'button', text: '거래 요청', onclick: () => {
            if (!c.fit && !confirm(c.name + '의 등록된 영업대상에 이번 폐기물이 없습니다. 그래도 거래 요청을 보낼까요?')) return;
            requestDialog(c, {search_id: s.id, need: s.need}); }}))
        : c.phone ? h('a', {class: 'btn small ghost', href: 'tel:' + c.phone.replace(/[^\d+]/g, ''), text: '전화 ' + c.phone}) : null));
  const thread = h('div', {class: 'thread'}, h('div', {class: 'thread-inner'}, s.turns.map(t => h('div', {class: 'turn ' + t.role}, t.text,
    t.companies?.length ? h('div', {class: 'co-cards'}, t.companies.map(companyCard)) : null,
    t.cites?.length ? h('div', {class: 'cites'}, t.cites.map((c, i) => h('details', {class: 'law'},
      h('summary', null, h('span', {class: 'kind ' + (c.kind || ''), text: c.kind_label}), h('span', {class: 'law-tag', text: `[${i + 1}] ${c.label}`})),
      h('p', {text: c.text})))) : null)),
    asking && s.turns.length <= 2 ? h('div', {class: 'quick', style: 'margin-top:10px'}, ['이 분류번호가 맞는지 어떻게 확인해요?', '어느 업체에 먼저 연락하면 좋아요?', '지정폐기물인지 확인하려면 뭘 해야 해요?'].map(q =>
      h('button', {type: 'button', text: q, onclick: () => send(q)}))) : null));
  const center = h('div', {class: 'center'}, thread, h('div', {class: 'composer-wrap'}, box.el));
  const kids = [center];
  const pub = s.public || [];
  if (s.intake) {  // 입력서로 시작한 기록: 1차 후보 없이 법령 기준 추천 하나만(지도 포함)
    const box = h('div'); casePanel(s.id, box);
    kids.push(h('aside', {class: 'side'}, h('div', {class: 'side-head'}, h('b', {text: '법령 기준 추천'}),
      h('small', {class: 'faint', text: '입력서 사실로 법령을 판단해, 허가와 처리 방법이 맞는 업체를 골랐습니다'})),
      h('div', {class: 'side-scrolling', style: 'padding-top:10px'}, box)));
  } else if (s.results.length || pub.length) {
    const action = c => c.public ? (c.phone ? h('a', {class: 'btn small ghost', href: 'tel:' + c.phone.replace(/[^\d+]/g, ''), text: '전화', onclick: e => e.stopPropagation()}) : chip('연락처 없음'))
      : c.request === 'accepted' ? chip('거래방 열림', 'ok') : c.request === 'pending' ? chip('답변 대기', 'warn')
      : h('button', {class: 'btn small', text: '거래 요청', onclick: e => { e.stopPropagation(); requestDialog(c, {search_id: s.id, need: s.need}); }});
    const stab = S.searchTab || 'list';
    const switcher = h('div', {class: 'side-tabs', role: 'tablist'},
      h('button', {'aria-selected': String(stab === 'list'), text: (s.intake ? '1차 후보 ' : '후보 업체 ') + (s.results.length + pub.length) + '곳', title: '허가 품목과 거리로만 고른 후보입니다. 법령 기준 순위는 ‘법령 판단’ 탭에 나옵니다.', onclick: () => { S.searchTab = 'list'; renderSearch(); }}),
      h('button', {'aria-selected': String(stab === 'judge'), text: '법령 판단', onclick: () => { S.searchTab = 'judge'; renderSearch(); }}));
    if (stab === 'judge') {
      const box = h('div'); casePanel(s.id, box);
      kids.push(h('aside', {class: 'side'}, switcher, h('div', {class: 'side-scrolling', style: 'padding-top:14px'}, box)));
    } else
    kids.push(h('aside', {class: 'side'}, switcher,
      h('div', {class: 'side-scrolling', style: 'padding-top:14px'}, h('div', {style: 'margin-bottom:12px'}, needChips(s.need)),
        s.origin ? h('p', {class: 'faint', style: 'font-size:12.5px;margin-bottom:6px', text: `거리 기준: 입력서의 배출 현장 (${s.origin.label})`}) : null,
        h('p', {class: 'warn-note', style: 'margin-bottom:10px', text: '법령 검토 전 1차 후보입니다. 등록한 품목·허가 품목과 거리로만 골랐습니다. 분류번호를 정하고 ‘법령 판단’을 실행하면 처리 경로가 맞는 업체만 법령 기준으로 순위를 매깁니다(30~90초).'}),
        resultPanel([...s.results, ...pub], s.origin ? {...me, lat: s.origin.lat, lng: s.origin.lng, address: s.origin.label} : me, action),
        h('p', {class: 'faint', style: 'font-size:12.5px;margin-top:14px', text: '가입 업체는 등록한 품목과 거리로, 공공자료 업체는 환경부·한국환경공단 업체현황의 허가 품목·재활용 유형(시행규칙 별표 4의3)과 시·군·구 거리로 골랐습니다. 공공자료 업체는 앱에 가입하지 않아 거래 요청 대신 직접 연락해야 하며, 허가 범위와 반입 가능 여부는 반드시 상대 업체에 확인하세요.'}))));
  }
  body.replaceChildren(...kids);
  thread.scrollTop = thread.scrollHeight;
}
function requestDialog(company, {search_id, room_id, need, as_role}) {
  const note = h('textarea', {class: 'input', rows: 3, maxLength: 600, placeholder: '예: 시료를 먼저 보내드릴 수 있습니다. 이번 주 통화 가능하실까요?'});
  const dlg = h('dialog', null,
    h('h2', {text: room_id ? company.name + '에 참여 요청' : company.name + '에 거래 요청'}),
    h('p', {class: 'muted', style: 'font-size:13.5px;margin-bottom:12px', text: room_id
      ? '상대 업체가 승인하면 이 거래방에 함께 들어옵니다.' : '상대 업체가 승인하면 두 업체의 거래방이 열립니다. 아래 조건이 함께 전달됩니다.'}),
    need && needChips(need),
    h('label', {class: 'field', style: 'margin-top:14px'}, h('span', null, '전달 메모', h('i', {text: '선택'})), note),
    h('div', {class: 'dlg-actions'}, h('button', {class: 'btn', text: '취소', onclick: () => dlg.close()}),
      h('button', {class: 'btn primary', text: '요청 보내기', onclick: guard(async () => {
        await api('requests', {to: company.id, search_id, room_id, as_role, note: note.value});
        dlg.close(); toast(company.name + '에 요청을 보냈습니다.');
        await refreshBoot();
        if (room_id) { S.invite.data = null; await refreshRoom(true); }
        else if (S.search) { S.search = await api('search/' + S.search.id); renderSearch(); }
      })})));
  dlg.addEventListener('close', () => dlg.remove());
  document.body.append(dlg); dlg.showModal();
}

// ── 요청함 ────────────────────────────────────────────────────────────
function renderRequests() {
  setTop('요청함');
  const all = S.boot.requests, list = all.filter(q => q.incoming === (S.reqTab === 'in'));
  const decide = (q, accept) => guard(async () => {
    const out = await api('requests/' + q.id + '/decide', {accept});
    await refreshBoot();
    if (out.room_id) location.hash = '#/r/' + out.room_id; else renderRequests();
  });
  const row = q => {
    const o = q.other;
    const lead = q.incoming
      ? (q.invite ? `${o.name}에서 ‘${q.room_title}’ 거래에 ${q.as_role}로 참여해 달라고 요청했습니다. 이야기해 보시겠어요?`
        : `${o.name}에서 ${q.title} 거래를 이야기해 보고 싶어 합니다. 대화를 시작하시겠어요?`)
      : (q.invite ? `‘${q.room_title}’ 거래에 ${q.as_role}로 참여를 요청했습니다.` : `${q.title} 거래를 요청했습니다.`);
    const status = {pending: chip(q.incoming ? '답변 필요' : '답변 대기', 'warn'), accepted: chip('승인됨', 'ok'), declined: chip('거절됨')}[q.status];
    return h('div', {class: 'req'},
      h('div', {class: 'who'}, h('b', {text: o.name}), h('span', {text: o.roles.map(r => ROLE[r]).join(' · ')}),
        h('div', {style: 'margin-top:6px;display:flex;gap:5px;flex-wrap:wrap'}, status, o.demo && chip('예시', 'line'))),
      h('div', null, h('p', {class: 'lead', text: lead}),
        h('dl', null, q.need.flatMap(n => [h('dt', {text: n.label}), h('dd', {text: n.value})]),
          q.note && [h('dt', {text: '전달 메모'}), h('dd', {text: q.note})],
          h('dt', {text: '업체'}), h('dd', {text: [o.address, km(o.distance_km), o.methods, o.capacity || o.vehicles].filter(Boolean).join(' · ')})),
        h('div', {class: 'actions'},
          q.incoming && q.status === 'pending' && [h('button', {class: 'btn primary', text: '승인하고 대화 시작', onclick: decide(q, true)}), h('button', {class: 'btn', text: '거절', onclick: decide(q, false)})],
          q.status === 'accepted' && q.room_id && h('button', {class: 'btn', text: '거래방 열기', onclick: () => { location.hash = '#/r/' + q.room_id; }}),
          !q.incoming && q.status === 'pending' && h('button', {class: 'btn ghost', text: '요청 취소', onclick: guard(async () => {
            if (!confirm(`${o.name}에 보낸 요청을 취소할까요?`)) return;
            await api('requests/' + q.id + '/cancel', {}); await refreshBoot(); renderRequests(); toast('요청을 취소했습니다.'); })}))));
  };
  const count = kind => all.filter(q => q.incoming === (kind === 'in') && q.status === 'pending').length;
  const tab = (kind, label) => h('button', {'aria-selected': String(S.reqTab === kind), text: label + (count(kind) ? ' ' + count(kind) : ''), onclick: () => { S.reqTab = kind; renderRequests(); }});
  document.getElementById('body').replaceChildren(h('div', {class: 'page'}, h('div', {class: 'page-inner'},
    h('h2', {text: '요청함'}), h('p', {text: '다른 업체가 보낸 거래 요청을 확인하고 승인하거나 거절합니다. 승인하면 그 업체와의 거래방이 열립니다.'}),
    h('div', {class: 'tabs', role: 'tablist'}, tab('in', '받은 요청'), tab('out', '보낸 요청')),
    list.length ? list.map(row) : h('p', {class: 'empty', text: S.reqTab === 'in' ? '아직 받은 요청이 없습니다.' : '아직 보낸 요청이 없습니다. ‘새 거래 찾기’에서 후보 업체에 요청을 보내 보세요.'}))));
}

// ── 거래방 ────────────────────────────────────────────────────────────
function messageNode(m) {
  if (m.kind === 'system') return h('div', {class: 'msg system', text: m.body});
  if (m.kind === 'guide') return h('div', {class: 'msg guide'}, h('div', {class: 'from', text: 'AI 안내'}), h('div', {class: 'text', text: m.body}));
  const href = m.file && '/api/p/files/' + m.file.id;
  const link = m.file && (m.file.mime.startsWith('image/')
    ? h('a', {class: 'photo', href, target: '_blank', rel: 'noopener', title: m.file.name}, h('img', {src: href, alt: m.file.name, loading: 'lazy'}))
    : h('a', {class: 'filelink', href, target: '_blank', rel: 'noopener'}, icon('file'), m.file.name));
  return h('div', {class: 'msg' + (m.mine ? ' mine' : '')}, h('div', {class: 'from', text: m.mine ? '나' : m.name}),
    h('div', {class: 'bubble'}, m.body, m.body && link ? h('br') : null, link));
}
// 사진은 긴 변 1800px의 JPEG로 줄여서 올린다. 휴대폰 사진처럼 큰 파일이나 HEIC도 브라우저가 열 수 있으면 올라간다.
async function prepareFile(f) {
  const base64 = blob => new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1]); r.onerror = () => no(Error('파일을 읽지 못했습니다.')); r.readAsDataURL(blob); });
  const isImage = f.type.startsWith('image/') || /\.(heic|heif|jpe?g|png|webp|gif|bmp)$/i.test(f.name);
  if (!isImage) {
    if (f.type !== 'application/pdf') throw Error('사진(이미지)이나 PDF 파일만 올릴 수 있습니다.');
    if (f.size > 5 * 1024 * 1024) throw Error('PDF는 5MB 이하로 올려 주세요.');
    return {name: f.name, mime: f.type, data: await base64(f)};
  }
  const url = URL.createObjectURL(f);
  try {
    const img = new Image(); img.src = url;
    await img.decode().catch(() => { throw Error('이 브라우저에서 열 수 없는 사진 형식입니다. JPG나 PNG로 바꿔 올려 주세요.'); });
    const scale = Math.min(1, 1800 / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = Object.assign(document.createElement('canvas'), {width: Math.round(img.naturalWidth * scale), height: Math.round(img.naturalHeight * scale)});
    const ctx = canvas.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height); ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(ok => canvas.toBlob(ok, 'image/jpeg', 0.85));
    if (!blob) throw Error('사진을 변환하지 못했습니다.');
    return {name: (f.name.replace(/\.[^.]+$/, '') || '사진') + '.jpg', mime: 'image/jpeg', data: await base64(blob)};
  } finally { URL.revokeObjectURL(url); }
}
function renderRoom() {
  const d = S.room, id = d.room.id, body = document.getElementById('body');
  setTop(d.room.title);
  const thread = h('div', {class: 'thread', id: 'roomThread'}, h('div', {class: 'thread-inner', id: 'roomMsgs'}, d.messages.map(messageNode)));
  // 고른 파일은 바로 보내지 않고 입력창 위에 올려 둔다. 보내기를 눌러야 전송된다.
  let pending = null;
  const tray = h('div', {class: 'tray', hidden: true});
  const showTray = () => {
    tray.hidden = !pending;
    tray.replaceChildren(...(pending ? [h('div', {class: 'attached'},
      pending.mime.startsWith('image/') ? h('img', {src: 'data:' + pending.mime + ';base64,' + pending.data, alt: ''}) : icon('file'),
      h('span', {text: pending.name}),
      h('button', {type: 'button', 'aria-label': '첨부 취소', title: '첨부 취소', text: '×', onclick: () => { pending = null; showTray(); box.refresh(); }}))] : []));
  };
  const stage = guard(async f => {
    if (!f) return;
    pending = await prepareFile(f); showTray(); box.refresh(); box.area.focus();
  });
  const fileInput = h('input', {type: 'file', accept: 'application/pdf,image/*', hidden: true, onchange: e => { const f = e.target.files[0]; e.target.value = ''; stage(f); }});
  const box = composer({placeholder: '거래방에 메시지 보내기', note: '참여 업체 모두에게 보입니다', tray, hasExtra: () => pending,
    attach: h('button', {class: 'icon-btn', type: 'button', title: '사진·PDF 첨부 (붙여넣기, 끌어다 놓기도 됩니다)', 'aria-label': '파일 첨부', onclick: () => fileInput.click()}, icon('clip')),
    onsend: async text => {
      if (pending) { await api('rooms/' + id + '/files', {...pending, text}); pending = null; showTray(); }
      else await api('rooms/' + id + '/messages', {text});
      await refreshRoom();
    }});
  box.area.addEventListener('paste', e => { const f = [...(e.clipboardData?.files || [])][0]; if (f) { e.preventDefault(); stage(f); } });
  const drop = e => { if (e.dataTransfer?.types?.includes('Files')) { e.preventDefault(); if (e.type === 'drop') stage(e.dataTransfer.files[0]); } };
  body.replaceChildren(
    h('div', {class: 'center', ondragover: drop, ondrop: drop}, h('div', {class: 'dealbar', id: 'dealbar'}), thread, h('div', {class: 'composer-wrap'}, box.el, fileInput)),
    h('aside', {class: 'side'}, h('div', {class: 'side-tabs', id: 'sideTabs', role: 'tablist'}), h('div', {id: 'pane', style: 'flex:1;min-height:0;display:flex;flex-direction:column'})));
  renderDealbar(); renderPane();
  thread.scrollTop = thread.scrollHeight;
}
function renderDealbar() {
  const d = S.room, me = d.members.find(m => m.me), bar = document.getElementById('dealbar'); if (!bar) return;
  bar.replaceChildren(h('div', {class: 'dealbar-inner'},
    h('div', {class: 'members'}, d.members.map(m => h('span', {class: 'chip' + (m.agreed ? ' ok' : ''), title: [m.address, m.methods].filter(Boolean).join(' · ')},
      m.agreed && h('span', {class: 'dot'}), m.name, h('span', {class: 'faint', text: m.role_label})))),
    d.room.agreed ? chip('거래 합의 완료', 'ok')
      : h('button', {class: 'btn small ' + (me.agreed ? '' : 'blue'), text: me.agreed ? '합의 취소' : '거래 진행에 합의', onclick: guard(async () => {
        await api('rooms/' + d.room.id + '/agree', {agree: !me.agreed}); await refreshRoom();
      })}),
    d.room.can_delete ? h('button', {class: 'btn small ghost danger', text: '거래방 삭제', title: '대화·서류·첨부까지 모두 지웁니다', onclick: guard(async () => {
      if (!confirm(`‘${d.room.title}’ 거래방을 삭제할까요?\n\n대화, 작성한 서류, 첨부 파일, 법령 판단 기록이 참여 업체 모두에게서 사라지고 되돌릴 수 없습니다.`)) return;
      await api('rooms/' + d.room.id + '/delete', {}); S.room = null; await refreshBoot(); location.hash = '#/'; toast('거래방을 삭제했습니다.');
    })}) : null));
}
function renderPane() {
  const d = S.room, tabs = document.getElementById('sideTabs'), pane = document.getElementById('pane'); if (!tabs) return;
  const docs = d.docs;
  const tab = (key, label, extra) => h('button', {'aria-selected': String(S.tab === key), onclick: () => { S.tab = key; renderPane(); }}, label, extra && h('small', {text: extra}));
  tabs.replaceChildren(tab('ai', 'AI 도우미'), tab('docs', '서류', docs ? docs.done + '/' + docs.total : ''), tab('judge', '법령 판단'), tab('steps', '절차'), tab('invite', '업체 초대', d.invites.length ? '대기 ' + d.invites.length : ''));
  if (S.tab === 'ai') return paneAI(pane);
  const scroll = h('div', {class: 'side-scrolling', style: 'padding-top:14px'});
  const keep = pane.querySelector('.side-scrolling')?.scrollTop || 0;
  pane.replaceChildren(scroll);
  if (S.tab === 'judge') { if (!S.judgeBox || S.judgeBox.dataset.room !== d.room.id) { S.judgeBox = h('div', {'data-room': d.room.id}); casePanel(d.room.id, S.judgeBox); } scroll.append(S.judgeBox); return; }
  if (S.tab === 'docs') paneDocs(scroll); else if (S.tab === 'steps') paneSteps(scroll); else paneInvite(scroll);
  scroll.scrollTop = keep;
}
function paneAI(pane) {
  const d = S.room, id = d.room.id;
  const list = h('div', {class: 'ai-thread'});
  const draw = () => {
    list.replaceChildren(...(d.ai_turns.length ? d.ai_turns.map(t => h('div', {class: 'turn ' + t.role, text: t.body}))
      : [h('p', {class: 'muted', text: '이 대화는 우리 업체에만 보입니다. 준비할 서류, 남은 절차, 기한처럼 이 거래에서 궁금한 점을 물어보세요.'})]));
    list.scrollTop = list.scrollHeight;
  };
  const ask = async text => {
    d.ai_turns.push({role: 'user', body: text}, {role: 'pending', body: '확인하고 있습니다…'}); draw();
    try { const out = await api('rooms/' + id + '/ai', {message: text}); d.ai_turns.pop(); d.ai_turns.push({role: 'assistant', body: out.reply}); }
    catch (e) { d.ai_turns.pop(); d.ai_turns.push({role: 'assistant', body: '답변을 받지 못했습니다. ' + e.message}); }
    draw();
  };
  const box = composer({placeholder: 'AI에게 물어보기', note: aiNote(), onsend: ask});
  const quick = h('div', {class: 'quick'}, ['우리가 준비할 서류는?', '다음 절차 알려줘'].map(q => h('button', {type: 'button', text: q, onclick: () => ask(q)})),
    !d.members.some(m => m.role === 'hauler') && h('button', {type: 'button', text: '운반업체 찾기', onclick: () => { S.tab = 'invite'; S.invite.want = 'hauler'; renderPane(); findInvite(); }}));
  pane.replaceChildren(list, h('div', {class: 'ai-compose'}, quick, box.el));
  draw();
}

// ── 기초 입력서로 시작 ────────────────────────────────────────────────
// 입력서(docx) → 폐기물 고르기 → 읽은 값 확인·보충 질문·분류번호·찾을 업체 → 업체 찾기 + 법령 판단.
// 빈칸은 정해진 질문으로만 채운다(AI 해석 없음). '모름'은 모름으로 넘기고 판단 결과에서 다시 안내한다.
const WANT_LABEL = {recycler: '재활용·가공업체', disposer: '처분업체(소각 등)', hauler: '수집·운반업체'};
function readAsBase64(f) {
  return new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1]); r.onerror = () => no(Error('파일을 읽지 못했습니다.')); r.readAsDataURL(f); });
}
function renderIntake() {
  setTop('기초 입력서로 시작');
  const I = S.intake = S.intake || {};
  const body = document.getElementById('body');
  const inner = h('div', {class: 'page-inner intake', style: 'max-width:820px'});
  body.replaceChildren(h('div', {class: 'page'}, inner));
  const file = h('input', {type: 'file', accept: '.json,application/json,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document', hidden: true});
  file.onchange = guard(async () => {
    const f = file.files[0]; file.value = ''; if (!f) return;
    inner.replaceChildren(h('p', {class: 'faint', text: '입력서를 읽고 있습니다…'}));
    try {
      const out = await api('intake/read', {file: {name: f.name, data: await readAsBase64(f)}});
      Object.assign(I, {file: f.name, company: out.company, items: out.items, item: null, kind: out.kind, notice: out.notice || ''});
      if (out.items.length === 1) pick(out.items[0]);
    } catch (e) { toast(e.message); }
    renderIntake();
  });
  const pick = it => { I.item = it; I.facts = {...it.facts}; I.x = it; I.wastes = [...it.wastes]; I.want = I.want || ['recycler', 'hauler']; };
  const upload = h('button', {class: 'btn primary', text: I.items ? '다른 파일 올리기' : '입력서·전달 파일 올리기', onclick: () => file.click()});
  const pending = it => it.part1 ? (it.p1q || []).length : it.code_fixed ? 0 : (it.questions || []).length;
  const replaceItem = it => { I.items = I.items.map(x => x.index === it.index ? it : x); pick(it); renderIntake(); };
  const step = (n, title, ...kids) => h('section', {class: 'istep'}, h('h3', null, h('span', {class: 'no', text: n}), title), ...kids);

  if (!I.items) {
    inner.append(h('h2', {text: '기초 입력서로 시작하기'}),
      h('p', {text: '폐기물 정보를 정해진 양식으로 받으면 문장을 해석하는 과정이 없어 같은 내용이면 늘 같은 조건으로 업체를 찾습니다. 입력서에 적은 출처(원료 투입대장 몇 행 등)는 판단 근거로 함께 남습니다.'}),
      step(1, '입력서나 전달 파일을 올리세요', h('p', {class: 'muted', text: '둘 중 가진 것을 올립니다.'}),
        h('ul', {class: 'muted', style: 'margin:0 0 8px 18px;line-height:1.7'},
          h('li', {text: 'Word 기초 입력서(.docx): 파트 1이 읽어 비어 있는 칸만 확인 질문으로 묻고, 입력서에 적힌 분류번호를 그대로 씁니다.'}),
          h('li', {text: '분류 에이전트 전달 파일(.json): 분류번호(selected_code)와 분석값·월별 기록·근거를 그대로 씁니다.'})),
        h('div', {style: 'display:flex;gap:8px;flex-wrap:wrap'}, upload, h('a', {class: 'btn ghost', href: '/api/p/intake-template', text: '빈 입력서 받기 (.docx)'}))),
      step(2, '확인 질문에 답하고 업체를 찾습니다', h('p', {class: 'muted', text: '읽은 값을 확인하면 지도에 업체 후보가 뜨고, 법령 판단(30~90초)이 이어서 처리 경로·필요 서류·법령 기준 업체 순위를 냅니다.'})), file);
    return;
  }
  const head = h('div', {class: 'intake-head'},
    h('div', null, h('h2', {text: I.company?.company_name || '기초 입력서'}),
      h('p', {class: 'muted', text: [I.kind === 'handoff' ? '분류 에이전트 전달 파일' : I.kind === 'part1' ? '기초 입력서 (파트 1 접수)' : null, I.file, I.company?.site_address, I.items.length + '개 폐기물'].filter(Boolean).join(' · ')})),
    upload, file);
  inner.append(head);
  if (I.notice) inner.append(h('p', {class: 'warn-note', text: I.notice}));
  if (!I.item) {
    inner.append(step('', '어떤 폐기물부터 찾을까요?', h('p', {class: 'muted', text: '입력서에 폐기물이 여러 개 있습니다. 하나씩 업체를 찾습니다.'}),
      h('div', {class: 'cards'}, I.items.map(it => h('button', {class: 'card', style: 'text-align:left', onclick: () => { pick(it); renderIntake(); }},
        h('span', {class: 'no', text: it.index}), h('div', null, h('h3', null, it.facts.name, (I.done || []).includes(it.index) ? chip('찾기 완료', 'ok') : null),
          h('p', {text: [it.facts.process, it.facts.monthly_ton && '월 ' + it.facts.monthly_ton + '톤', pending(it) ? '확인 질문 ' + pending(it) + '개' : '확인 질문 없음'].filter(Boolean).join(' · ')})))))));
    return;
  }
  const F = I.facts, X = I.x;
  const recheck = guard(async () => { I.x = await api('intake/check', {facts: F}); if (!I.wastesTouched) I.wastes = [...I.x.wastes]; renderIntake(); });
  const P1 = I.item.part1;
  // 파트 1 확인 질문 (Word 입력서): 답은 파트 1 접수 DB에 저장되고, 시작할 때 다시 내보내 판단에 넣는다
  const p1cards = (P1 ? I.item.p1q || [] : []).map(q => {
    const el = h('input', {class: 'input', maxLength: 400, placeholder: q.hint ? '지금 값: ' + q.hint : ''});
    const send = guard(async v => { if (!v) return toast('답을 적거나 ‘모름’을 누르세요.');
      replaceItem(await api('intake/p1-answer', {part1: P1, request: q.id, value: v})); toast('저장했습니다.'); });
    el.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); send(el.value.trim()); } });
    return h('div', {class: 'ask-card'}, h('b', {text: q.label || '확인 질문'}), h('p', {text: q.text}),
      h('div', {class: 'qrow'}, el, h('button', {class: 'btn small', type: 'button', text: '저장', onclick: () => send(el.value.trim())}),
        h('button', {class: 'btn small ghost', type: 'button', text: '모름', onclick: () => send('모름')})));
  });
  // 보충 질문 (입력서·전달 파일이 아닌 경우만)
  const qcards = P1 ? p1cards : I.item.code_fixed ? [] : X.questions.map(q => {
    let input;
    const answer = v => { F[q.key] = v; recheck(); };
    if (q.kind === 'pick') input = h('div', {class: 'picks'}, [...q.options, '모름'].map(o => h('button', {type: 'button', text: o, onclick: () => answer(o)})));
    else {
      const el = h(q.kind === 'area' ? 'textarea' : 'input', {class: 'input', rows: q.kind === 'area' ? 2 : null, maxLength: 400, inputmode: q.kind === 'number' ? 'decimal' : null});
      const save = () => { const v = el.value.trim(); if (!v) return toast('답을 적거나 ‘모름’을 누르세요.'); if (q.kind === 'number' && !/^\d+(\.\d+)?$/.test(v)) return toast('숫자만 적어 주세요. 예: 5 또는 0.3'); answer(v); };
      el.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); save(); } });
      input = h('div', {class: 'qrow'}, el, h('button', {class: 'btn small', type: 'button', text: '확인', onclick: save}), h('button', {class: 'btn small ghost', type: 'button', text: '모름', onclick: () => answer('모름')}));
    }
    return h('div', {class: 'ask-card'}, h('b', {text: q.label}), h('p', {text: q.text}), input, h('small', {class: 'faint', text: '왜 묻나요? ' + q.why}));
  });
  // 읽은 값 (고칠 수 있음)
  const grid = h('div', {class: 'facts'});
  for (const key of ['name', 'process', 'form', 'components', 'contamination', 'contamination_detail', 'hazard', 'analysis', 'mixture', 'separable', 'monthly_ton', 'address', 'facility_type']) {
    if (!F[key]) continue;
    const el = h(key === 'process' ? 'textarea' : 'input', {class: 'input', rows: key === 'process' ? 2 : null, maxLength: 400, value: F[key]});
    el.value = F[key];
    el.addEventListener('change', () => { F[key] = el.value.trim(); recheck(); });
    grid.append(h('label', {class: 'field' + (['name', 'process', 'address', 'components'].includes(key) ? ' full' : '')}, h('span', null, X.labels[key] || key), el));
  }
  // 분류번호 (+ 추천받기: 같은 폐기물을 보는 동안 결과를 유지). 전달 파일이면 그 값으로 고정
  const fixed = I.item.code_fixed;
  const codeInput = h('input', {class: 'input', placeholder: '00-00-00', maxLength: 8, value: F.code || ''});
  codeInput.value = F.code || '';
  codeInput.addEventListener('change', () => { F.code = codeInput.value.trim(); recheck(); });
  const codeRef = h('input', {class: 'input', maxLength: 200, placeholder: '근거 (예: 신고증명서 기재 번호)'});
  const codeP1 = h('input', {class: 'input', placeholder: '예: 51-03-01', maxLength: 8});
  const p1CodeBox = h('div', {class: 'field full'}, h('span', null, '분류번호 (알고 있으면)'),
    h('div', {class: 'qrow'}, codeP1, codeRef, h('button', {class: 'btn small', type: 'button', text: '기록', onclick: guard(async () => {
      replaceItem(await api('intake/p1-code', {part1: P1, code: codeP1.value.trim(), reference: codeRef.value.trim()})); toast('분류번호를 기록했습니다.'); })})),
    h('small', {class: 'faint', text: '시행규칙 별표 4의 번호입니다. 모르면 비워 두세요. 업체는 찾되 법령 판단은 번호가 정해진 뒤에 합니다.'}));
  const codeBox = P1 && !fixed ? p1CodeBox : fixed ? h('div', {class: 'field full'}, h('span', null, P1 ? '분류번호 (입력서에 적힌 값)' : '분류번호 (분류 에이전트가 정한 값)'),
    h('div', {class: 'fixed-code'}, h('b', {text: F.code || '없음'}), h('span', {text: I.item.code_label || ''})),
    I.item.code_basis ? h('small', {class: 'faint', text: '근거: ' + I.item.code_basis}) : null,
    I.item.code_conditions ? h('small', {class: 'faint', text: '조건: ' + I.item.code_conditions}) : null,
    h('small', {class: F.code ? 'faint' : 'error', text: !F.code ? I.item.code_note : P1 ? '입력서의 분류번호 칸 값을 파트 1에 기록해 그대로 씁니다.' : '전달 파일의 selected_code를 그대로 씁니다. 바꾸려면 분류 에이전트에서 다시 내보내세요.'}))
  : h('div', {class: 'field full'}, h('span', null, '분류번호 (시행규칙 별표 4)'), codeInput,
    h('div', {class: 'picks', style: 'margin-top:6px'}, X.codes.map(c => h('button', {type: 'button', 'aria-pressed': String(F.code === c.code), title: c.name + ' · ' + c.group,
      text: c.code + ' ' + c.name.replace(/\(.*$/, '').slice(0, 16) + (c.designated ? ' (지정)' : ''), onclick: () => { F.code = F.code === c.code ? '' : c.code; recheck(); }}))),
    X.code_check ? h('small', {class: /상위|없습니다|형식/.test(X.code_check) ? 'error' : 'faint', text: X.code_check}) : null,
    I.item.code_note ? h('small', {class: 'faint', text: I.item.code_note}) : null,
    h('small', {class: 'faint', text: '이름이 비슷한 번호를 보여 줍니다. 확실하지 않으면 비워 두세요. 업체는 찾되 법령 판단은 분류번호를 정한 뒤에 합니다(거래방의 ‘분류 확인’ 서류로 기관에 확인할 수 있습니다).'}));
  // 가진 문서 첨부 (파트 1 attach-history): 예전 신고서·인계서·처리실적
  const histKind = h('select', {class: 'input', style: 'max-width:220px'}, Object.entries(I.item.history_kinds || {}).map(([k, v]) => h('option', {value: k, text: v})));
  const histIssuer = h('input', {class: 'input', maxLength: 80, placeholder: '발급 기관·회사'});
  const histFile = h('input', {type: 'file', hidden: true});
  histFile.onchange = guard(async () => { const f = histFile.files[0]; histFile.value = ''; if (!f) return;
    replaceItem(await api('intake/p1-attach', {part1: P1, kind: histKind.value, issuer: histIssuer.value.trim(), file: {name: f.name, data: await readAsBase64(f)}}));
    toast('첨부했습니다.'); });
  const attachBox = P1 ? h('div', null, h('p', {class: 'muted', text: '예전 배출자 신고서·인계서·처리실적이 있으면 붙이세요. 파트 1이 근거로 기록해 판단에 함께 넘깁니다.'}),
    h('div', {class: 'qrow'}, histKind, histIssuer, h('button', {class: 'btn small', type: 'button', text: '파일 고르기', onclick: () => histFile.click()}), histFile)) : null;
  // 찾을 업체
  const toggle = (arr, v) => arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v];
  const wantBox = h('div', {class: 'picks'}, Object.entries(WANT_LABEL).map(([k, label]) => h('button', {type: 'button', 'aria-pressed': String(I.want.includes(k)), text: label,
    onclick: () => { I.want = toggle(I.want, k); renderIntake(); }})));
  S.options = S.options || null;
  const wasteBox = h('div', {class: 'picks'});
  const drawWastes = () => wasteBox.replaceChildren(...(S.options?.wastes || I.wastes).map(w => h('button', {type: 'button', 'aria-pressed': String(I.wastes.includes(w)), text: w,
    onclick: () => { I.wastes = toggle(I.wastes, w); I.wastesTouched = true; drawWastes(); }})));
  drawWastes();
  if (!S.options) api('options').then(o => { S.options = o; drawWastes(); }).catch(() => {});
  const go = h('button', {class: 'btn primary', text: F.code ? '이 내용으로 업체 찾기 + 법령 판단' : '이 내용으로 업체 찾기'});
  go.onclick = guard(async () => {
    const left = pending(I.item);
    if (left && !confirm(`아직 답하지 않은 확인 질문이 ${left}개 있습니다. 빈칸으로 두고 진행할까요?`)) return;
    go.disabled = true; go.textContent = '찾는 중…';
    try {
      const out = await api('intake/start', {facts: F, wastes: I.wastes, want: I.want, sources: I.item.sources, file: I.file,
        part1: P1 || null, handoff: P1 ? null : I.item.handoff || null});
      S.search = out; S.searchTab = F.code ? 'judge' : 'list';
      I.done = [...(I.done || []), I.item.index]; I.item = null;
      await refreshBoot(); location.hash = '#/s/' + out.id;
    } catch (e) { toast(e.message); go.disabled = false; go.textContent = '다시 시도'; }
  });
  inner.append(
    I.items.length > 1 ? h('button', {class: 'btn small ghost', style: 'margin:4px 0 8px', text: '← 다른 폐기물 고르기', onclick: () => { I.item = null; renderIntake(); }}) : null,
    step(1, `‘${F.name || '폐기물'}’ ${P1 || !fixed ? '입력서' : '전달 파일'}에서 읽은 내용`, h('p', {class: 'muted', text: '틀린 값은 바로 고치세요. 고친 값으로 질문과 후보가 다시 정해집니다.'}), grid,
      I.item.sources.length ? h('details', {class: 'related'}, h('summary', {text: '입력서에 적힌 근거·출처 ' + I.item.sources.length + '개'}), ...I.item.sources.map(t => h('p', {class: 'cond', text: t}))) : null),
    step(2, qcards.length ? `확인 질문 ${qcards.length}개` : '확인 질문', qcards.length
      ? h('p', {class: 'muted', text: P1 ? '파트 1이 입력서에서 비었거나 확인이 필요한 칸을 묻습니다(한 번에 3개까지). 모르면 ‘모름’을 누르세요. 답은 법적 확정이 아니라 사용자 진술로 기록됩니다.' : '입력서에 비어 있는 칸만 묻습니다. 모르면 ‘모름’을 누르세요.'})
      : h('p', {class: 'muted', text: '지금 물을 질문이 없습니다.'}), ...qcards),
    step(3, '분류번호', codeBox),
    P1 ? step('+', '가진 문서 첨부 (선택)', attachBox) : null,
    step(4, '찾을 업체', h('p', {class: 'muted', text: '품목은 입력서 값에서 자동으로 골랐습니다. 다르면 고르세요.'}), wasteBox, h('div', {style: 'height:10px'}), wantBox),
    h('div', {class: 'istep-go'}, F.code || fixed ? null : h('small', {class: 'warn-note', style: 'margin-right:auto;max-width:460px', text: '분류번호가 없으면 법령 판단 없이 허가 품목·거리로 고른 1차 후보만 나옵니다. 법령 기준 추천을 받으려면 3번에서 분류번호를 고르세요.'}), go));
}

// ── 법령 판단(파트 2 엔진) ─────────────────────────────────────────────
// 폐기물 사실을 확인하면 서로 다른 회사의 AI 모델들이 법령 원문을 근거로 분류·지정 여부·처리 경로·필요한 서류를 판단한다.
function casePanel(owner, box) {
  const P = {data: null, timer: null, facts: {}};
  const FIELDS = [['name'], ['code'], ['process', 'area'], ['form'], ['components'], ['contamination', 'pick', ['깨끗', '오염', '모름']],
    ['contamination_detail'], ['hazard', 'pick', ['아니오', '예', '모름']], ['analysis', 'pick', ['있음·지정 아님', '있음·지정 해당', '없음']], ['mixture', 'pick', ['단일', '혼합', '모름']], ['separable', 'pick', ['가능', '불가능', '모름']],
    ['monthly_ton'], ['address'], ['facility_type', 'pick', ['배출시설계', '비배출시설계', '모름']]];
  const load = async () => {
    try { P.data = await api('case/' + owner); P.facts = {...P.data.facts}; draw(); } catch (e) { box.replaceChildren(h('p', {class: 'error', text: e.message})); }
  };
  const poll = () => { clearTimeout(P.timer); P.timer = setTimeout(async () => {
    if (!box.isConnected) return;
    try { const d = await api('case/' + owner); const was = P.data?.job?.status; P.data = d; if (d.job?.status !== 'running' || was !== 'running') draw(); else drawProgress(); } catch {}
    if (P.data?.job?.status === 'running') poll();
  }, 2500); };
  const progressBox = h('div', {class: 'progress'});
  const drawProgress = () => progressBox.replaceChildren(h('b', {text: '판단 중입니다 · 보통 30~90초'}),
    ...(P.data.progress || []).map(t => h('div', {text: t})));
  const factForm = () => {
    const grid = h('div', {class: 'facts'});
    for (const [key, kind, opts] of FIELDS) {
      const label = P.data.labels[key];
      let input;
      if (kind === 'pick') {
        input = h('div', {class: 'picks'}, opts.map(o => h('button', {type: 'button', 'aria-pressed': String((P.facts[key] || '') === o), text: o,
          onclick: () => { P.facts[key] = P.facts[key] === o ? '' : o; draw(); }})));
      } else {
        input = h(kind === 'area' ? 'textarea' : 'input', {class: 'input', rows: kind === 'area' ? 2 : null, maxLength: 400,
          placeholder: {code: '00-00-00', monthly_ton: '예: 5', components: '예: PE 98%, 종이 라벨 2%', form: '예: 마른 필름 조각'}[key] || ''});
        input.value = P.facts[key] || '';
        input.addEventListener('input', () => { P.facts[key] = input.value; });
      }
      const row = h('label', {class: 'field' + (['process', 'name', 'address', 'components'].includes(key) ? ' full' : '')}, h('span', null, label), input);
      if (key === 'code' && P.data.code_fixed) {
        input.readOnly = true;
        row.append(h('small', {class: 'faint', text: `분류 에이전트가 정한 번호입니다(${P.data.code_fixed.label || ''}). 여기서 바꾸지 않습니다.`}));
      } else if (key === 'code') {
        row.append(h('div', {class: 'picks', style: 'margin-top:6px'}, (P.data.codes || []).slice(0, 5).map(c =>
          h('button', {type: 'button', 'aria-pressed': String(P.facts.code === c.code), title: c.name + ' · ' + c.group,
            text: c.code + ' ' + c.name.replace(/\(.*$/, '').slice(0, 14) + (c.designated ? ' (지정)' : ''), onclick: () => { P.facts.code = c.code; draw(); }}))),
          h('small', {class: 'faint', text: '시행규칙 별표 4에서 이름이 비슷한 번호입니다. 확실하지 않으면 서류 탭의 ‘분류 확인 필요’로 기관에 확인하세요.'}));
      }
      grid.append(row);
    }
    return grid;
  };
  // 파트 1 Word 기초 입력서
  const docx = h('input', {type: 'file', accept: '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document', hidden: true});
  docx.onchange = async () => {
    const f = docx.files[0]; docx.value = ''; if (!f) return;
    try {
      const data = await new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1]); r.onerror = () => no(Error('파일을 읽지 못했습니다.')); r.readAsDataURL(f); });
      P.intake = await api('case/' + owner + '/intake', {file: {name: f.name, data}});
      if (P.intake.items.length === 1) useItem(P.intake.items[0]); else draw();
    } catch (e) { toast(e.message); }
  };
  const useItem = it => { Object.assign(P.facts, it.facts); P.sources = it.sources; P.intake = null; draw(); toast('입력서의 ‘' + it.facts.name + '’ 내용을 채웠습니다. 확인 후 판단을 실행하세요.'); };
  function factSummary(d, running) {
    const order = ['name', 'code', 'process', 'form', 'components', 'mixture', 'separable', 'contamination', 'contamination_detail', 'hazard', 'analysis', 'monthly_ton', 'address', 'facility_type'];
    const rows = order.filter(k => (P.facts[k] || '').trim()).map(k => h('div', {class: 'fs-row'}, h('span', {class: 'fs-label', text: d.labels[k] || k}), h('span', {class: 'fs-val', text: P.facts[k]})));
    return h('div', {class: 'fact-summary'},
      h('div', {class: 'desk-doc-top'}, h('b', {text: '판단에 쓴 사실'}), chip(d.source === 'intake' ? '기초 입력서에서 가져옴' : d.prefilled ? '가입 정보·거래 조건에서 채움' : '저장된 사실', 'line'),
        h('span', {style: 'flex:1'}),
        h('button', {class: 'btn small ghost', type: 'button', disabled: running, text: '사실 고치기', onclick: () => { P.editing = true; draw(); }})),
      h('details', {class: 'fs-more'}, h('summary', {text: `${P.facts.name || '폐기물'} · ${P.facts.code || '분류번호 없음'} · 사실 ${rows.length}개 보기`}), h('div', {class: 'fs-grid'}, ...rows)),
      !d.result && !running ? h('div', {style: 'display:flex;gap:8px;margin-top:8px'}, h('button', {class: 'btn primary small', type: 'button', text: '판단 실행', onclick: run})) : null);
  }
  async function run() {
    P.editing = false;
    const facts = Object.fromEntries(Object.entries(P.facts).filter(([, v]) => v && v !== '모름'));
    try { P.answers = {}; P.data = await api('case/' + owner + '/run', {facts}); draw(); poll(); }
    catch (e) { toast(e.message); }
  }
  function draw() {
    const d = P.data, job = d.job || {}, running = job.status === 'running';
    const kids = [
      h('div', {class: 'case-head'}, h('h3', {text: '법령 판단'}),
        d.result && !running ? null : h('p', {text: '폐기물 사실을 확인하고 실행하면, 서로 다른 회사의 AI 모델 2~3개가 폐기물관리법·시행령·시행규칙 원문을 인용해 분류번호·지정 여부·처리 경로·필요한 서류를 판단하고, 공공자료 업체를 거리·허가 범위·운송 탄소로 순위를 매깁니다.'}),
        !d.ready && h('p', {class: 'warn-note', text: 'AI 키가 없어 엔진의 시험용 답으로 형식만 보여 줍니다.'})),
      // 사실이 이미 있으면(입력서·이전 판단) 짧은 요약만 보여 주고, 고칠 때만 입력 칸을 펼친다
      P.facts.code && !P.editing ? factSummary(d, running) :
      h('details', {class: 'case-facts', open: true}, h('summary', {text: '판단할 사실' + (d.prefilled ? ' · 가입 정보와 거래 조건으로 미리 채움' : '')}),
        h('div', {class: 'intake-bar'}, h('small', {class: 'faint', text: 'Word 기초 입력서로 한 번에 채울 수 있습니다.'}),
          h('button', {class: 'btn small ghost', type: 'button', text: '입력서 올리기', onclick: () => docx.click()}),
          h('a', {class: 'btn small ghost', href: '/api/p/intake-template', text: '빈 입력서 받기'}), docx),
        P.intake ? h('div', {class: 'ask-card'}, h('small', {class: 'faint', text: '입력서에 폐기물이 여러 개 있습니다. 판단할 것을 고르세요.'}),
          ...P.intake.items.map(it => h('button', {class: 'btn small', type: 'button', style: 'align-self:flex-start', text: it.index + '. ' + it.facts.name, onclick: () => useItem(it)}))) : null,
        factForm(),
        P.sources?.length ? h('details', {class: 'related'}, h('summary', {text: '입력서에 적힌 근거 ' + P.sources.length + '개'}), ...P.sources.map(t => h('p', {class: 'cond', text: t}))) : null,
        h('div', {style: 'display:flex;gap:8px;align-items:center;margin-top:10px'},
          h('button', {class: 'btn primary small', type: 'button', disabled: running, text: running ? '판단 중…' : d.result ? '다시 판단' : '판단 실행', onclick: run}),
          h('button', {class: 'btn ghost small', type: 'button', disabled: running, text: '저장만', onclick: guard(async () => { await api('case/' + owner + '/facts', {facts: P.facts}); toast('저장했습니다.'); })})))];
    if (running) { drawProgress(); kids.push(progressBox); }
    if (job.status === 'failed') kids.push(h('p', {class: 'error', text: job.error || '판단을 끝내지 못했습니다. 사실을 확인하고 다시 실행해 주세요.'}), ...(d.progress || []).map(t => h('small', {class: 'faint', text: t})));
    if (d.result && !running) kids.push(...(d.plain ? plainNodes(d.plain, d.result) : resultNodes(d.result)));
    box.replaceChildren(h('div', {class: 'case'}, ...kids));
  }
  // 쉬운 결과: 결론 → 지금 할 일 → 맡길 곳 → 준비할 서류 → 근거 보기
  function plainNodes(v, r) {
    const out = [], bad = v.mismatch;
    P.answers = P.answers || {};
    out.push(h('div', {class: 'verdict big ' + v.verdict.tone}, h('b', {text: {ok: '🟢 ', warn: '🟡 ', bad: '🔴 '}[v.verdict.tone] + v.verdict.title}),
      h('p', {text: v.verdict.why}), v.basis_line && !bad ? h('small', {text: v.basis_line}) : null,
      !bad && v.cards?.conclusion?.quotes?.length ? quoteList('결론 근거 원문', v.cards.conclusion.quotes) : null));
    const cc = (v.cards?.checks || []).filter((c, i, a) => a.findIndex(x => x.title === c.title) === i);
    if (!bad && cc.length) out.push(h('details', {class: 'block'}, h('summary', null, h('b', {text: `확인할 것 ${cc.length}가지`}), h('small', {class: 'faint', text: ' · 법령만으로 정하지 못해 자료나 담당자 확인이 필요한 것'})),
      ...cc.map(c => h('div', {class: 'check-row'}, chip(c.kind || '확인', /자료/.test(c.kind) ? 'accent' : 'warn'), h('span', {text: c.text})))));
    if (bad) {
      out.push(h('div', {class: 'block'}, h('h4', {text: '분류번호 다시 고르기'}),
        h('p', {class: 'muted', text: '이름과 설명이 맞는 번호 후보입니다. 맞는 것을 고른 뒤 다시 판단하세요. 확실하지 않으면 거래방 서류의 ‘분류 확인’ 절차로 관할 기관에 확인합니다.'}),
        h('div', {class: 'picks'}, bad.candidates.map(c => h('button', {type: 'button', 'aria-pressed': String(P.facts.code === c.code),
          text: c.code + ' ' + c.name.replace(/\(.*$/, '').slice(0, 18) + (c.designated ? ' (지정)' : ''), title: c.name, onclick: () => { P.facts.code = c.code; draw(); }}))),
        bad.reason ? h('details', {class: 'related'}, h('summary', {text: '맞지 않는다고 본 이유'}), h('p', {class: 'cond', text: bad.reason})) : null,
        h('button', {class: 'btn primary small', type: 'button', style: 'align-self:flex-start;margin-top:8px', disabled: P.facts.code === bad.code,
          text: P.facts.code === bad.code ? '번호를 먼저 고르세요' : P.facts.code + '로 다시 판단', onclick: run})));
    } else if (v.todo.length) {
      const shown = P.moreTodo ? v.todo : v.todo.slice(0, 3);
      out.push(h('div', {class: 'block'}, h('h4', {text: `지금 할 일 · 알려 주실 것 ${v.todo.length}가지`}),
        h('p', {class: 'muted', text: '판단을 확정하는 데 모자란 사실입니다. 아는 것만 답하고 ‘답하고 다시 판단’을 누르세요(30~90초). 모르면 ‘모름’으로 두면 됩니다.'}),
        ...shown.map(t => todoCard(t)),
        v.todo.length > 3 ? h('button', {class: 'btn small ghost', type: 'button', style: 'align-self:flex-start', text: P.moreTodo ? '접기' : `${v.todo.length - 3}가지 더 보기`, onclick: () => { P.moreTodo = !P.moreTodo; draw(); }}) : null,
        h('button', {class: 'btn primary small', type: 'button', style: 'align-self:flex-start;margin-top:6px', text: Object.keys(P.answers).length ? `답 ${Object.keys(P.answers).length}개로 다시 판단` : '답하고 다시 판단',
          disabled: !Object.keys(P.answers).length, onclick: run})));
    }
    const rt = v.routes[0];
    if (rt && !bad) {
      const members = v.members || [], verified = v.verified || [];
      const pts = [...verified.map(m => ({...m, id: 'v:' + m.id})), ...rt.partners.map((p, i) => ({...p, id: 'p:' + i, public: !p.member})),
        ...members.map(m => ({...m, id: 'm:' + m.id}))];
      const reqBtn = m => m.request === 'accepted' ? h('a', {class: 'btn small ghost', href: '#/r/' + m.room_id, text: '거래방 열기', onclick: e => e.stopPropagation()}) : m.request === 'pending' ? chip('답변 대기', 'warn')
        : h('button', {class: 'btn small', type: 'button', text: '거래 요청', onclick: e => { e.stopPropagation(); requestDialog(m, {search_id: owner, need: S.search?.need}); }});
      const mapBox = h('div', {class: 'map', style: 'height:240px;margin:6px 0 10px'});
      const rows = h('div');
      const focus = id => { const scope = mapBox.parentElement || rows;
        scope.querySelectorAll('[data-id]').forEach(n => n.classList.toggle('on', n.dataset.id === id));
        scope.querySelector(`[data-id="${id}"]`)?.scrollIntoView({block: 'nearest', behavior: 'smooth'}); P.mapApi?.focus(id); };
      if (pts.some(x => x.lat)) queueMicrotask(() => { P.mapApi = makeMap(mapBox, v.origin ? {lat: v.origin.lat, lng: v.origin.lng} : null, pts, focus); });
      if (verified.length) rows.append(h('div', {class: 'sub-head', text: '앱 가입 · 법령 기준 통과 · 바로 거래 요청 가능'}),
        ...verified.map((m, i) => h('div', {class: 'rank-row', 'data-id': 'v:' + m.id, onclick: () => focus('v:' + m.id)}, h('span', {class: 'no', text: i + 1}),
          h('div', null, h('b', {text: m.name}), chip('공공자료 허가 확인', 'ok'), h('small', {class: 'faint', text: ' ' + [m.region, km(m.distance_km)].filter(Boolean).join(' · ')}),
            h('p', {text: '허가: ' + m.basis})), reqBtn(m))));
      rows.append(h('div', {class: 'sub-head', text: `법령 기준에 맞는 공공자료 업체 ${rt.partner_total ?? rt.partners.length}곳 중 가까운 순 · 앱에 가입해 연결된 곳은 거래 요청 가능`}));
      for (const st of v.steps || []) if (st.rejected.length) rows.append(h('details', {class: 'funnel'},
        h('summary', {text: `${st.role_ko} 업체 거르기 과정 보기 (${st.total.toLocaleString()}곳 → ${st.found.toLocaleString()}곳)`}),
        h('div', {class: 'f-row'}, h('b', {text: st.total.toLocaleString() + '곳'}), h('span', {text: '같은 폐기물 분류를 다루는 업체 (공공자료)'})),
        ...st.rejected.map(x => h('div', {class: 'f-row'}, h('b', {class: 'minus', text: '−' + x.n.toLocaleString() + '곳'}), h('span', null, x.why, h('small', {class: 'faint', text: ' 근거: ' + x.law})))),
        h('div', {class: 'f-row keep'}, h('b', {text: st.found.toLocaleString() + '곳'}), h('span', {text: '법적으로 맡길 수 있는 업체 → 가까운 순 (순위 기준은 거리이며 법 기준이 아님)'}))));
      out.push(h('div', {class: 'block'}, h('h4', {text: '맡길 곳'}),
        h('p', {class: 'muted', text: rt.title + (v.origin ? ` · 거리 기준: 입력서의 배출 현장(${v.origin.label})` : '')}),
        pts.some(x => x.lat) ? mapBox : null, rows,
        ...rt.partners.map((p, i) => h('div', {class: 'rank-row', 'data-id': 'p:' + i, onclick: () => focus('p:' + i)}, h('span', {class: 'no', text: verified.length + i + 1}),
          h('div', null, h('b', {text: p.name}), p.member ? chip('앱 가입', 'ok') : null, p.cautions?.length ? chip('확인 필요 ' + p.cautions.length, 'warn') : null,
            h('small', {class: 'faint', text: ' ' + [p.region, p.road_km != null ? `${p.road_km} km (도로)` : p.km != null && `약 ${p.km} km (${p.distance_basis === '시군구 중심점' ? '직선·시군구 기준' : '직선'})`].filter(Boolean).join(' · ')}),
            h('p', {text: [p.license, p.does.length && '재활용 유형 ' + p.does.join(', ')].filter(Boolean).join(' · ')}),
            p.why.length ? h('p', {class: 'faint', text: '맞는 이유: ' + p.why.join(', ')}) : null,
            p.carbon != null ? h('small', {class: 'faint', text: `운송 탄소 월 약 ${p.carbon} kgCO₂e`}) : null,
            p.evidence?.length || p.cautions?.length ? partnerChecks(p) : null),
          p.member ? reqBtn(p.member) : p.phone ? h('a', {class: 'btn small ghost', href: 'tel:' + p.phone.replace(/[^\d+]/g, ''), text: '전화'}) : null)),
        members.length ? h('div', {class: 'sub-head', text: '허가 미확인 가입 업체 · 공공자료와 연결되지 않음'}) : null,
        ...members.map((m, i) => h('div', {class: 'rank-row muted-row', 'data-id': 'm:' + m.id, onclick: () => focus('m:' + m.id)}, h('span', {class: 'no', text: verified.length + rt.partners.length + i + 1}),
          h('div', null, h('b', {text: m.name}), m.demo ? chip('예시', 'warn') : null, h('small', {class: 'faint', text: ' ' + [ROLE[m.match_role] || '', km(m.distance_km)].filter(Boolean).join(' · ')}),
            m.why ? h('p', {text: m.why}) : null, h('p', {class: 'faint', text: m.note})), reqBtn(m))),
        ...rt.next.map(n => h('small', {class: 'faint', text: n})),
        h('small', {class: 'faint', text: '실제 반입 가능 여부와 허가 범위는 업체에 직접 확인하세요.'})));
    } else if (rt && bad && rt.hidden) out.push(h('p', {class: 'faint', text: `잘못된 분류번호 기준으로 찾은 업체 ${rt.hidden}곳과 서류 목록은 숨겼습니다.`}));
    const prepOf = Object.fromEntries((P.data?.prep || []).map(x => [x.key, x]));
    const prepBtn = key => { const x = prepOf[key]; if (!x) return null;
      return h('button', {class: 'btn small' + (x.started ? '' : ' primary'), type: 'button', text: x.started ? `이어서 작성 ${x.filled}/${x.total}칸` : '지금 작성',
        onclick: () => { location.hash = '#/p/' + owner + '/d/' + key; }}); };
    if (v.docs.length && !bad) out.push(h('div', {class: 'block'}, h('h4', {text: '준비할 서류'}),
      h('p', {class: 'muted', text: '업체가 정해지기 전에도 우리 쪽 내용을 미리 써 둘 수 있어요. 함께 쓰는 서류는 상대 업체 칸을 비워 두고, 거래방이 열리면 그대로 이어집니다.'}),
      ...v.docs.map(x => h('div', {class: 'doc-need'}, h('div', {class: 'desk-doc-top'}, h('b', {text: x.title}), chip(x.status, x.sure ? 'ok' : 'warn'),
          x.who ? chip(x.who, x.who === '함께 작성' || x.who === '각자 입력' ? 'accent' : 'line') : null),
        x.who_note ? h('small', {text: x.who_note}) : null,
        x.why ? h('small', {class: 'faint', text: x.why}) : null,
        h('div', {style: 'display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px'}, x.basis ? chip(x.basis, 'line') : null, h('span', {style: 'flex:1'}), x.prep_key ? prepBtn(x.prep_key) : null))),
      prepOf.rfq ? h('div', {class: 'doc-need'}, h('div', {class: 'desk-doc-top'}, h('b', {text: '반입·운반 견적 문의서'}), chip('우리 업체가 작성', 'line')),
        h('small', {text: '폐기물 정보·물량·반출 조건을 한 번 써 두면 후보 업체 여러 곳에 같은 내용으로 견적을 물을 수 있어요.'}),
        h('div', {style: 'display:flex;justify-content:flex-end;margin-top:4px'}, prepBtn('rfq'))) : null,
      h('small', {class: 'faint', text: '‘함께 작성’ 서류는 업체에 거래를 요청해 거래방이 열리면 같이 씁니다. 거래방 서류 탭의 ‘서류 비서’가 누가 무엇을 채울지 이어서 챙깁니다.'})));
    const C = v.cards;
    if (C && !bad && C.standards.length) out.push(h('details', {class: 'block'}, h('summary', null, h('b', {text: `맡길 업체가 지켜야 할 기준 ${C.standards.length}가지`}), h('small', {class: 'faint', text: ' · 업체를 고를 때 이 기준을 지킬 수 있는지 확인'})),
      ...C.standards.map(x => h('div', {class: 'std'}, h('div', {class: 'desk-doc-top'}, h('b', {text: x.title}), x.who ? chip(x.who, 'line') : null, x.when ? chip(x.when, 'line') : null),
        ...x.lines.map(ln => h('div', {class: 'law'}, h('p', null, h('span', {class: 'law-tag', text: ln.where + ' '}), ln.text), C.reviewed ? null : h('span', {class: 'draft', text: 'AI 요약 · 검수 전'})))))));
    if (C && C.details.length) {
      const tone = l => /확인됨|해당 없음|아님/.test(l) ? 'ok' : /기준 찾음/.test(l) ? 'accent' : /조건/.test(l) ? 'warn' : 'bad';
      out.push(h('details', {class: 'block'}, h('summary', null, h('b', {text: `판단 근거 ${C.details.length}건`}), h('small', {class: 'faint', text: ' · 항목을 누르면 답과 법령 원문'})),
        ...C.details.map(d => h('details', {class: 'jbasis'}, h('summary', null, h('span', {class: 'qtext', text: d.title}), chip(d.label, tone(d.label))),
          d.summary ? h('p', {text: d.summary}) : null, d.detail && d.detail !== d.summary ? h('p', {class: 'faint', text: d.detail}) : null,
          d.conditions.length ? h('p', {class: 'cond', text: '조건: ' + d.conditions.join(' / ')}) : null,
          d.quotes.length ? quoteList('확인한 기준', d.quotes, true) : null))));
    }
    out.push(h('details', {class: 'block evidence'}, h('summary', {text: '근거 보기 · ' + v.trust}),
      ...(rt ? [rt.laws.length ? h('div', {class: 'law-chips'}, rt.laws.slice(0, 10).map(l => chip(l, 'line'))) : null,
        rt.conditions.length ? h('div', null, h('small', {class: 'faint', text: '지켜야 할 조건'}), ...rt.conditions.map(c => h('p', {class: 'cond', text: c}))) : null] : []),
      ...resultChecks(r)));
    out.push(h('p', {class: 'faint', style: 'font-size:12px', text: '법령 원문과 공공자료에 기댄 사전 검토입니다. 업체의 실제 허가 범위·반입 가능 여부와 신고 대상 여부는 해당 업체와 관할 기관에 확인하세요.'}));
    return out;
  }
  function quoteList(title, qs, open) {
    return h('details', {class: 'quotes', open: open || null}, h('summary', {text: title + ' ' + qs.length + '개'}),
      ...qs.map(q => h('div', {class: 'law'}, h('p', null, h('span', {class: 'law-tag', text: q.where + ' '}), q.quote))));
  }
  function partnerChecks(p) {
    const row = (mark, cls, text, law) => h('div', {class: 'ck'}, h('span', {class: 'mk ' + cls, text: mark}), h('span', {class: 'ck-t', text}), law ? h('small', {class: 'faint', text: law}) : null);
    const d = h('details', {class: 'pchecks'}, h('summary', {text: `확인표 · 자료로 확인 ${p.evidence.length} · 확인 필요 ${p.cautions.length} · 받을 서류 ${p.ask.length}`}),
      ...p.evidence.map(e => row('✓', 'y', `${e.item}: ${e.value}`, e.law)),
      ...p.cautions.map(c => row('!', 'w', c, '업체 회신으로 확인')),
      ...p.ask.map(a => row('?', 'q', a.item, a.law)));
    d.addEventListener('click', e => e.stopPropagation());
    return d;
  }
  function todoCard(t) {
    const set = val => { if (val === null) delete P.answers[t.key]; else P.answers[t.key] = val; if (val !== null) P.facts[t.key] = val; draw(); };
    let input;
    if (t.kind === 'pick') input = h('div', {class: 'picks'}, [...t.options, '모름'].map(o => h('button', {type: 'button', 'aria-pressed': String((P.answers[t.key] ?? '') === o), text: o, onclick: () => set(P.answers[t.key] === o ? null : o)})));
    else {
      const el = h(t.kind === 'area' ? 'textarea' : 'input', {class: 'input', rows: t.kind === 'area' ? 2 : null, maxLength: 400, placeholder: t.current ? '지금: ' + t.current : ''});
      el.value = P.answers[t.key] ?? (t.kind === 'area' ? (t.current || '') : '');
      el.addEventListener('change', () => { const v = el.value.trim(); if (v && v !== t.current) { P.answers[t.key] = v; P.facts[t.key] = v; } else delete P.answers[t.key]; draw(); });
      input = h('div', {class: 'qrow'}, el, h('button', {class: 'btn small ghost', type: 'button', text: '모름', onclick: () => set('모름')}));
    }
    return h('div', {class: 'ask-card' + (P.answers[t.key] ? ' answered' : '')},
      h('div', {class: 'desk-doc-top'}, h('b', {text: t.label}), P.answers[t.key] ? chip('답함', 'ok') : null),
      h('p', {text: t.ask}), input,
      t.why.length ? h('small', {class: 'faint', text: '이 답으로 정해지는 것: ' + t.why.join(', ')}) : null,
      t.guide ? h('details', {class: 'law'}, h('summary', null, h('span', {class: 'kind practice', text: t.guide.kind_label}), h('span', {class: 'law-tag', text: t.guide.label})), h('p', {text: t.guide.text})) : null);
  }
  function resultChecks(r) {
    return [h('details', {class: 'checks'}, h('summary', {text: `질문별 판단 ${r.checks.length}개 · 모델 의견과 원문 인용`}),
      ...r.checks.map(c => h('details', {class: 'jcheck'},
        h('summary', null, h('span', {class: 'qtext', text: c.question}), chip(c.answer, c.answer === '예' ? 'ok' : c.answer === '아니오' ? '' : 'warn'), h('small', {class: 'faint', text: c.status === 'needs_info' ? '정보 부족' : c.status})),
        c.conditions.length ? h('p', {class: 'cond', text: '조건: ' + c.conditions.map(x => typeof x === 'string' ? x : (x.text || JSON.stringify(x))).join(' / ')}) : null,
        ...c.votes.map(v => h('p', {class: 'vote'}, h('b', {text: v.model.split('/').pop() + ' · ' + v.answer + ' '}), v.reason)),
        ...c.quotes.map(q => h('div', {class: 'law', style: 'margin-top:6px'}, h('p', null, h('span', {class: 'law-tag', text: q.label + ' '}), q.text))))))];
  }
  const legalTone = t => /가능|적법|확보/.test(t || '') && !/조건|확인/.test(t || '') ? 'ok' : /불가|금지/.test(t || '') ? 'bad' : 'warn';
  function resultNodes(r) {
    const out = [];
    const route = r.routes[0];
    const bad = r.code_mismatch;
    if (bad) {  // 분류번호가 폐기물과 맞지 않으면 아래 경로·업체는 그 번호 기준이라 쓸 수 없다
      out.push(h('div', {class: 'verdict bad'},
        h('b', {text: `분류번호 ${bad.code}(${bad.code_name})는 ‘${bad.name}’과 맞지 않습니다`}),
        h('small', {text: '여러 AI 모델이 법령 원문(시행규칙 별표 4)과 대조해 맞지 않는다고 판단했습니다. 아래 경로·업체·서류는 잘못된 번호 기준이라 쓰지 마세요.' + (bad.reason ? ' 이유: ' + bad.reason.slice(0, 160) : '')}),
        bad.candidates.length ? h('div', {class: 'picks', style: 'margin-top:8px'}, h('small', {class: 'faint', text: '이름이 맞는 번호 후보: '}),
          ...bad.candidates.map(c => h('button', {type: 'button', text: c.code + ' ' + c.name.replace(/\(.*$/, '').slice(0, 16) + (c.designated ? ' (지정)' : ''),
            onclick: () => { P.facts.code = c.code; draw(); box.querySelector('details.case-facts')?.setAttribute('open', ''); toast(c.code + '로 바꿨습니다. 맞는지 확인하고 ‘다시 판단’을 누르세요.'); }}))) : null));
    }
    out.push(h('div', {class: 'verdict ' + (route ? legalTone(route.legal) : 'bad')},
      h('b', {text: route ? `${route.kind || route.title} · ${route.legal}` : '가능한 처리 경로를 찾지 못했습니다'}),
      h('small', {text: `분류번호 ${r.code || '-'} · ${r.status === 'designated' ? '지정폐기물' : '일반폐기물'} 기준 · 모델 호출 ${r.calls ?? '-'}회`})));
    for (const rt of r.routes) {
      out.push(h('div', {class: 'route'},
        h('div', {class: 'route-top'}, h('b', {text: rt.title}), chip(rt.legal, legalTone(rt.legal)), rt.secured && chip('업체 ' + rt.secured)),
        rt.laws.length ? h('div', {class: 'law-chips'}, rt.laws.slice(0, 8).map(l => chip(l, 'line'))) : null,
        rt.conditions.length ? h('details', {class: 'related'}, h('summary', {text: '지켜야 할 조건 ' + rt.conditions.length + '개'}), ...rt.conditions.map(c => h('p', {class: 'cond', text: c}))) : null,
        rt.emitter_checks.length ? h('div', {class: 'ask-card'}, h('small', {class: 'faint', text: '배출업체가 확인할 것'}), ...rt.emitter_checks.map(t => h('p', {text: t}))) : null,
        rt.partners.length && bad ? h('p', {class: 'faint', text: `업체 ${rt.partners.length}곳은 잘못된 분류번호 기준이라 숨겼습니다.`}) : null,
        rt.partners.length && !bad ? h('div', {class: 'ranked'}, h('small', {class: 'faint', text: `추천 업체 (조건에 맞는 ${rt.partner_total ?? rt.partners.length}곳 중 상위 · 공공자료, 앱 미가입)`}),
          ...rt.partners.map(p => h('div', {class: 'rank-row'},
            h('span', {class: 'no', text: p.rank}),
            h('div', null, h('b', {text: p.name}), h('small', {class: 'faint', text: ' ' + [p.role, p.region].filter(Boolean).join(' · ')}),
              h('p', {text: [p.km != null && '약 ' + p.km + ' km', p.carbon != null && '운송 탄소 월 ' + p.carbon + ' kgCO₂e', p.license, p.rcodes.length && '재활용 유형 ' + p.rcodes.join(', ')].filter(Boolean).join(' · ')}),
              p.why.length ? h('p', {class: 'faint', text: p.why.join(' · ')}) : null),
            p.phone ? h('a', {class: 'btn small ghost', href: 'tel:' + p.phone.replace(/[^\d+]/g, ''), text: '전화'}) : null))) : null,
        ...rt.next_roles.map(n => h('p', {class: 'faint', style: 'font-size:12.5px', text: `${n.역할}: 후보 ${n.후보_수}곳 · ${n.메모 || ''}`}))));
    }
    if (r.docs.length) out.push(h('div', {class: 'block'}, h('h4', {text: '이 경로에 필요한 서류'}),
      ...r.docs.map(x => h('div', {class: 'doc-need'}, h('b', {text: x.title}), h('small', {class: 'faint', text: ' ' + (x.why || '')}), h('div', null, chip(x.basis, 'line'))))));
    if (r.questions.length) out.push(h('div', {class: 'block'}, h('h4', {text: '판단을 마치려면 확인할 것'}),
      ...r.questions.map(q => h('div', {class: 'ask-card'}, h('p', {text: q.text}),
        q.guide ? h('details', {class: 'law'}, h('summary', null, h('span', {class: 'kind practice', text: q.guide.kind_label}), h('span', {class: 'law-tag', text: q.guide.label})), h('p', {text: q.guide.text})) : null))));
    out.push(h('details', {class: 'block checks'}, h('summary', {text: `질문별 판단 ${r.checks.length}개 · 모델 의견과 원문 인용`}),
      ...r.checks.map(c => h('details', {class: 'jcheck'},
        h('summary', null, h('span', {class: 'qid', text: c.id}), h('span', {class: 'qtext', text: c.question}), chip(c.answer, c.answer === '예' ? 'ok' : c.answer === '아니오' ? '' : 'warn'), h('small', {class: 'faint', text: c.status})),
        c.conditions.length ? h('p', {class: 'cond', text: '조건: ' + c.conditions.map(x => typeof x === 'string' ? x : (x.text || JSON.stringify(x))).join(' / ')}) : null,
        ...c.votes.map(v => h('p', {class: 'vote'}, h('b', {text: v.model.split('/').pop() + ' · ' + v.answer + ' '}), v.reason)),
        ...c.quotes.map(q => h('div', {class: 'law', style: 'margin-top:6px'}, h('p', null, h('span', {class: 'law-tag', text: q.label + ' '}), q.text)))))));
    out.push(h('p', {class: 'faint', style: 'font-size:12px', text: '판단은 법령 원문과 공공자료에 기댄 사전 검토입니다. 업체의 실제 허가 범위·반입 가능 여부와 신고 대상 여부는 해당 업체와 관할 기관에 확인하세요.'}));
    return out;
  }
  box.replaceChildren(h('p', {class: 'faint', text: '불러오는 중…'}));
  load().then(() => { if (P.data?.job?.status === 'running') poll(); });
}

// ── 서류 작성 ─────────────────────────────────────────────────────────
// 거래방의 서류를 같은 화면 틀에서 작성한다. 가운데는 작성 도우미와의 대화, 오른쪽은 작성본 미리보기와 입력 항목.
async function loadForms() {
  if (S.forms) return S.forms;
  const r = await fetch('/api/config', {cache: 'no-store'});
  if (!r.ok) throw Error('서식 목록을 불러오지 못했습니다.');
  S.forms = Object.fromEntries((await r.json()).documents.map(d => [d.id, d]));
  return S.forms;
}
function canWriteHere(docId) { return !!S.forms?.[docId]?.fields?.length; }
function openDoc(item) {
  if (canWriteHere(item.doc)) { location.hash = '#/r/' + S.room.room.id + '/d/' + item.key; return; }
  // 원본 서식의 칸을 직접 고르는 서류는 기존 작성 화면을 쓴다.
  const q = new URLSearchParams({room: S.room.room.id, item: item.key, doc: item.doc});
  window.open('/workspace?' + q, '_blank', 'noopener');
}
async function postRaw(url, body) {
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (!r.ok) { const data = await r.json().catch(() => ({})); throw Error(data.error || '요청을 처리하지 못했습니다. (' + r.status + ')'); }
  return r;
}
function leaveDoc() {
  const D = S.doc; if (!D) return;
  clearInterval(D.beat); clearTimeout(D.saveTimer); clearTimeout(D.pdfTimer);
  D.closed = true;
  const url = '/api/p/' + D.base;
  navigator.sendBeacon?.(url, new Blob([JSON.stringify({action: 'stop'})], {type: 'application/json'}));
  S.doc = null;
}
const D_locked_count = info => (info.locked || []).length;
async function renderDoc(roomId, itemKey, opts = {}) {
  // opts.prep: 매칭 전 준비 공간(거래 찾기 기록 roomId=s_…). 우리 쪽 칸만 쓰고, 공유·완료 표시·상대 요청은 거래방이 열린 뒤에 한다
  const prep = !!opts.prep, base = (prep ? 'case/' : 'rooms/') + roomId + '/docs/' + itemKey;
  await loadForms();
  const info = await api(base + '/prefill');
  const form = S.forms[info.doc];
  if (!form?.fields?.length) throw Error('이 서류는 이 화면에서 작성할 수 없습니다.');
  const keyOf = 'doc:' + roomId + ':' + itemKey;
  let saved = []; try { saved = JSON.parse(localStorage.getItem(keyOf) || '[]'); } catch {}
  const D = S.doc = {room: roomId, item: itemKey, info, form, values: {...info.values}, sources: {...info.sources}, turns: saved,
    focus: null, page: 0, pages: 0, regions: [], pdf: '', tab: 'preview', others: info.others || [], pdfNo: 0,
    review: info.review || null, basis: info.basis || {}, meta: info.meta || {}, proposals: info.proposals || {}, touched: {},
    base, prep, locked: new Set(info.locked || [])};
  if (S.docTab) { D.tab = S.docTab; S.docTab = null; }
  const labelOf = Object.fromEntries(form.fields.map(f => [f.key, f.label]));
  const mineFields = () => form.fields.filter(f => !D.locked.has(f.key));
  const filled = () => mineFields().filter(f => (D.values[f.key] || '').trim()).length;
  const nextEmpty = () => form.fields.find(f => !(D.values[f.key] || '').trim() && !(S.doc?.locked || new Set()).has(f.key));
  const remember = () => { try { localStorage.setItem(keyOf, JSON.stringify(D.turns.slice(-80))); } catch {} };

  // 화면 틀
  const status = h('div', {style: 'display:flex;gap:6px;align-items:center'});
  const mainAction = h('button', {class: 'btn small primary', text: prep ? 'PDF 저장' : info.shared ? '채팅방에 공유' : '준비 완료로 표시'});
  setTop(info.title,
    status,
    prep ? h('button', {class: 'btn small ghost', text: '법령 판단으로', onclick: () => { S.searchTab = 'judge'; location.hash = '#/s/' + roomId; }})
      : h('button', {class: 'btn small ghost', text: '거래방으로', onclick: () => { location.hash = '#/r/' + roomId; }}),
    mainAction);
  document.getElementById('top').append(h('span', {class: 'faint', style: 'font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis',
    text: info.room_title + ' · ' + (prep ? '매칭 전 준비 · ' + (D_locked_count(info) ? '우리 쪽 칸만 작성' : '우리 업체가 쓰는 서류') : info.shared ? '함께 작성하는 서류' : '우리 업체가 준비하는 서류')}));
  const thread = h('div', {class: 'thread'}), inner = h('div', {class: 'thread-inner'}); thread.append(inner);
  const pin = h('div', {class: 'pinbar', hidden: true});
  const quick = h('div', {class: 'quick', style: 'max-width:760px;margin:0 auto 8px'});
  const box = composer({placeholder: '알고 있는 내용을 편하게 적어 주세요', note: '', tray: pin, onsend: text => talk(text)});
  const tabs = h('div', {class: 'side-tabs', role: 'tablist'});
  const pane = h('div', {class: 'side-scrolling', style: 'padding-top:14px'});
  document.getElementById('body').replaceChildren(
    h('div', {class: 'center'}, thread, h('div', {class: 'composer-wrap'}, quick, box.el)),
    h('aside', {class: 'side doc-side'}, tabs, pane));

  const showStatus = () => {
    status.replaceChildren(...D.others.map(n => h('span', {class: 'chip warn live'}, h('span', {class: 'dot'}), n + ' 작성 중')),
      chip(filled() + ' / ' + mineFields().length + ' 입력' + (D.locked.size ? ` · 상대 칸 ${D.locked.size}` : ''), filled() === mineFields().length ? 'ok' : ''));
  };
  const drawTurns = () => {
    const turns = D.turns.length ? D.turns : [{role: 'assistant', text: intro()}];
    inner.replaceChildren(...turns.map(t => h('div', {class: 'turn ' + t.role}, t.text,
      t.labels?.length ? h('div', {class: 'needs', style: 'margin-top:8px'}, t.labels.filter(k => labelOf[k]).map(k =>
        h('button', {class: 'chip line', type: 'button', text: labelOf[k] + ' ›', onclick: () => choose(k, true)}))) : null,
      t.cites?.length ? h('div', {class: 'cites'}, t.cites.map((c, i) => lawCard(c, '[' + (i + 1) + '] '))) : null,
      t.related?.length ? h('details', {class: 'related'}, h('summary', {text: '함께 볼 조문 ' + t.related.length + '개'}),
        ...t.related.map(c => lawCard(c, ''))) : null,
      t.ask ? askCard(t) : null,
      t.suggest?.length ? suggestCard(t) : null)));
    thread.scrollTop = thread.scrollHeight;
  };
  const lawCard = (c, prefix) => h('details', {class: 'law'},
    h('summary', null, c.kind_label ? h('span', {class: 'kind ' + (c.kind || ''), text: c.kind_label}) : null,
      h('span', {class: 'law-tag', text: prefix + c.label}), c.title ? h('small', {class: 'faint', text: ' ' + c.title}) : null),
    h('p', {text: c.text}), c.effective ? h('small', {class: 'faint', text: '시행 ' + c.effective.replace(/(\d{4})(\d{2})(\d{2})/, '$1.$2.$3') + ' · 국가법령정보 원문'})
      : c.kind === 'practice' ? h('small', {class: 'faint', text: '법적 구속력이 없는 실무 안내입니다.' + (c.url ? ' ' + c.url : '')}) : null);
  const suggestCard = t => {
    if (t.applied) return h('small', {class: 'faint', text: '선택한 값을 넣었습니다.'});
    const rows = t.suggest.map(x => h('label', {class: 'sug' + (x.ok ? '' : ' bad')},
      h('input', {type: 'checkbox', checked: !!x.pick, disabled: !x.ok, onchange: e => { x.pick = e.target.checked; }}),
      h('div', null, h('b', {text: x.label}), h('div', {text: x.value}),
        x.current ? h('small', {class: 'faint', text: '지금 값: ' + x.current}) : null, !x.ok ? h('small', {class: 'error', text: x.why}) : null)));
    return h('div', {class: 'ask-card'}, ...rows, h('button', {class: 'btn small primary', type: 'button', text: '선택한 값 넣기', onclick: () => {
      const picked = t.suggest.filter(x => x.pick && x.ok && !D.locked.has(x.key));
      for (const x of picked) { D.values[x.key] = x.value; D.sources[x.key] = '올린 자료'; D.touched[x.key] = '올린 자료'; D.meta[x.key] = {source: '올린 자료', conf: 'high', ai: true}; }
      t.applied = true; t.labels = picked.map(x => x.key); remember(); drawTurns(); showStatus(); schedule(80); save(); reReview(); drawFields();
    }}));
  };
  const askCard = t => {
    const a = t.ask;
    if (prep) return h('small', {class: 'faint', text: '다른 업체가 채울 칸(' + a.fields.map(k => labelOf[k]).filter(Boolean).join(', ') + ')은 업체가 정해져 거래방이 열리면 요청합니다.'});
    const area = h('textarea', {class: 'input', rows: 3, maxLength: 1500}); area.value = a.message;
    const send = h('button', {class: 'btn small primary', type: 'button', text: t.asked ? '보냈습니다' : a.to_name + '에 요청 보내기', disabled: !!t.asked,
      onclick: guard(async () => { await api(base + '/ask', {message: area.value}); t.asked = true; remember(); drawTurns(); toast('거래방 채팅에 요청을 보냈습니다.'); })});
    return h('div', {class: 'ask-card'}, h('small', {class: 'faint', text: '다른 업체가 채워야 하는 칸 · ' + a.fields.map(k => labelOf[k]).filter(Boolean).join(', ')}), t.asked ? null : area, send);
  };
  const intro = () => {
    const keys = Object.keys(info.values).filter(k => labelOf[k]), next = nextEmpty();
    const from = [...new Set(keys.map(k => (info.sources[k] || '').match(/^‘(.+)’에 적은 내용$/)?.[1]).filter(Boolean))];
    const pre = keys.length;
    if (prep) return info.title + '를 업체가 정해지기 전에 미리 작성합니다. ' + form.description +
      (D.locked.size ? `\n\n상대 업체가 채울 칸과 단가·계약기간처럼 업체와 협의로 정할 값 ${D.locked.size}칸은 비워 둡니다. 업체에 거래를 요청해 거래방이 열리면 지금 쓴 내용이 그대로 옮겨지고, 그때 상대 업체가 나머지를 채웁니다.` : '\n\n우리 업체만 쓰는 서류라 다 쓰면 PDF로 저장해 바로 쓸 수 있습니다.') +
      (pre ? `\n\n입력서·법령 판단·업체 정보로 ${pre}개 칸을 먼저 채웠습니다.` : '') +
      (next ? `\n\n다음 빈칸은 ‘${next.label}’입니다.` : '');
    return info.title + '를 함께 작성합니다. ' + form.description +
      (pre ? `\n\n가입할 때 입력한 업체 정보와 거래 조건${from.length ? ', ' + from.map(t => '‘' + t + '’').join(', ') + '에 적은 내용' : ''}으로 ${pre}개 칸을 먼저 채웠습니다. 오른쪽 미리보기에서 확인하세요.` : '') +
      (next ? `\n\n다음 빈칸은 ‘${next.label}’입니다. 알고 있는 내용을 그대로 적어 주시면 맞는 칸에 넣겠습니다.` : '\n\n모든 칸이 채워졌습니다. 내용을 확인해 주세요.');
  };
  const drawPin = () => {
    const f = form.fields.find(x => x.key === D.focus);
    pin.hidden = !f;
    pin.replaceChildren(...(f ? [h('div', {class: 'pin-field'},
      h('div', null, h('b', {text: f.label}), h('small', {text: (f.help || '') + (f.example ? '  예: ' + f.example : '')}),
        (D.basis[f.key] || []).length ? h('div', {class: 'basis'}, '근거 ', ...(D.basis[f.key]).map(b =>
          h('button', {type: 'button', class: 'chip line', text: b.label, onclick: () => talk('‘' + f.label + '’ 칸의 법적 근거(' + b.label + ')를 원문에 따라 설명해 주세요.')}))) : null),
      h('button', {type: 'button', 'aria-label': '선택 해제', text: '×', onclick: () => choose(null)}))] : []));
    box.area.placeholder = f ? `‘${f.label}’에 넣을 값을 적어 주세요` : '알고 있는 내용을 편하게 적어 주세요';
  };
  const drawQuick = () => {
    const next = nextEmpty();
    quick.replaceChildren(...[
      next && h('button', {type: 'button', text: '다음 빈칸 · ' + next.label, onclick: () => choose(next.key, true)}),
      h('button', {type: 'button', text: '이 서류는 누가 어디에 내나요?', onclick: () => talk('이 서류는 누가 작성하고 어디에 제출해?')}),
      D.focus && h('button', {type: 'button', text: '이 칸에는 뭘 적나요?', onclick: () => talk('이 칸에는 무엇을 적나요? 법령 기준으로 알려 주세요.')}),
      checkList().length ? h('button', {type: 'button', text: '확인할 칸 ' + checkList().length + '개', onclick: () => { D.tab = 'check'; drawPane(); }}) : null,
      h('button', {type: 'button', text: '법령 검토' + (D.review ? ' · ' + reviewBadge() : ''), onclick: () => { D.tab = 'review'; drawPane(); }}),
      h('button', {type: 'button', 'aria-pressed': String(!!D.interview), text: D.interview ? '질문 모드 끄기' : '질문에 답하며 작성',
        onclick: () => { D.interview = !D.interview; drawQuick(); if (D.interview) talk('빈칸을 하나씩 물어봐 주세요. 서식 용어 대신 쉬운 말로요.'); }}),
      h('button', {type: 'button', text: '자료로 채우기', title: '사업자등록증·허가증·이전 서류 사진이나 PDF', onclick: () => picker.click()})].filter(Boolean));
  };
  function choose(key, ask) {
    D.focus = key; drawPin(); drawQuick(); drawRegions(); drawFields();
    if (key && ask) box.area.focus();
    if (key && D.tab === 'preview') { const r = D.regions.find(x => x.key === key); if (r && r.page !== D.page) showPage(r.page); }
  }

  // 미리보기
  const sheet = h('div', {class: 'sheet'}), pageNav = h('div', {class: 'page-nav'});
  // 칸이 아닌 곳을 누르면 이전 선택을 풀고 알려 준다(이전 칸 안내가 남아 엉뚱한 칸처럼 보이지 않게)
  sheet.addEventListener('click', e => {
    if (e.target.closest('.hot') || !sheet.querySelector('img')) return;
    if (D.focus) choose(null);
    toast('이 부분은 입력 칸이 아니에요. 점선으로 표시된 칸을 눌러 주세요.');
  });
  const drawRegions = () => {
    sheet.querySelectorAll('.hot').forEach(n => n.remove());
    for (const r of D.regions.filter(r => r.page === D.page)) {
      const ai = (D.values[r.key] || '').trim() && (D.meta[r.key]?.ai || /에이전트|자료|원본/.test(D.sources[r.key] || ''));
      sheet.append(h('button', {type: 'button', class: 'hot' + (r.key === D.focus ? ' on' : '') + ((D.values[r.key] || '').trim() ? '' : ' empty') + (ai ? ' ai' : '') + (D.proposals[r.key] ? ' prop' : '') + (D.locked.has(r.key) ? ' locked' : ''),
        title: labelOf[r.key] || '', 'aria-label': (labelOf[r.key] || '') + ' 선택',
        style: `left:${r.x * 100}%;top:${r.y * 100}%;width:${r.width * 100}%;height:${r.height * 100}%`, onclick: () => choose(r.key, true)}));
    }
  };
  async function showPage(index) {
    const no = D.pdfNo;
    const r = await postRaw('/api/finish/preview', {pdf: D.pdf, page: index});
    const blob = await r.blob(); if (no !== D.pdfNo || D.closed) return;
    D.pages = Number(r.headers.get('X-Pdf-Pages')) || 1; D.page = index;
    const url = URL.createObjectURL(blob), img = h('img', {src: url, alt: info.title + ' ' + (index + 1) + '쪽'});
    await img.decode().catch(() => {});
    sheet.querySelector('img') && URL.revokeObjectURL(sheet.querySelector('img').src);
    sheet.replaceChildren(img); drawRegions();
    pageNav.replaceChildren(
      h('button', {class: 'icon-btn', type: 'button', 'aria-label': '이전 쪽', disabled: index === 0, text: '‹', onclick: () => showPage(index - 1)}),
      h('span', {text: (index + 1) + ' / ' + D.pages + '쪽'}),
      h('button', {class: 'icon-btn', type: 'button', 'aria-label': '다음 쪽', disabled: index >= D.pages - 1, text: '›', onclick: () => showPage(index + 1)}),
      h('span', {style: 'flex:1'}),
      h('button', {class: 'btn small ghost', type: 'button', text: 'PDF 저장', onclick: download}));
  }
  const render = guard(async () => {
    const no = ++D.pdfNo;
    const out = await (await postRaw('/api/pdf', {doc: form.id, values: clean(), include_fields: true})).json();
    if (no !== D.pdfNo || D.closed) return;
    D.pdf = out.pdf; D.regions = out.regions || [];
    await showPage(Math.min(D.page, 99)).catch(() => showPage(0));
  });
  const schedule = (ms = 450) => { clearTimeout(D.pdfTimer); D.pdfTimer = setTimeout(render, ms); };
  function download() {
    if (!D.pdf) return;
    const bytes = Uint8Array.from(atob(D.pdf), c => c.charCodeAt(0));
    const a = h('a', {href: URL.createObjectURL(new Blob([bytes], {type: 'application/pdf'})), download: info.title + '.pdf'});
    document.body.append(a); a.click(); a.remove();
  }

  // 입력 항목
  const fieldBox = h('div', {class: 'fields'});
  function drawFields() {
    if (D.tab !== 'fields') return;
    const keep = document.activeElement?.dataset?.key;
    fieldBox.replaceChildren(...form.fields.map(f => {
      const long = f.multiline || /주소|장소|내역|내용|사유|계획|목적|방법|특이|기타|요약|비고|설명|조건/.test(f.label);
      const input = h(long ? 'textarea' : 'input', {class: 'input', 'data-key': f.key, rows: long ? 2 : null, maxLength: 5000});
      input.value = D.values[f.key] || '';
      if (D.locked.has(f.key)) { input.disabled = true; input.placeholder = (info.locked_why || {})[f.key] === 'agreed' ? '업체와 협의해 정함 (거래방에서)' : '업체가 정해지면 상대 업체가 채움'; }
      input.addEventListener('focus', () => { if (D.focus !== f.key) { D.focus = f.key; drawPin(); drawQuick(); } });
      input.addEventListener('input', () => { set(f.key, input.value, '직접 입력'); src.textContent = input.value.trim() ? '직접 입력' : ''; });
      const src = h('small', {class: 'faint', text: D.values[f.key] ? D.sources[f.key] || '' : ''});
      const choices = Array.isArray(f.choices) && f.choices.length ? h('div', {class: 'picks', style: 'margin-top:6px'}, f.choices.map(c =>
        h('button', {type: 'button', 'aria-pressed': String((D.values[f.key] || '') === c), text: c, onclick: () => { set(f.key, D.values[f.key] === c ? '' : c, '직접 입력'); drawFields(); }}))) : null;
      const m = D.meta[f.key] || {}, ai = !!D.values[f.key] && (m.ai || /에이전트|자료|원본/.test(D.sources[f.key] || ''));
      const marks = [ai ? chip('AI', 'accent') : null, m.conf === 'check' && D.values[f.key] ? chip('확신 낮음', 'warn') : null, D.proposals[f.key] ? chip('제안 있음', 'accent') : null];
      return h('label', {class: 'field doc-field' + (D.focus === f.key ? ' on' : '') + (ai ? ' ai' : '')}, h('span', null, f.label, ' ', ...marks.filter(Boolean), ' ', src), input, choices);
    }));
    if (keep) fieldBox.querySelector(`[data-key="${keep}"]`)?.focus();
  }
  const drawPane = () => {
    const tab = (k, t) => h('button', {'aria-selected': String(D.tab === k), text: t, onclick: () => { D.tab = k; drawPane(); }});
    tabs.replaceChildren(tab('check', '확인할 칸' + (checkList().length ? ' ' + checkList().length : '')), tab('preview', '미리보기'), tab('fields', '입력 항목'), tab('review', '검토' + (D.review ? ' · ' + reviewBadge() : '')));
    if (D.tab === 'check') { pane.replaceChildren(checkBox); drawCheck(); }
    else if (D.tab === 'review') { pane.replaceChildren(reviewBox); drawReview(); }
    else if (D.tab === 'preview') pane.replaceChildren(h('p', {class: 'faint', style: 'font-size:12.5px;margin-bottom:8px', text: '서식의 칸을 누르면 그 칸을 골라 대화로 채울 수 있습니다.'}), sheet, pageNav);
    else { pane.replaceChildren(fieldBox); drawFields(); }
  };

  // 법령 검토
  const reviewBadge = () => { const c = D.review.counts; return c.error ? '오류 ' + c.error : c.warning ? '확인 ' + c.warning : '통과'; };
  const reviewBox = h('div', {class: 'review'});
  function drawReview() {
    if (D.tab !== 'review') return;
    const r = D.review;
    if (!r) { reviewBox.replaceChildren(h('p', {class: 'faint', text: '검토 결과를 불러오는 중입니다.'})); return; }
    const head = {fix: ['고쳐야 할 항목이 있습니다', 'bad'], check: ['확인이 필요한 항목이 있습니다', 'warn'], pass: ['법정 필수 항목 검토를 통과했습니다', 'ok']}[r.verdict];
    const sev = {error: ['오류', 'bad'], warning: ['확인', 'warn'], info: ['안내', '']};
    reviewBox.replaceChildren(
      r.verdict !== 'pass' ? h('button', {class: 'btn small primary', type: 'button', style: 'align-self:flex-start', text: '에이전트에게 고치기 맡기기', onclick: () => D.fixAll()}) : null,
      h('div', {class: 'verdict ' + head[1]}, h('b', {text: head[0]}),
        h('small', {text: r.rules ? '폐기물관리법 시행규칙의 필수 기재 사항 규칙, 별표 4 분류번호, 거래방 업체·다른 서류와 대조했습니다.' :
          '이 서식은 법정 필수 항목 규칙이 아직 없어 분류번호·업체 상호·다른 서류와의 일치만 확인했습니다.'})),
      ...r.findings.map(f => h('div', {class: 'finding'},
        h('div', {class: 'finding-top'}, chip(sev[f.severity][0], sev[f.severity][1]),
          f.key ? h('button', {type: 'button', class: 'linkish', text: f.label + ' ›', onclick: () => { choose(f.key, true); }}) : null),
        h('p', {text: f.message}),
        h('small', {class: 'faint', text: f.key ? (f.severity === 'info' ? '참고 안내예요.' : `‘${f.label}’ 칸을 채우거나 고치면 사라져요.`)
          : f.severity === 'info' ? '서류 칸과 관계없는 안내예요. 고칠 칸은 없어요.' : '서류 칸이 아니라 함께 갖춰야 할 서류·절차예요.'}),
        f.basis ? h('details', {class: 'law'}, h('summary', null, h('span', {class: 'law-tag', text: f.basis.label})),
          f.quote ? h('p', {text: '“' + f.quote + '”'}) : h('p', {class: 'faint', text: '원문은 질문으로 확인할 수 있습니다.'})) : null)),
      r.empty.length ? h('p', {class: 'faint', style: 'margin-top:12px;font-size:12.5px', text: '비어 있는 칸 ' + r.empty.length + '개: ' + r.empty.slice(0, 12).join(', ') + (r.empty.length > 12 ? ' 외' : '')}) : null,
      ...(r.notes || []).map(n => h('p', {class: 'faint', style: 'font-size:12px', text: n})));
  }
  const reReview = () => { clearTimeout(D.reviewTimer); D.reviewTimer = setTimeout(async () => {
    try { D.review = await api(base + '/review', {values: clean()}); drawReview(); drawCheck(); drawQuick(); drawTabsOnly(); } catch {}
  }, 1200); };
  const drawTabsOnly = () => { const t = tabs.children[3], c = tabs.children[0]; if (t && D.review) t.textContent = '검토 · ' + reviewBadge(); if (c) c.textContent = '확인할 칸' + (checkList().length ? ' ' + checkList().length : ''); };

  // 확인할 칸: 제안(원본·고치기·바뀐 값) → 검토 오류·확인 → AI가 채웠고 확신이 낮은 칸. 사람은 이것만 본다.
  const checkBox = h('div', {class: 'review'});
  function checkList() {
    const out = [], seen = new Set();
    for (const [k, p] of Object.entries(D.proposals)) if (labelOf[k]) { out.push({key: k, type: 'prop', p}); seen.add(k); }
    for (const f of (D.review?.findings || [])) if (f.key && !seen.has(f.key) && f.severity !== 'info') { out.push({key: f.key, type: 'finding', f}); seen.add(f.key); }
    for (const [k, m] of Object.entries(D.meta)) if (labelOf[k] && !seen.has(k) && m.conf === 'check' && D.values[k]) { out.push({key: k, type: 'low', m}); seen.add(k); }
    return out;
  }
  const decide = guard(async (accept, reject, edits) => {
    const out = await api(base + '/decide', {accept, reject, edits, values: clean()});
    for (const [k, v] of Object.entries(out.applied || {})) { D.values[k] = v; D.sources[k] = out.meta[k]?.source || '제안 수락'; }
    D.meta = out.meta || D.meta; D.proposals = out.proposals || {}; D.review = out.review || D.review;
    showStatus(); schedule(80); save(); drawPane(); drawQuick();
    if (accept.length) toast(`${Object.keys(out.applied || {}).length}칸을 넣었습니다.`);
  });
  function drawCheck() {
    if (D.tab !== 'check') return;
    const list = checkList();
    const props = list.filter(x => x.type === 'prop');
    const kindLabel = {original: '원본에서', fix: '검토 고치기', changed: '출처가 바뀜'};
    checkBox.replaceChildren(...[
      h('div', {class: 'verdict ' + (list.length ? 'warn' : 'ok')}, h('b', {text: list.length ? `사람이 확인할 칸 ${list.length}개` : '확인할 칸이 없습니다'}),
        h('small', {text: '에이전트가 제안한 값, 검토에서 걸린 칸, 확신이 낮은 칸만 모았습니다. 나머지 칸은 업체 정보·다른 서류처럼 출처가 분명한 값입니다.'})),
      props.length > 1 ? h('div', {style: 'display:flex;gap:6px'},
        h('button', {class: 'btn small primary', type: 'button', text: `제안 ${props.length}개 모두 수락`, onclick: () => decide(props.map(x => x.key), [], {})}),
        h('button', {class: 'btn small ghost', type: 'button', text: '모두 거절', onclick: () => decide([], props.map(x => x.key), {})})) : null,
      ...list.map(x => {
        const label = h('button', {type: 'button', class: 'linkish', text: labelOf[x.key] + ' ›', onclick: () => { D.tab = 'preview'; drawPane(); choose(x.key, true); }});
        if (x.type === 'prop') {
          const p = x.p, edit = h('input', {class: 'input', value: p.value}); edit.value = p.value;
          return h('div', {class: 'finding prop'},
            h('div', {class: 'finding-top'}, chip(kindLabel[p.kind] || '제안', p.kind === 'changed' ? 'warn' : 'accent'), p.conf === 'check' ? chip('확신 낮음', 'warn') : null, label),
            D.values[x.key] ? h('div', {class: 'diff'}, h('del', {text: D.values[x.key]}), ' → ') : null, edit,
            h('small', {class: 'faint', text: p.source + (p.why ? ' · ' + p.why : '')}),
            h('div', {style: 'display:flex;gap:6px'}, h('button', {class: 'btn small primary', type: 'button', text: '수락', onclick: () => decide([x.key], [], {[x.key]: edit.value})}),
              h('button', {class: 'btn small ghost', type: 'button', text: '거절', onclick: () => decide([], [x.key], {})})));
        }
        if (x.type === 'finding') return h('div', {class: 'finding'}, h('div', {class: 'finding-top'}, chip(x.f.severity === 'error' ? '오류' : '확인', x.f.severity === 'error' ? 'bad' : 'warn'), label),
          h('p', {text: x.f.message}), D.values[x.key] ? h('small', {class: 'faint', text: '지금 값: ' + D.values[x.key]}) : null);
        return h('div', {class: 'finding'}, h('div', {class: 'finding-top'}, chip('확신 낮음', 'warn'), label),
          h('p', {text: D.values[x.key]}), h('small', {class: 'faint', text: x.m.source + ' · 맞으면 그대로 두고, 틀리면 칸을 눌러 고치세요.'}),
          h('button', {class: 'btn small ghost', type: 'button', text: '맞습니다', onclick: () => { set(x.key, D.values[x.key], (x.m.source || '') + ' (확인함)'); D.meta[x.key] = {...x.m, conf: 'high'}; drawPane(); }}));
      }),
      list.length ? null : h('p', {class: 'faint', text: '서류 비서의 ‘원본 자료 올리기’나 검토 탭의 ‘에이전트에게 고치기 맡기기’를 쓰면 제안이 여기에 모입니다.'})].filter(Boolean));
  }

  // 값 저장
  const clean = () => Object.fromEntries(Object.entries(D.values).filter(([k, v]) => labelOf[k] && v && v.trim()));
  function set(key, value, source) {
    if (D.locked.has(key)) { toast('이 칸은 업체가 정해진 뒤 상대 업체가 채웁니다.'); return; }
    D.values[key] = value; if (value) D.sources[key] = source; else delete D.sources[key];
    D.touched[key] = source; if (D.meta[key]) D.meta[key] = {source, conf: 'high', ai: /에이전트|자료|원본/.test(source)};
    showStatus(); drawQuick(); schedule(); save(); reReview();
  }
  const save = () => { clearTimeout(D.saveTimer); D.saveTimer = setTimeout(beat, 1200); };
  async function beat() {
    if (D.closed) return;
    const sources = D.touched; D.touched = {};
    try { const out = await api(base, {action: 'editing', values: clean(), sources}); D.others = out.others || []; showStatus(); }
    catch { Object.assign(D.touched, sources); }
  }

  // 대화
  async function talk(text) {
    const pinned = D.focus || (!S.boot.ai.ready ? nextEmpty()?.key : null);
    if (!D.turns.length) D.turns.push({role: 'assistant', text: intro()});
    const asking = /\?|뭐|무엇|어디|누가|언제|어떻게|왜|알려|설명|근거|법/.test(text);
    D.turns.push({role: 'user', text}); const wait = {role: 'pending', text: asking ? '법령과 실무 자료를 찾아 확인하고 있습니다… (보통 10~20초)' : '확인하고 있습니다…'}; D.turns.push(wait); drawTurns();
    try {
      const history = D.turns.filter(t => t.role === 'user' || t.role === 'assistant').slice(-11, -1).map(t => ({role: t.role, text: t.text}));
      const out = await api(base + '/agent', {message: text, values: clean(), focus: pinned || null, turns: history, interview: !!D.interview});
      const changed = [];
      for (const [k, v] of Object.entries(out.updates || {})) if (labelOf[k] && !D.locked.has(k) && typeof v === 'string' && v !== D.values[k]) { D.values[k] = v; D.sources[k] = '작성 에이전트'; D.touched[k] = '작성 에이전트'; D.meta[k] = {source: '작성 에이전트', conf: 'high', ai: true}; changed.push(k); }
      D.turns.splice(D.turns.indexOf(wait), 1, {role: 'assistant', text: out.reply || '확인했습니다.', labels: changed,
        cites: out.citations || [], related: out.related || [], ask: out.ask || null});
      if (out.review) { D.review = out.review; drawReview(); }
      if (changed.length) { showStatus(); schedule(80); save(); if (D.focus && changed.includes(D.focus)) D.focus = out.next || null; }
      // 질문 모드: AI가 다음에 물은 칸을 골라 둬서, 사용자의 다음 답이 그 칸으로 들어가게 한다
      if (D.interview && out.next && labelOf[out.next] && !D.locked.has(out.next)) D.focus = out.next;
    } catch (e) { D.turns.splice(D.turns.indexOf(wait), 1, {role: 'assistant', text: '처리하지 못했습니다. ' + e.message}); }
    remember(); drawTurns(); drawPin(); drawQuick(); drawRegions(); drawFields();
  }

  // 자료(사진·PDF)에서 값 제안 → 사용자가 골라 넣기
  const picker = h('input', {type: 'file', accept: 'image/*,application/pdf', hidden: true});
  document.getElementById('body').append(picker);
  picker.onchange = async () => {
    const file = picker.files[0]; picker.value = ''; if (!file) return;
    if (!D.turns.length) D.turns.push({role: 'assistant', text: intro()});
    D.turns.push({role: 'user', text: '자료 올림 · ' + file.name}); const wait = {role: 'pending', text: '자료를 읽고 있습니다…'}; D.turns.push(wait); drawTurns();
    try {
      const out = await api(base + '/extract', {file: await prepareFile(file), values: clean()});
      D.turns.splice(D.turns.indexOf(wait), 1, {role: 'assistant', text: (out.doc_kind ? `‘${out.doc_kind}’${out.issuer ? '(' + out.issuer + ')' : ''}에서 ` : '') +
        (out.suggestions.length ? `칸 ${out.suggestions.length}개에 넣을 값을 찾았습니다. 넣을 값을 골라 주세요.` : '이 서식에 넣을 값을 찾지 못했습니다.') + (out.note ? '\n' + out.note : ''),
        suggest: out.suggestions.map(x => ({...x, pick: x.ok && !x.current}))});
    } catch (e) { D.turns.splice(D.turns.indexOf(wait), 1, {role: 'assistant', text: '자료를 읽지 못했습니다. ' + e.message}); }
    remember(); drawTurns();
  };
  async function fixAll() {
    if (!D.turns.length) D.turns.push({role: 'assistant', text: intro()});
    D.turns.push({role: 'user', text: '검토에서 나온 문제를 고쳐 주세요.'}); const wait = {role: 'pending', text: '검토 결과를 고치고 다시 검토하는 중입니다…'}; D.turns.push(wait); drawTurns();
    try {
      const out = await api(base + '/fix', {values: clean()});
      D.proposals = out.proposals || {}; D.review = out.review;
      const fixed = out.rounds.flatMap(r => r.fixed);
      const text = (fixed.length ? `${out.rounds.length}번 검토하며 고칠 값 ${fixed.length}개를 찾았습니다. 바로 넣지 않고 ‘확인할 칸’에 제안으로 올렸습니다.\n` + fixed.map(f => `· ${f.label} ← ${f.value} (${f.source})`).join('\n') : '근거 자료에서 고칠 값을 찾지 못했습니다.') +
        (out.questions.length ? '\n\n직접 확인해야 할 것:\n' + out.questions.map(q => `· ${q.label}${q.to ? ' (' + q.to + ')' : ''}: ${q.text}`).join('\n') : '');
      D.turns.splice(D.turns.indexOf(wait), 1, {role: 'assistant', text, labels: fixed.map(f => f.key)});
      if (fixed.length) { D.tab = 'check'; }
    } catch (e) { D.turns.splice(D.turns.indexOf(wait), 1, {role: 'assistant', text: '고치지 못했습니다. ' + e.message}); }
    remember(); drawTurns(); drawPane();
  }
  D.fixAll = fixAll;

  mainAction.onclick = guard(async () => {
    if (prep) { save(); download(); if (D.locked.size) toast('상대 업체 칸은 비어 있는 우리 쪽 초안입니다. 업체가 정해지면 거래방에서 이어서 씁니다.'); return; }
    mainAction.disabled = true;
    try {
      D.review = await api(base + '/review', {values: clean()}); drawReview(); drawTabsOnly();
      if (D.review.counts.error && !confirm(`법령 검토에서 고쳐야 할 항목 ${D.review.counts.error}건이 나왔습니다. 검토 탭에서 확인할 수 있습니다.\n그래도 ${info.shared ? '공유' : '완료 표시'}할까요?`)) return;
      if (info.shared) {
        if (!D.pdf) throw Error('작성본이 미리보기에 나타난 뒤 공유하세요.');
        const out = await api(base + '/share', {pdf: D.pdf, values: clean()});
        toast(`작성본 v${out.version}을 거래방 채팅에 공유했습니다.`);
      } else { await api(base, {action: 'done'}); toast('거래방에 준비 완료로 표시했습니다.'); }
    } finally { mainAction.disabled = false; }
  });

  showStatus(); drawTurns(); drawPin(); drawQuick(); drawPane(); render(); beat();
  D.beat = setInterval(beat, 15000);
}
function paneDocs(box) {
  const d = S.room, id = d.room.id, docs = d.docs;
  if (!docs) { box.append(h('p', {class: 'placeholder', text: '참여 업체가 모두 ‘거래 진행에 합의’를 누르면 업체별로 준비할 서류와 함께 작성할 서류를 여기에 정리해 드립니다.'})); return; }
  const act = (key, body) => guard(async () => { await api('rooms/' + id + '/docs/' + key, body); await refreshRoom(); });
  const flag = (name, label) => h('label', null, h('input', {type: 'checkbox', checked: !!docs.flags[name], onchange: guard(async e => {
    await api('rooms/' + id + '/flags', {...docs.flags, [name]: e.target.checked}); await refreshRoom();
  })}), label);
  const deskBox = h('div', {class: 'desk'}); box.append(deskBox); deskBoard(id, deskBox);
  box.append(h('div', {class: 'flags'}, h('span', {class: 'faint', text: '이 거래에 해당하면 체크'}), flag('business', '배출자 신고 대상'), flag('designated', '지정폐기물'), flag('construction', '건설폐기물'), flag('classify', '분류 확인 필요')));
  for (const phase of docs.phases) {
    const rows = phase.items.map(i => {
      const state = h('div', {class: 'state'});
      let lead;
      if (i.shared) {
        lead = h('span', {class: 'check readonly', role: 'img', 'aria-checked': String(i.done), 'aria-label': i.done ? '완료' : '진행 중'}, i.done && icon('check'));
        state.append(chip('함께 작성', 'accent'));
        for (const e of i.editing) state.append(h('span', {class: 'chip warn live'}, h('span', {class: 'dot'}), e.name + ' 작성 중'));
        if (i.file) {
          const okCount = i.parties.filter(p => p.confirmed).length;
          state.append(chip(`v${i.file.version} 공유 · ${i.file.by_name}`), chip(`확인 ${okCount}/${i.parties.length}`, okCount === i.parties.length ? 'ok' : ''));
        } else if (!i.editing.length) state.append(chip('작성 전 · ' + i.author, 'line'));
        state.append(h('span', {class: 'spacer'}));
        if (i.file) state.append(h('a', {class: 'btn small ghost', href: '/api/p/files/' + i.file.file_id, target: '_blank', rel: 'noopener', text: '공유본 열기'}));
        if (i.mine && i.doc) state.append(h('button', {class: 'btn small', 'data-open': i.key, text: i.file ? '이어서 작성' : '작성하기', onclick: () => openDocFor(i)}));
        if (i.mine && i.file && !i.parties.find(p => p.id === S.boot.me.id)?.confirmed) state.append(h('button', {class: 'btn small blue', text: '확인', onclick: act(i.key, {action: 'confirm'})}));
      } else if (i.mine) {
        lead = h('button', {class: 'check', role: 'checkbox', 'aria-checked': String(!!i.my_done), 'aria-label': i.title + ' 준비 완료', onclick: act(i.key, {action: i.my_done ? 'undone' : 'done'})}, i.my_done && icon('check'));
        state.append(chip(i.kind, 'line'), chip(i.my_done ? '준비 완료' : '우리 업체 준비', i.my_done ? 'ok' : ''));
        for (const o of i.owners.filter(o => o.id !== S.boot.me.id)) state.append(chip(o.name + (o.done ? ' 완료' : ' 준비 중'), o.done ? 'ok' : ''));
        state.append(h('span', {class: 'spacer'}));
        if (i.doc) state.append(h('button', {class: 'btn small', 'data-open': i.key, text: '작성하기', onclick: () => openDocFor(i)}));
      } else {
        lead = h('span', {class: 'check readonly', role: 'img', 'aria-checked': String(i.done), 'aria-label': i.done ? '완료' : '준비 중'}, i.done && icon('check'));
        state.append(chip(i.kind, 'line'), ...i.owners.map(o => chip(o.name + (o.done ? ' 완료' : ' 준비 중'), o.done ? 'ok' : '')));
      }
      return h('div', {class: 'doc' + (i.done ? ' done' : '')}, h('div', {class: 'doc-top'}, lead, h('div', {class: 'title'}, i.title, h('small', {text: i.note}))),
        h('div', {class: 'doc-desk', 'data-key': i.key}), state);
    });
    box.append(h('div', {class: 'phase'}, h('div', {class: 'phase-head'}, h('h3', {text: phase.label}), h('span', {text: phase.done + '/' + phase.total})), rows));
  }
  if (S.deskCache?.room === id) fillDocRows(box, S.deskCache.data);
}
// 서류 비서가 센 진행 정보를 아래 서류 목록의 각 줄에 붙인다(채운 칸·제안·바뀜·확인할 칸·기한). 서류는 목록에 한 번만 나온다.
function deskInfo(key) { return S.deskCache?.room === S.room?.room?.id ? (S.deskCache.data.docs || []).find(x => x.key === key) : null; }
function openDocFor(i) { const x = deskInfo(i.key); S.docTab = x && x.check && !x.done ? 'check' : null; openDoc(i); }
function fillDocRows(scope, b) {
  for (const x of b.docs || []) {
    const slot = scope.querySelector(`.doc-desk[data-key="${x.key}"]`);
    if (!slot) continue;
    const pct = x.total ? Math.round(x.filled / x.total * 100) : null;
    slot.replaceChildren(...[...(x.done ? [] : [
      pct !== null ? h('span', {class: 'meter', title: x.filled + '/' + x.total + '칸'}, h('span', {style: `width:${pct}%`})) : null,
      pct !== null ? h('small', {class: 'faint', text: x.filled + '/' + x.total + '칸'}) : null,
      x.proposals ? chip('제안 ' + x.proposals, 'accent') : null, x.changed ? chip('바뀜 ' + x.changed, 'warn') : null,
      x.check ? chip('확인할 칸 ' + x.check, x.errors ? 'bad' : 'warn') : null]),
      x.deadline && !x.done ? chip('기한 ' + (x.deadline.date ? x.deadline.date + ' · ' : '') + x.deadline.text, x.deadline.date ? 'warn' : 'line') : null].filter(Boolean));
    const btn = scope.querySelector(`[data-open="${x.key}"]`);
    if (btn && x.check && !x.done) btn.textContent = '확인하기';
  }
}
// 서류 비서: 이 거래의 서류 묶음을 에이전트가 먼저 챙긴다 — 빠진 서류, 진행률·확인할 칸, 상대 업체에 받을 칸, 기한, 원본 올리기.
async function deskBoard(roomId, box, given) {
  let b = given;
  if (!b) {
    if (S.deskCache?.room === roomId) b = S.deskCache.data;
    else box.replaceChildren(h('p', {class: 'faint', text: '서류 비서가 진행 상황을 정리하고 있습니다…'}));
    try { b = await api('rooms/' + roomId + '/desk'); S.deskCache = {room: roomId, data: b}; } catch (e) { box.replaceChildren(); return; }
  } else S.deskCache = {room: roomId, data: b};
  const redraw = data => deskBoard(roomId, box, data);
  const file = h('input', {type: 'file', hidden: true, accept: '.docx,image/*,application/pdf'});
  file.onchange = guard(async () => {
    const f = file.files[0]; file.value = ''; if (!f) return;
    up.disabled = true; up.textContent = '읽고 나눠 넣는 중…';
    try {
      const payload = /\.docx$/i.test(f.name) ? {name: f.name, mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', data: await readAsBase64(f)} : await prepareFile(f);
      const out = await api('rooms/' + roomId + '/desk/original', {file: payload});
      const n = out.docs.reduce((a, x) => a + x.proposed, 0);
      toast(n ? `‘${out.original.kind || out.original.name}’에서 서류 ${out.docs.length}종에 ${n}칸을 제안했습니다. 각 서류의 ‘확인할 칸’에서 수락하세요.` : '이 자료에서 새로 넣을 값을 찾지 못했습니다.');
      if (out.note) toast(out.note);
      redraw(out.board);
    } finally { up.disabled = false; up.textContent = '원본 자료 올리기'; }
  });
  const up = h('button', {class: 'btn small', type: 'button', text: '원본 자료 올리기', title: '기초 입력서(.docx)·사업자등록증·허가증·이전 서류(사진·PDF)', onclick: () => file.click()});
  const date = h('input', {class: 'input', type: 'date', value: b.start_date || '', style: 'width:auto;padding:5px 8px;font-size:13px'});
  date.value = b.start_date || '';
  date.onchange = guard(async () => redraw(await api('rooms/' + roomId + '/desk/start', {date: date.value})));
  const bulk = h('button', {class: 'btn small', type: 'button', text: '내 서류 초안 한 번에 만들기',
    title: '업체 정보·거래 조건·법령 판단에 쓴 사실·다른 서류에 적은 값으로 우리 업체가 맡은 서류의 빈칸을 채웁니다. 이미 적은 값은 바꾸지 않습니다.',
    onclick: guard(async () => {
      bulk.disabled = true;
      try { const out = await api('rooms/' + roomId + '/drafts', {}); const added = out.docs.reduce((a, x) => a + x.added, 0);
        toast(`서류 ${out.docs.length}종에 ${added}칸을 채웠습니다. 아래 각 서류에서 확인하세요.`); S.deskCache = null; await refreshRoom(true); }
      finally { bulk.disabled = false; }
    })});
  box.replaceChildren(h('div', {class: 'desk-card'},
    h('div', {class: 'desk-head'}, h('b', {text: '서류 비서'}), h('span', {class: 'spacer'}), bulk, up, file),
    h('p', {class: 'desk-headline', text: b.headline}),
    ...b.missing.map(m => h('div', {class: 'ask-card'}, h('small', {class: 'faint', text: '법령 판단에서 필요하다고 나온 서류'}),
      h('b', {text: m.title}), m.why ? h('p', {text: m.why}) : null, m.basis ? chip(m.basis, 'line') : null,
      m.flag ? h('button', {class: 'btn small', type: 'button', text: `‘${m.flag_label}’ 켜고 서류 추가`, onclick: guard(async () => {
        await api('rooms/' + roomId + '/flags', {...S.room.docs.flags, [m.flag]: true}); S.deskCache = null; await refreshRoom(true);
      })}) : null)),
    ...b.need_from.map(n => h('div', {class: 'ask-card'},
      h('div', {class: 'desk-doc-top'}, h('b', {text: `${n.name}에 받을 칸 ${n.count}개`}), n.sent ? chip('요청 보냄', 'ok') : null),
      ...n.docs.map(x => h('small', {class: 'faint', text: x.title + ': ' + x.fields.slice(0, 8).join(', ') + (x.fields.length > 8 ? ' 외 ' + (x.fields.length - 8) + '칸' : '')})),
      h('button', {class: 'btn small', type: 'button', text: n.sent ? '다시 요청' : '채팅으로 요청 보내기', onclick: guard(async () => {
        redraw(await api('rooms/' + roomId + '/desk/request', {company: n.company_id})); toast('거래방 채팅에 요청을 보냈습니다.'); await refreshRoom(true);
      })}))),
    h('div', {class: 'desk-foot'}, h('small', {class: 'faint', text: '폐기물 발생(첫 반출 예정)일'}), date,
      b.originals.length ? h('small', {class: 'faint', text: '올린 원본: ' + b.originals.map(o => o.kind || o.name).join(', ')}) : null)));
  fillDocRows(box.parentElement || box, b);
}
function paneSteps(box) {
  const steps = S.room.steps;
  if (!steps) { box.append(h('p', {class: 'placeholder', text: '거래에 합의하면 계약부터 정산·사후관리까지 남은 절차를 단계별로 보여 드립니다.'})); return; }
  box.append(h('div', {class: 'steps-head', text: steps.headline}));
  for (const p of steps.phases) {
    box.append(h('div', {class: 'step ' + p.state}, h('span', {class: 'mark'}, p.state === 'done' && icon('check')),
      h('div', null, h('h3', null, p.label, chip({done: '완료', current: '진행 중', upcoming: '예정'}[p.state] + ' ' + p.done + '/' + p.total, p.state === 'done' ? 'ok' : p.state === 'current' ? 'accent' : '')),
        h('p', {text: p.about}),
        p.remaining.length ? h('ul', null, p.remaining.map(r => h('li', {class: r.mine ? 'mine' : ''}, h('span', {text: r.title}), h('span', {text: r.who})))) : null)));
  }
}
const findInvite = guard(async () => {
  S.invite.data = await api('rooms/' + S.room.room.id + '/find', {want: S.invite.want});
  if (S.tab === 'invite') renderPane();
});
function paneInvite(box) {
  const d = S.room, inv = S.invite;
  const select = h('select', {class: 'input', style: 'max-width:200px', onchange: e => { inv.want = e.target.value; inv.data = null; findInvite(); }},
    [['hauler', '수집·운반업체'], ['recycler', '재활용·가공업체'], ['disposer', '처분업체']].map(([v, t]) => h('option', {value: v, text: t, selected: inv.want === v})));
  box.append(h('p', {class: 'muted', style: 'font-size:13.5px;margin-bottom:10px', text: '이 거래에 운반업체나 다른 처리업체가 더 필요하면 요청을 보내세요. 상대가 승인하면 같은 거래방에 들어옵니다.'}),
    h('div', {style: 'display:flex;gap:8px;align-items:center;margin-bottom:12px'}, select, h('button', {class: 'btn small', text: '찾기', onclick: findInvite})));
  if (d.invites.length) box.append(h('div', {class: 'needs', style: 'margin-bottom:12px'}, d.invites.map(i => chip(i.name + ' · 답변 대기', 'warn'))));
  if (!inv.data) return;
  if (!inv.data.results.length) { box.append(h('p', {class: 'placeholder', text: '조건에 맞는 업체가 없습니다. 다른 유형으로 찾아보세요.'})); return; }
  const role = {hauler: 'hauler', recycler: 'processor', disposer: 'processor'}[inv.want];
  box.append(...resultPanel(inv.data.results, inv.data.origin, c => c.request === 'pending' ? chip('답변 대기', 'warn')
    : h('button', {class: 'btn small', text: '참여 요청', onclick: e => { e.stopPropagation(); requestDialog(c, {room_id: d.room.id, as_role: role}); }})));
}
async function refreshRoom(force) {
  if (S.route.name !== 'room' || !S.room) return;
  const id = S.room.room.id;
  const d = await api('rooms/' + id + '?after=' + S.last);
  if (S.route.name !== 'room' || S.room?.room.id !== id) return;
  const fresh = d.messages.filter(m => m.id > S.last);
  if (fresh.length) {
    const thread = document.getElementById('roomThread'), near = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 140;
    document.getElementById('roomMsgs').append(...fresh.map(messageNode));
    S.room.messages.push(...fresh); S.last = d.last_id;
    if (near || fresh.some(m => m.mine)) thread.scrollTop = thread.scrollHeight;
  }
  S.seen[id] = d.last_id; localStorage.setItem('seen', JSON.stringify(S.seen));
  const sig = JSON.stringify([d.room, d.members, d.docs, d.steps, d.invites]);
  Object.assign(S.room, {room: d.room, members: d.members, docs: d.docs, steps: d.steps, invites: d.invites});
  if (sig !== S.sig.room || force) {
    S.sig.room = sig; setTop(d.room.title); renderDealbar();
    if (S.tab !== 'ai') renderPane(); else renderPaneTabsOnly();
  }
}
function renderPaneTabsOnly() {
  const d = S.room, tabs = document.getElementById('sideTabs'); if (!tabs) return;
  const small = tabs.querySelectorAll('button');
  if (small[1] && d.docs) { small[1].querySelector('small')?.remove(); small[1].append(h('small', {text: d.docs.done + '/' + d.docs.total})); }
}

// ── 업체 정보 ──────────────────────────────────────────────────────────
async function renderProfile() {
  setTop('업체 정보');
  S.options = S.options || await api('options');
  const form = profileForm(S.boot.me.profile, S.options), error = h('p', {class: 'error', role: 'alert'});
  document.getElementById('body').replaceChildren(h('div', {class: 'page'}, h('div', {class: 'page-inner', style: 'max-width:760px'},
    h('h2', {text: '업체 정보'}), h('p', {text: '가입할 때 적은 업체 기초 정보입니다. 거래 상대 찾기와 서류의 업체 칸에 쓰입니다.'}),
    form.el, h('div', {class: 'section'}, error, h('button', {class: 'btn primary', text: '저장', onclick: async () => {
      error.textContent = '';
      try { const out = await api('profile', {profile: form.read()}); S.boot.me = out.me; renderSidebar(); toast('업체 정보를 저장했습니다.'); }
      catch (e) { error.textContent = e.message; }
    }})))));
}

// ── 흐름 ──────────────────────────────────────────────────────────────
async function refreshBoot() {
  const boot = await api('bootstrap');
  const sig = JSON.stringify([boot.rooms, boot.searches, boot.requests, boot.me]);
  S.boot = boot;
  if (sig !== S.sig.boot) {
    S.sig.boot = sig; renderSidebar();
    if (S.route.name === 'requests') renderRequests();
  }
}
const isEmitter = () => { const r = S.boot?.me.profile.roles || []; return r.includes('emitter') || !r.length; };
const route = guard(async () => {
  if (!S.boot) return;
  const [, name, id] = location.hash.split('/');
  document.querySelector('.menu')?.remove();
  leaveDoc();
  const sub = location.hash.split('/');
  if (name === 'p' && id && sub[3] === 'd' && sub[4]) {
    S.route = {name: 'prep', id, item: sub[4]}; renderSidebar();
    try { await renderDoc(id, decodeURIComponent(sub[4]), {prep: true}); } catch (e) { toast(e.message); location.hash = '#/s/' + id; }
  } else if (name === 'r' && id && sub[3] === 'd' && sub[4]) {
    S.route = {name: 'doc', id, item: sub[4]}; renderSidebar();
    try { await renderDoc(id, decodeURIComponent(sub[4])); } catch (e) { toast(e.message); location.hash = '#/r/' + id; }
  } else if (name === 'r' && id) {
    S.route = {name: 'room', id}; renderSidebar();
    try { await loadForms().catch(() => {}); S.room = await api('rooms/' + id + '?full=1'); } catch (e) { toast(e.message); location.hash = '#/'; return; }
    S.last = S.room.last_id; S.sig.room = ''; S.invite.data = null;
    if (!S.room.docs && (S.tab === 'docs' || S.tab === 'steps')) S.tab = 'ai';
    S.seen[id] = S.last; localStorage.setItem('seen', JSON.stringify(S.seen));
    renderRoom(); renderSidebar();
  } else if (name === 'requests') { S.route = {name: 'requests'}; renderSidebar(); renderRequests(); }
  else if (name === 'profile') { S.route = {name: 'profile'}; renderSidebar(); await renderProfile(); }
  else if (name === 'intake' || (!name && isEmitter())) { S.route = {name: 'intake'}; renderSidebar(); renderIntake(); }
  else {
    S.route = {name: 'search', id: name === 's' ? id : null};
    S.search = S.route.id ? await api('search/' + S.route.id).catch(() => null) : null;
    if (S.route.id && !S.search) S.route.id = null;
    renderSidebar(); renderSearch();
  }
});
let ticking = false, beat = 0;
async function tick() {
  if (ticking || !S.boot || document.hidden) return;
  ticking = true;
  try { await refreshRoom(); if (++beat % 2 === 0) await refreshBoot(); }
  catch (e) { if (e.status === 401) { S.boot = null; start(); } }
  ticking = false;
}
async function start() {
  let session;
  try { session = await api('session'); }
  catch (e) { app.replaceChildren(h('p', {style: 'padding:40px', text: '서버에 연결하지 못했습니다. ' + e.message})); return; }
  if (!session.signed_in) { S.boot = null; renderAuth(session); return; }
  S.boot = session; S.sig = {}; S.room = null; S.search = null; S.intake = null;  // 다른 계정·새 로그인에 이전 입력서가 남지 않게
  renderShell(); route();
}
window.addEventListener('hashchange', route);
document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });
setInterval(tick, 3000);
start();
