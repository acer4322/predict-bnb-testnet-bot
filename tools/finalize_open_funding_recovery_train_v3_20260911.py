"""Finalize terminal, independently audited results. No train/replay/large reads on host."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r4_v0/p0_provenance_v1'
D=ROOT/'data/research/lan_worker_returns/open-funding-recovery-train-20260911-v3'
A=ROOT/'data/research/lan_worker_returns/open-funding-recovery-audit-20260911-v3'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def main():
    p=D/'COMPACT.json';ap=A/'COMPACT.json'
    assert p.stat().st_size<160000 and ap.stat().st_size<45000
    s=json.loads(p.read_text(encoding='utf-8'));a=json.loads(ap.read_text(encoding='utf-8'))
    assert s['verdict']=='RECOVERED_OPEN_FUNDING_TRAINING_AND_FROZEN_CHECK_COMPLETED'
    assert a['verdict']=='INDEPENDENT_RECOVERY_ACCOUNTING_CLOSURE_AND_TRAINING_AUDIT_PASS'
    assert sha(p)==a['source_result_sha256'] and sha(D/'FROZEN_WHOLE_POLICY.json')==a['frozen_policy_sha256']
    for e in s['evaluations']:
        t=D/e['trace']['path'];assert t.stat().st_size<8*1024**2 and sha(t)==e['trace']['sha256']
    golden=s['evaluations'][0];check=[e for e in s['evaluations'] if e['split']=='PIPELINE_CHECK']
    assert len(check)==2 and all(e['unresolved_owners']==0 for e in s['evaluations'])
    regular=[e for e in s['evaluations'] if e['split']!='GOLDEN']
    baseline=[e for e in s['evaluations'] if e['split']=='TRAIN' and e['variant']=='INITIAL']
    selected=[e for e in s['evaluations'] if e['split']=='TRAIN' and e['variant']==s['selected']]
    def simple(e):
        keys=['variant','market_id','split','submits','cancel_requests','native_receipts','zero_fill_orders','partial_orders',
            'unresolved_owners','pending_cash','final_cost','UP_branch','DOWN_branch','late_new_orders','max_live_owners',
            'min_requested_qty','max_requested_qty','resource_censor_events','receipt_support']
        z={k:e[k] for k in keys};z.update(loss=e['loss']['total_loss'],
            worst_branch=min(e['UP_branch'],e['DOWN_branch']),peak_cash_requirement=e['trace']['peak_cash_requirement'],
            drain_steps=len(e['terminal_drain']))
        return z
    summary=dict(version='OPEN_FUNDING_RECOVERY_TRAIN_ACCEPTANCE_V3',
        verdict='BLOCKERS_RESOLVED_AND_JOINT_TRAINING_COMPLETED_NO_STRATEGY_PROMOTION',
        jobs_terminal_and_collected=True,worker=s['worker'],max_threads=4,
        native_runs=11,unique_consumed_markets=3,golden_runs=1,train_and_check_runs=10,
        funding_mode=s['funding_mode'],capital_cap=None,legacy180_restored=False,live_changes=0,
        funding_unit_tests=s['unit_tests']['passed'],recovery_unit_tests=s['recovery_tests']['passed'],
        golden_source_prefix_exact=s['golden_source_prefix_exact'],golden_final_unresolved=golden['unresolved_owners'],
        golden_prefix_counts=golden['source_prefix']['counts'],golden_receipt_support=golden['receipt_support'],
        old_resource_limit=32,new_engineering_ceiling=4096,new_capacity_proof='max2NEW per less2048source observations; no post-end NEW',
        regular_resource_censor_events=sum(e['resource_censor_events'] for e in regular),
        maximum_observed_concurrency=max(e['max_live_owners'] for e in regular),
        unresolved_owners_total=0,appended_market_events_for_drain=0,
        joint_parameter_updates=1,parameter_count=12,selected=s['selected'],train_losses=s['train_losses'],
        train_loss_improvement=s['train_loss_improvement'],check_loss_improvement=s['check_loss_improvement'],
        initial_check=simple(check[0]),selected_check=simple(check[1]),
        initial_train=[simple(e) for e in baseline],selected_train=[simple(e) for e in selected],
        frozen_before_check=s['freeze_before_check'],no_post_check_retune=True,
        actual_target_order_labels_known=False,source_era='2026-09-07 consumed BTC5M; not recent-size transfer',
        policy_type='12parameter coupled whole-passive-controller; not neural network/full4channel teacher',
        native_active_implemented=False,unseen_holdout=False,
        audit={k:a[k] for k in ['native_submits_audited','raw_receipts_audited','effective_receipt_advances_audited',
            'frames_audited','maximum_score_reproduction_error','original_zero_count_disagreement_explained']},
        source_result_path=p.relative_to(ROOT).as_posix(),source_result_sha256=sha(p),
        audit_result_path=ap.relative_to(ROOT).as_posix(),audit_result_sha256=sha(ap),
        frozen_policy_path=(D/'FROZEN_WHOLE_POLICY.json').relative_to(ROOT).as_posix(),frozen_policy_sha256=a['frozen_policy_sha256'],
        runtime_path='tools/open_funding_recovery_runtime_v3.py',
        next_scope='Continue whole-system training from the frozen result and economic/trajectory diagnostics; no repeat blocker audit, no money/time caps restored. Active remains explicit capability gap.')
    sp=R/'MINIMAL_STUDENT_OPEN_FUNDING_RECOVERY_ACCEPTANCE_V3_20260911.json'
    assert not sp.exists();sp.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    train_table='\n'.join('| '+k+' | '+f'{v:.9f}'+' |' for k,v in s['train_losses'].items())
    result_table='\n'.join('| '+label+' | '+f"{e['loss']['total_loss']:.9f} | {e['submits']} | {e['receipt_support']['effective_cumulative_advance_receipts']} | {e['UP_branch']:+.4f} | {e['DOWN_branch']:+.4f} | {e['trace']['peak_cash_requirement']:.2f} |" for label,e in [('初始策略',check[0]),('凍結策略',check[1])])
    risk_table='\n'.join('| '+str(b['market_id'])+' | '+f"{min(b['UP_branch'],b['DOWN_branch']):+.4f} | {min(c['UP_branch'],c['DOWN_branch']):+.4f} |" for b,c in zip(baseline,selected))
    report=f'''# 無金額上限學生：阻塞修復與整輪訓練接續 V3

任務20260911-v3。所有數字來自已結束工作與獨立核對，不把中途RUNNING快照當最終狀態。

## 這輪完成的事
前一輪的零成交統計、32owner截斷與期末未結三個阻塞已處理，並真正完成後續整輪候選訓練。第二台{s['worker']}，max_threads4；1次舊路徑一致性回放＋10次訓練/檢查回放，只有3個已消耗市場，不能說11個独立市場。

21項非約束資金測試＋12項表示/複製/數值分類測試通過。舊路徑的{golden['source_prefix']['counts']['native_action']}個送撤單事件、{golden['source_prefix']['counts']['canonical_receipt']}筆raw收據、{golden['source_prefix']['counts']['own_state']}個OWN狀態在來源時間內全部hash一致；沒有靠改策略或改成交結果修復統計。

## 一、零成交差異不是57筆有意義的成交遺失
舊數字920個native累計零成交=863個完全無raw收據+57個只含浮點非前進raw收據。最大逐owner的delta合計/累計差1.4210854715202004e-14。

現在所有raw收據保留，另統計canonical累計有前進的事件。原743筆raw中429筆有累計前進，314筆沒有；57個residual-only owner的原生累計全為0、leaves未變，且差異落在由實際浮點精度與操作次數推導的ULP範圍內。原生累計真正大於0的微小成交仍保留，不依lot/最低份額四捨五入，不調寬金額/份額容忍值去通過。

模型實際成本和持倉仍用原始native回饋核對；只是不能把任何正浮點qty都算成一次有效交易技能。後續資料與報告分開raw/effective，舊紀錄不刪改。

## 二、期末回報用原生事件處理完，沒有補行情
讀完資料不代表自己送出的下單/撤單確認已經到齊。使用native wait_next_feed(include_order_resp=True)接收排定回報，原策略仍處理新收到的確認並在真正市場結束後只做收尾。不新增行情、不複製最後book、不偽造fill/expiry/terminal。

對照回放的20個未結owner全部獲原生終態；延伸的是回覆處理，不是延長市場交易或新增下單。EOF rc1可能處理終態但保留舊current_timestamp，最後觀測標記為保守query上界，不冒充精確終態時間。兩個原生合成回歸還確認0.5份晚到部分成交沒有被丟掉。

## 三、容量不再在這批策略內截斷
32改為明示工程容量4096；此固定actor每個已保存觀測最多新2單，每場少於2048觀測，且市場結束後不能NEW，所以該容量由可達行動數保證不會成為策略限制，而非挑一個看起來賺錢的槽位數。

正式10次評估的resource censor={summary['regular_resource_censor_events']}，最高實際並行owner={summary['maximum_observed_concurrency']}；全部期末預留閉合。原GOLDEN32對照仍有51次resource censor，僅用來驗證修復沒有改原路徑，不拿它當無限制樣本。

同時減少重複複製不可變結構、重複查詢已確認終態的snapshot；所有canonical收據仍檢查，owner與可變份額/責任獨立複製，資料／策略／native binary沒有變動。加速前後源路徑逐筆一致才准許進入訓練。

## 四、實際訓練結果
仍使用原來的12參數完整被動policy，direction/exposure、目標份額、ticket、掛價、撤換、OWN反馈一起變動；不是旁路分類器。一次固定種子聯合擾動與梯度估計，兩個TRAIN市場選checkpoint，先凍結再看第三個已消耗PIPELINE_CHECK。不是神經網路重建Target，也不是已經有完整私有action teacher。

|TRAIN候選|整輪路徑loss（低較好）|
|---|---:|
{train_table}

選定：{s['selected']}。TRAIN loss改善（正為改善）={s['train_loss_improvement']:.9f}；第三場改善={s['check_loss_improvement']:.9f}。不在CHECK後改模型或重挑候選。

|第三場2022602|loss|新單|累計前進收據|UP結算分支|DOWN結算分支|峰值資金需求|
|---|---:|---:|---:|---:|---:|---:|
{result_table}

兩端分支只根據native模擬已確認持倉/成本計算，未讀winner，非實盤淨利證明。峰值資金需求是輸出，不是現在要設置或投入的上限。

|TRAIN市場|初始最差分支|凍結候選最差分支|
|---|---:|---:|
{risk_table}

loss仍是觀察到的Target份額/兩端payoff路徑模仿，不應以loss下降自動宣布所有風險都改善。沒有使用舊min(cost/100,1)獎勵，沒有用100/50/110裁單。Qref只是TRAIN數值单位，不是交易上限、Target原單或已知bankroll。

## 五、獨立覆核與邊界
獨立重建{a['complete_native_runs']}個native結果、{a['frames_audited']}個策略frame、{a['native_submits_audited']}次新單及{a['raw_receipts_audited']}筆raw收據（{a['effective_receipt_advances_audited']}筆有累計前進）。zero/partial/terminal與支付、峰值需求、選模、凍結及loss均核對，最大loss重算差{a['maximum_score_reproduction_error']:.3g}。沒有新增回放或擬合來做audit。

所有市場皆是consumed9/7，第三場不是untouched holdout，也不是近期Target尺寸transfer。原單qty/private目的仍未知。Native ACTIVE能力仍缺，這是被動學生整輪優化，不宣稱完整四通道或穩定獲利。這批來源以已記錄feed與原生排隊回覆為準，不能憑EOF收尾通過推論真實交易所沒有未收集事件。

研究資金仍非約束、180/Pair/TTL硬策略規則不恢復；8781/live/真實資金均未動。

## 接續入口
已不再卡於前置統計或期末收尾。下一輪從凍結完整policy與三場經濟/路徑差異接續，不重做這些阻塞，不退回成交分類器；若補Active，須接在同一責任/執行/回饋介面，不能把四種行動分別學好就當整套成熟。

- 主結果：{p.relative_to(ROOT).as_posix()}
- 主結果SHA：{sha(p)}
- 獨立audit：{ap.relative_to(ROOT).as_posix()}
- Audit SHA：{sha(ap)}
- 凍結policy：{summary['frozen_policy_path']}
- 模型SHA：{summary['frozen_policy_sha256']}
- Runtime：tools/open_funding_recovery_runtime_v3.py
- 整合判定：{sp.relative_to(ROOT).as_posix()}
'''
    rp=R/'MINIMAL_STUDENT_OPEN_FUNDING_RECOVERY_RETURN_V3_20260911.md'
    assert not rp.exists();rp.write_text(report,encoding='utf-8')
    marker='<!-- OPEN_FUNDING_RECOVERY_TRAIN_V3_FINAL_20260911 -->'
    note=f'''\n\n{marker}
## 最新：非約束資金整輪訓練已恢復並完成
讀MINIMAL_STUDENT_OPEN_FUNDING_RECOVERY_RETURN_V3_20260911.md與MINIMAL_STUDENT_OPEN_FUNDING_RECOVERY_ACCEPTANCE_V3_20260911.json。
零成交差異已解（920=863無raw+57浮點非累計前進），不刪raw。GOLDEN逐筆一致、native-only drain清完20pending。正式10評估resource censor0/terminal pending0；4096是基於每觀測最多2NEW的非約束工程容量，不是策略調槽或資金上限。1golden+10評估/3consumedmarkets，12參數1joint update，選{s['selected']}，TRAIN改善{s['train_loss_improvement']:.9f}/CHECK改善{s['check_loss_improvement']:.9f}。凍結與獨立audit全部完成，不留未提交/等待中的工作。資金仍null、舊180/Pair/TTL不恢復，8781未改。不是full4channel/最近同尺度/untouched或盈利promotion。下一步從已凍結整輪policy與經濟/路徑結果接續，不重做本輪阻塞與旁路分類器。
'''
    for name in ['MINIMAL_STUDENT_OPEN_FUNDING_CURRENT_20260911.md','MINIMAL_STUDENT_TRAINING_CURRENT_20260911.md','GPT6_MASTER_ENTRYPOINT_MIN_V2_20260910.md']:
        f=R/name
        if f.exists() and f.stat().st_size<200000 and marker not in f.read_text(encoding='utf-8'):
            with f.open('a',encoding='utf-8') as h:h.write(note)
    print(json.dumps(dict(report_path=rp.relative_to(ROOT).as_posix(),report_sha256=sha(rp),
        summary_path=sp.relative_to(ROOT).as_posix(),summary_sha256=sha(sp),summary=summary),ensure_ascii=False))


if __name__=='__main__':main()
