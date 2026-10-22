"""Offline evaluation of actual local profiles against controlled synthetic events.
No API calls. No claim that engineering checks are a blind expert benchmark.
"""
import json
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.server import App
from backend.config import Config

with tempfile.TemporaryDirectory(dir=ROOT/'tmp',prefix='evaluation-') as folder:
    app=App(ROOT,Path(folder)/'evaluation.sqlite',Config(mode='test'))
    companies=app.agent.companies()
    results=[]
    rankings=[]
    for c in companies:
        for role in ('ceo','cfo','treasury'):
            feed=app.agent.feed(c['id'],role)
            rankings.append({'company':c['id'],'role':role,'ranking':[{'id':n['id'],'score':n['score']} for n in feed]})
            for event in feed:
                run=app.agent.analyze(c['id'],event['id'],role)
                evidence={e['id']:e for e in run['evidence']}
                citation_ok=all(set(claim['evidence_ids'])<=set(evidence) for kind in ('facts','bank_view','interpretation') for claim in run[kind])
                quotations_ok=all(any(claim['text'] in evidence[ref]['text'] for ref in claim['evidence_ids']) for claim in run['bank_view'])
                results.append({'company':c['id'],'event':event['id'],'role':role,'duration_ms':run['metrics']['duration_ms'],
                    'valid_citations':citation_ok,'bank_quotes_match':quotations_ok,'bank_present':bool(run['bank_view']),
                    'adaptive':any(t['tool']=='adaptive_search' for t in run['trace'])})
    times=sorted(r['duration_ms'] for r in results)
    output={'checked_at':datetime.now(timezone.utc).isoformat(),'mode':'test','profiles':len(companies),'runs':len(results),
        'valid_citation_runs':sum(r['valid_citations'] for r in results),'exact_bank_quote_runs':sum(r['bank_quotes_match'] for r in results),
        'runs_with_bank':sum(r['bank_present'] for r in results),'adaptive_runs':sum(r['adaptive'] for r in results),
        'latency_ms':{'median':statistics.median(times),'max':max(times),'p95':times[max(0,int(len(times)*.95)-1)]},
        'api_requests':0,'api_tokens':0,'cost_rub':None,'rankings':rankings,'results':results,
        'limitations':['Controlled synthetic news, only three provided company profiles.',
            'Citation completeness and exact quotes do not establish semantic correctness.',
            'Expert relevance/precision@k and live model quality not measured.']}
    (ROOT/'reports/evaluation.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in output.items() if k not in {'results','rankings'}},ensure_ascii=False,indent=2))
