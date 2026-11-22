"""Read-only public route checks; no case data, cookies, credentials or submissions."""
import argparse
import hashlib
import json
import socket
import ipaddress
import sqlite3
from contextlib import closing
from datetime import datetime,timezone,timedelta
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin,urlsplit,urlunsplit,parse_qsl,urlencode
from urllib.request import Request,build_opener,HTTPRedirectHandler
from contact_routes import ROUTES,route_card

ALLOWED={'www.epeople.go.kr','www.ktr.or.kr','cs.ktr.or.kr','www.nier.go.kr'}

def stamp():return datetime.now(timezone.utc).isoformat()
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def clean_url(url):
    p=urlsplit(url)
    query='' if 'Logn' in p.path or 'login' in p.path.lower() else urlencode(sorted((k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith(('utm_','jsession','tx','token'))))
    return urlunsplit((p.scheme,p.netloc,p.path or '/',query,''))

def validate_url(url,resolve=False):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in ALLOWED or p.username or p.password or p.port not in (None,443):
        raise ValueError('Approved HTTPS official hosts only')
    if resolve:
        for addr in socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(addr[4][0]).is_global:raise ValueError('Non-public destination rejected')
    return url

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl,True)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

class Page(HTMLParser):
    def __init__(self,html):
        super().__init__();self.links=[];self.text=[];self.skip=0;self.feed(html)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('script','style'):self.skip+=1
        if tag=='a' and a.get('href'):self.links.append(unescape(a['href']))
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.skip=max(0,self.skip-1)
    def handle_data(self,text):
        if not self.skip:self.text.append(text)
    def plain(self):return ' '.join(' '.join(self.text).split())

def fetch(url):
    validate_url(url,True)
    req=Request(url,headers={'User-Agent':'WasteCase-RouteCheck/0.4 (public navigation verification)'})
    with build_opener(SafeRedirect()).open(req,timeout=18) as r:
        data=r.read(2*1024*1024+1)
        if len(data)>2*1024*1024:raise ValueError('Page exceeds 2MiB')
        charset=r.headers.get_content_charset() or 'utf-8'
        return dict(status=r.status,url=clean_url(r.url),html=data.decode(charset,errors='replace'))

def observe(route_id,fetcher=fetch):
    r=ROUTES[route_id];out=dict(route_id=route_id,checked_at=stamp(),catalog_hash=digest(r),scope='HTTP public link + destination boundary + procedure text; NOT browser replay',result='unknown')
    try:
        source=fetcher(r['source_url']);target=fetcher(r['submission_url']);procedure=fetcher(r['procedure_url'])
        if any(p['status']!=200 for p in (source,target,procedure)):
            out.update(result='http_error');return out
        links={clean_url(urljoin(r['source_url'],u)) for u in Page(source['html']).links if not u.startswith(('javascript:','#'))}
        present=clean_url(r['submission_url']) in links
        text=Page(target['html']).plain();final=clean_url(target['url'])
        if not present:state='route_needs_review'
        elif any(s in text for s in ('임시 점검','점검 중','서비스 점검')):state='maintenance'
        elif r['login_path'] and urlsplit(final).path==r['login_path']:state='login_required'
        elif all(s in text for s in r['target_markers']):state='public_page_only'
        else:state='target_needs_review'
        content=Page(procedure['html']).plain()
        start,end=('민원 처리 절차는?','알아두세요!') if route_id=='authority' else ('의뢰(택배)접수 절차','신청서 양식 다운로드')
        if start not in content or end not in content:
            out.update(result='procedure_needs_review',target_url=final);return out
        segment=content.split(start,1)[1].split(end,1)[0]
        shape=dict(source_link_present=present,submission=clean_url(r['submission_url']),destination=final,boundary=state)
        out.update(result=state,target_url=final,path_hash=digest(shape),procedure_hash=digest(segment),
                   source_hash=digest(source['html']),procedure_excerpt=segment[:2500])
    except Exception as e:
        # Do not persist redirect/session tokens, cookies or raw error URLs.
        out.update(result='unreachable',error_type=type(e).__name__)
    return out

def connection(path):
    from pathlib import Path
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(path);c.row_factory=sqlite3.Row
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='submissions'").fetchone():
        c.close();raise ValueError('경로 이력 DB는 폐기물 DB와 분리하세요.')
    c.executescript('''CREATE TABLE IF NOT EXISTS route_checks(id INTEGER PRIMARY KEY,route_id TEXT NOT NULL,checked_at TEXT NOT NULL,observation TEXT NOT NULL,decision TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS route_approvals(id INTEGER PRIMARY KEY,route_id TEXT NOT NULL,check_id INTEGER NOT NULL,reviewer TEXT NOT NULL,approved_at TEXT NOT NULL);''')
    return c

