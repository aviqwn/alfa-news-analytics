"""Explicit bounded state machine: plan -> tools -> validate -> conditional repair.

Test mode is rule-based and never represented as model inference. Live mode uses
the same grounded state; the LLM may draft text but cannot select arbitrary tools.
"""
import hashlib
import json
import re
import time
import uuid
from datetime import date
from pathlib import Path
from .config import Config
from .llm import generate, LiveError
from .sources import CATEGORIES, bank_excerpt, classify, event_scope, read_array, retrieve, terms, valid_url

ROLES={'ceo':'Генеральный директор','cfo':'Финансовый директор','treasury':'Казначейство'}
AGENT_VERSION='2026-10-22-v3'
PRODUCT_KEYS={'loan':('CL_PRODUCT_LOA_MARK','loan'), 'deposit':('CL_PRODUCT_DEP_MARK','deposit'), 'fts':('FTS_FLAG','fts'),
    'lease':('CL_PRODUCT_LEA_MARK','lease'), 'factoring':('FACTORING_MARK','factoring')}

class AnalysisError(ValueError): pass

def has_feature(company,kind):
    for f in company.get('features',[]):
        if f['key'] in PRODUCT_KEYS.get(kind,(kind,)):
            return str(f.get('value','')).lower() in {'1','1.0','true','да','есть','yes'}
    return False

def feature(company,kind):
    keys=PRODUCT_KEYS.get(kind,(kind,))
    return next((f for f in company.get('features',[]) if f['key'] in keys), None)

def contradictions(chunks):
    # Compare explicit structured observations only, or unambiguous current-rate claims
    # on the same date. Historical dates/forecasts are not automatically conflicts.
    observations={}
    for c in chunks:
        assertions=c.get('assertions',{})
        if not assertions:
            match=re.search(r'ключевая ставка (?:составляет|сохранена на уровне)\s*(\d+(?:[.,]\d+)?)\s*%',c.get('text','').lower())
            if match: assertions={'key_rate':match.group(1).replace(',','.')}
        for k,v in assertions.items(): observations.setdefault((k,c.get('date')),set()).add(str(v))
    return [str(k[0]) for k,values in observations.items() if len(values)>1]

