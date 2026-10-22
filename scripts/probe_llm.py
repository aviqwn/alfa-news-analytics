"""Explicit one-call connectivity probe. Never prints completion or reasoning."""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run import load_env
from backend.config import Config
from backend.llm import LiveError, request_completion
from backend.store import Store


def probe(config,store,marker,force=False):
    marker=Path(marker)
    if marker.exists() and not force:
        try:previous=json.loads(marker.read_text())
        except (ValueError,OSError):previous={}
        if previous.get('success') and previous.get('endpoint')==config.endpoint and previous.get('model_id')==config.model_id:
            return previous,True
    # Reserve the marker lock atomically to prevent concurrent one-call probes.
    marker.parent.mkdir(parents=True,exist_ok=True)
    lock=marker.with_suffix('.lock')
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise LiveError('Проверка уже выполняется; повтор не отправлен','probe_in_progress') from None
    os.close(fd)
    endpoint_parts=urlsplit(config.endpoint)
    safe_endpoint=config.endpoint if endpoint_parts.scheme=='https' and endpoint_parts.hostname and not (endpoint_parts.username or endpoint_parts.password or endpoint_parts.query or endpoint_parts.fragment) else None
    safe_model=config.model_id
    if config.api_key:
        if safe_endpoint and config.api_key in safe_endpoint:safe_endpoint=None
        if config.api_key in safe_model:safe_model=None
    result={'success':False,'date':datetime.now(timezone.utc).isoformat(),'endpoint':safe_endpoint,
        'model_id':safe_model,'usage':None,'latency_ms':0,'error_code':None}
    start=time.monotonic()
    try:
        # Read once more after acquiring the lock, closing a concurrent completion race.
        if marker.exists() and not force:
            try:previous=json.loads(marker.read_text())
            except (ValueError,OSError):previous={}
            if previous.get('success') and previous.get('endpoint')==config.endpoint and previous.get('model_id')==config.model_id:
                return previous,True
        try:
            completion,usage=request_completion(config,store,[{'role':'system','content':'Reply exactly OK'},
                {'role':'user','content':'Reply exactly OK'}],max_output_tokens=128,json_mode=False)
            result['usage']=usage
            result['success']=completion.strip()=='OK'
            if not result['success']:result['error_code']='probe_output_mismatch'
        except LiveError as exc:
            result['error_code']=exc.error_code
            result['usage']=exc.usage
        result['latency_ms']=round((time.monotonic()-start)*1000)
        temporary=marker.with_suffix('.pending')
        temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        os.replace(temporary,marker)
        return result,False
    finally:lock.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description='Один безопасный запрос проверки LLM без данных компании')
    parser.add_argument('--force',action='store_true',help='Явно разрешить повтор после успешной проверки')
    args=parser.parse_args()
    load_env(ROOT/'.env')
    try:
        config=Config.from_env()
        db=Path(os.getenv('APP_DB_PATH',str(ROOT/'runtime/app.sqlite')))
        if not db.is_absolute():db=ROOT/db
        if not db.resolve().is_relative_to(ROOT):raise LiveError('Путь БД должен быть внутри проекта','invalid_db_path')
        result,cached=probe(config,Store(db),ROOT/'runtime/llm_probe.json',args.force)
        print(json.dumps({'success':result['success'],'skipped_existing_success':cached,
            'error_code':result['error_code'],'usage':result['usage'],'latency_ms':result['latency_ms']},ensure_ascii=False))
        return 0 if result['success'] else 1
    except (LiveError,ValueError) as exc:
        print(json.dumps({'success':False,'error_code':getattr(exc,'error_code','invalid_configuration')},ensure_ascii=False))
        return 1

if __name__=='__main__':raise SystemExit(main())
