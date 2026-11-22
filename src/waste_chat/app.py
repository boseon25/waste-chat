"""Python 3.10+ · VS Code 터미널에서 python app.py"""
import argparse
import base64
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, quote, urlsplit

from services import envfile

envfile.load()  # .env의 TIMELYGPT_API_KEY를 다른 모듈보다 먼저 읽는다.

from services import catalog, conversation, directory, doc_agent, pdf_writer, platform_api, store, timely

PDF_LOCK = threading.Lock()
platform_api.PDF_LOCK = PDF_LOCK
doc_agent.PDF_LOCK = PDF_LOCK
HTML = "text/html; charset=utf-8"
CSS = "text/css; charset=utf-8"
JAVASCRIPT = "application/javascript; charset=utf-8"
STATIC = {
    "/": ("platform/app.html", HTML),
    "/workspace": ("index.html", HTML),
    "/platform/app.css": ("platform/app.css", CSS),
    "/platform/app.js": ("platform/app.js", JAVASCRIPT),
    "/vendor/leaflet/leaflet.js": ("vendor/leaflet/leaflet.js", JAVASCRIPT),
    "/vendor/leaflet/leaflet.css": ("vendor/leaflet/leaflet.css", CSS),
    "/finish": ("finish.html", HTML),
    "/finish.html": ("finish.html", HTML),
    "/style.css": ("style.css", CSS),
    "/workspace.js": ("workspace.js", JAVASCRIPT),
    "/ui/app.js": ("ui/app.js", JAVASCRIPT),
    "/ui/app.css": ("ui/app.css", CSS),
    "/ui/finish.js": ("ui/finish.js", JAVASCRIPT),
    "/ui/finish.css": ("ui/finish.css", CSS),
}


LOCAL_HOSTS = {"127.0.0.1", "localhost"}   # --lan이면 이 컴퓨터의 네트워크 IP가 추가된다.
EXTRA_HOSTS = set()                         # --allow-host로 받은 외부 주소(터널 등)


def lan_address():
    """같은 네트워크에서 이 컴퓨터를 가리키는 IP. 실제로 패킷을 보내지는 않는다."""
    probe=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1",9))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def find_tunnel_program():
    """cloudflared 실행 파일. PATH, 프로젝트의 tools/bin, 프로젝트 옆 .tools 폴더 순으로 찾는다."""
    found=shutil.which("cloudflared")
    if found: return found
    for folder in (catalog.ROOT/"tools"/"bin", catalog.ROOT.parent/".tools"):
        if (folder/"cloudflared").is_file(): return str(folder/"cloudflared")
    return None