class Agent:
    def __init__(self, root, store, config=None):
        self.root=Path(root);self.store=store;self.config=config or Config()

    def companies(self):
        if self.config.data_mode=='synthetic':return read_array(self.root/'data/synthetic/companies.json','companies')
        return read_array(self.root/'data/processed/companies.json','companies') or read_array(self.root/'data/synthetic/companies.json','companies')

    def chunks(self):
        if self.config.data_mode=='synthetic':return []
        return read_array(self.root/'data/processed/bank_chunks.json','chunks')

    def company(self,company_id):
        company=next((c for c in self.companies() if c['id']==company_id),None)
        if not company: raise AnalysisError('Компания не найдена')
        return company

    def feed(self,company_id,role):
        company=self.company(company_id)
        if role not in ROLES:raise AnalysisError('Неизвестная роль')
        items=[]
        for news in self.store.news():
            score=20;reasons=[]
            category=classify(news['title']+' '+news.get('text',''))
            news={**news,'category':category}
            if category=='rates':
                if has_feature(company,'loan'):
                    score+=35;reasons.append('В профиле есть кредитный продукт: условия финансирования требуют проверки')
                if has_feature(company,'deposit'):
                    score+=20;reasons.append('Есть депозитный продукт: важны условия размещения средств')
                if role in {'cfo','treasury'}: score+=15;reasons.append('Ставки входят в задачи выбранной финансовой роли')
            if category=='fx' and has_feature(company,'fts'):
                score+=40;reasons.append('Есть сведения ФТС; направление торговли и валюта операций неизвестны')
            if category in {'commodities','demand'}:
                industry=str(company.get('industry','')).lower()
                if category=='commodities' and any(x in industry for x in ('стро','металл','производ')):
                    score+=38;reasons.append('Вид деятельности связан со строительством или производством; состав закупок нужно уточнить')
                if category=='demand' and any(x in industry for x in ('торгов','рознич')):
                    score+=35;reasons.append('Основной вид деятельности — торговля; изменение спроса может менять оборот')
                if role=='ceo':score+=15;reasons.append('Спрос и цепочки поставок важны для управленческого горизонта')
            if category=='regulation':score+=20;reasons.append('Применимость регулирования требует проверки видов деятельности')
            try:
                age=(date.today()-date.fromisoformat(news['date'])).days
                score+=max(0,12-age//7) if age>=0 else -15
                if age>120: reasons.append('Событие архивное: используйте как сценарий, не текущую новость')
                if age<0:reasons.append('Дата события в будущем: достоверность и период требуют проверки')
            except ValueError:score-=15
            reasons.append('Контекст: '+company.get('name',company_id)+' · '+ROLES[role])
            items.append({**news,'score':min(99,max(0,score)),'reasons':reasons})
        return sorted(items,key=lambda n:(-n['score'],-date.fromisoformat(n['date']).toordinal(),n['id']))

    def analyze(self,company_id,event_id,role,question='',parent_run_id=None):
        start=time.monotonic()
        company=self.company(company_id)
        if role not in ROLES:raise AnalysisError('Неизвестная роль')
        if not isinstance(question,str) or len(question)>2000:raise AnalysisError('Вопрос должен быть текстом не длиннее 2000 символов')
        event=next((n for n in self.store.news() if n['id']==event_id),None)
        if not event:raise AnalysisError('Событие не найдено')
        parent=None
        if parent_run_id:
            parent=self.store.get_run(parent_run_id)
            if not parent or parent['company_id']!=company_id or parent['event_id']!=event_id:
                raise AnalysisError('Предыдущий разбор не принадлежит этому контексту')
        if self.config.mode=='live' and self.config.blockers(): raise LiveError('Живая интеграция не настроена. Проверьте /api/health; тестовый режим включается отдельно.')
        chunks=self.chunks()
        signature=json.dumps([AGENT_VERSION,company,event,role,question,parent_run_id,chunks,self.config.mode,self.config.endpoint,self.config.model_id,date.today().isoformat()],ensure_ascii=False,sort_keys=True)
        key=hashlib.sha256(signature.encode()).hexdigest()
        cached=self.store.cached(key)
        if cached:
            cached['metrics']={**cached['metrics'],'cache_hit':True,'request_cost_rub':0,'request_tokens':0}
            return cached
        trace=[]
        def log(tool,status,detail):
            if time.monotonic()-start>45:
                raise AnalysisError('Достигнут предел времени разбора 45 секунд; результат не опубликован')
            trace.append({'step':len(trace)+1,'tool':tool,'status':status,'detail':detail})
        log('company_context','ok','Загружены подтвержденные признаки '+company_id+'; роль '+ROLES[role])
        if parent:
            log('load_memory','ok','Восстановлен разбор '+parent['id']+'; сохранены событие, источники и ранее обозначенные пробелы')
        category=classify(event['title']+' '+event.get('text',''))
        scope=event_scope(event['title']+' '+event.get('text',''))
        question_category=classify(question)
        category_for_search=question_category if question_category!='general' else category
        graph_needed=any(t in question.lower() for t in ('контраг','связ','групп','платеж','платёж')) or any(f['key']=='payment_counterparties_count' and float(f.get('value') or 0)>0 for f in company.get('features',[]))
        plan=['company_context','event_extract']
        if graph_needed:plan.append('relationship_context')
        if question:plan.append('question_focus')
        plan.extend(['bank_search','evidence_check','draft','verify'])
        log('plan','ok',json.dumps({'tools':plan,'focus':category_for_search,'max_repairs':2,'reason':'Уточняющий вопрос и профиль определяют набор инструментов'},ensure_ascii=False))
        log('event_extract','ok','Категория: '+category+'; источник: '+event.get('source','ручной ввод')+'; текст источника не исполняется')
        unknowns=list(company.get('unknowns',[]))
        scope_warning=''
        if scope=='retail':scope_warning='Событие относится к физическим лицам. Ставки и условия розничных продуктов не являются условиями финансирования или депозитов этой компании.'
        if scope=='named_entity':scope_warning='Событие относится к именованной организации. Связь с обезличенной компанией и ее контрагентами не установлена.'
        if scope_warning:unknowns.append(scope_warning)
        if graph_needed:
            graph=[f for f in company.get('features',[]) if any(k in f['key'].lower() for k in ('group','payment','shared_person'))]
            log('relationship_context','ok' if graph else 'missing','; '.join(f"{f['label']}: {f['value']}" for f in graph) if graph else 'Подтвержденных графовых признаков нет')
            unknowns.append('Платежные связи и общие физлица не подтверждают владение или содержание договоров; именованные участники новости не сопоставлены с обезличенными ID.')
        if question:log('question_focus','ok','Фокус вопроса: '+category_for_search+'; исходная компания и событие сохранены')
        query=event['title']+' '+question
        selected=retrieve(chunks,query,category_for_search,event['date'])
        log('bank_search','ok' if selected else 'missing','Запрос: '+query[:300]+'; найдено фрагментов: '+str(len(selected)))
        current=[c for c in selected if not c.get('stale') and not c.get('future') and not c.get('synthetic') and c.get('manual_verified',False)]
        conflicts=contradictions(current)
        if not current or conflicts:
            reason='противоречие '+', '.join(conflicts) if conflicts else 'нет актуальной релевантной банковской оценки'
            log('evidence_check','repair',reason)
            expansion_terms=() if category_for_search=='commodities' else CATEGORIES.get(category_for_search,())
            expanded=event['title']+' '+' '.join(expansion_terms)+' '+company.get('industry','')+' '+question
            repaired=retrieve(chunks,expanded,category_for_search,event['date'],broad=True)
            log('adaptive_search','ok' if repaired else 'missing','Изменен запрос после проверки: '+expanded[:300]+'; найдено '+str(len(repaired)))
            selected=list({c['id']:c for c in selected+repaired}.values())[:6]
            current=[c for c in selected if not c.get('stale') and not c.get('future') and not c.get('synthetic') and c.get('manual_verified',False)]
            conflicts=contradictions(current)
            if conflicts:
                log('resolve_conflict','restricted','Расхождение осталось: '+', '.join(conflicts)+'. Спорная оценка исключена из позиции банка')
                current=[]
                unknowns.append('Обнаружено противоречие банковских фрагментов: '+', '.join(conflicts)+'. Текущая позиция не установлена.')
        if not current:unknowns.append('Актуальная релевантная оценка Альфа-Банка не найдена; позиция банка не синтезируется.')
        stale=[c for c in selected if c.get('stale')]
        if stale:unknowns.append('В поиске есть архивные материалы, документы с истекшим периодом или неизвестной датой. Они не подтверждают текущую позицию банка. Порог свежести прототипа — 120 дней, это техническая эвристика, не срок действия прогноза.')
        evidence=[{'id':'news:'+event['id'],'title':event['title'],'date':event['date'],'text':event.get('text',event.get('summary','')),'url':event.get('url',''),'kind':'news','synthetic':event.get('synthetic',False)}]
        profile_id='profile:'+company_id
        evidence.append({'id':profile_id,'title':'Профиль '+company.get('name',company_id),'date':company.get('as_of','Дата среза не указана'),'text':'; '.join(f"{f['label']}: {f['value']} (источник: {f.get('source','не указан')})" for f in company.get('features',[])),'url':'','kind':'profile','synthetic':company.get('synthetic',False)})
        for c in selected:
            evidence.append({**c,'kind':'bank','id':'bank:'+c['id'],'url':c.get('url','')})
        news_id=evidence[0]['id']
        bank_view=[{'text':bank_excerpt(c,category_for_search),'evidence_ids':['bank:'+c['id']]} for c in current[:2]]
        sentences=re.split(r'(?<=[.!?])\s+',event.get('text',event.get('summary',event['title'])))
        fact_text=' '.join(sentences[:3])
        if len(fact_text)>1300:fact_text=fact_text[:1297]+'…'
        facts=[{'text':fact_text,'evidence_ids':[news_id]}]
        interpretation,actions=self._interpret(company,category_for_search,role,news_id,profile_id,current,graph_needed)
        if scope!='general':
            interpretation[0]['text']=('Прямое применение условий новости к компании не подтверждено. '+scope_warning+' '+interpretation[0]['text'])
        if parent:unknowns=list(dict.fromkeys(unknowns+parent.get('unknowns',[])))
        answer=None
        if question:
            answer=('Разрез «'+ROLES[role]+'». '+interpretation[0]['text']+' '+
                    ('Проверены доступные связи; установленных договоров или владения нет. ' if graph_needed else '')+
                    ('Актуальная банковская оценка приложена к разбору.' if current else 'Для более точного ответа не хватает актуальной банковской оценки.'))
        usage={'input_tokens':0,'output_tokens':0,'cost_rub':None,'requests':0}
        log('draft','ok','Детерминированные правила тестового режима; модель не вызывалась' if self.config.mode=='test' else 'Подготовлен ограниченный контекст для разрешенной модели')
        if self.config.mode=='live':
            accepted_bank_ids={'bank:'+c['id'] for c in current}
            compact_evidence=[]
            for e in evidence:
                if e['kind']=='bank' and e['id'] not in accepted_bank_ids:continue
                compact_evidence.append({k:v for k,v in {**e,'text':('Признаки и происхождение приведены в company.features' if e['kind']=='profile' else e.get('text','')[:1500])}.items() if k in {'id','kind','title','date','page','text','synthetic','manual_verified'}})
            compact_company={k:company.get(k) for k in ('id','name','industry','features','unknowns','synthetic')}
            compact_event={k:event.get(k) for k in ('id','title','date','category','synthetic')}
            context={'company':compact_company,'event':compact_event,'role':role,'question':question,'previous_unknowns':parent.get('unknowns',[])[:4] if parent else [],'evidence':compact_evidence,'bank_view':bank_view}
            draft,usage=generate(self.config,self.store,context)
            if not isinstance(draft,dict):raise LiveError('Ответ модели не соответствует схеме')
            interpretation=draft.get('interpretation',[]);actions=draft.get('actions',[])
            if not isinstance(draft.get('unknowns',[]),list) or any(not isinstance(s,str) or len(s)>1000 for s in draft.get('unknowns',[])):raise LiveError('Некорректные неизвестные в ответе модели')
            unknowns.extend(draft.get('unknowns',[])[:8])
            answer=draft.get('answer') if question else None
            if answer is not None and (not isinstance(answer,str) or len(answer)>3000):raise LiveError('Некорректный ответ модели')
            log('llm_generate','ok','Серверный адаптер; модель '+self.config.model_name+'; инструменты модели не предоставлены')
        self._validate_claims(interpretation,actions,evidence,company,news_id,profile_id)
        future_event=date.fromisoformat(event['date'])>date.today()
        if future_event:unknowns.append('Дата события в будущем; фактическое наступление не подтверждено.')
        checks=[{'name':'evidence_integrity','passed':True,'detail':'Каждый персональный тезис ссылается на новость и профиль; ID и ключи признаков проверены'},
            {'name':'bank_current','passed':bool(current),'detail':'Найдена актуальная цитата банка' if current else 'Вывод ограничен: актуальная позиция банка отсутствует'},
            {'name':'contradictions','passed':not bool(conflicts),'detail':'Расхождения не выявлены в проверяемых утверждениях' if not conflicts else 'Расхождение сохранено явно; спорный тезис исключен'},
            {'name':'dates','passed':not future_event and not bool(stale),'detail':'Архивные/будущие материалы исключены из текущей позиции банка; дата события проверена'},
            {'name':'sensitive_inference','passed':True if self.config.mode=='test' else None,'detail':'Тестовые правила не выводят сумму долга, валюту, владение и долю рынка из флагов' if self.config.mode=='test' else 'Автоматическая проверка фактической и семантической корректности AI не подтверждена; нужен экспертный контроль'}]
        log('verify','ok','Проверены ссылки, признаки, даты; пробелы: '+str(len(unknowns))+'; переход к ограниченному выводу при отсутствии данных')
        run={'id':str(uuid.uuid4()),'company_id':company_id,'event_id':event_id,'role':role,'mode':self.config.mode,
            'title':event['title'],'facts':facts,'bank_view':bank_view,'interpretation':interpretation,'actions':actions,
            'unknowns':list(dict.fromkeys(unknowns)),'evidence':evidence,'trace':trace,'checks':checks,
            'metrics':{'duration_ms':round((time.monotonic()-start)*1000),'grounded_claim_ratio':1.0,'citation_completeness':1.0,'semantic_grounding_verified':False,'cache_hit':False,**usage},
            'answer':answer,'question':question,'parent_run_id':parent_run_id,
            'disclosure':'Тестовый режим: правила и шаблоны, без вызова LLM' if self.config.mode=='test' else 'Интерпретация AI: '+self.config.model_name}
        self.store.save_run(run,key)
        return run

    def _interpret(self,company,category,role,news_id,profile_id,bank,graph_needed):
        fs=company.get('features',[])
        base_keys=[f['key'] for f in fs if f['key'] in {'MAINOKVED_CODE','MAINOKVED_NAME','industry','okved'}]
        if not base_keys and fs:base_keys=[fs[0]['key']]
        refs=[news_id,profile_id]
        if bank:refs.append('bank:'+bank[0]['id'])
        name=company.get('name',company['id'])
        known=[];keys=list(base_keys)
        for kind in ('loan','deposit','fts','lease','factoring'):
            f=feature(company,kind)
            if f and has_feature(company,kind):known.append(kind);keys.append(f['key'])
        if category=='rates':
            if 'loan' in known:
                text=f'У {name} подтвержден кредитный продукт. Событие по ставкам имеет смысл сопоставить с ближайшими пересмотрами условий: если договор предусматривает плавающую ставку или рефинансирование, денежный поток может измениться. Размер долга и тип ставки неизвестны.'
                if 'deposit' in known:
                    text+=' Одновременно отмечен депозитный продукт: условия новых размещений могут менять процентные доходы. Остаток и срок размещения неизвестны; кредитный и депозитный эффекты следует оценивать раздельно, не предполагать их взаимное погашение.'
            elif 'deposit' in known:
                text=f'У {name} подтвержден депозитный продукт. Событие по ставкам может менять доходность новых размещений; остаток средств и срок депозита неизвестны, поэтому эффект не оценен численно.'
            else:
                text=f'В доступных признаках {name} кредитный продукт не подтвержден. Из новости о ставке нельзя сделать вывод о прямом изменении процентных расходов. Для компании с деятельностью «{company.get("industry","")}» стоит проверить косвенный эффект через спрос и условия расчетов.'
        elif category=='fx':
            text=f'Для {name} '+('есть сведения ФТС. Это основание проверить международные расчеты, но не доказательство импорта, экспорта или валютной позиции. ' if 'fts' in known else 'сведения о внешнеторговых операциях не подтверждены. Прямую валютную чувствительность установить нельзя. ')+ 'Сценарий влияния курса зависит от валют договоров и дат платежей; этих данных нет.'
        elif category=='commodities':
            text=f'Основная деятельность {name}: {company.get("industry", "не установлена")}. Новость о сырье может быть связана с себестоимостью или условиями поставщиков только при наличии соответствующих закупок. Состав закупок неизвестен; подтвержденная платежная связь сама по себе не раскрывает предмет договора.'
        elif category=='demand':
            text=f'Для {name} с деятельностью «{company.get("industry", "не установлена")}» изменение спроса может влиять на оборот и сроки возврата денег. Наличие '+('кредитного продукта делает полезной совместную проверку оборотного капитала и договорного графика' if 'loan' in known else 'продуктовых флагов не позволяет оценить размер оборотного капитала')+'. Эффект зависит от ассортимента и структуры клиентов, которые не представлены.'
        else:
            text=f'Событие следует проверить на применимость к {name}: основной вид деятельности — {company.get("industry", "не установлен")}. Прямое финансовое влияние не подтверждено доступными данными; сначала нужны затронутые договоры, продукты и сроки.'
        if bank:
            excerpt=bank_excerpt(bank[0],category)
            # Exact bank premise participates in the conditional recommendation, with citation.
            bank_date=bank[0].get('date','дата не установлена')
            premise=excerpt if len(excerpt)<=220 else excerpt[:217]+'…'
            text+=' В материале от '+str(bank_date)+' банк отмечает: «'+premise+'». '
            if category=='rates' and any(w in excerpt.lower() for w in ('пауз','сохранен','сохранени')):
                text+='Для компании это основание проверить бюджет при сценарии, в котором новое финансирование не дешевеет; действующие договоры не меняются автоматически.'
            elif category=='demand':
                text+='Эту оценку следует сопоставить с ассортиментом, спросом покупателей и сроками оборачиваемости компании, прежде чем менять план продаж.'
            else:
                text+='Эта оценка задает контекст сценария устойчивости, но не подтверждает конкретную сумму влияния на компанию.'
        if graph_needed:
            related=next((f for f in fs if f['key']=='payment_counterparties_count'),None)
            group=next((f for f in fs if f['key']=='group_members_count'),None)
            if related:
                text+=f' В выборке у компании {related["value"]} платежных связей: при проверке сценария стоит сопоставить сроки расчетов по этим связям; предметы договоров не установлены.'
                keys.append(related['key'])
            if group:
                text+=f' В той же группе набора {group["value"]} компаний; это основание проверить согласованность планов, но не консолидированную финансовую позицию.'
                keys.append(group['key'])
        if role=='cfo':
            text+=' Разрез финансового директора: сопоставить сценарий с бюджетом, маржой и стоимостью финансирования.'
            actions=['Сверить ближайшие договорные даты и условия с финансовым бюджетом.', 'Построить условный сценарий денежного потока после уточнения сумм и чувствительности; не переносить продуктовый флаг в сумму обязательств.']
        elif role=='treasury':
            text+=' Разрез казначейства: проверить календарь платежей, доступную ликвидность и сроки размещения средств.'
            actions=['Сверить календарь ближайших платежей и поступлений с договорными сроками.', 'Запросить валюты, суммы и даты обязательств; после уточнения оценить разрыв ликвидности.']
        else:
            text+=' Разрез генерального директора: проверить устойчивость продаж, поставок и инвестиционных решений.'
            actions=['Поручить финансовой и коммерческой командам проверить затронутые договоры и спрос.', 'Сравнить базовый и неблагоприятный сценарии инвестиций после уточнения неизвестных данных.']
        if graph_needed:actions.append('Проверить существенных контрагентов по разрешенным внутренним идентификаторам; не устанавливать реальные имена по обезличенным связям.')
        return [{'text':text,'evidence_ids':refs,'feature_keys':list(dict.fromkeys(keys))}],actions

    def _validate_claims(self,claims,actions,evidence,company,news_id,profile_id):
        if not isinstance(claims,list) or not 1<=len(claims)<=5:raise LiveError('Нет валидных персональных тезисов')
        if not isinstance(actions,list) or len(actions)>5 or any(not isinstance(s,str) or len(s)>1000 for s in actions):raise LiveError('Действия не соответствуют схеме')
        ids={e['id'] for e in evidence};features={f['key'] for f in company.get('features',[])}
        unverified_bank={e['id'] for e in evidence if e.get('kind')=='bank' and (e.get('stale') or e.get('future') or not e.get('manual_verified') or e.get('synthetic'))}
        for c in claims:
            if not isinstance(c,dict) or not isinstance(c.get('text'),str) or len(c['text'])>4000:raise LiveError('Неверная схема тезиса')
            refs=c.get('evidence_ids');keys=c.get('feature_keys')
            if not isinstance(refs,list) or any(not isinstance(x,str) for x in refs) or not {news_id,profile_id}.issubset(refs) or not set(refs)<=ids:
                raise LiveError('Тезис содержит неизвестную ссылку или не опирается на новость и профиль')
            if not isinstance(keys,list) or not keys or any(not isinstance(x,str) for x in keys) or not set(keys)<=features:
                raise LiveError('Тезис содержит неподтвержденный признак')
            if set(refs) & unverified_bank:
                raise LiveError('Текущий вывод ссылается на непроверенный, архивный или будущий банковский фрагмент')
            # URLs inside generated prose are forbidden; navigation uses verified evidence only.
            if re.search(r'https?://|javascript:|data:',c['text'],re.I):raise LiveError('Ссылка вне панели проверенных доказательств')
