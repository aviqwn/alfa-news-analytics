import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS news(id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, company_id TEXT, cache_key TEXT, created TEXT, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS runs_cache ON runs(cache_key);
                CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY, day TEXT, reserved REAL, charged REAL, status TEXT, input_tokens INTEGER, output_tokens INTEGER);
                CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY, run_id TEXT, rating INTEGER, comment TEXT, created TEXT);
            ''')
            # Serialized and repeatable migration for databases created before usage accounting.
            db.execute('BEGIN IMMEDIATE')
            columns={row['name'] for row in db.execute('PRAGMA table_info(ledger)')}
            if 'input_tokens' not in columns:db.execute('ALTER TABLE ledger ADD COLUMN input_tokens INTEGER')
            if 'output_tokens' not in columns:db.execute('ALTER TABLE ledger ADD COLUMN output_tokens INTEGER')
            if 'access_mode' not in columns:db.execute("ALTER TABLE ledger ADD COLUMN access_mode TEXT NOT NULL DEFAULT 'priced'")
            if 'reserved_tokens' not in columns:db.execute('ALTER TABLE ledger ADD COLUMN reserved_tokens INTEGER NOT NULL DEFAULT 0')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_news(self, item):
        # Same normalized title + date is one event, even across differing tracking URLs.
        fp = hashlib.sha256((item['title'].strip().casefold() + '|' + item['date']).encode()).hexdigest()
        item = dict(item)
        item.setdefault('id', 'event-' + fp[:16])
        with self.connection() as db:
            previous = db.execute('SELECT data FROM news WHERE fingerprint=?', (fp,)).fetchone()
            if previous: return json.loads(previous['data']), False
            db.execute('INSERT INTO news VALUES(?,?,?)', (item['id'], fp, json.dumps(item, ensure_ascii=False)))
        return item, True

    def news(self):
        with self.connection() as db:
            return [json.loads(row['data']) for row in db.execute('SELECT data FROM news')]

    def save_run(self, run, cache_key):
        with self.connection() as db:
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?)', (run['id'], run['company_id'], cache_key, datetime.now(timezone.utc).isoformat(), json.dumps(run, ensure_ascii=False)))

    def get_run(self, run_id):
        with self.connection() as db:
            row = db.execute('SELECT data FROM runs WHERE id=?', (run_id,)).fetchone()
        return json.loads(row['data']) if row else None

    def cached(self, key):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.connection() as db:
            row = db.execute('SELECT data FROM runs WHERE cache_key=? AND created>? ORDER BY created DESC LIMIT 1', (key, cutoff)).fetchone()
        return json.loads(row['data']) if row else None

    @staticmethod
    def budget_day():
        # Organizer limit boundary is not supplied: conservative rolling 24h enforced below.
        return datetime.now(timezone.utc).isoformat()

    def reserve(self, cost, max_requests=100):
        if cost <= 0 or cost > 500: raise ValueError('Стоимость запроса вне допустимого бюджета')
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        reservation = str(uuid.uuid4())
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT COALESCE(SUM(MAX(reserved,charged)),0) AS used, COUNT(*) AS calls FROM ledger WHERE day>? AND access_mode='priced'", (cutoff,)).fetchone()
            if row['used'] + cost > 500 or row['calls'] >= max_requests:
                raise ValueError('Лимит общего ключа: бюджет 500 ₽ за скользящие 24 часа или число запросов исчерпаны')
            db.execute('INSERT INTO ledger(id,day,reserved,charged,status) VALUES(?,?,?,?,?)', (reservation, self.budget_day(), cost, 0, 'reserved'))
        return reservation

    def reserve_free(self, tokens, max_tokens=100000, max_requests=20):
        if type(tokens) is not int or not 0<tokens<=max_tokens or not 1<=max_tokens<=100000 or not 1<=max_requests<=20:
            raise ValueError('Недопустимая резервация бесплатного теста')
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
        reservation=str(uuid.uuid4())
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT COALESCE(SUM(reserved_tokens),0) AS tokens, COUNT(*) AS calls FROM ledger WHERE day>? AND access_mode='free_trial'",(cutoff,)).fetchone()
            if row['tokens']+tokens>max_tokens or row['calls']>=max_requests:
                raise ValueError('Локальный лимит бесплатного теста: токены или число запросов за 24 часа исчерпаны')
            # Monetary fields are internal zero reservations, not a claimed known RUB price.
            db.execute("INSERT INTO ledger(id,day,reserved,charged,status,access_mode,reserved_tokens) VALUES(?,?,0,0,'reserved','free_trial',?)",(reservation,self.budget_day(),tokens))
        return reservation

    def settle(self, reservation, cost=None, usage=None):
        if usage is not None:
            if not isinstance(usage,dict) or any(type(usage.get(k)) is not int or usage[k]<0 for k in ('input_tokens','output_tokens')):
                raise ValueError('Некорректный подтвержденный расход токенов')
        with self.connection() as db:
            # Unknown usage/errors retain the full reservation, including ambiguous timeouts.
            if cost is None:
                db.execute("UPDATE ledger SET status=CASE WHEN input_tokens IS NOT NULL AND output_tokens IS NOT NULL THEN status ELSE 'unknown' END WHERE id=?", (reservation,))
            else:
                db.execute("UPDATE ledger SET charged=?, reserved=?, status='charged' WHERE id=?", (cost, cost, reservation))
            if usage is not None:
                db.execute('UPDATE ledger SET input_tokens=?, output_tokens=? WHERE id=?', (usage['input_tokens'],usage['output_tokens'],reservation))
                db.execute("UPDATE ledger SET reserved_tokens=?, status='usage_recorded' WHERE id=? AND access_mode='free_trial'",(usage['input_tokens']+usage['output_tokens'],reservation))

    def feedback(self, run_id, rating, comment):
        with self.connection() as db:
            db.execute('INSERT INTO feedback VALUES(?,?,?,?,?)', (str(uuid.uuid4()), run_id, rating, comment, datetime.now(timezone.utc).isoformat()))

    def metrics(self, scope=None):
        with self.connection() as db:
            runs = [json.loads(x['data']) for x in db.execute('SELECT data FROM runs')]
            if scope is not None: runs = [r for r in runs if r['company_id'] in scope]
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            budget = db.execute("SELECT COALESCE(SUM(MAX(reserved,charged)),0) AS used, COUNT(*) AS requests FROM ledger WHERE day>? AND access_mode='priced'", (cutoff,)).fetchone()
            free=db.execute("SELECT COALESCE(SUM(reserved_tokens),0) AS tokens, COUNT(*) AS requests FROM ledger WHERE day>? AND access_mode='free_trial'",(cutoff,)).fetchone()
            usage = db.execute('SELECT COALESCE(SUM(input_tokens),0) AS input_tokens, COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(CASE WHEN input_tokens IS NULL OR output_tokens IS NULL THEN 1 ELSE 0 END),0) AS unknown_requests FROM ledger').fetchone()
            feedback = list(db.execute('SELECT run_id,rating FROM feedback'))
        ids = {r['id'] for r in runs}
        ratings = [r['rating'] for r in feedback if r['run_id'] in ids]
        return {'runs': len(runs), 'mean_duration_ms': round(sum(r['metrics']['duration_ms'] for r in runs)/max(1,len(runs))),
            'grounded_claim_ratio': round(sum(r['metrics']['grounded_claim_ratio'] for r in runs)/max(1,len(runs)), 3),
            'bank_coverage': round(sum(bool(r['bank_view']) for r in runs)/max(1,len(runs)), 3),
            'adaptive_runs': sum(any(t['tool']=='adaptive_search' for t in r['trace']) for r in runs),
            'feedback_count': len(ratings), 'mean_rating': round(sum(ratings)/len(ratings),2) if ratings else None,
            'budget': {'limit_rub':500,'reserved_or_spent_rub':round(budget['used'],4),'requests':budget['requests'],'window':'rolling_24h'},
            'tokens':{'input_tokens':usage['input_tokens'],'output_tokens':usage['output_tokens'],'total_tokens':usage['input_tokens']+usage['output_tokens'],'window':'all_time','scope':'shared_key_ledger'},
            'unknown_usage_requests':usage['unknown_requests'],
            'free_trial':{'requests':free['requests'],'reserved_or_used_tokens':free['tokens'],'cost_rub':None,'window':'rolling_24h','quota_note':'Локальные инженерные ограничения, не квоты провайдера'},
            'note':'Токены учитываются по ledger общего ключа, включая неуспешные разборы; неизвестный расход не считается нулевым. grounded_claim_ratio означает полноту ID-ссылок, не семантическую истинность. Экспертная релевантность и корректность пока не измерены.'}