def record(path,observation):
    with closing(connection(path)) as c,c:
        row=c.execute('SELECT x.observation FROM route_approvals a JOIN route_checks x ON x.id=a.check_id WHERE a.route_id=? ORDER BY a.id DESC LIMIT 1',(observation['route_id'],)).fetchone()
        old=json.loads(row[0]) if row else None
        if observation['result'] not in ('maintenance','login_required','public_page_only'):decision='verification_failed'
        elif not old:decision='first_observation_needs_review'
        elif observation['catalog_hash']!=old['catalog_hash'] or observation.get('path_hash')!=old.get('path_hash'):decision='route_changed_needs_review'
        elif observation.get('procedure_hash')!=old.get('procedure_hash'):decision='procedure_changed_needs_review'
        else:decision='unchanged'
        cursor=c.execute('INSERT INTO route_checks(route_id,checked_at,observation,decision) VALUES(?,?,?,?)',
            (observation['route_id'],observation['checked_at'],json.dumps(observation,ensure_ascii=False),decision))
        return dict(check_id=cursor.lastrowid,decision=decision,observation=observation)

def approve(path,check_id,reviewer):
    if not reviewer.strip():raise ValueError('검토자 식별 필요')
    with closing(connection(path)) as c,c:
        row=c.execute('SELECT * FROM route_checks WHERE id=?',(check_id,)).fetchone()
        if not row:raise ValueError('검사 기록 없음')
        latest=c.execute('SELECT MAX(id) FROM route_checks WHERE route_id=?',(row['route_id'],)).fetchone()[0]
        if latest!=check_id:raise ValueError('최신 검사만 검토할 수 있습니다.')
        obs=json.loads(row['observation'])
        if obs['result'] not in ('maintenance','login_required','public_page_only'):raise ValueError('실패/불명확 관찰을 정상 경로로 승인할 수 없습니다.')
        if obs['catalog_hash']!=digest(ROUTES[row['route_id']]):raise ValueError('경로 정의가 바뀌었습니다. 재검사하세요.')
        c.execute('INSERT INTO route_approvals(route_id,check_id,reviewer,approved_at) VALUES(?,?,?,?)',(row['route_id'],check_id,reviewer.strip(),stamp()))

def status(route_id,registry=None,now=None):
    from pathlib import Path
    now=now or datetime.now(timezone.utc);r=route_card(route_id)
    observed=r['known_status'];checked=r['verified_on']+'T00:00:00+09:00';decision='bundled_browser_observation';version=1
    if registry and Path(registry).exists():
        with closing(sqlite3.connect(Path(registry).resolve().as_uri()+'?mode=ro',uri=True)) as c:
            row=c.execute('SELECT observation,decision FROM route_checks WHERE route_id=? ORDER BY id DESC LIMIT 1',(route_id,)).fetchone()
            if row:
                obs=json.loads(row[0]);observed=obs['result'];checked=obs['checked_at'];decision=row[1]
                if obs['catalog_hash']!=digest(ROUTES[route_id]):decision='catalog_changed_needs_review'
            approvals=c.execute('SELECT x.observation FROM route_approvals a JOIN route_checks x ON x.id=a.check_id WHERE a.route_id=? ORDER BY a.id',(route_id,)).fetchall()
            hashes=[json.loads(x[0]).get('path_hash') for x in approvals]
            version=max(1,sum(i==0 or v!=hashes[i-1] for i,v in enumerate(hashes)))
    stale=now-datetime.fromisoformat(checked)>timedelta(days=7)
    safe=not stale and observed=='login_required' and decision in ('unchanged','bundled_browser_observation')
    r.update(last_checked_at=(r['verified_on'] if decision=='bundled_browser_observation' else checked),monitor_result=observed,monitor_decision=decision,route_version=version,
             stale=stale,usable_submission_url=r['submission_url'] if safe else None,
             submission_note='로그인/인증 입구까지 확인; 실제 제출은 사용자 수행' if safe else '점검/변경/미확인 또는 유효기간 경과. 실제 접수 가능 여부를 재확인하세요.')
    return r

if __name__=='__main__':
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='action',required=True)
    a=s.add_parser('check');a.add_argument('--route',choices=[*ROUTES,'all'],default='all');a.add_argument('--registry',required=True)
    a=s.add_parser('approve');a.add_argument('--registry',required=True);a.add_argument('--check',type=int,required=True);a.add_argument('--reviewer',required=True)
    a=s.add_parser('show');a.add_argument('--route',choices=list(ROUTES),required=True);a.add_argument('--registry')
    args=p.parse_args()
    if args.action=='check':
        for rid in ROUTES if args.route=='all' else [args.route]:print(json.dumps(record(args.registry,observe(rid)),ensure_ascii=False,indent=2))
    elif args.action=='approve':approve(args.registry,args.check,args.reviewer);print('관찰 검토 기록 저장. 사이트 정의/사용자 사실은 자동 수정하지 않음.')
    else:print(json.dumps(status(args.route,args.registry),ensure_ascii=False,indent=2))
