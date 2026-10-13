import io,unittest,zipfile
from freight.core import read_table,parse_orders,ValidationError
import openpyxl
class ExcelTests(unittest.TestCase):
 def workbook(self,rows):
  w=openpyxl.Workbook();s=w.active
  for r in rows:s.append(r)
  b=io.BytesIO();w.save(b);return b.getvalue()
 def test_xlsx_values(self):
  rows=parse_orders(self.workbook([['id','weight','origin','destination'],['A',800,'PORT06','PORT09']]),'o.xlsx');self.assertEqual(rows[0]['weight'],'800')
 def test_xlsx_formula_rejected(self):
  with self.assertRaises(ValidationError):read_table(self.workbook([['id','weight'],['A','=SUM(1,2)']]),'o.xlsx')
 def test_empty_xlsx(self):
  with self.assertRaises(ValidationError):read_table(self.workbook([]),'o.xlsx')
 def test_duplicate_headers_rejected(self):
  with self.assertRaises(ValidationError):read_table(b'id,id\n1,2','x.csv')
