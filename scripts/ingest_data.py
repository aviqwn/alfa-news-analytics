#!/usr/bin/env python3
"""Reproduce CSV audit and conservative profiles. No model calls. Run from any cwd.
Downloads use scripts/download_sources.py. Bank scan indexing uses locally authorized
macOS Vision snapshots and explicitly marked visually verified excerpts.
"""
from pathlib import Path
import argparse, hashlib, json
from datetime import datetime, timezone
import pandas as pd
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'data/raw'; OUT=ROOT/'data/processed'; REPORT=ROOT/'reports'
def dump(path,obj):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--download',action='store_true');ap.add_argument('--ocr',action='store_true');a=ap.parse_args()
 if a.download:
  import subprocess,sys
  subprocess.run([sys.executable,str(ROOT/'scripts/download_sources.py')],check=True)
 tables={p.stem:pd.read_csv(p,sep=';',dtype=str,encoding='utf-8',keep_default_na=True) for p in sorted((RAW/'companies').glob('*.csv'))}
 c=tables['df_Uid_data_hack'];l=tables['df_Fid_Uid_link_hack'];p=tables['df_Uid_payment_hack'];ids=set(c.SYNTHETIC_U)
 audit={}
 for name,d in tables.items():
  audit[name]={'rows':len(d),'columns':list(d.columns),'exact_duplicate_rows':int(d.duplicated().sum()),'missing':{k:int(v) for k,v in d.isna().sum().items()},'unique':{k:int(v) for k,v in d.nunique().items()}}
 audit['coverage']={'company_unique_ids':len(ids),'company_extra_rows_for_repeated_ids':int(c.SYNTHETIC_U.duplicated().sum()),'company_distinct_repeated_ids':int(c.loc[c.SYNTHETIC_U.duplicated(keep=False),'SYNTHETIC_U'].nunique()),'link_company_ids':int(l.SYNTHETIC_U.nunique()),'link_ids_in_company_table':len(set(l.SYNTHETIC_U)&ids),'link_rows_with_known_company':int(l.SYNTHETIC_U.isin(ids).sum()),'payment_payer_unique':int(p.PAYER.nunique()),'payment_beneficiary_unique':int(p.BENEFICIARY.nunique()),'payment_payers_in_company_table':len(set(p.PAYER)&ids),'payment_beneficiaries_in_company_table':len(set(p.BENEFICIARY)&ids),'payment_rows_both_known':int((p.PAYER.isin(ids)&p.BENEFICIARY.isin(ids)).sum()),'payment_self_edges':int((p.PAYER==p.BENEFICIARY).sum()),'payment_duplicate_pairs':int(p.duplicated(['PAYER','BENEFICIARY']).sum()),'group_unique_ids':int(c.GROUP_ID.nunique())}
 audit['numeric']={}
 for d,key in [(c,'REVENUE_COMPANY'),(p,'PAY_AMT'),(p,'PAY_CNT')]:
  n=pd.to_numeric(d[key],errors='coerce');audit['numeric'][key]={'non_numeric':int(n.isna().sum()),'min':float(n.min()),'median':float(n.median()),'max':float(n.max()),'negative':int((n<0).sum()),'zero':int((n==0).sum()),'unit':'not specified in source' if key!='PAY_CNT' else 'count; aggregation period unspecified'}
 flags=[x for x in c.columns if x.endswith('_MARK') or x in ['FACTORING_MARK','FTS_FLAG','NEGATIVEMARK']]
 audit['flag_values']={k:c[k].value_counts(dropna=False).to_dict() for k in flags}
 audit['inconsistencies']={'fts_zero_with_country':int(((c.FTS_FLAG=='0.0')&c.FTS_COUNTRY_NAME.notna()).sum()),'fts_one_without_country':int(((c.FTS_FLAG=='1.0')&c.FTS_COUNTRY_NAME.isna()).sum())}
 audit['duplicate_company_conflicts']={col:int(c[c.SYNTHETIC_U.duplicated(keep=False)].groupby('SYNTHETIC_U')[col].nunique(dropna=False).gt(1).sum()) for col in c.columns if col!='SYNTHETIC_U'}
 dump(REPORT/'data_audit.json',audit)
 labels={'MAINOKVED_CODE':'Основной ОКВЭД','MAINOKVED_NAME':'Вид деятельности','REVENUE_COMPANY':'Годовая выручка (единица в источнике не указана)','CL_PRODUCT_LOA_MARK':'Отметка кредитного продукта','CL_PRODUCT_DEP_MARK':'Отметка депозитного продукта','CL_PRODUCT_LEA_MARK':'Отметка лизингового продукта','CL_PRODUCT_GAR_MARK':'Отметка банковских гарантий','CL_PRODUCT_RKO_MARK':'Отметка РКО','FACTORING_MARK':'Отметка факторинга','FTS_FLAG':'Отметка наличия информации ФТС','FTS_COUNTRY_NAME':'Страны, указанные в данных ФТС','GROUP_ID':'Идентификатор группы в наборе','REGION':'Регион регистрации','CITY':'Город регистрации','AMOUNT_WORKERS_FNS_GRADATION':'Градация числа работников ФНС'}
 profiles=[]
 for cid in ['U_5639','U_225628','U_68157']:
  selected=c.loc[c.SYNTHETIC_U==cid]
  if len(selected)!=1:raise ValueError('Ambiguous or missing company profile: '+cid)
  row=selected.iloc[0]; line=int(c.index[c.SYNTHETIC_U==cid][0])+2
  src=f'df_Uid_data_hack.csv:row={line};SYNTHETIC_U={cid}'
  features=[{'key':k,'label':label,'value':str(row[k]),'source':src+';column='+k,'kind':'observed'} for k,label in labels.items() if pd.notna(row[k])]
  cp=p[(p.PAYER==cid)|(p.BENEFICIARY==cid)].drop_duplicates(); counterparties=(set(cp.PAYER)|set(cp.BENEFICIARY))-{cid}
  related_people=set(l.loc[l.SYNTHETIC_U==cid,'SYNTHETIC_F']); related_companies=set(l.loc[l.SYNTHETIC_F.isin(related_people),'SYNTHETIC_U'])-{cid}
  counts=[('group_members_count','Компаний с тем же GROUP_ID в наборе',int(c.loc[c.GROUP_ID==row.GROUP_ID,'SYNTHETIC_U'].nunique()),src+';group-by=GROUP_ID'),('payment_counterparties_count','Платежных связей с другими компаниями в наборе',len(counterparties),'df_Uid_payment_hack.csv;PAYER-or-BENEFICIARY='+cid+';distinct-other-ids;exclude-self'),('payment_known_counterparties_count','Контрагентов с профилем в наборе',len(counterparties&ids),'df_Uid_payment_hack.csv+df_Uid_data_hack.csv;company='+cid),('shared_person_companies_count','Других компаний с общим обезличенным физлицом',len(related_companies),'df_Fid_Uid_link_hack.csv;SYNTHETIC_U='+cid+';shared-SYNTHETIC_F')]
  features.extend({'key':k,'label':label,'value':v,'source':s,'kind':'derived'} for k,label,v,s in counts)
  profiles.append({'id':cid,'name':'Компания '+cid,'industry':str(row.MAINOKVED_NAME).strip(),'okved':str(row.MAINOKVED_CODE),'revenue':float(row.REVENUE_COMPANY),'revenue_unit':'unknown','features':features,'unknowns':['Единица и отчетный год выручки не указаны; соответствие порогу 15 млрд ₽ требует подтверждения.','Размер, валюта, ставка и срок долга не известны; отметка продукта не равна задолженности.','Направление, объем и валюта внешней торговли не известны; страна ФТС не доказывает экспорт или импорт.','Юридический характер группы и связей физлиц неизвестен; владение и контроль не установлены.','Доля рынка и полный охват платежей неизвестны; PAY_AMT без единицы и периода.'],'synthetic':False})
 dump(OUT/'companies.json',profiles)
 files=json.loads((RAW/'source-pages/files.json').read_text());manifest=[];documents=[]
 for f in files:
  path=RAW/f['kind']/f['name'];entry={**f,'path':str(path.relative_to(ROOT)),'bytes':path.stat().st_size,'sha256':sha(path),'downloaded_on':'2026-10-22'}
  if f['kind']=='analytics':
   reader=PdfReader(path);pages=[x.extract_text() or '' for x in reader.pages]
   entry.update(pages=len(pages),text_characters=sum(len(t.strip()) for t in pages),extraction='image_only' if not any(t.strip() for t in pages) else 'pdf_text')
   documents.append(entry)
  manifest.append(entry)
 dump(REPORT/'data_manifest.json',{'version':1,'source':'organizer-provided Google Drive folders from case PDF pages 15 and 17','files':manifest})
 dump(OUT/'bank_documents.json',documents)
 from index_bank import index_bank,run_ocr,META
 if a.ocr:run_ocr()
 chunks=index_bank()
 for document in documents:
  name=document['name'];title,date,topics=META[name]
  related=[ch for ch in chunks if ch['document']==name]
  document.update(title=title,date=date,period='2026-08' if date is None else date,date_precision='month' if date is None else 'day',date_verification='visually checked page 1',valid_until=None,valid_until_reason='Document does not specify an expiry date; relevance must be evaluated against the event date.',topics=topics,archived=bool(date and date<'2026-01-01'),manual_verification={'metadata_page':1,'title_and_date':True,'verified_fragment_count':sum(bool(ch['manual_verified']) for ch in related),'full_text':False},extraction='macos_vision_ocr' if related else 'image_only',extraction_quality={'ocr_pages':len(json.loads((OUT/'ocr'/f"{Path(name).stem}.json").read_text())) if (OUT/'ocr'/f"{Path(name).stem}.json").exists() else 0,'chunk_count':len(related),'limitations':'OCR may interleave columns and misread tables and numbers; only explicitly marked fragments were visually verified.'})
 dump(OUT/'bank_documents.json',documents)
 manifest_path=REPORT/'data_manifest.json'
 complete_manifest=json.loads(manifest_path.read_text())
 derivative_paths=[OUT/'companies.json',OUT/'bank_chunks.json',OUT/'bank_documents.json',RAW/'manual_bank_annotations.json',*sorted((OUT/'ocr').glob('*.json')),*sorted((OUT/'ocr').glob('*.png')),*sorted((OUT/'bank_text').glob('*.txt')),REPORT/'data_audit.json']
 complete_manifest['local_derivatives']=[{'path':str(path.relative_to(ROOT)),'sha256':sha(path),'bytes':path.stat().st_size} for path in derivative_paths if path.exists()]
 dump(manifest_path,complete_manifest)
 lines=['# Аудит данных организаторов','','Дата исследования: 22.10.2026. Файлы скачаны по двум ссылкам из оригинального задания (стр. 15, 17); оригиналы неизменны. SHA256, размеры и URL — в `data_manifest.json`. Кодировки CSV: UTF-8, разделитель `;`. Все CSV прочитаны полностью, ОКВЭД сохранен строкой.','','## Схемы, пропуски и дубли','']
 for name,d in tables.items():
  st=audit[name];lines += [f'### {name}.csv',f"{st['rows']:,} строк, {len(d.columns)} колонок; точных дублей: {st['exact_duplicate_rows']}.",'','| Поле | Пропусков | Уникальных значений |','|---|---:|---:|']
  lines.extend(f'| {k} | {st["missing"][k]} | {st["unique"][k]} |' for k in d.columns);lines+=['']
 lines += ['## Покрытие связей','',*['- '+k+': '+str(v) for k,v in audit['coverage'].items()],'','Повторяющийся SYNTHETIC_U имеет противоречивые атрибуты; такие профили не объединяются автоматически. В демонстрации выбраны только уникальные идентификаторы. Подробности конфликтующих колонок — в data_audit.json. Одно физлицо может связывать несколько организаций, но тип связи в таблице не указан. GROUP_ID означает только принадлежность к одной группе по исходной разметке. Эти данные не доказывают владение или контроль. Самоплатежи исключены из счетчика контрагентов; один точный дубль платежа исключается при расчете признаков. Неизвестные контрагенты не получают вымышленных профилей.','','## Числовые поля и смысл','',json.dumps(audit['numeric'],ensure_ascii=False,indent=2),'','Единицы REVENUE_COMPANY и PAY_AMT, валюта, даты и период платежей не заданы в CSV или описании полей на стр. 15–16. Даже кратность сумм тысяче не доказывает единицу. Порог кейса 15 млрд рублей нельзя уверенно применить к сырым числам. Все значения сохраняются без пересчета и без подписи ₽. PAY_CNT — количество, но период агрегации неизвестен.','',f"Противоречия ФТС: FTS_FLAG=0 при заполненной стране — {audit['inconsistencies']['fts_zero_with_country']}; FTS_FLAG=1 без страны — {audit['inconsistencies']['fts_one_without_country']}. Наличие страны не трактуется как импорт, экспорт или валютная позиция.",'','## Профили демонстрации','','- U_5639 и U_225628: одинаковый ОКВЭД 46.90. У первого отметки кредита, депозита и ФТС равны 1; у второго равны 0.','- U_68157: контрастный ОКВЭД 41.20.9 (строительство), кредитный продукт отмечен.','- Это реальные обезличенные записи предоставленного набора (`synthetic:false`), а не идентифицированные реальные организации. Отдельный синтетический сценарий должен иметь отдельную маркировку.','- `0` означает отсутствие отметки в наборе, а не гарантированное отсутствие продукта или риска во всем бизнесе.','','## Аналитика банка','','В папке скачано 9 PDF, '+str(sum(d['pages'] for d in documents))+' страниц. Извлечение pypdf дает пустой текст: документы являются сканами. Исходные PDF доступны без изменений. С разрешения пользователя (22.10.2026: локальный OCR разрешен организаторами) выполнен локальный macOS Vision OCR всех страниц, без внешних AI-сервисов. Отдельно добавляются локальные вручную сверенные по PNG фрагменты с manual_verified=true (если файл аннотаций присутствует). Остальные фрагменты имеют extraction=macos_vision_ocr, manual_verified=false и показатель OCR confidence; он не является гарантией точности. OCR смешивает колонки и может ошибаться в таблицах. Даты всех документов сверены визуально по первым страницам; для золота известен только месяц август 2026, date=null.','', 'Список Google Drive отличается от стр. 17 задания: отдельного Commodities performance нет, вместо него имеется ALFA_Russian_Steels_1К21PreviewUpdate_15Apr2021.pdf. Документ 2021 года — архивный и не может обосновывать текущие прогнозы. Дата из имени без визуальной проверки не считается датой публикации.','',f'В основном индексе фрагментов: {len(chunks)} (из них {sum(bool(ch.get("manual_verified")) for ch in chunks)} вручную сверены). Полный реестр PDF: `data/processed/bank_documents.json`.','', '## Воспроизведение','','`python scripts/ingest_data.py --download` скачивает оригиналы, пересчитывает аудит, профили и SHA256. Без `--download` работает офлайн на сохраненных оригиналах. Нужны pandas и pypdf. `python scripts/ingest_data.py --ocr` заново выполняет локальное OCR на macOS (нужен Swift/Xcode Command Line Tools); обычный запуск повторно использует сохраненные OCR JSON. На Linux автоматическое выполнение Vision недоступно; обработанные локальные данные сохраняются между запусками. Ключевые транскрипции в локальном data/raw/manual_bank_annotations.json имеют extraction=manual_verified и цитируемые страницы. Ни вызовов LLM, ни автоматического восстановления имен организаций/лиц нет.']
 (REPORT/'DATA_AUDIT.md').write_text('\n'.join(lines)+'\n')
 print(json.dumps({'companies':len(profiles),'bank_chunks':len(chunks),'files':len(manifest),'coverage':audit['coverage']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
