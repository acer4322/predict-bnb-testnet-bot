"""Finalize terminal evidence and continuation. No new training/replay or transfer."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r4_v0/p0_provenance_v1'
D=ROOT/'data/research/lan_worker_returns/minimal-student-whole-episode-train-20260911-v1'
A=ROOT/'data/research/lan_worker_returns/minimal-student-whole-episode-audit-20260911-v1'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for x in iter(lambda:f.read(262144),b''):h.update(x)
    return h.hexdigest()


def main():
    assert (D/'COMPACT.json').stat().st_size<150000 and (A/'COMPACT.json').stat().st_size<20000
    s=json.loads((D/'COMPACT.json').read_text(encoding='utf-8'))
    a=json.loads((A/'COMPACT.json').read_text(encoding='utf-8'))
    assert s['verdict']=='WHOLE_EPISODE_POLICY_TRAINING_AND_FROZEN_CHECK_COMPLETED'
    assert a['verdict']=='ACCOUNTING_AND_SCORE_REPRODUCED_OBJECTIVE_SCALE_CONFOUND_IDENTIFIED'
    assert sha(D/'COMPACT.json')==a['source_result_sha256']=='3f9332a42b015e31dbd902fc28dad0277fe8827a9939da7d3125b87b54a20da7'
    assert sha(D/'FROZEN_WHOLE_POLICY.json')==s['frozen_model_sha256']==a['frozen_model_sha256']
    for e in s['evaluations']:
        p=D/e['trace']['path'];assert p.stat().st_size<500000 and sha(p)==e['trace']['sha256']
        assert e['unresolved_owners']==0
    check=[x for x in s['evaluations'] if x['split']=='PIPELINE_CHECK']
    trainbase=[x for x in s['evaluations'] if x['split']=='TRAIN' and x['variant']=='INITIAL']
    trainchosen=[x for x in s['evaluations'] if x['split']=='TRAIN' and x['variant']==s['selected']]
    summary=dict(version='MINIMAL_STUDENT_WHOLE_EPISODE_TRAIN_ACCEPTANCE_V1',
        verdict='ACTUAL_JOINT_POLICY_TRAINING_COMPLETED_CANDIDATE_NOT_PROMOTED',
        previous_stop='Previous rule-regression job terminal; no subsequent trainer had been dispatched.',
        train_job='minimal-student-whole-episode-train-20260911-v1',
        audit_job='minimal-student-whole-episode-audit-20260911-v1',jobs_terminal=True,
        auto_collected_files_verified=True,manual_collect_was_blocked=True,
        worker=s['worker'],max_threads=4,unique_markets=3,consumed_markets=True,
        native_runs_complete=10,joint_policy_parameters=12,joint_update_steps=1,
        training_method='Prespecified12parameter joint perturbation and one whole-episode gradient estimate; train-only checkpoint choice.',
        is_neural_network_training=False,policy_class_is_prespecified=True,
        training_units='Full causal native episodes, with real OWN state feedback.',
        train_losses=s['train_losses'],selected=s['selected'],
        train_relative_loss_reduction=s['train_loss_improvement']/s['train_losses']['INITIAL'],
        check_initial_loss=check[0]['loss']['total_loss'],check_selected_loss=check[1]['loss']['total_loss'],
        check_relative_loss_increase=-s['check_loss_improvement']/check[0]['loss']['total_loss'],
        selected_check={k:check[1][k] for k in ['submits','cancel_requests','native_receipts','zero_fill_orders','partial_orders',
            'late_new_orders','receipt_conditioned_continuations','min_requested_qty','max_requested_qty',
            'UP_branch','DOWN_branch','final_cost','unresolved_owners']},
        initial_check={k:check[0][k] for k in ['submits','native_receipts','UP_branch','DOWN_branch','final_cost']},
        train_branch_comparisons=[dict(market_id=b['market_id'],initial_floor=min(b['UP_branch'],b['DOWN_branch']),
            selected_floor=min(c['UP_branch'],c['DOWN_branch']),initial_UP=b['UP_branch'],selected_UP=c['UP_branch'],
            initial_DOWN=b['DOWN_branch'],selected_DOWN=c['DOWN_branch']) for b,c in zip(trainbase,trainchosen)],
        objective_decomposition=a['train_objective_decomposition'],
        audit=dict(native_runs=10,policy_frames=a['audited_policy_frames'],submits=a['audited_submits'],receipts=a['audited_receipts'],
            scoring_max_abs_error=a['max_scoring_abs_difference'],accounting_max_abs_error=a['max_accounting_abs_difference']),
        native_active_implemented=False,full_target_policy_learned=False,live_changes=0,new_markets=0,
        latest_rule_profile=s['world_profile']['profile_id'],legacy180_restored=False,
        actual_target_bankroll_known=False,target_original_order_qty_known=False,
        source_result_sha256=sha(D/'COMPACT.json'),audit_result_sha256=sha(A/'COMPACT.json'),
        frozen_policy_sha256=s['frozen_model_sha256'],frozen_policy_path=(D/'FROZEN_WHOLE_POLICY.json').relative_to(ROOT).as_posix(),
        followup='Correct full-episode economic-objective scaling before larger training; preserve current scores/frozen model; no post-check retuning, no return to isolated classifier or hidden180 rules. Active remains a separate explicit capability gap.')
    sp=R/'MINIMAL_STUDENT_WHOLE_EPISODE_TRAIN_ACCEPTANCE_V1_20260911.json'
    assert not sp.exists();sp.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    report=f'''# 極簡學生：完整被動策略的首次整輪聯合訓練

任務標識：20260911-v1。原始worker時間戳保留在job status，不以文件檔名代替事件時間。

## 為什麼前面停止
上一個training-rules-v2-clockfix工作已terminal；原生未顯示價位分支未觸發後按stop condition返回，沒有接著派出新的訓練工作。不是第二台還在跑模型。這次已派出並完成實際whole-episode訓練及獨立覆核。

## 本輪完成
第二台{s['worker']}，max_threads4。3個已消耗市場，10次完整原生回放；不是10個獨立市场。前兩場TRAIN，第三場只有消耗過的PIPELINE_CHECK。一次12參數的聯合擾動/梯度估計，包含曝光方向、資金使用、份額、掛價深度、撤換與自身狀態回饋。它是固定policy class的參數優化，不是神經網路已重建Target私有系統。

候選每次從自己的空初態開始跑完整市場；同一policy決定整段KEEP/CANCEL/NEW與預算分配，成交由原生引擎回報。Target只在結束後的整段路徑評分器使用，沒有把它的庫存、未來成交或未知原單qty當學生當下輸入。

有效世界仍是MINIMAL_SYSTEM_TRAINING_WORLD_V2：沒有恢復180s硬截止、Pair/TTL策略硬門檻；資金/未完成責任/真實成交仍保留。總測試額度100未增加，沒有8781/live修改。Native Active仍未接入，本輪只證明完整被動控制器可被聯合訓練，不稱完整四通道模仿。

## 固定候選與凍結
|候選|TRAIN整輪平均誤差（低較好）|
|---|---:|
|初始|{s['train_losses']['INITIAL']:.9f}|
|聯合正向擾動PLUS|{s['train_losses']['PLUS']:.9f}|
|聯合反向擾動MINUS|{s['train_losses']['MINUS']:.9f}|
|由兩側評分估出的聯合更新|{s['train_losses']['JOINT_UPDATE']:.9f}|

選中的PLUS是依兩個TRAIN場的平均loss決定，並非看第三場選。全部12參數共同變動；不是逐個獨立角色修補。梯度估計產生的JOINT_UPDATE也有評估，但不如PLUS；不得宣稱採用的是梯度更新點。凍結參數JSON在載入第三場資料前寫入，後續hash未改，沒有看CHECK調參。

TRAIN誤差{summary['train_relative_loss_reduction']:.2%}下降，但CHECK由{summary['check_initial_loss']:.9f}升至{summary['check_selected_loss']:.9f}（增加{summary['check_relative_loss_increase']:.2%}）。這不是generalization或獲利畢業，候選不升級。

## 確實改变了原生行為
檢查場2022602：新單29→62，原生成交收據26→39；凍結策略有52筆剩餘180s內新單，16個含新收據的後續管理觀測，實際requested qty18.70–45.83。48張零成交、4張部分成交，terminal未釋放預留0。未顯示價位新單計數3，至少不再只停在上一個零成交規則witness，但此計數不直接等於該類成交全部驗收。

兩端結算分支：初始UP+30.417807/DOWN+51.174104，凍結UP+44.282143/DOWN+81.635232；成本57.367748→89.923412。這是同一舊檢查場的條件結算，不讀winner、不表示實盤profit，也未認證全部成本/衝擊。不能挑這一場的好兩端忽略TRAIN下行變差。

TRAIN2022527最差分支+2.209685→-1.109219；2022538為-3.096236→-6.311224。兩個TRAIN場最差分支都變差，雖然所選loss下降。

## 獨立覆核識別出目標函數的尺度偏差
觀測到Target三場的累計買入成本2679.87、2111.10、3855.83；這些不是已核定私有bankroll，可能也不涵蓋未見資金操作。不能據此自動增加OUR額度。

評分中的cost-utilization使用min(cost/100,1)，導致TRAIN的319/332（96.0843%）個Target事件該座標已飽和。學生多花資金就能在這一項接近Target，即使其餘行為更差。

整輪loss改善分解：
- 資金使用項貢獻 +0.019546210。
- 其他三個經濟路徑座標合計 -0.001425059，反而變差。
- 最終下行懲罰 -0.005405259，亦變差。
- 合計仍顯示 +0.012715892 的表面改善。

因此這輪不能說已學到正確管理邏輯。最重要的負結果不是「再把參數調大」，而是完整系統的教學評分也必須尊重尺度、風險與能力差異。使用整轮評分不會自動保證老師/目標函数正確。

此分析是已凍結結果的分解，不是修改loss後重新宣布同一CHECK通過。沒有重訓、更改模型、加入舊180禁令或改成反向方向規則。Target觀察軌跡也不是原始掛撤單老師；未知原qty仍UNKNOWN。

## 檢查與回傳
獨立job重建10次回放、14864個policy frame、255次新單、196筆native收據。來源數據及回傳trace hash核對；持倉/成本重建最大誤差0，loss重算誤差<=1.53e-16。這些重複回放不是更多市場樣本。

訓練job minimal-student-whole-episode-train-20260911-v1 已succeeded；audit job minimal-student-whole-episode-audit-20260911-v1 已succeeded。手動collect被工具阻擋，沒有另開替代傳輸繞過；預先啟動的auto collector已返回，本機COMPACT、凍結模型及10個trace均存在且hash核對通過。

## 下一個精確接續點
保留完整policy/native執行與已訓參數作對照。先修正整輪目標/尺度契約，避免把耗用OUR100額度當成接近Target的主要獎勵；再用分開的經濟品質、下行風險和交易活動檢查新的system-level訓練。不能把已觀察Target累計成本當原始授權，也不能硬填Target原單份額。不得回去做旁路分類器或重复V2規則見證；也不得把單一消耗過的CHECK好分支當新驗證。Native Active缺口仍須明列，不把它當永久安全限制。

## 可重現檔案
- 模型：{summary['frozen_policy_path']}
- 模型SHA：{s['frozen_model_sha256']}
- 主結果：{(D/'COMPACT.json').relative_to(ROOT).as_posix()}
- 主結果SHA：{sha(D/'COMPACT.json')}
- 獨立覆核：{(A/'COMPACT.json').relative_to(ROOT).as_posix()}
- 獨立覆核SHA：{sha(A/'COMPACT.json')}
- 學生policy：tools/minimal_student_joint_policy_train_v1.py
- 接續判定：{sp.relative_to(ROOT).as_posix()}
'''
    rp=R/'MINIMAL_STUDENT_WHOLE_EPISODE_TRAIN_RETURN_V1_20260911.md'
    assert not rp.exists();rp.write_text(report,encoding='utf-8')
    nextfile=R/'MINIMAL_STUDENT_TRAINING_CURRENT_20260911.md'
    assert not nextfile.exists()
    nextfile.write_text('''# 極簡系統學生：最新接續入口20260911

先讀MINIMAL_STUDENT_WHOLE_EPISODE_TRAIN_RETURN_V1_20260911.md與MINIMAL_STUDENT_WHOLE_EPISODE_TRAIN_ACCEPTANCE_V1_20260911.json。

上一輪規則witness後未派訓練的斷點已接起：本輪完整被動policy12參數聯合訓練，10/10native全市場回放完成；2TRAIN/1consumedCHECK，1step共同參數更新及train-only選模。選PLUS，非梯度更新點；不宣稱神經網路或full4channel完成。全job terminal/auto collect/hash確認，沒有暗中跑未回報工作。

TRAIN loss0.108814→0.096098但CHECK0.152927→0.170047；native行動/成交改變不是旁路classifier。独立覆核確認14864frames/255submits/196receipts，沒有帳務錯誤。負結果：96.08%TRAIN Target utilization被100尺度截斷，分數改善主要來自花更多錢；兩TRAIN最差分支都變差。候選不得promotion。凍結模型與所有舊loss保存，無post-check重訓。

有效training world仍V2，研究180/Pair/TTL硬閘門不准恢復，真實成交/帳務/資金保留，8781未改。下一步是整輪目標的尺度與經濟品質校正，並處理Active原生能力缺口；不是重做規則前置或再訓成交分類器。Target私有初始資金/原始qty/目的未知不補假標籤，舊9/7cohort不強套近期30/55，也不把累計交易成本當資金總額。重型工作仍第二台max_threads4。
''',encoding='utf-8')
    marker='<!-- MINIMAL_STUDENT_WHOLE_EPISODE_TRAIN_COMPLETED_20260911 -->'
    note='\n\n'+marker+'\n## 極簡系統學生已進入實際整輪聯合參數訓練\n讀MINIMAL_STUDENT_TRAINING_CURRENT_20260911.md及MINIMAL_STUDENT_WHOLE_EPISODE_TRAIN_RETURN_V1_20260911.md。10native/3consumed市場、12coupled parameters，訓練與audit均terminal collected；候選PLUS不promotion（TRAIN下降而CHECK上升、資金尺度項主導表面改善）。下一步整輪目標尺度/經濟品質，不回頭規則witness或旁路分類。V2移除研究180等硬策略規則仍有效，8781未改。\n'
    for name in ['MINIMAL_STUDENT_TRAINING_RULES_CURRENT_20260910.md','GPT6_MASTER_ENTRYPOINT_MIN_V2_20260910.md']:
        p=R/name
        if p.exists() and p.stat().st_size<200000 and marker not in p.read_text(encoding='utf-8'):
            with p.open('a',encoding='utf-8') as f:f.write(note)
    print(json.dumps(dict(summary_path=sp.relative_to(ROOT).as_posix(),summary_sha256=sha(sp),
        report_path=rp.relative_to(ROOT).as_posix(),report_sha256=sha(rp),current_path=nextfile.relative_to(ROOT).as_posix(),summary=summary),ensure_ascii=False))


if __name__=='__main__':main()
