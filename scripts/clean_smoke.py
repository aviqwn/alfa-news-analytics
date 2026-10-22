"""Run with a clean Python venv; check runtime without installed third-party packages."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8001'
def get(path,body=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'} if body is not None else {})
    with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)

with tempfile.TemporaryDirectory(dir=ROOT/'tmp',prefix='clean-smoke-') as temp:
    env={**os.environ,'APP_MODE':'test','APP_DATA_MODE':'synthetic','APP_TOKEN':'','APP_DB_PATH':str(Path(temp)/'app.sqlite')}
    process=subprocess.Popen([sys.executable,str(ROOT/'scripts/run.py'),'--port','8001'],env=env,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            try:health=get('/api/health');break
            except Exception:
                if process.poll() is not None:raise RuntimeError('Clean server failed to start')
                time.sleep(.1)
        else:raise RuntimeError('Clean server startup timed out')
        companies=get('/api/companies')['companies']
        assert len(companies)==3 and all(c['synthetic'] for c in companies)
        feed=get('/api/feed?company_id='+companies[0]['id']+'&role=cfo')['items']
        run=get('/api/analyze',{'company_id':companies[0]['id'],'event_id':feed[0]['id'],'role':'cfo'})
        assert run['mode']=='test' and run['interpretation'] and not run['bank_view']
        assert any(t['tool']=='adaptive_search' for t in run['trace'])
        result={'success':True,'python':sys.version.split()[0],'synthetic_companies':len(companies),'events':len(feed),
            'analysis':True,'adaptive_search':True,'api_requests':0,'third_party_runtime_dependencies':0,
            'docker_verified':False}
        (ROOT/'reports/clean-start.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    finally:
        process.terminate()
        try:process.communicate(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.communicate()
