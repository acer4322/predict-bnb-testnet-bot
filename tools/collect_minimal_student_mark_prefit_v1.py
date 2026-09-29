"""Compact result validation/handoff only; host does no fitting or dataset scans."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r4_v0/p0_provenance_v1'
D=ROOT/'data/research/lan_worker_returns/minimal-student-mark-prefit-20260910-v1'
A=ROOT/'data/research/lan_worker_returns/minimal-student-mark-prefit-audit-20260910-v1'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert (D/'COMPACT.json').stat().st_size<30000 and (A/'COMPACT.json').stat().st_size<5000
    r=json.loads((D/'COMPACT.json').read_text(encoding='utf-8'))
    a=json.loads((A/'COMPACT.json').read_text(encoding='utf-8'))
    assert r['verdict']=='AUXILIARY_PREFIT_COMPLETED_NOT_POLICY_PROMOTION'
    assert a['verdict']=='INDEPENDENT_METRICS_AND_RELOADED_INFERENCE_PASS'
    assert sha(D/'COMPACT.json')==a['final_source_result_sha256']
    for name in a['immutable_artifacts_verified']:
        p=D/name;assert p.stat().st_size<100000 and sha(p)==r['artifact_hashes'][name]
    assert r['research_logistic_fits']==8 and r['model_freeze_before_check']
    out=dict(version='MINIMAL_STUDENT_OBSERVED_MARK_PREFIT_ACCEPTANCE_V1',
        technical_verdict='TRAINING_FREEZE_AND_INDEPENDENT_AUDIT_PASS',
        predictive_verdict=r['incremental_public_result'],promotion=False,
        scope='EVENT_CONDITIONED_OBSERVED_MARK_AUXILIARY_NOT_POLICY',
        research_logistic_fits=8,feature_families=2,heads=r['heads'],
        synthetic_unit_fits=r['synthetic_unit_fits'],unit_tests_passed=r['unit_tests']['passed'],
        source_freeze_checks_passed=r['checks_passed'],
        train_markets=[2022527,2022538],train_rows=r['train_rows'],
        pipeline_check_market=2022602,pipeline_check_rows=r['pipeline_check_rows'],
        untouched_holdout=False,period='2026-09-07 BTC5M consumed',
        pipeline_scores=r['pipeline_scores'],comparisons=r['comparisons'],diagnostics=r['diagnostics'],
        independent_audit=dict(rows=a['rows'],metric_values_checked=a['metric_values_checked'],
            reloaded_probabilities_checked=a['reloaded_model_probabilities_checked'],
            metric_max_abs_difference=a['metric_max_abs_difference'],
            inference_max_abs_difference=a['inference_max_abs_difference']),
        worker=dict(hostname=r['hostname'],max_threads=4,training_elapsed_seconds=r['elapsed_seconds'],
                    audit_elapsed_seconds=a['elapsed_seconds']),
        HFT=0,live_changes=0,controller_changes=0,new_markets=0,
        original_size_teacher=False,OUR_state_expert_teacher=False,
        recent_same_scale_transfer=False,no_post_check_retune=True,
        frozen_model_path=(D/'FROZEN_AUXILIARY_MODELS.json').relative_to(ROOT).as_posix(),
        frozen_model_sha256=r['frozen_model_sha256'],
        source_result_path=(D/'COMPACT.json').relative_to(ROOT).as_posix(),
        source_result_sha256=sha(D/'COMPACT.json'),
        audit_result_path=(A/'COMPACT.json').relative_to(ROOT).as_posix(),
        audit_result_sha256=sha(A/'COMPACT.json'),limitations=r['limitations'])
    result=R/'MINIMAL_STUDENT_OBSERVED_MARK_PREFIT_ACCEPTANCE_V1_20260910.json'
    assert not result.exists();result.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    table='\n'.join('| '+label+' | '+f"{r['pipeline_scores'][key]['macro']['log_loss']:.6f} | {r['pipeline_scores'][key]['macro']['brier']:.6f} | {r['pipeline_scores'][key]['macro']['roc_auc']:.6f} |" for key,label in [('PRIOR','訓練期類別比例'),('TIME_PRICE','時間／價格'),('PUBLIC_FULL','完整公開特徵')])
    report='''# 極簡學生：可見成交形態小型預訓練結果 V1

日期：2026-09-10。

## 本輪結論
**實際訓練、模型凍結與獨立驗證已完成；完整公開特徵模型沒有優於簡單基準，不接入控制器、不擴大訓練、不追著第三場分數調參。**
這是已限定的 observed-fill 輔助任務，不是「把極簡策略整個訓成Target」已完成或失敗。

## 本輪實際做了什麼
第二台DESKTOP-JIERAGF，max_threads4。兩個feature family各4個binary head，共8個研究Logistic fits；另有3個合成單元測試fits，沒有混算成真實訓練。沒有使用GPU、安裝新套件、重建資料、重掃大DB、新HFT、實單或controller修改。
TRAIN為2022527/2022538共332個可見成交批次。2022602的152批僅PIPELINE_CHECK，全部市場已消耗，不能當untouched確認。四個有正負例的BID head為Maker UP/Down與Taker UP/Down；四個ASK常數head完全不計分。

模型固定為mean BCE +0.1/2的L2 slope penalty，unpenalized intercept；預設確定性Newton法，沒有grid search。所有模型在讀第三場資料前已存成FROZEN_AUXILIARY_MODELS.json，之後沒有重fit或模型挑選。

## 第三場固定比較
四head等權平均；Log loss與Brier越低越好，AUC越高越好。

|方法|Log loss|Brier|AUC|
|---|---:|---:|---:|
'''+table+'''

完整公開模型相對時間／價格，平均log loss改善=-0.016320，即更差；四個head只有Maker DOWN略改善，另三個退步。相對只用TRAIN比例，完整公開模型四個head的log loss全部更差。

這也顯示不能只追AUC：完整模型平均AUC0.543464，高於簡單時間／價格的0.522249，但機率誤差更差；時間／價格的Taker UP AUC0.713869看似突出，其log loss0.376457仍劣於比例基準0.342140。沒有用某個漂亮head取代事前比較。

完整模型TRAIN平均AUC0.708280、第三場0.543464，表現沒有在這次跨場檢查維持。輸入上也存在超出TRAIN尺度的值：完整模型最大絕對標準化值22.288，涉及book age、深度及短期spot return等欄。這與樣本不足／跨場分布變化或過擬合相容，但本輪未辨識主要因果；不得直接說「只是份額錯了」或「所有模仿模型都不行」。

## 完成的獨立驗證
20個元件測試通過；28個來源／凍結檢查通過。另一個第二台job使用標準函式庫而非原numpy評分程式，核對300個指標值、484筆來源label以及3872個reload機率輸出，最大差約2.22e-16。模型hash在評分前後相同。

兩個job均succeeded並collect：
- minimal-student-mark-prefit-20260910-v1
- minimal-student-mark-prefit-audit-20260910-v1

凍結模型/metrics/predictions/code/prereg採固定hash；原工作執行中stdout/stderr hash不作immutable證據，已由獨立audit明示處理，未修改原始結果。

## 沒有被本輪證明的事
本模型僅回答「在Target已觀察到成交的事件中，哪些Maker/Taker與side標記較可能出現」。它沒有學何時掛單、要HOLD嗎、如何選原始掛價／份額、修復／加倉目的、OUR不同持倉下的答案或整輪盈利。

Target event_ms是整秒的成交觀測時間，不是原始placement time；相對成交時刻strict-past仍不保證相對原始掛單時刻可用。原始quantity與OUR expert action依然未知，不填18/30/55。未訓OUR僅10個非零變化的轉移lane。旧9/7資料沒有強套近期30/55，因此也沒有測出近期同尺度迴圈是否可學。

技術訓練通過不等於預測採用通過，更不等於交易策略進步。此負結果只屬這個小樣本、事前固定的線性輔助模型，不能擴張成骨架模仿路線被證偽。

## 接續邊界
本輪auxiliary分類器支線封存為已測，不繼續增加特徵／重跑同三場／換大模型把分數磨好，也不自動Stage-A16或shadow下單。回到原本的控制目標：沿用已接好的explicit quantity seam與OWN狀態，處理在學生當前狀態下「哪些可執行候選值得選」的教學依據；這需要明確的合法動作與結果／價值支援，不能拿這個成交形態分類器冒充管理層。這是下一個研究介面，不宣稱本輪已完成它。

## 可重現路徑
主結果：data/research/lan_worker_returns/minimal-student-mark-prefit-20260910-v1/COMPACT.json
完整指標：同目錄METRICS.json
凍結模型：同目錄FROZEN_AUXILIARY_MODELS.json
每筆機率：同目錄PREDICTIONS.jsonl.gz
獨立覆核：data/research/lan_worker_returns/minimal-student-mark-prefit-audit-20260910-v1/COMPACT.json
整合判定：data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_OBSERVED_MARK_PREFIT_ACCEPTANCE_V1_20260910.json
模型SHA256：341272f0a5a0590bdb9153a649df3903b5e86f9e2933b1166a1cfb35ee713763
'''
    p=R/'MINIMAL_STUDENT_OBSERVED_MARK_PREFIT_RETURN_V1_20260910.md'
    assert not p.exists();p.write_text(report,encoding='utf-8')
    marker='<!-- MINIMAL_STUDENT_OBSERVED_MARK_PREFIT_V1_20260910 -->'
    note='\n\n'+marker+'\n## 極簡學生輔助預訓練已完成：不升級控制器\n讀`MINIMAL_STUDENT_OBSERVED_MARK_PREFIT_RETURN_V1_20260910.md`及`MINIMAL_STUDENT_OBSERVED_MARK_PREFIT_ACCEPTANCE_V1_20260910.json`。第二台8研究fits（2feature families×4heads），332TRAIN/152consumed PIPELINE_CHECK，第三場前全部freeze。20unit/28contract checks與獨立300metrics/3872機率PASS，job全collect。完整PUBLIC LL0.540154劣於TIME_PRICE0.523834與PRIOR0.515385；vs時間價格1head改善3退步、vs比例4head全差。TRAIN AUC0.708→check0.543；不是政策/盈利PASS。0HFT/0live/0controller變更，原qty/OUR expert未知、未作近期同尺度transfer。本auxiliary支線已測封存，不反覆磨分類分數或擴大市場；接續回到explicit quantity+OWN狀態下可執行候選的教學／價值支援，不把observed fill classifier當controller。其他root主線不覆蓋。\n'
    for name in ['GPT6_MASTER_ENTRYPOINT_MIN_V2_20260910.md','PAIR_CORE_RULE_FIDELITY_CURRENT_20260910.md']:
        path=R/name;text=path.read_text(encoding='utf-8')
        if marker not in text:
            with path.open('a',encoding='utf-8') as f:f.write(note)
    print(json.dumps(dict(report_path=p.relative_to(ROOT).as_posix(),report_bytes=p.stat().st_size,
        report_sha256=sha(p),acceptance_path=result.relative_to(ROOT).as_posix(),acceptance_bytes=result.stat().st_size,
        acceptance_sha256=sha(result),audit_result_sha256=sha(A/'COMPACT.json'),
        model_path=out['frozen_model_path'],model_sha256=out['frozen_model_sha256'],
        pipeline_macro={k:v['macro'] for k,v in r['pipeline_scores'].items()}),ensure_ascii=False))


if __name__=='__main__':main()
