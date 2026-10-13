"""Deterministic quotation engine. All monetary arithmetic uses Decimal."""
from __future__ import annotations
import csv, hashlib, io, json, re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

FIELDS = ['Carrier','Orig_Port','Dest_Port','Min_Weight_Quant','Max_Weight_Quant','Service_Level','Min_Cost','Rate','Mode_DSC','TPT_Day_Count','Carrier_Type']
CENT = Decimal('0.01')

class ValidationError(ValueError): pass

def number(value, label, minimum=Decimal('0')):
    if isinstance(value, bool): raise ValidationError(f'{label}必须是数值')
    try: n=Decimal(str(value).strip())
    except (InvalidOperation, ValueError): raise ValidationError(f'{label}必须是数值')
    if not n.is_finite() or n < minimum or n > Decimal('1000000000'):
        raise ValidationError(f'{label}须为 {minimum} 到 1,000,000,000 之间的有限数值')
    return n

def plain(n): return format(n, 'f')
def money(n): return format(n.quantize(CENT, rounding=ROUND_HALF_UP), '.2f')

def decode_csv(data):
    for encoding in ['utf-8-sig','gb18030']:
        try: return data.decode(encoding)
        except UnicodeError: pass
    raise ValidationError('文件编码不支持，请另存为 UTF-8 CSV')

def read_table(data: bytes, filename: str):
    if len(data)>8*1024*1024: raise ValidationError('文件不得超过 8 MB')
    if filename.lower().endswith('.csv'):
        text=decode_csv(data)
        if not text.strip(): raise ValidationError('文件为空')
        reader=csv.DictReader(io.StringIO(text))
        fields=[str(x or '').strip() for x in (reader.fieldnames or [])]
        rows=[]
        for raw in reader:
            if None in raw: raise ValidationError('CSV 行列数不一致')
            rows.append({str(k).strip():str(v or '').strip() for k,v in raw.items()})
    elif filename.lower().endswith('.xlsx'):
        from zipfile import ZipFile, BadZipFile
        try:
            with ZipFile(io.BytesIO(data)) as z:
                if sum(x.file_size for x in z.infolist())>32*1024*1024:
                    raise ValidationError('XLSX 解压后过大，请拆分文件')
            import openpyxl
            wb=openpyxl.load_workbook(io.BytesIO(data),read_only=True,data_only=False)
            try:
                iterator=wb.active.iter_rows(values_only=True)
                fields=[str(x or '').strip() for x in next(iterator)]
                rows=[]
                for row in iterator:
                    if not any(x is not None for x in row): continue
                    if any(isinstance(x,str) and x.startswith('=') for x in row):
                        raise ValidationError('请上传值表，不支持 Excel 公式作为报价或订单')
                    rows.append({k:str(v if v is not None else '').strip() for k,v in zip(fields,row)})
                    if len(rows)>20000: raise ValidationError('最多支持 20,000 行')
            finally: wb.close()
        except (BadZipFile,StopIteration): raise ValidationError('XLSX 文件损坏或为空')
    else: raise ValidationError('仅支持 .csv 和 .xlsx 文件')
    if not fields or any(not x for x in fields) or len(fields)!=len(set(fields)):
        raise ValidationError('表头不能有空列名或重复列名')
    if len(rows)>20000: raise ValidationError('最多支持 20,000 行')
    if not rows: raise ValidationError('文件中没有数据行')
    return fields,rows

@dataclass(frozen=True)
class Rate:
    row: int
    carrier: str
    origin: str
    destination: str
    low: Decimal
    high: Decimal
    service: str
    minimum: Decimal
    rate: Decimal
    mode: str
    days: int
    carrier_type: str

