#!/usr/bin/env python3
"""Loopback-only desktop web application; no credentials are sent to the browser."""
from __future__ import annotations
import argparse, base64, datetime, hashlib, json, os, secrets, sys, threading, urllib.parse, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from freight.core import Catalog, compare, batch, parse_orders, ValidationError
from freight.agent import configured, extract, explain, ModelError
from freight.reports import result_zip, html_report

BASE=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
STATE=Path(os.environ.get('FREIGHT_DATA_DIR',Path.home()/'.freight-agent'))
CATALOGS={};RESULTS={};LOCK=threading.RLock();TOKEN=secrets.token_urlsafe(32)

def load_env():
    candidates=[Path.cwd()/'.env',Path(sys.executable).parent/'.env'] if getattr(sys,'frozen',False) else [BASE/'.env']
    for p in candidates:
        if p.is_file():
            for line in p.read_text(encoding='utf-8').splitlines():
                if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                    k,v=line.split('=',1)
                    if k.strip() in ['DEEPSEEK_API_KEY','DEEPSEEK_MODEL','FREIGHT_PORT']:os.environ.setdefault(k.strip(),v.strip().strip('"\''))

def catalog(body):
    with LOCK:c=CATALOGS.get(body.get('catalog_id') or DEFAULT)
    if not c:raise ValidationError('报价版本不存在，请重新导入')
    return c

def remember(value):
    payload=json.dumps(value,ensure_ascii=False,sort_keys=True).encode()
    rid=hashlib.sha256(payload+secrets.token_bytes(8)).hexdigest()[:20]
    value['result_id']=rid;value['generated_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    STATE.mkdir(parents=True,exist_ok=True)
    tmp=STATE/(rid+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.replace(STATE/(rid+'.json'))
    with LOCK:RESULTS[rid]=value
    return value

def stored(rid):
    if len(rid)!=20 or any(c not in '0123456789abcdef' for c in rid):raise ValidationError('结果编号无效')
    with LOCK:value=RESULTS.get(rid)
    if value is None:
        p=STATE/(rid+'.json')
        if not p.is_file():raise ValidationError('结果不存在')
        value=json.loads(p.read_text())
    return value

def file_bytes(body):
    try:data=base64.b64decode(body['data'],validate=True)
    except (KeyError,ValueError):raise ValidationError('文件内容无效')
    name=Path(str(body.get('filename','upload.csv')).replace('\\','/')).name[:120]
    return data,name

class Handler(BaseHTTPRequestHandler):
    def log_message(self,format,*args):
        # Request bodies and API credentials are never logged.
        print(f'{self.command} {urllib.parse.urlsplit(self.path).path} {args[1] if len(args)>1 else ""}',flush=True)
    def send(self,data,ctype='application/json; charset=utf-8',status=200,filename=None):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer')
        if filename:self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+urllib.parse.quote(filename))
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
    def allowed(self):
        host=self.headers.get('Host','').split(':')[0]
        return host in ['127.0.0.1','localhost']
    def do_GET(self):
        if not self.allowed():return self.send({'error':'仅限本机访问'},status=403)
        parsed=urllib.parse.urlsplit(self.path);path=parsed.path
        try:
            if path=='/api/health':return self.send({'ok':True,'version':'1.0.0','model_configured':configured(),'model':os.environ.get('DEEPSEEK_MODEL','deepseek-chat'),'catalog':CATALOGS[DEFAULT].info(),'token':TOKEN})
            if path.startswith('/api/result/'):
                rid=path.split('/')[-1];return self.send(stored(rid))
            if path.startswith('/api/export/'):
                rid=path.split('/')[-1];r=stored(rid)
                if urllib.parse.parse_qs(parsed.query).get('format')==['html']:
                    if 'results' in r:raise ValidationError('批量结果请下载 ZIP')
                    return self.send(html_report(r),'text/html; charset=utf-8')
                return self.send(result_zip(r),'application/zip',filename='运输比价成果_'+rid+'.zip')
            if path=='/api/sample/orders':return self.send((BASE/'samples/模拟订单.csv').read_bytes(),'text/csv; charset=utf-8',filename='模拟订单.csv')
            if path=='/api/sample/rates':return self.send((BASE/'samples/FreightRates.csv').read_bytes(),'text/csv; charset=utf-8',filename='FreightRates.csv')
            files={'/':'index.html','/style.css':'style.css','/app.js':'app.js'}
            if path in files:
                ext=Path(files[path]).suffix;return self.send((BASE/'static'/files[path]).read_bytes(),{'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'application/javascript; charset=utf-8'}[ext])
            self.send({'error':'资源不存在'},status=404)
        except ValidationError as e:self.send({'error':str(e)},status=400)
        except Exception:self.send({'error':'读取失败，请检查文件是否完整'},status=500)
    def do_POST(self):
        if not self.allowed() or self.headers.get('X-Freight-Token')!=TOKEN:return self.send({'error':'请求未授权，请刷新本机页面'},status=403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=12*1024*1024:raise ValidationError('请求为空或超过 12 MB')
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict):raise ValidationError('请求须为 JSON 对象')
            if self.path=='/api/rates/upload':
                data,name=file_bytes(body);c=Catalog.load(data,name)
                with LOCK:CATALOGS[c.id]=c
                return self.send(c.info())
            if self.path=='/api/parse':return self.send(extract(body.get('text')))
            if self.path=='/api/compare':return self.send(remember(compare(catalog(body),body.get('order',{}))))
            if self.path=='/api/batch':
                data,name=file_bytes(body);return self.send(remember(batch(catalog(body),parse_orders(data,name))))
            if self.path=='/api/explain':
                r=stored(body.get('result_id',''))
                if 'results' in r:raise ValidationError('AI 简报当前支持单笔订单')
                value=explain(r);r['ai_explanation']=value
                (STATE/(r['result_id']+'.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2))
                return self.send(value)
            self.send({'error':'接口不存在'},status=404)
        except (ValidationError,ValueError,TypeError) as e:self.send({'error':str(e)},status=400)
        except ModelError as e:self.send({'error':str(e),'fallback':'offline_rules'},status=502)
        except Exception as e:
            print('Request failed:',type(e).__name__,flush=True)
            self.send({'error':'处理失败，请检查输入文件。核心计算可重试。'},status=500)

def main():
    global DEFAULT
    if sys.stdout and hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    if sys.stderr and hasattr(sys.stderr,'reconfigure'):sys.stderr.reconfigure(encoding='utf-8',errors='replace')
    load_env();parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=int(os.environ.get('FREIGHT_PORT','8899')));parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    c=Catalog.load((BASE/'samples/FreightRates.csv').read_bytes(),'FreightRates.csv');DEFAULT=c.id;CATALOGS[c.id]=c
    try:server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    except OSError as e:
        import errno
        print(f'端口 {args.port} 被占用，请用 --port 8900 重试' if e.errno==errno.EADDRINUSE else f'无法启动本机服务：{e}');return 1
    print(f'运筹 FreightPilot v1.0.0 — http://127.0.0.1:{args.port}',flush=True)
    if not args.no_browser:threading.Timer(.6,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
    return 0

if __name__=='__main__':sys.exit(main())
