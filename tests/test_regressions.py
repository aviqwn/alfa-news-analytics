import json
import tempfile
import unittest
from pathlib import Path
from backend.agent import Agent
from backend.config import Config
from backend.server import App, ApiError
from backend.sources import classify, retrieve

ROOT=Path(__file__).resolve().parents[1]

class RegressionTests(unittest.TestCase):
    def test_delivery_not_interest_rate(self):
        self.assertEqual(classify('Поставщик изменил условия доставки и поставки оборудования'), 'general')
        self.assertEqual(classify('Ключевая ставка сохранена, кредитные ставки требуют проверки'), 'rates')

    def test_steel_not_fuel_bank_position(self):
        chunks=[{'id':'fuel','title':'Инфляция и бензин','text':'Рост цен на топливо и сырье увеличивает инфляцию.',
                 'topics':['inflation','fuel','fx'],'date':'2026-10-01','manual_verified':True}]
        self.assertEqual(retrieve(chunks,'Поставщики повышают цены на сталь','commodities','2026-10-22'),[])

    def test_nominal_and_unknown_currency_do_not_imply_fx(self):
        self.assertEqual(classify('Памятная серебряная монета номиналом 3 рубля'), 'general')
        self.assertEqual(classify('Условия доставки изменились; суммы и валюта не известны'), 'general')
        self.assertEqual(classify('Валютный курс меняется, рубль укрепляется'), 'fx')

    def test_both_loan_and_deposit_are_explained(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as tmp:
            app=App(ROOT,Path(tmp)/'test.sqlite',Config(data_mode='synthetic'))
            run=app.agent.analyze('DEMO_TRADE_A','demo-rates','cfo')
            text=run['interpretation'][0]['text']
            self.assertIn('депозит',text)
            self.assertIn('кредит',text)

    def test_bad_identifier_is_client_error(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as tmp:
            app=App(ROOT,Path(tmp)/'test.sqlite',Config(data_mode='synthetic'))
            with self.assertRaises(ApiError) as caught:
                app.dispatch('POST','/api/analyze',{}, {'company_id':[],'event_id':{},'role':'ceo'})
            self.assertEqual(caught.exception.status,400)

if __name__=='__main__':unittest.main()