@dataclass(frozen=True)
class Catalog:
    id: str
    name: str
    sha256: str
    rates: tuple[Rate,...]
    rejected: tuple[dict,...]
    duplicates: int
    total: int

    @classmethod
    def load(cls, data, filename):
        fields,rows=read_table(data,filename)
        missing=set(FIELDS)-set(fields)
        if missing: raise ValidationError('报价表缺少字段：'+', '.join(sorted(missing)))
        rates=[];rejected=[];seen=set();duplicates=0
        for line,r in enumerate(rows,2):
            try:
                if any(not r.get(k) for k in FIELDS): raise ValidationError('必填字段为空')
                low=number(r['Min_Weight_Quant'],'最小重量');high=number(r['Max_Weight_Quant'],'最大重量')
                if high<low: raise ValidationError('最大重量小于最小重量')
                minimum=number(r['Min_Cost'],'最低收费');unit=number(r['Rate'],'单位费率')
                days=number(r['TPT_Day_Count'],'运输天数')
                if days!=days.to_integral_value(): raise ValidationError('运输天数须为整数')
                rate=Rate(line,r['Carrier'],r['Orig_Port'].upper(),r['Dest_Port'].upper(),low,high,r['Service_Level'].upper(),minimum,unit,r['Mode_DSC'].strip().upper(),int(days),r['Carrier_Type'])
                signature=tuple(getattr(rate,k) for k in Rate.__dataclass_fields__ if k!='row')
                if signature in seen: duplicates+=1;continue
                seen.add(signature);rates.append(rate)
            except ValidationError as e: rejected.append({'source_row':line,'reason':str(e)})
        if not rates: raise ValidationError('报价表没有有效报价；'+(rejected[0]['reason'] if rejected else '空表'))
        sha=hashlib.sha256(data).hexdigest()
        return cls(sha[:16],filename,sha,tuple(rates),tuple(rejected),duplicates,len(rows))

    def info(self):
        return {'id':self.id,'name':self.name,'sha256':self.sha256,'total':self.total,'valid':len(self.rates),'rejected':list(self.rejected),'duplicates':self.duplicates,'carriers':len({r.carrier for r in self.rates}),'origins':sorted({r.origin for r in self.rates}),'destinations':sorted({r.destination for r in self.rates}),'routes':len({(r.origin,r.destination) for r in self.rates})}

def order(raw: dict[str,Any]):
    weight=number(raw.get('weight',''),'货物重量',Decimal('0.01'))
    if weight != weight.quantize(CENT): raise ValidationError('重量最多支持两位小数，防止落入报价区间的小数间隙')
    origin=str(raw.get('origin','')).strip().upper();destination=str(raw.get('destination','')).strip().upper()
    if not origin or not destination: raise ValidationError('起运港与目的港为必填项')
    if any(not re.fullmatch(r'[A-Z0-9_-]{1,40}',x) for x in [origin,destination]): raise ValidationError('港口代码仅支持英文字母、数字、下划线与连字符')
    raw_days=raw.get('max_days')
    if raw_days in [None,'']: days=None
    else:
        n=number(raw_days,'最大运输天数')
        if n!=n.to_integral_value(): raise ValidationError('最大运输天数必须为整数')
        days=int(n)
    return {'id':str(raw.get('id') or 'ORDER-001')[:100],'weight':plain(weight),'origin':origin,'destination':destination,'max_days':days,'service':str(raw.get('service') or '').strip().upper(),'mode':str(raw.get('mode') or '').strip().upper()}

