"""Actual isolated Edge UI checks. Optional one explicit UI native submission.
No user browser profile, live endpoint or unrelated process is accessed.
"""
import argparse,base64,hashlib,json,socket,subprocess,tempfile,time
from pathlib import Path
from urllib.request import urlopen
import websocket
P=Path(__file__).resolve().parent;ROOT=P.parents[1]
OUT=ROOT/'data/research/strategy_playground_v1_20260921'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--submit-native',action='store_true');ap.add_argument('--model-switch',action='store_true');a=ap.parse_args()
    edge=next((Path(p) for p in [r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'] if Path(p).exists()),None)
    if edge is None:raise RuntimeError('Edge unavailable; browser checks not performed')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    checks=[];errors=[];ws=None;proc=None;started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='ui_v1_',dir=OUT,ignore_cleanup_errors=True) as profile:
        try:
            proc=subprocess.Popen([str(edge),'--headless=new','--disable-gpu','--no-first-run','--disable-extensions','--disable-background-networking',
                '--remote-debugging-address=127.0.0.1','--remote-debugging-port='+str(port),'--user-data-dir='+profile,'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            target=None
            for _ in range(80):
                try:
                    with urlopen('http://127.0.0.1:'+str(port)+'/json',timeout=.5) as r:pages=json.load(r)
                    target=next((x for x in pages if x['type']=='page'),None)
                    if target:break
                except Exception:pass
                time.sleep(.1)
            if not target:raise RuntimeError('Isolated DevTools not ready')
            ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=12,suppress_origin=True);seq=0
            def call(method,params=None):
                nonlocal seq
                seq+=1;ident=seq;ws.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
                while True:
                    z=json.loads(ws.recv())
                    if z.get('method')=='Runtime.exceptionThrown':errors.append(z)
                    if z.get('id')==ident:
                        if 'error' in z:raise RuntimeError(str(z['error']))
                        return z.get('result',{})
            def ev(expression):
                z=call('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True})
                if z.get('exceptionDetails'):raise RuntimeError(str(z['exceptionDetails']))
                return z.get('result',{}).get('value')
            def wait(expression,seconds=10):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    if ev(expression):return
                    time.sleep(.1)
                raise AssertionError('UI condition failed: '+expression+'; '+str(ev("document.getElementById('message')?.textContent")))
            call('Page.enable');call('Runtime.enable')
            call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1300,'deviceScaleFactor':1,'mobile':False})
            call('Page.navigate',{'url':'http://127.0.0.1:4332/'})
            wait("typeof base!=='undefined' && base!==null && document.querySelectorAll('#market option').length===24")
            checks.append('24_markets_real_cached_R65_native_loaded')
            assert ev("document.getElementById('timeline').max==='300000' && document.getElementById('timeline').min==='0'")
            checks.append('full_300_second_timestamp_axis')
            before=ev('JSON.stringify([base.fills,base.final,base.source_hashes])')
            ev('pause();setCursor(80000)')
            assert ev("document.getElementById('elapsed').textContent==='01:20.000' && at(base,80000).ms<=80000 && at(base,0)===null")
            checks.append('scrub_80_seconds_strict_past_snapshot')
            ev("document.getElementById('speed').value='30';document.getElementById('play').click()")
            time.sleep(.5);ev('pause()');position=ev('cursor')
            assert 82000<position<115000,position
            time.sleep(.2);assert ev('cursor')==position
            checks.append('30x_play_and_pause_use_elapsed_display_time')
            ev("document.getElementById('speed').value='0.5';setCursor(1000);setCursor(299000);setCursor(80000)")
            assert ev('JSON.stringify([base.fills,base.final,base.source_hashes])')==before
            assert ev("!Object.keys(request()).some(k=>['speed','horizon','steps'].includes(k))")
            checks.append('speed_and_scrub_leave_native_fills_and_request_unchanged')
            ev("document.getElementById('repairBias').value=(candidate&&candidate.settings.repair_bias===0.25?'0':'0.25');document.getElementById('repairBias').dispatchEvent(new Event('input',{bubbles:true}))")
            if ev('candidate!==null'):assert ev("document.getElementById('candidateTag').textContent.includes('舊結果')")
            checks.append('parameter_change_does_not_relabel_old_curve')
            assert ev('document.documentElement.scrollWidth<=innerWidth')
            checks.append('desktop_no_horizontal_overflow')
            h=min(6500,int(call('Page.getLayoutMetrics')['cssContentSize']['height']))
            shot=call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True,'clip':{'x':0,'y':0,'width':1440,'height':h,'scale':1}})['data']
            (OUT/'V1_UI_DESKTOP.png').write_bytes(base64.b64decode(shot))
            call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True});time.sleep(.2)
            assert ev('document.documentElement.scrollWidth<=innerWidth')
            checks.append('mobile_390px_no_horizontal_overflow')
            h=min(6500,int(call('Page.getLayoutMetrics')['cssContentSize']['height']))
            shot=call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True,'clip':{'x':0,'y':0,'width':390,'height':h,'scale':1}})['data']
            (OUT/'V1_UI_MOBILE.png').write_bytes(base64.b64decode(shot))
            call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1300,'deviceScaleFactor':1,'mobile':False})
            submitted=None
            if a.submit_native:
                # Exercise the same explicit user-facing controls and Run button.
                ev("pause();setCursor(80000);document.getElementById('addBias').value='-0.5';document.getElementById('repairBias').value='0.25';document.getElementById('activeBias').value='0';document.getElementById('keepBias').value='0';document.getElementById('candidateSeed').value='20260920';document.getElementById('additionMode').value='DYNAMIC';document.getElementById('clockMode').value='P50';document.getElementById('applyMode').value='CURSOR';document.getElementById('note').value='V1 UI acceptance: preferences change at 80s, engineering test only.';dirty()")
                if a.model_switch:
                    ev("document.getElementById('candidateSeed').value='20260921';document.getElementById('addBias').value='0';document.getElementById('repairBias').value='0';document.getElementById('applyMode').value='START';document.getElementById('note').value='V1 UI acceptance: switch frozen R65 checkpoint, no training.';dirty()")
                packet=ev('request()')
                assert packet['apply_ms']==(0 if a.model_switch else 80000)
                ev("document.getElementById('run').click()")
                wait("document.getElementById('jobId').textContent.startsWith('pgv1-c-')",seconds=32)
                submitted={'job_id':ev("document.getElementById('jobId').textContent"),'request':packet,
                    'scope':'EXPLICIT_BROWSER_BUTTON_NATIVE_REQUEST','state':'SUBMITTED_NOT_YET_VALIDATED'}
                (OUT/('UI_MODEL_SWITCH_SUBMISSION.json' if a.model_switch else 'UI_NATIVE_SUBMISSION.json')).write_text(json.dumps(submitted,ensure_ascii=False,indent=2),encoding='utf-8')
                checks.append('real_UI_run_button_submitted_timed_preferences')
            assert not errors,errors
            checks.append('no_javascript_runtime_exceptions')
            report={'status':'PASS','browser':'Edge isolated headless','checks':checks,'count':len(checks),'seconds':time.monotonic()-started,
                'fill_snapshot_sha256':hashlib.sha256(before.encode()).hexdigest(),'native_submission':submitted,'live_changes':0}
            name='UI_MODEL_SWITCH_VALIDATION.json' if a.model_switch else 'UI_VALIDATION_WITH_SUBMIT.json' if a.submit_native else 'UI_VALIDATION.json'
            (OUT/name).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
            try:call('Browser.close')
            except Exception:pass
        except Exception as e:
            report={'status':'FAIL','error':repr(e),'checks_passed':checks,'javascript_errors':errors}
            (OUT/'UI_FAILURE.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report));raise
        finally:
            if ws:
                try:ws.close()
                except Exception:pass
            if proc:
                try:proc.wait(timeout=4)
                except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=4)
if __name__=='__main__':main()
