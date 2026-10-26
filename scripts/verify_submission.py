"""Offline end-to-end check of a fresh checkout or the synthetic Docker service.

No external network, RSS refresh or LLM calls. External mode refuses other datasets.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def exercise(base, token):
    def call(path, payload=None):
        headers = {'Authorization': 'Bearer ' + token}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers['Content-Type'] = 'application/json'
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=10) as response:
            return json.load(response)

    health = call('/api/health')
    assert health['mode'] == 'test' and health['data_mode'] == 'synthetic', 'Only synthetic test service allowed'
    companies = call('/api/companies')['companies']
    assert {c['id'] for c in companies} == {'DEMO_TRADE_A', 'DEMO_TRADE_B', 'DEMO_BUILD'}
    assert all(c['synthetic'] for c in companies)
    feed_a = call('/api/feed?company_id=DEMO_TRADE_A&role=cfo')['items']
    feed_b = call('/api/feed?company_id=DEMO_TRADE_B&role=cfo')['items']
    score = lambda feed: next(x['score'] for x in feed if x['id'] == 'demo-rates')
    assert score(feed_a) > score(feed_b)
    args = {'company_id': 'DEMO_TRADE_A', 'event_id': 'demo-rates', 'role': 'cfo'}
    run = call('/api/analyze', args)
    assert run['interpretation'] and run['mode'] == 'test' and not run['bank_view']
    assert any(t['tool'] == 'adaptive_search' for t in run['trace'])
    again = call('/api/analyze', args)
    assert again['id'] == run['id'] and again['metrics']['cache_hit']
    followup = call('/api/analyze', {**args, 'question': 'Какие связи группы известны?', 'parent_run_id': run['id']})
    assert followup['answer'] and followup['parent_run_id'] == run['id']
    assert any(t['tool'] == 'load_memory' for t in followup['trace'])
    assert call('/api/runs/' + run['id'])['id'] == run['id']
    call('/api/feedback', {'run_id': run['id'], 'rating': 5, 'comment': 'Синтетическая проверка комплекта сдачи'})
    event = call('/api/news', {'title': 'Синтетический сценарий сдачи: спрос снизился',
        'text': 'В учебном сценарии спрос на оптовые товары снизился. Это не сообщение о реальном рынке.',
        'date': '2026-10-22', 'synthetic': True})['item']
    fresh = call('/api/analyze', {**args, 'event_id': event['id']})
    assert fresh['event_id'] == event['id'] and fresh['interpretation']
    return {'success': True, 'companies': len(companies), 'checks': [
        'synthetic_only', 'company_personalization', 'analysis', 'missing_bank_adaptation',
        'cache', 'followup_memory', 'saved_run', 'feedback', 'new_event'], 'llm_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', help='Existing loopback synthetic service, e.g. http://127.0.0.1:18080')
    parser.add_argument('--report', help='Optional JSON report path')
    args = parser.parse_args()
    if args.base_url:
        url = urlsplit(args.base_url)
        if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'} or url.username or url.password or url.query or url.fragment or url.path not in {'', '/'}:
            parser.error('Only a loopback HTTP base URL is allowed')
        result = exercise(args.base_url.rstrip('/'), 'local-demo-only-change-before-sharing')
        result['execution'] = 'existing_loopback_service'
    else:
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='submission-check-') as folder:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            token = 'submission-test-token'
            env = {k: v for k, v in os.environ.items() if not k.startswith(('APP_', 'LLM_'))}
            env.update(APP_MODE='test', APP_DATA_MODE='synthetic', APP_TOKEN=token,
                APP_DB_PATH=str(Path(folder) / 'test.sqlite'), APP_COMPANY_SCOPE='*',
                APP_ALLOWED_HOSTS='', LLM_API_KEY='')
            process = subprocess.Popen([sys.executable, 'scripts/run.py', '--host', '127.0.0.1', '--port', str(port)],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            base = f'http://127.0.0.1:{port}'
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Synthetic server exited during startup')
                    try:
                        with urllib.request.urlopen(base + '/api/health', timeout=1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Synthetic server startup timed out')
                result = exercise(base, token)
                result['execution'] = 'fresh_python_process'
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    result['python'] = sys.version.split()[0]
    if args.report:
        Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
