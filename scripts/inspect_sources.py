import pandas as pd,json
from pathlib import Path
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'data/raw/companies').glob('*.csv'):
 d=pd.read_csv(p,sep=';',dtype=str)
 print(p.name,len(d), 'duplicates',int(d.duplicated().sum()),'missing',d.isna().sum().to_dict(),flush=True)
 if 'MAINOKVED_CODE' in d:
  d['rev']=pd.to_numeric(d.REVENUE_COMPANY,errors='coerce')
  print(d[d.rev>15e9].groupby(['MAINOKVED_CODE','MAINOKVED_NAME']).size().sort_values(ascending=False).head(25).to_string(),flush=True)
  print(d[d.MAINOKVED_CODE.str.startswith('24',na=False)&(d.rev>15e9)].head(20).to_json(orient='records',force_ascii=False),flush=True)
for p in (ROOT/'data/raw/analytics').glob('*.pdf'):
 r=PdfReader(p);out=ROOT/'data/processed/bank_text'/f'{p.stem}.txt';out.parent.mkdir(parents=True,exist_ok=True)
 pages=[page.extract_text() or '' for page in r.pages]
 out.write_text('\n\n'.join(f'=== PAGE {i+1} ===\n{t}' for i,t in enumerate(pages)))
 print('\nPDF',p.name,'PAGES',len(pages),'\n',pages[0][:2400],flush=True)
