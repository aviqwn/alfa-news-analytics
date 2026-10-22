"""Bounded deterministic classification, lexical retrieval and allowlisted RSS."""
import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

CATEGORIES = {
    'rates': ('ставк', 'инфляц', 'кредит', 'облигац', 'денежн'),
    'fx': ('валют', 'рубл', 'экспорт', 'импорт', 'курс'),
    'commodities': ('металл', 'нефт', 'золот', 'сырь', 'сталь', 'стали', 'алюмин'),
    'demand': ('потреблен', 'спрос', 'рознич', 'торгов', 'продаж'),
    'regulation': ('регулир', 'закон', 'налог', 'пошлин', 'санкц'),
}

def stem_hits(text, words):
    # Match starts of words: «доставка» and «поставки» are not «ставка».
    return sum(len(re.findall(r'\b'+re.escape(word),text.lower())) for word in words)

def terms(text):
    return set(w[:7] for w in re.findall(r'[а-яёa-z0-9]{4,}', str(text).lower()))

def classify(text):
    text = text.lower()
    # A commemorative coin's rouble denomination is not an FX-market event.
    if 'монет' in text and any(s in text for s in ('памятн', 'номинал', 'посвященн')): return 'general'
    # Explicitly unknown currency is a limitation, not evidence of FX exposure.
    text = re.sub(r'валют[аы]\s+(?:не\s*известн\w*)', '', text)
    if 'системно значимых кредитных организаций' in text: return 'regulation'
    if any(s in text for s in ('лицензи', 'инсайдер', 'реестр', 'нарушени')): return 'regulation'
    scores = {k:stem_hits(text,words) for k,words in CATEGORIES.items()}
    winner = max(scores,key=scores.get)
    return winner if scores[winner] else 'general'

def event_scope(text):
    text=text.lower()
    if any(s in text for s in ('физических лиц','физическим лицам','населени','ипотек','потребительск')):
        return 'retail'
    if any(s in text for s in ('отозва','аннулиров')) and 'лицензи' in text:
        return 'named_entity'
    return 'general'

def clean_text(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]*>', ' ', str(text)))).strip()

def valid_url(url, local=False):
    if not isinstance(url,str) or len(url)>2048: return False
    if local and url.startswith('/sources/') and '..' not in url and '\\' not in url: return True
    p=urlsplit(url)
    return p.scheme in {'https','http'} and bool(p.hostname) and not p.username and not p.password

def read_array(path, key):
    if not Path(path).exists(): return []
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    return value.get(key,[]) if isinstance(value,dict) else value

def retrieve(chunks, query, category, event_date, broad=False):
    query_terms=terms(query)
    weighted=[]
    for raw in chunks:
        c=dict(raw)
        content=c.get('text','')
        overlap=len(query_terms & terms(content+' '+c.get('title','')))
        category_hits=stem_hits(content,CATEGORIES.get(category,()))
        compatible={'rates':{'rates','inflation','credit','bonds'},'fx':{'fx'},'demand':{'demand','retail','labor'},'commodities':{'commodities','steel','gold','oil'},'regulation':{'regulation'}}
        if c.get('topics') and category in compatible and not set(c['topics']) & compatible[category]:continue
        # A fuel or gold view does not answer a steel event, even though all are commodities.
        if re.search(r'\b(?:сталь|стали|металл)',query.lower()) and not re.search(r'\b(?:сталь|стали|металл|steel)',(content+' '+c.get('title','')).lower()):continue
        if overlap < (1 if broad else 2) and not category_hits: continue
        c['stale']=True
        c['future']=False
        try:
            delta=(date.fromisoformat(event_date)-date.fromisoformat(c['date'])).days
            c['stale']=delta>120
            c['future']=delta<0
        except (ValueError,KeyError,TypeError): pass
        if c.get('archived'):c['stale']=True
        if c.get('valid_until'):
            try:c['stale']=c['stale'] or date.fromisoformat(event_date)>date.fromisoformat(c['valid_until'])
            except ValueError:c['stale']=True
        if c.get('period') and re.fullmatch(r'\d{4}-\d{2}',str(c['period'])):
            c['stale']=c['stale'] or event_date[:7]>c['period']
        score=overlap*2+min(8,category_hits)-(10 if c['stale'] else 0)-(50 if c['future'] else 0)
        if c.get('manual_verified') and (overlap or category_hits):score+=15
        c['retrieval_score']=score
        weighted.append(c)
    weighted.sort(key=lambda x:(x['retrieval_score'],x.get('date') or ''),reverse=True)
    # Distinct pages remain traceable. Prefer different documents to expose disagreement.
    selected=[]
    seen=set()
    for c in weighted:
        doc=c.get('source_file', c.get('title',''))
        if doc not in seen:
            selected.append(c);seen.add(doc)
        if len(selected)==3:break
    return selected

def bank_excerpt(chunk, category):
    # Extractive quotation: never invent a bank position.
    sentences=re.split(r'(?<=[.!?])\s+|\n+',chunk.get('text',''))
    scored=[(stem_hits(s,CATEGORIES.get(category,())),i,s.strip()) for i,s in enumerate(sentences) if len(s.strip())>35]
    scored.sort(key=lambda x:(-x[0],x[1]))
    text=scored[0][2] if scored else chunk.get('text','')
    return text[:700]

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Перенаправление источника запрещено')

RSS_URL='https://www.cbr.ru/rss/RssPress'

def fetch_cbr():
    req=urllib.request.Request(RSS_URL,headers={'User-Agent':'AlfaHackPrototype/1.0','Accept':'application/rss+xml, application/xml'})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=12) as response:
        raw=response.read(1_000_001)
    if len(raw)>1_000_000: raise ValueError('Лента превышает предел 1 MB')
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper(): raise ValueError('XML entities запрещены')
    root=ET.fromstring(raw)
    items=[]
    for item in root.findall('.//item')[:60]:
        title=clean_text(item.findtext('title',''))[:500]
        text=clean_text(item.findtext('description',''))[:20000]
        link=item.findtext('link','').strip()
        if not title or not valid_url(link) or urlsplit(link).hostname not in {'cbr.ru','www.cbr.ru'}: continue
        try: published=parsedate_to_datetime(item.findtext('pubDate','')).date().isoformat()
        except (TypeError,ValueError): continue
        items.append({'title':title,'text':text or title,'summary':(text or title)[:260], 'date':published,'url':link,'source':'Банк России · RSS','category':classify(title+' '+text),'synthetic':False})
    return items
