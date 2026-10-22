"""HTTP integration boundary: real sockets, actual serialization and authorization."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from backend.config import Config
from backend.server import App, make_handler

ROOT = Path(__file__).resolve().parents[1]


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='http-test-')
        cls.app = App(ROOT, Path(cls.tmp.name) / 'app.sqlite', Config(data_mode='synthetic'))
        cls.company = cls.app.agent.companies()[0]['id']
        cls.other = cls.app.agent.companies()[1]['id']
        cls.app.config.auth_token = 'http-test-token'
        cls.app.scope = {cls.company}
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(cls.app))
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    def request(self, path, body=None, auth=True, headers=None):
        h = {'Authorization': 'Bearer http-test-token'} if auth else {}
        if body is not None:
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request('POST' if body is not None else 'GET', path,
                     json.dumps(body) if body is not None else None, h)
        response = conn.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        conn.close()
        return result

    def test_auth_scope_and_session(self):
        self.assertEqual(self.request('/api/companies', auth=False)[0], 401)
        status, headers, raw = self.request('/api/companies')
        self.assertEqual(status, 200)
        self.assertEqual([c['id'] for c in json.loads(raw)['companies']], [self.company])
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertEqual(self.request('/api/feed?company_id=' + self.other)[0], 403)

    def test_health_never_exposes_secrets(self):
        status, headers, raw = self.request('/api/health', auth=False)
        self.assertEqual(status, 200)
        self.assertNotIn(b'http-test-token', raw)
        self.assertFalse(json.loads(raw)['live_ready'])
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_origin_host_and_content_type(self):
        self.assertEqual(self.request('/api/news', {}, headers={'Origin': 'https://attacker.invalid'})[0], 403)
        self.assertEqual(self.request('/api/companies', headers={'Host': 'attacker.invalid'})[0], 403)
        self.assertEqual(self.request('/api/news', {}, headers={'Content-Type': 'text/plain'})[0], 415)

    def test_static_path_and_source_boundaries(self):
        for path in ['/.env', '/%2e%2e/AGENTS.md', '/sources/%2e%2e/%2e%2e/AGENTS.md', '/sources/not-a-pdf.txt']:
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request('/sources/anything.pdf', auth=False)[0], 401)

    def test_new_event_followup_and_feedback(self):
        payload = {'title': 'Новая новость для HTTP теста о валютных рисках',
                   'text': 'Синтетический тест: колебания валютного курса требуют проверки валюты договоров.',
                   'date': '2026-10-22', 'url': 'https://example.invalid/http-test', 'synthetic': True}
        status, _, raw = self.request('/api/news', payload)
        self.assertEqual(status, 200)
        event = json.loads(raw)['item']['id']
        self.assertFalse(json.loads(self.request('/api/news', payload)[2])['created'])
        args = {'company_id': self.company, 'event_id': event, 'role': 'cfo'}
        status, _, raw = self.request('/api/analyze', args)
        self.assertEqual(status, 200)
        run = json.loads(raw)
        self.assertEqual(self.request('/api/runs/' + run['id'])[0], 200)
        status, _, raw = self.request('/api/analyze', {**args, 'question': 'Какие связи группы известны?', 'parent_run_id': run['id']})
        self.assertEqual(status, 200)
        self.assertIn('load_memory', [t['tool'] for t in json.loads(raw)['trace']])
        self.assertEqual(self.request('/api/feedback', {'run_id': run['id'], 'rating': 5, 'comment': 'HTTP test'})[0], 200)
        self.assertEqual(self.request('/api/feedback', {'run_id': run['id'], 'rating': 0})[0], 400)

    def test_manual_url_is_not_fetched(self):
        payload = {'title': 'Синтетическая новость без сетевого доступа', 'text': 'Достаточно длинный текст синтетического события.', 'date': '2026-10-22', 'url': 'http://127.0.0.1:1/not-fetched'}
        self.assertEqual(self.request('/api/news', payload)[0], 200)
        self.assertEqual(self.request('/api/news', {**payload, 'url': 'javascript:alert(1)'})[0], 400)


if __name__ == '__main__':
    unittest.main()