def compare(catalog:Catalog,raw:dict):
    o=order(raw);w=Decimal(o['weight']);candidates=[];reasons={'route':0,'weight':0,'service':0,'mode':0,'time':0}
    for r in catalog.rates:
        if (r.origin,r.destination)!=(o['origin'],o['destination']): reasons['route']+=1;continue
        if not r.low<=w<=r.high: reasons['weight']+=1;continue
        if o['service'] and o['service']!=r.service: reasons['service']+=1;continue
        if o['mode'] and o['mode']!=r.mode: reasons['mode']+=1;continue
        product=w*r.rate;cost=max(r.minimum,product);eligible=o['max_days'] is None or r.days<=o['max_days']
        if not eligible: reasons['time']+=1
        candidates.append({'source_row':r.row,'carrier':r.carrier,'service':r.service,'mode':r.mode,'days':r.days,'weight_min':plain(r.low),'weight_max':plain(r.high),'minimum_cost':plain(r.minimum),'unit_rate':plain(r.rate),'weight_cost':plain(product),'raw_cost':plain(cost),'cost':money(cost),'eligible':eligible,'minimum_applied':r.minimum>product,'formula':f"max({plain(r.minimum)}, {o['weight']} × {plain(r.rate)}) = {money(cost)}",'reason':'符合要求' if eligible else f"超出 {o['max_days']} 天时效"})
    # Compare exact costs; rounding is presentation only. Stable row index resolves ties.
    candidates.sort(key=lambda r:(Decimal(r['raw_cost']),r['days'],r['carrier'],r['source_row']))
    eligible=[r for r in candidates if r['eligible']]
    best=eligible[0] if eligible else None
    for r in candidates:
        r['recommended']=r is best
        r['pareto']=r['eligible'] and not any(c['eligible'] and Decimal(c['raw_cost'])<=Decimal(r['raw_cost']) and c['days']<=r['days'] and (Decimal(c['raw_cost'])<Decimal(r['raw_cost']) or c['days']<r['days']) for c in candidates)
    fastest=min(candidates,key=lambda r:(r['days'],Decimal(r['raw_cost']),r['source_row'])) if candidates else None
    savings=Decimal(fastest['raw_cost'])-Decimal(best['raw_cost']) if fastest and best else None
    # Baseline is explicitly the cheapest of the fastest matched quotes, not user's actual spend.
    message='已推荐满足约束的最低成本方案' if best else ('路线与重量匹配，但没有方案满足时效；请调整约束' if candidates else '没有可用报价，请检查路线、重量、服务和运输方式')
    return {'order':o,'status':'ok' if best else 'no_feasible_plan','message':message,'candidates':candidates,'matched':len(candidates),'eligible':len(eligible),'recommendation':best,'fastest_baseline':fastest,'savings_vs_fastest':money(savings) if savings is not None else None,'savings_percent':round(float(savings/Decimal(fastest['raw_cost'])*100),2) if savings is not None and Decimal(fastest['raw_cost']) else None,'exclusions':reasons,'catalog':catalog.info(),'billing':{'formula':'max(Min_Cost, 货物重量 × Rate)','weight_interval':'两端包含；重量最多两位小数','rounding':'Decimal 精确比较，显示金额四舍五入到两位小数','units':'重量单位与币种须由业务确认；展示为报价单位 / 货币单位','scope':'每单整体交给一个承运商；不包含燃油、税费、体积重、容量约束与跨单拼单'},'trace':[{'tool':'validate_order','status':'ok','detail':f"{o['origin']} → {o['destination']} · {o['weight']} 报价单位"},{'tool':'match_rates','status':'ok','detail':f'有效报价 {len(catalog.rates)} 条，匹配 {len(candidates)} 条'},{'tool':'calculate_costs','status':'ok','detail':'按最低收费与重量计费较高者核算'},{'tool':'apply_constraints','status':'ok','detail':f'满足时效 {len(eligible)} 条'},{'tool':'recommend_plan','status':'ok' if best else 'no_result','detail':f"{best['carrier']} · {best['cost']}" if best else message}]}

def batch(catalog,rows):
    if not rows or len(rows)>1000: raise ValidationError('批量订单须为 1 到 1,000 行')
    results=[];seen=set()
    for i,raw in enumerate(rows,2):
        try:
            normalized=order(raw)
            if normalized['id'] in seen: raise ValidationError('订单编号重复，请使用唯一编号')
            seen.add(normalized['id'])
            r=compare(catalog,normalized);r['input_row']=i;results.append(r)
        except ValidationError as e: results.append({'input_row':i,'order':{'id':str(raw.get('id') or f'ROW-{i}')},'status':'invalid','message':str(e),'recommendation':None})
    valid=[r for r in results if r['recommendation']]
    return {'results':results,'summary':{'orders':len(rows),'recommended':len(valid),'no_feasible':sum(r['status']=='no_feasible_plan' for r in results),'invalid':sum(r['status']=='invalid' for r in results),'total_cost':money(sum((Decimal(r['recommendation']['raw_cost']) for r in valid),Decimal('0'))),'note':'总费用仅汇总成功推荐的订单；未推荐订单未计入。逐单独立最优，不包含拼单或承运商容量优化。'},'catalog':catalog.info()}

def parse_orders(data,filename):
    fields,rows=read_table(data,filename)
    mapping={'订单编号':'id','重量':'weight','货物总重量':'weight','起运港':'origin','起运港代码':'origin','目的港':'destination','目的港代码':'destination','最大运输天数':'max_days','服务等级':'service','运输方式':'mode'}
    fields=[mapping.get(f,f) for f in fields]
    if not {'id','weight','origin','destination'}<=set(fields): raise ValidationError('订单文件须包含 id、weight、origin、destination，可选 max_days、service、mode；也支持中文表头')
    return [{mapping.get(k,k):v for k,v in r.items()} for r in rows]
