import json,os,subprocess,tempfile,time,urllib.request,sys,socket
from pathlib import Path
root=Path(__file__).resolve().parents[1]
exe=root/'dist'/('FreightPilot.exe' if os.name=='nt' else 'FreightPilot')
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
with tempfile.TemporaryDirectory() as data:
    proc=subprocess.Popen([str(exe),'--no-browser','--port',str(port)],env={**os.environ,'FREIGHT_DATA_DIR':data},stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    try:
        for attempt in range(60):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as r:h=json.load(r)
                break
            except Exception:time.sleep(.5)
        else:raise RuntimeError('packaged service did not start')
        req=urllib.request.Request(f'http://127.0.0.1:{port}/api/compare',data=json.dumps({'order':{'weight':800,'origin':'PORT06','destination':'PORT09','max_days':3,'service':'DTD'}}).encode(),headers={'Content-Type':'application/json','X-Freight-Token':h['token']})
        with urllib.request.urlopen(req,timeout=10) as r:result=json.load(r)
        assert result['recommendation']['cost']=='67.84',result
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/export/{result['result_id']}") as r:
            import io,zipfile
            with zipfile.ZipFile(io.BytesIO(r.read())) as z:assert '比价结果.xlsx' in z.namelist()
        print('Packaged executable verified: health, quote=67.84, XLSX export')
    finally:proc.terminate();proc.wait(timeout=10)
