"""Single command HTTP application. Local demo or explicit bearer token + scope."""
import argparse
import hmac
import json
import mimetypes
import os
import re
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from .agent import Agent, AnalysisError
from .config import Config
from .llm import LiveError
from .sources import classify, clean_text, fetch_cbr, read_array, valid_url
from .store import Store

ROOT=Path(__file__).resolve().parent.parent

class ApiError(Exception):
    def __init__(self,status,message):self.status=status;self.message=message

class App:
    def __init__(self,root=ROOT,db_path=None,config=None):
        self.root=Path(root).resolve();self.config=config or Config.from_env()
        self.store=Store(db_path or self.root/'runtime/app.sqlite')
        self.agent=Agent(self.root,self.store,self.config)
        for n in read_array(self.root/'data/synthetic/news.json','news'):self.store.add_news(n)
        self.scope=None if self.config.company_scope=='*' else set(self.config.company_scope.split(','))
        self.run_semaphore=threading.BoundedSemaphore(4)
        self.refresh_lock=threading.Lock();self.last_refresh=0

    def authorize_company(self,company_id):
        if not isinstance(company_id,str) or not company_id or len(company_id)>200:raise ApiError(400,'Некорректный ID компании')
        if self.scope is not None and company_id not in self.scope:raise ApiError(403,'Компания вне области доступа')

    def dispatch(self,method,path,query,body):
        if method=='GET' and path=='/api/health':
            verified=False
            try:
                marker=json.loads((self.root/'runtime/llm_probe.json').read_text())
                verified=bool(marker.get('success') and marker.get('endpoint')==self.config.endpoint and marker.get('model_id')==self.config.model_id)
            except (OSError,ValueError):pass
            return {'status':'ok','mode':self.config.mode,'live_ready':not bool(self.config.blockers()),'blockers':self.config.blockers(),
                'auth_required':bool(self.config.auth_token),'data_mode':self.config.data_mode,'live_verified':verified,
                'provider':self.config.provider_name,
                'access_mode':self.config.access_mode,'model_id':self.config.model_id,
                'free_max_requests_24h':self.config.free_max_requests_24h,'free_max_tokens_24h':self.config.free_max_tokens_24h,
                'disclosure':'Тестовый режим не использует LLM' if self.config.mode=='test' else 'Живой режим требует подтвержденной настройки'}
        if method=='GET' and path=='/api/companies':
            return {'companies':[c for c in self.agent.companies() if self.scope is None or c['id'] in self.scope]}
        if method=='GET' and path=='/api/feed':
            company_id=query.get('company_id',[''])[0];self.authorize_company(company_id)
            return {'items':self.agent.feed(company_id,query.get('role',['ceo'])[0]),'mode':self.config.mode}
        if method=='POST' and path=='/api/analyze':
            for field in ('event_id','role','parent_run_id'):
                value=body.get(field)
                if value is not None and (not isinstance(value,str) or len(value)>200):raise ApiError(400,'Некорректный параметр '+field)
            company_id=body.get('company_id','');self.authorize_company(company_id)
            if not self.run_semaphore.acquire(blocking=False):raise ApiError(429,'Одновременно выполняется максимум 4 разбора')
            try:return self.agent.analyze(company_id,body.get('event_id',''),body.get('role','ceo'),body.get('question',''),body.get('parent_run_id'))
            finally:self.run_semaphore.release()
        if method=='GET' and path.startswith('/api/runs/'):
            run=self.store.get_run(path.removeprefix('/api/runs/'))
            if not run:raise ApiError(404,'Разбор не найден')
            self.authorize_company(run['company_id']);return run
        if method=='POST' and path=='/api/news':
            if not all(isinstance(body.get(k),str) for k in ('title','text','date')):raise ApiError(400,'Нужны текстовые title, text и date')
            title=clean_text(body['title']);text=clean_text(body['text']);url=body.get('url') or ''
            if not 4<=len(title)<=500 or not 10<=len(text)<=20000:raise ApiError(400,'Заголовок: 4–500, текст: 10–20000 символов')
            try:date.fromisoformat(body['date'])
            except (ValueError,TypeError):raise ApiError(400,'Дата должна иметь формат YYYY-MM-DD') from None
            if url and not valid_url(url):raise ApiError(400,'Некорректная ссылка источника')
            item,created=self.store.add_news({'title':title,'text':text,'summary':text[:260],'date':body['date'],'url':url,
                'category':classify(title+' '+text),'source':'Ручной ввод · содержание не проверено','synthetic':bool(body.get('synthetic',False)),'verification':'user_supplied'})
            return {'item':item,'created':created}
        if method=='POST' and path=='/api/refresh':
            if body.get('source')!='cbr':raise ApiError(400,'Разрешен только источник cbr')
            if not self.refresh_lock.acquire(blocking=False):raise ApiError(429,'Загрузка уже выполняется')
            try:
                if time.monotonic()-self.last_refresh<30:raise ApiError(429,'Повторите загрузку через 30 секунд')
                self.last_refresh=time.monotonic()
                try:items=fetch_cbr()
                except Exception:raise ApiError(502,'Источник Банка России сейчас недоступен или формат не прошел проверку') from None
                imported=sum(self.store.add_news(n)[1] for n in items)
                return {'source':'cbr','received':len(items),'imported':imported,'duplicates':len(items)-imported}
            finally:self.refresh_lock.release()
        if method=='GET' and path=='/api/metrics':return self.store.metrics(self.scope)
        if method=='POST' and path=='/api/feedback':
            if not isinstance(body.get('run_id'),str):raise ApiError(400,'Некорректный ID разбора')
            run=self.store.get_run(body.get('run_id',''))
            if not run:raise ApiError(404,'Разбор не найден')
            self.authorize_company(run['company_id'])
            rating=body.get('rating');comment=body.get('comment','')
            if type(rating) is not int or not 1<=rating<=5 or not isinstance(comment,str) or len(comment)>2000:raise ApiError(400,'Оценка 1–5, комментарий до 2000 символов')
            self.store.feedback(run['id'],rating,comment);return {'saved':True}
        raise ApiError(404,'Маршрут не найден')

