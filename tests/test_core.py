import csv, io, sys, unittest, zipfile
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from freight.core import Catalog,compare,batch,order,ValidationError,FIELDS,parse_orders
from freight.reports import result_zip

def rates(rows):
    stream=io.StringIO();writer=csv.writer(stream);writer.writerow(FIELDS)
    for carrier,lo,hi,minimum,rate,days in rows:writer.writerow([carrier,'PORT06','PORT09',lo,hi,'DTD',minimum,rate,'AIR ',days,'TYPE'])
    return Catalog.load(stream.getvalue().encode(),'rates.csv')

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.cat=rates([('A',0,100,20,1,3),('B',0,100,10,2,1)])
        self.order={'weight':'10','origin':'port06','destination':'PORT09','max_days':3}
    def test_minimum_is_floor_not_surcharge(self):
        r=compare(self.cat,self.order);self.assertEqual(r['recommendation']['cost'],'20.00');self.assertEqual(r['recommendation']['carrier'],'B')
    def test_weight_rate_can_exceed_floor(self):
        r=compare(self.cat,{**self.order,'weight':30});self.assertEqual(r['recommendation']['cost'],'30.00')
    def test_weight_endpoints_inclusive(self):
        c=rates([('A',10,20,0,1,1)])
        for w in ['10','20']:self.assertEqual(compare(c,{**self.order,'weight':w})['matched'],1)
        for w in ['9.99','20.01']:self.assertEqual(compare(c,{**self.order,'weight':w})['matched'],0)
    def test_time_limit_excludes_cheaper_slow_plan(self):
        r=compare(self.cat,{**self.order,'weight':30,'max_days':1});self.assertEqual(r['recommendation']['carrier'],'B');self.assertEqual(r['eligible'],1)
    def test_no_feasible_does_not_recommend(self):
        r=compare(self.cat,{**self.order,'max_days':0});self.assertIsNone(r['recommendation']);self.assertEqual(r['matched'],2)
    def test_zero_day_valid(self):
        c=rates([('A',0,100,0,1,0)]);r=compare(c,{**self.order,'max_days':0});self.assertEqual(r['eligible'],1)
    def test_optional_days(self):
        self.assertEqual(compare(self.cat,{**self.order,'max_days':''})['eligible'],2)
    def test_decimal_exact_cost(self):
        c=rates([('A',0,100,0,'.1',1)]);r=compare(c,{**self.order,'weight':'.3'});self.assertEqual(r['recommendation']['raw_cost'],'0.03')
    def test_rank_before_rounding(self):
        c=rates([('A',0,100,0,'1.004',1),('B',0,100,0,'1.003',3)])
        self.assertEqual(compare(c,{**self.order,'weight':1})['recommendation']['carrier'],'B')
    def test_invalid_weight(self):
        for w in ['0','-1','NaN','Infinity','1.001',True]:
            with self.subTest(w=w),self.assertRaises(ValidationError):order({**self.order,'weight':w})
    def test_non_integer_time(self):
        with self.assertRaises(ValidationError):order({**self.order,'max_days':'1.5'})
    def test_bad_route(self):
        with self.assertRaises(ValidationError):order({**self.order,'origin':'<script>'})
    def test_unknown_route_no_match(self):
        self.assertEqual(compare(self.cat,{**self.order,'origin':'PORT99'})['matched'],0)
    def test_service_filter(self):
        self.assertEqual(compare(self.cat,{**self.order,'service':'DTP'})['matched'],0)
    def test_mode_filter(self):
        self.assertEqual(compare(self.cat,{**self.order,'mode':'GROUND'})['matched'],0)
    def test_duplicate_quote_removed(self):
        c=rates([('A',0,100,0,1,1)]*2);self.assertEqual(c.duplicates,1);self.assertEqual(len(c.rates),1)
    def test_invalid_quote_isolated(self):
        c=rates([('A',0,100,0,1,1),('B',50,10,0,1,1)]);self.assertEqual(len(c.rejected),1);self.assertEqual(c.rejected[0]['source_row'],3)
    def test_batch_invalid_does_not_block_other_orders(self):
        r=batch(self.cat,[{**self.order,'id':'ONE'},{**self.order,'id':'TWO','weight':0},{**self.order,'id':'THREE','max_days':0}]);self.assertEqual(r['summary']['recommended'],1);self.assertEqual(r['summary']['invalid'],1);self.assertEqual(r['summary']['no_feasible'],1);self.assertEqual(r['summary']['total_cost'],'20.00')
    def test_duplicate_order_rejected(self):
        r=batch(self.cat,[{**self.order,'id':'ONE'}]*2);self.assertEqual(r['summary']['invalid'],1)
    def test_csv_formula_injection_escaped(self):
        r=compare(rates([('=SUM(A1)',0,100,0,1,1)]),self.order)
        with zipfile.ZipFile(io.BytesIO(result_zip(r))) as z:self.assertIn("'=SUM(A1)",z.read('比价明细.csv').decode('utf-8-sig'))
    def test_export_snapshot_same_recommendation(self):
        r=compare(self.cat,self.order)
        import json
        with zipfile.ZipFile(io.BytesIO(result_zip(r))) as z:self.assertEqual(json.loads(z.read('运行快照.json'))['recommendation'],r['recommendation'])
    def test_chinese_order_headers(self):
        rows=parse_orders('订单编号,重量,起运港,目的港,最大运输天数\nA,10,PORT06,PORT09,3'.encode(),'orders.csv');self.assertEqual(rows[0]['weight'],'10')
    def test_real_competition_dataset(self):
        c=Catalog.load((Path(__file__).resolve().parents[1]/'samples/FreightRates.csv').read_bytes(),'FreightRates.csv');self.assertEqual(len(c.rates),1537)
        r=compare(c,{'weight':800,'origin':'PORT06','destination':'PORT09','max_days':3,'service':'DTD','mode':'AIR'})
        self.assertEqual(r['recommendation']['cost'],'67.84');self.assertEqual(r['recommendation']['days'],3);self.assertEqual(r['savings_vs_fastest'],'162.88')
        r=compare(c,{**r['order'],'max_days':1});self.assertEqual(r['recommendation']['cost'],'230.72')
    def test_pareto_front(self):
        c=rates([('A',0,100,0,1,3),('B',0,100,0,2,1),('C',0,100,0,3,3)])
        r=compare(c,self.order);self.assertEqual({x['carrier'] for x in r['candidates'] if x['pareto']},{'A','B'})

if __name__=='__main__':unittest.main()