def open_tunnel(program,port):
    """임시 공개 주소를 만든다. 이 서버가 꺼지면 터널도 함께 닫힌다."""
    proc=subprocess.Popen([program,"tunnel","--no-autoupdate","--url",f"http://127.0.0.1:{port}"],
                          stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    def watch():
        announced=False
        for line in proc.stderr:
            found=re.search(r"https://([a-z0-9-]+\.trycloudflare\.com)",line)
            if found and not announced:
                announced=True
                EXTRA_HOSTS.add(found.group(1))
                print(f"\n다른 사람에게 알려줄 주소   {found.group(0)}\n(이 창을 닫거나 Ctrl+C를 누르면 주소도 사라집니다)",flush=True)
        if not announced:
            print("\n공개 주소를 만들지 못했습니다. 인터넷 연결을 확인하고 다시 실행하세요.",flush=True)
    threading.Thread(target=watch,daemon=True).start()
    return proc


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_bytes(self, status, body, content_type, **headers):
        self.send_response(status)
        self.send_header("Content-Type",content_type)
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("X-Frame-Options","SAMEORIGIN")
        for name,value in headers.items():
            self.send_header(name,str(value))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self,status,out):
        self.send_bytes(status,json.dumps(out,ensure_ascii=False).encode(),"application/json; charset=utf-8")

    def local_request(self):
        """허용한 주소로 들어온 같은 출처 요청만 받는다. 기본은 이 컴퓨터, --lan이면 같은 네트워크의 내 IP까지."""
        host=self.headers.get("Host","")
        name,_,port=host.rpartition(":") if ":" in host else (host,"","")
        here=name in LOCAL_HOSTS and port==str(self.server.server_port)
        if (here or host in EXTRA_HOSTS) and self.headers.get("Origin") in (None,"http://"+host,"https://"+host):
            return True
        self.send_json(403,{"error":"이 프로그램을 실행한 주소로 다시 열어 주세요."})
        return False

    def session_token(self):
        cookies=SimpleCookie(self.headers.get("Cookie",""))
        return cookies["sid"].value if "sid" in cookies else None

    def platform(self,method,path,data):
        out=platform_api.handle(method,path.removeprefix("/api/p/"),data,self.session_token())
        headers=dict(out.get("headers",{}))
        if "cookie" in out:
            age="" if out["cookie"] else "; Max-Age=0"
            headers["Set-Cookie"]=f"sid={out['cookie']}; Path=/; HttpOnly; SameSite=Strict{age}"
        if "bytes" in out:
            self.send_bytes(out["status"],out["bytes"],out["type"],**headers)
        else:
            self.send_bytes(out["status"],json.dumps(out["json"],ensure_ascii=False).encode(),"application/json; charset=utf-8",**headers)

    def do_GET(self):
        if not self.local_request(): return
        url=urlsplit(self.path)
        path=url.path
        try:
            if path.startswith("/api/p/"):
                self.platform("GET",path,dict(parse_qsl(url.query)))
            elif path in STATIC:
                name,typ=STATIC[path]
                self.send_bytes(200,(catalog.ROOT / "web" / name).read_bytes(),typ)
            elif path=="/api/config":
                self.send_json(200,{"documents":list(catalog.DOCS.values()),"stages":catalog.STAGES,"contexts":catalog.CONTEXTS,
                                    "default_model":envfile.model() or timely.MODEL,"has_env_key":bool(envfile.api_key())})
            elif path.startswith("/api/blank/"):
                doc=catalog.DOCS.get(path.removeprefix("/api/blank/"),{})
                if not doc.get("file"):
                    self.send_json(404,{"error":"이 항목에는 배포용 원본 PDF가 없습니다."}); return
                f=catalog.ROOT / doc["file"]
                self.send_bytes(200,f.read_bytes(),"application/pdf",
                                **{"Content-Disposition":"inline; filename*=UTF-8''"+quote(f.name)})
            else:
                self.send_json(404,{"error":"지원하지 않는 주소입니다."})
        except (BrokenPipeError,ConnectionResetError):
            return
        except OSError:
            self.send_json(404,{"error":"파일을 찾지 못했습니다. 프로젝트 ZIP을 전부 풀어 주세요."})

    def do_POST(self):
        if not self.local_request(): return
        path=urlsplit(self.path).path
        if path.startswith("/api/p/"):
            if self.headers.get_content_type()!="application/json":
                self.send_json(415,{"error":"JSON 형식이 필요합니다."}); return
            try:
                size=int(self.headers.get("Content-Length","0"))
                if not 0<size<=8*1024*1024: raise ValueError
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict): raise ValueError
            except ValueError:
                self.send_json(400,{"error":"요청 내용을 확인하세요."}); return
            try:
                self.platform("POST",path,data)
            except (BrokenPipeError,ConnectionResetError): return
            except Exception:
                logging.exception("platform request failed")
                self.send_json(500,{"error":"요청을 처리하지 못했습니다."})
            return
        if path not in {"/api/chat","/api/pdf","/api/finish/preview","/api/finish/export"}:
            self.send_json(404,{"error":"지원하지 않는 주소입니다."}); return
        if self.headers.get_content_type()!="application/json":
            self.send_json(415,{"error":"JSON 형식이 필요합니다."}); return
        try:
            limit=7*1024*1024 if path.startswith("/api/finish/") else 256_000
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=limit: raise ValueError("요청 크기를 확인하세요.")
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict): raise ValueError("입력 자료 형식을 확인하세요.")
            if path.startswith("/api/finish/"):
                from services import finish
                with PDF_LOCK:
                    if path=="/api/finish/preview":
                        out,n,w,h=finish.preview_info(data)
                        headers={"X-Pdf-Pages":n,"X-Pdf-Width":w,"X-Pdf-Height":h}
                        self.send_bytes(200,out,"image/png",**headers)
                    else:
                        self.send_bytes(200,finish.export(data),"application/pdf")
                return
            doc_id=data.get("doc")
            if not isinstance(doc_id,str) or doc_id not in catalog.DOCS:
                raise ValueError("현재 화면에서 서식을 선택하세요.")
            doc=catalog.DOCS[doc_id]
            if path=="/api/chat":
                key=data.get("key") or envfile.api_key()
                data.setdefault("model",envfile.model())
                self.send_json(200,conversation.update(doc,data,key))
            else:
                labels={f["key"]:f["label"] for f in doc["fields"]}
                if not labels: raise ValueError("이 항목은 발급 원본 또는 공식 입력 경로를 사용하세요.")
                values=conversation.clean_values(data.get("values",{}),labels)
                with PDF_LOCK:
                    if data.get("include_fields") is True:
                        out,regions=pdf_writer.render_with_regions(doc,values)
                    else:
                        out=pdf_writer.render_document(doc,values)
                        regions=None
                if regions is None:
                    self.send_bytes(200,out,"application/pdf")
                else:
                    self.send_json(200,{"pdf":base64.b64encode(out).decode("ascii"),"regions":regions})
        except (BrokenPipeError,ConnectionResetError): return
        except (ValueError,TypeError) as e:
            self.send_json(400,{"error":str(e)})
        except Exception:
            self.send_json(500,{"error":"요청을 처리하지 못했습니다. 설치 상태와 원본 파일을 확인하세요."})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port",type=int,default=0)
    parser.add_argument("--no-browser",action="store_true")
    parser.add_argument("--lan",action="store_true",help="같은 와이파이·사내망의 다른 컴퓨터에서도 접속할 수 있게 연다")
    parser.add_argument("--allow-host",action="append",default=[],metavar="주소",
                        help="터널 등 외부 주소로 들어오는 접속을 허용한다 (예: abc.trycloudflare.com)")
    parser.add_argument("--public",action="store_true",help="임시 공개 주소를 만들어 어느 네트워크에서나 접속할 수 있게 한다 (cloudflared 필요)")
    opts=parser.parse_args()
    program=find_tunnel_program() if opts.public else None
    if opts.public and not program:
        raise SystemExit("--public에는 cloudflared가 필요합니다. README의 ‘어느 네트워크에서나 접속하게 하기’를 따라 설치한 뒤 다시 실행하세요.")
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    store.db()
    directory.seed_demo()
    EXTRA_HOSTS.update(h.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/") for h in opts.allow_host)
    shared=None
    if opts.lan:
        shared=lan_address()
        if shared: LOCAL_HOSTS.add(shared)
        if not opts.port: opts.port=8080  # 다른 사람에게 알려줄 주소가 실행할 때마다 바뀌지 않게 한다.
    with ThreadingHTTPServer(("0.0.0.0" if opts.lan else "127.0.0.1",opts.port),Handler) as server:
        url=f"http://127.0.0.1:{server.server_port}"
        if opts.lan:
            url+=(f"\n같은 네트워크의 다른 컴퓨터에서는   http://{shared}:{server.server_port}" if shared
                  else "\n네트워크 주소를 찾지 못했습니다. 와이파이 연결을 확인하세요.")
        for h in sorted(EXTRA_HOSTS): url+=f"\n외부 주소 허용   https://{h}"
        ai="AI 연결됨 (.env)" if envfile.api_key() else "AI 키 없음 · 기본 안내로 동작 (.env에 TIMELYGPT_API_KEY)"
        print(f"폐기물 순환 거래 작업실   {url}\n{ai}\n종료 시 Ctrl+C",flush=True)
        if not opts.no_browser: threading.Timer(.4,webbrowser.open,args=(url.split()[0],)).start()
        tunnel=open_tunnel(program,server.server_port) if program else None
        def stop(*_): raise KeyboardInterrupt  # 창을 닫거나 종료 신호를 받아도 터널을 함께 닫는다.
        for name in ("SIGTERM","SIGHUP"):
            if hasattr(signal,name): signal.signal(getattr(signal,name),stop)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally:
            if tunnel: tunnel.terminate()


if __name__=="__main__":
    main()
