"""Index locally extracted bank documents. OCR snapshots are not full manual verification."""
import json,hashlib,re,subprocess,os,sys
from pathlib import Path
from urllib.parse import quote
ROOT=Path(__file__).resolve().parents[1]
META={
'ALFA-Macro_Insights-Policy_Rate_11Sep.pdf':('Ключевая ставка ЦБ РФ: ставка без изменения на 14,0%, риторика стала жестче','2026-09-11',['rates','inflation','credit']),
'ALFA-7M26.pdf':('Макростатистика 7М26: данные по рынку труда не подтверждают охлаждения экономики','2026-09-07',['demand','labor','credit']),
'ALFA-consumption.pdf':('Потребление на пике','2026-08-05',['demand','retail','labor']),
'ALFA-macro_stat1H26.pdf':('Макростатистика 1П26: слабый рост и прогноз ВВП','2026-07-31',['demand','rates','fx']),
'Inflation_tracker_02.pdf':('Инфляция 25–31 августа: основные выводы','2026-09-02',['inflation','fuel','fx']),
'FI_Strategy_3Q26_22072026.pdf':('Тактический взгляд на 3-й квартал 2026: время собирать камни','2026-07-22',['rates','bonds','fx']),
'Бонд_Бриф_14092026.pdf':('Бонд Бриф: итоги недели','2026-09-14',['rates','bonds','credit']),
'Gold_digest_August26.pdf':('Обзор рынка золота, август 2026',None,['gold','commodities','fx']),
'ALFA_Russian_Steels_1К21PreviewUpdate_15Apr2021.pdf':('Российская сталь: ралли цен продолжается (архив 2021)','2021-04-15',['steel','commodities'])}
ANNOTATIONS=ROOT/'data/raw/manual_bank_annotations.json'
VERIFIED=json.loads(ANNOTATIONS.read_text()) if ANNOTATIONS.exists() else []
def run_ocr():
 if sys.platform!='darwin':raise RuntimeError('macOS Vision OCR requires macOS; use pre-extracted local snapshots or an organizer-approved OCR adapter.')
 tmp=ROOT/'tmp/pdf-ocr';tmp.mkdir(parents=True,exist_ok=True)
 env={**os.environ,'CLANG_MODULE_CACHE_PATH':str(tmp/'clang'),'SWIFT_MODULECACHE_PATH':str(tmp/'swift')}
 subprocess.run(['swiftc',str(ROOT/'scripts/ocr_pdf.swift'),'-o',str(tmp/'ocr_pdf')],check=True,env=env)
 for name in META:
  output=ROOT/'data/processed/ocr'/f'{Path(name).stem}.json';output.parent.mkdir(parents=True,exist_ok=True)
  subprocess.run([str(tmp/'ocr_pdf'),str(ROOT/'data/raw/analytics'/name),str(output)],check=True)
def index_bank():
 files=json.loads((ROOT/'data/raw/source-pages/files.json').read_text()); urls={x['name']:x['url'] for x in files}
 def base(name,page):
  title,date,topics=META[name]
  return {'document':name,'title':title,'date':date,'date_precision':'day' if date else 'month','period':'2026-08' if date is None else date,'date_source':'page 1, visually checked; month only for gold','page':page,'url':'/sources/'+quote(name)+'#page='+str(page),'source_url':urls[name]+'#page='+str(page),'kind':'bank','topics':topics,'synthetic':False,'archived':bool(date and date<'2026-01-01'),'temporal_note':'Позиция банка на дату публикации; не является сегодняшним прогнозом.' if date and date>='2026' else 'Архивный материал; не использовать как актуальный прогноз.' if date else 'День публикации не установлен; на обложке август 2026.'}
 chunks=[]
 for cid,name,page,text in VERIFIED:
  chunks.append({**base(name,page),'id':cid,'text':text,'extraction':'manual_verified','manual_verified':True,'quality':'fragment visually checked against source page','ocr_confidence':None})
 for name in META:
  file=ROOT/'data/processed/ocr'/f'{Path(name).stem}.json'
  if not file.exists():continue
  pages=json.loads(file.read_text())
  for page in pages:
   # Keep page boundary; overlap preserves context, no claim that tables are reliably reconstructed.
   text=page['text'].strip();parts=[]
   for offset in range(0,len(text),1400):
    fragment=text[max(0,offset-150):offset+1600]
    if len(fragment)>30:parts.append(fragment)
   for i,fragment in enumerate(parts):
    cid='bank-'+hashlib.sha256((name+':'+str(page['page'])+':'+str(i)).encode()).hexdigest()[:16]
    chunks.append({**base(name,page['page']),'id':cid,'text':fragment,'extraction':'macos_vision_ocr','manual_verified':False,'quality':'OCR; columns, tables and numbers require source verification','ocr_confidence':page.get('mean_confidence')})
 out=ROOT/'data/processed/bank_chunks.json';out.write_text(json.dumps(chunks,ensure_ascii=False,indent=2)+'\n')
 return chunks
if __name__=='__main__':
 if '--ocr' in sys.argv:run_ocr()
 print('Indexed',len(index_bank()),'chunks')
