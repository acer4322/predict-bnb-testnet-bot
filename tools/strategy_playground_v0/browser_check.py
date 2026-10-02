"""Actual isolated Edge headless UI checks; no user browser profile is opened."""
import base64,json,os,shutil,socket,subprocess,tempfile,time
from pathlib import Path
from urllib.request import urlopen
import websocket
import core

OUT=core.STORE

def main():
    edge=next((Path(p) for p in [r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'] if Path(p).exists()),None)
    if edge is None:raise RuntimeError('Edge unavailable: browser checks NOT performed')
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    checks=[];errors=[];started=time.monotonic();ws=None;p=None
    with tempfile.TemporaryDirectory(prefix='ui_profile_',dir=OUT,ignore_cleanup_errors=True) as prof:
        try:
            p=subprocess.Popen([str(edge),'--headless=new','--disable-gpu','--no-first-run','--disable-extensions','--disable-background-networking',
                '--remote-debugging-address=127.0.0.1','--remote-debugging-port='+str(port),'--user-data-dir='+prof,'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            target=None
            for _ in range(80):
                try:
                    with urlopen('http://127.0.0.1:'+str(port)+'/json',timeout=.5) as r:targets=json.load(r)
                    target=next((x for x in targets if x['type']=='page'),None)
                    if target:break
                except Exception:pass
                time.sleep(.1)
            if target is None:raise RuntimeError('DevTools readiness not confirmed')
            ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=8,suppress_origin=True)
            seq=0
            def call(method,params=None):
                nonlocal seq
                seq+=1;ident=seq;ws.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
                while True:
                    z=json.loads(ws.recv())
                    if z.get('method')=='Runtime.exceptionThrown':errors.append(z)
                    if z.get('id')==ident:
                        if 'error' in z:raise RuntimeError(str(z['error']))
                        return z.get('result',{})
            def ev(js):
                z=call('Runtime.evaluate',{'expression':js,'returnByValue':True,'awaitPromise':True})
                if z.get('exceptionDetails'):raise RuntimeError(str(z['exceptionDetails']))
                return z.get('result',{}).get('value')
            def wait(js):
                for _ in range(80):
                    if ev(js):return
                    time.sleep(.08)
                raise AssertionError('UI condition not met: '+js+'; message='+str(ev("document.getElementById('message')?.textContent")))
            call('Page.enable');call('Runtime.enable')
            call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1400,'deviceScaleFactor':1,'mobile':False})
            call('Page.navigate',{'url':'http://127.0.0.1:4331/'})
            wait("document.getElementById('connection')?.textContent.includes('7 個') && document.querySelectorAll('.action').length===14")
            checks.append('catalog_7_roots_14_actions')
            ev("document.getElementById('compare').click()")
            wait("document.querySelectorAll('#chart path').length===4 && document.getElementById('message').textContent.includes('已完成')")
            checks.append('paired_branch_4_svg_paths')
            ev("document.getElementById('repair_weight').value='2';document.getElementById('repair_weight').dispatchEvent(new Event('input',{bubbles:true}))")
            assert ev("document.querySelectorAll('#chart path').length===0 && [...document.querySelectorAll('.action')].every(b=>b.disabled)")
            checks.append('parameter_change_invalidates_old_actions_and_chart')
            ev("document.getElementById('refresh').click()")
            wait("!document.body.hasAttribute('aria-busy') && !document.querySelector('.action').disabled")
            ev("document.querySelector('.action').click()")
            wait("document.getElementById('eventLabel').textContent.includes('事件 1')")
            checks.append('manual_keep_one_event')
            ev("document.getElementById('undo').click()")
            wait("document.getElementById('eventLabel').textContent==='真實 native 起點'")
            checks.append('research_branch_undo')
            ev("document.getElementById('formula').value=\"__import__('os')\";document.getElementById('refresh').click()")
            wait("document.getElementById('message').classList.contains('error')")
            checks.append('unsafe_formula_shown_as_error')
            ev("document.getElementById('balanced').click()")
            wait("!document.body.hasAttribute('aria-busy') && !document.getElementById('message').classList.contains('error')")
            ev("document.getElementById('stop').click()")
            wait("document.getElementById('eventLabel').textContent.includes('STOP 已鎖定')")
            assert ev("[...document.querySelectorAll('.action')].every(b=>b.disabled)")
            checks.append('manual_stop_latches_and_disables_new_orders')
            ev("document.getElementById('reset').click()")
            wait("document.getElementById('eventLabel').textContent==='真實 native 起點' && !document.body.hasAttribute('aria-busy')")
            ev("document.getElementById('compare').click()")
            wait("document.querySelectorAll('#chart path').length===4 && !document.body.hasAttribute('aria-busy')")
            desktop_overflow=ev('document.documentElement.scrollWidth>innerWidth')
            assert not desktop_overflow
            height=min(6500,int(call('Page.getLayoutMetrics')['cssContentSize']['height']))
            image=call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True,'clip':{'x':0,'y':0,'width':1440,'height':height,'scale':1}})['data']
            (OUT/'UI_DESKTOP.png').write_bytes(base64.b64decode(image));checks.append('desktop_layout_no_horizontal_overflow')
            call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
            time.sleep(.2)
            assert not ev('document.documentElement.scrollWidth>innerWidth')
            height=min(6500,int(call('Page.getLayoutMetrics')['cssContentSize']['height']))
            image=call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True,'clip':{'x':0,'y':0,'width':390,'height':height,'scale':1}})['data']
            (OUT/'UI_MOBILE.png').write_bytes(base64.b64decode(image));checks.append('mobile_390px_no_horizontal_overflow')
            assert not errors,errors
            checks.append('no_javascript_runtime_exceptions')
            report={'status':'PASS','browser':'Edge isolated headless','checks':checks,'count':len(checks),'seconds':time.monotonic()-started,
                    'native_hft_runs':0,'worker_jobs':0,'live_changes':0,'desktop_screenshot':'UI_DESKTOP.png','mobile_screenshot':'UI_MOBILE.png'}
            (OUT/'UI_VALIDATION.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
            try:call('Browser.close')
            except Exception:pass
        except Exception as e:
            report={'status':'FAIL','error':repr(e),'passed_before_failure':checks,'javascript_errors':errors}
            (OUT/'UI_VALIDATION.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report));raise
        finally:
            if ws:
                try:ws.close()
                except Exception:pass
            if p:
                try:p.wait(timeout=4)
                except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=4)
if __name__=='__main__':main()