def make_handler(app,host='127.0.0.1'):
    class Handler(BaseHTTPRequestHandler):
        server_version='NewsAnalytics/1.0'
        protocol_version='HTTP/1.1'
        def setup(self):
            super().setup();self.connection.settimeout(40)
        def log_message(self,format,*args):
            # Request paths and error bodies may contain untrusted content. Log only method/status.
            pass
        def reply(self,status,payload,content_type='application/json; charset=utf-8'):
            if isinstance(payload,(dict,list)):payload=json.dumps(payload,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            try:self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_GET(self):self.handle_request('GET')
        def do_POST(self):self.handle_request('POST')
        def do_OPTIONS(self):self.reply(405,{'error':'CORS не разрешен'})
        def handle_request(self,method):
            try:
                raw_host=self.headers.get('Host','')
                hostname=urlsplit('//'+raw_host).hostname
                allowed={'localhost','127.0.0.1','::1',host}
                if host in {'0.0.0.0','::'}:
                    # Remote bind requires a token at startup. Host may be configured explicitly.
                    allowed.update(os.getenv('APP_ALLOWED_HOSTS','').split(','))
                if hostname not in allowed:raise ApiError(403,'Host не разрешен; настройте APP_ALLOWED_HOSTS для внешнего адреса')
                if len(self.path)>4096:raise ApiError(414,'Слишком длинный URL')
                parsed=urlsplit(self.path);path=unquote(parsed.path)
                protected=path.startswith('/api/') and path!='/api/health' or path.startswith('/sources/')
                if protected and app.config.auth_token:
                    token=self.headers.get('Authorization','')
                    # Source PDFs may be opened in a new tab using HttpOnly session cookie set by authenticated API.
                    cookie=self.headers.get('Cookie','')
                    session=next((v.partition('=')[2] for v in cookie.split('; ') if v.startswith('alfa_demo=')), '')
                    expected_session=hmac.digest(app.config.auth_token.encode(),b'local-pdf-session','sha256').hex()
                    if not hmac.compare_digest(token,'Bearer '+app.config.auth_token) and not (path.startswith('/sources/') and hmac.compare_digest(session,expected_session)):
                        raise ApiError(401,'Нужен демонстрационный токен доступа')
                body={}
                if method=='POST':
                    origin=self.headers.get('Origin')
                    if origin and origin not in {'http://'+raw_host,'https://'+raw_host}:raise ApiError(403,'Origin не разрешен')
                    if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ApiError(415,'Требуется application/json')
                    if self.headers.get('Transfer-Encoding'):raise ApiError(400,'Transfer-Encoding не поддерживается')
                    try:length=int(self.headers.get('Content-Length','0'))
                    except ValueError:raise ApiError(400,'Некорректная длина тела') from None
                    if not 0<length<=64000:raise ApiError(413,'Размер JSON должен быть от 1 до 64000 байт')
                    try:body=json.loads(self.rfile.read(length))
                    except (ValueError,UnicodeError):raise ApiError(400,'Некорректный JSON') from None
                    if not isinstance(body,dict):raise ApiError(400,'Ожидается объект JSON')
                if path.startswith('/api/'):
                    result=app.dispatch(method,path,parse_qs(parsed.query),body)
                    # Explicit session issue lets browser open protected PDFs without URL tokens.
                    if method=='GET' and path=='/api/companies' and app.config.auth_token:
                        payload=json.dumps(result,ensure_ascii=False).encode();self.send_response(200)
                        session=hmac.digest(app.config.auth_token.encode(),b'local-pdf-session','sha256').hex()
                        self.send_header('Set-Cookie','alfa_demo='+session+'; HttpOnly; SameSite=Strict; Path=/sources/; Max-Age=3600')
                        self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                    else:self.reply(200,result)
                    return
                if method!='GET':raise ApiError(405,'Метод не поддерживается')
                base=(app.root/'data/raw/analytics') if path.startswith('/sources/') else (app.root/'frontend')
                relative=path.removeprefix('/sources/') if path.startswith('/sources/') else path.lstrip('/') or 'index.html'
                target=(base/relative).resolve()
                if not target.is_relative_to(base.resolve()) or not target.is_file():raise ApiError(404,'Файл не найден')
                if path.startswith('/sources/') and target.suffix.lower()!='.pdf':raise ApiError(404,'Источник не найден')
                if target.stat().st_size>80_000_000:raise ApiError(413,'Файл слишком большой')
                self.reply(200,target.read_bytes(),mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            except ApiError as exc:
                self.close_connection=True;self.reply(exc.status,{'error':exc.message})
            except AnalysisError as exc:self.reply(400,{'error':str(exc)})
            except LiveError as exc:self.reply(429 if exc.error_code in {'provider_rate_limit','local_limit'} else 503,{'error':str(exc),'error_code':exc.error_code,'retry_after':exc.retry_after,'mode':app.config.mode})
            except Exception:self.reply(500,{'error':'Внутренняя ошибка. Подробности и секреты не передаются клиенту.'})
    return Handler

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args();config=Config.from_env()
    if args.host not in {'127.0.0.1','localhost','::1'} and not config.auth_token:parser.error('Для внешнего bind требуется APP_TOKEN')
    if config.mode not in {'test','live'} or config.data_mode not in {'auto','synthetic'}:parser.error('Неверный APP_MODE или APP_DATA_MODE')
    db_path=Path(os.getenv('APP_DB_PATH',str(ROOT/'runtime/app.sqlite')))
    if not db_path.is_absolute():db_path=ROOT/db_path
    if not db_path.resolve().is_relative_to(ROOT):parser.error('APP_DB_PATH должен находиться внутри проекта')
    app=App(db_path=db_path,config=config)
    server=ThreadingHTTPServer((args.host,args.port),make_handler(app,args.host))
    print(f'Новостная аналитика: http://{args.host}:{args.port}; режим {config.mode}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
