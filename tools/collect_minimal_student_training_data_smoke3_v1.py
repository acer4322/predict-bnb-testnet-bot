"""Lightweight host collect verification and handoff; no decoded dataset or model fit."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r4_v0/p0_provenance_v1'
D=ROOT/'data/research/lan_worker_returns/minimal-student-training-data-smoke3-20260910-v1'
S=ROOT/'data/research/lan_worker_returns/minimal-student-training-data-support-20260910-v1'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for x in iter(lambda:f.read(262144),b''):h.update(x)
    return h.hexdigest()


def main():
    a=json.loads((D/'COMPACT.json').read_text(encoding='utf-8'))
    b=json.loads((S/'COMPACT.json').read_text(encoding='utf-8'))
    assert a['verdict']=='SMALL_DATA_PACKAGE_VALIDATED_FOR_SCOPED_AUXILIARY_TASKS'
    assert b['verdict']=='READER_INPUT_ISOLATION_AND_TASK_SUPPORT_PASS'
    assert b['base_dataset_manifest_sha256']==a['artifact_hashes']['DATASET_MANIFEST.json']
    for rel,want in a['artifact_hashes'].items():
        p=D/rel;assert p.stat().st_size<2*1024**2 and sha(p)==want,rel
    use=dict(version='MINIMAL_STUDENT_TRAINING_DATA_ACCEPTANCE_V1',
        verdict='DATA_VALIDATED_SCOPED_AUXILIARY_INPUTS_NOT_COMPLETE_POLICY_TEACHER',
        dataset_path=D.relative_to(ROOT).as_posix(),
        dataset_manifest_sha256=b['base_dataset_manifest_sha256'],
        build_result_path=(D/'COMPACT.json').relative_to(ROOT).as_posix(),
        build_result_sha256=sha(D/'COMPACT.json'),
        support_result_path=(S/'COMPACT.json').relative_to(ROOT).as_posix(),
        support_result_sha256=sha(S/'COMPACT.json'),
        row_counts=a['totals'],loader_rows=a['loader_rows'],
        assertions_passed=a['assertions_passed'],independently_audited_rows=b['rows_independently_audited'],
        data_files=a['data_file_count'],compressed_data_bytes=a['data_bytes'],
        target_observation_pretraining=dict(allowed_as='SMALL_EVENT_CONDITIONED_AUXILIARY_PREFIT',
            active_heads=b['train_only_head_allowlist'],excluded_constant_heads=b['task_support']['observed_fill_marks']['TRAIN']['constant_heads'],
            head_selection_source='TRAIN_ONLY',is_placement_policy=False,is_timing_model=False,is_our_expert=False),
        own_transition_pretraining=dict(allowed_as='LOADER_OR_DIAGNOSTIC_ONLY_AT_CURRENT_SUPPORT',
            train_nonzero_transitions=10,pipeline_nonzero_transitions=6,
            required_zero_change_baseline=True,control_capability_claim_allowed=False),
        exact_original_target_qty_labels=0,expert_action_labels_on_own_states=0,
        period='2026-09-07 BTC5M consumed',matched_recent_target_size=False,
        model_fits=0,HFT=0,live_changes=0,new_markets=0,
        worker=dict(location='SECOND_LAN_WORKER',hostname=a['worker_hostname'],max_threads=4,
            build_elapsed_seconds=a['elapsed_seconds'],support_elapsed_seconds=b['elapsed_seconds']),
        support_counts=b['totals'],private_placement_time_known=False,
        source_time_notes=dict(target_events_all_recorded_at_integer_seconds=True,
            target_observed_delay_medians_ms=[r['target_observed_delay_ms']['median'] for r in a['rows_by_market']],
            not_target_execution_latency=True))
    output=R/'MINIMAL_STUDENT_TRAINING_DATA_ACCEPTANCE_V1_20260910.json'
    assert not output.exists();output.write_text(json.dumps(use,indent=2,ensure_ascii=False),encoding='utf-8')
    report='''# 極簡學生訓練資料：三場建立與讀取驗收

日期：2026-09-10。接續份額介面／原生OWN回饋已通過的進度，不重跑前置稽核或HFT。

## 結論
小型資料已真正建立，第二台兩個job均succeeded且collect完成。現在有可讀取的有限輔助預訓練資料，但不是完整Target掛撤單老師；本輪model fits=0、HFT=0、live8781變更=0、新／fresh／locked市場使用=0。

資料製作及独立覆核都在192.168.68.52 / DESKTOP-JIERAGF，max_threads=4。主機只做約1.35MB已知檔案的hash/copy/syntax與回傳檢查，沒有重掃大型資料庫。Worker內製作约2.945秒、補查约2.074秒，這不是整輪對話耗時或未來估時。

## 已製作
|內容|TRAIN（2022527、2022538）|PIPELINE_CHECK（2022602）|合計|
|---|---:|---:|---:|
|OUR決策／自身狀態轉移|2974|1484|4458|
|Target同時間戳成交批次|332|152|484|
|Target原始成交明細|1188|711|1899|
|Target有成交訂單識別|677|502|1179|
|OUR已送委託|55|8|63|

12個壓縮資料檔共1243429bytes（約1.24MB），另附manifest/hash、資料卡、範例、TRAIN-only特徵統計及可直接讀取的Python loader。63張OUR單的42筆native收據、49個零成交與3個部分成交終止全部保留。Target原始成交明細由484批次保留來源ID及aggregate，原始逐筆來源仍引用已釘hash的input源檔，不宣稱每筆都複製成獨立模型例子。

所有市場皆是2026-09-07已消耗資料。第三場只是資料管線檢查，不是未見市場績效驗證。相同market不得跨partition，沒有random row split。

## 兩條不混用的學習資料
### Target observed fill marks
以有觀察到成交為條件，學習Maker/Taker、UP/DOWN、BID/ASK的可見成交標記。實際訓練資料有正負樣本的是MAKER_UP_BID、MAKER_DOWN_BID、TAKER_UP_BID、TAKER_DOWN_BID。四個ASK標記全為0，必須從學習能力／平均accuracy計分中排除；選擇僅根據TRAIN，不用第三場挑head。

此任務不等於學會掛单、選擇HOLD或掛撤單時機。Target成交價格是可見成交結果，不冒充原始掛單選價。Active觀測照樣保存，學生沒有Active能力時不教成HOLD。

### OUR state/transition and order outcomes
輸入為OWN當時狀態与保守strict-past公開/委託簿特徵，已採取的新單另外作action條件；下一狀態/收據只在labels。OUR與Target同場同時並不產生專家答案，不把Target歷史下一筆貼到OUR不同庫存。

關鍵支援限制：TRAIN2974列只有10列非零持倉／成本變化，PIPELINE_CHECK1484列只有6列。其餘TRAIN2964列都是零變化，僅預測不變即可在零/非零判斷上約99.664%的表面一致率，**不是已訓練模型的結果，也不能說已學會修復／管理**。這條lane當前適合loader/狀態驗證；後續若作學習，必須比較零變化baseline、獨立報告真正變動事件，不能以總row accuracy畢業。

## 已做的資料檢查
23個建置檢查類別通過；另獨立完整讀取4458+484=4942個模型列，做輸入/標籤分離、future-label污染測試與missing-mask回讀。修改未來公開特徵／book或Target標籤，不會改變較早x；修改OWN未來持倉或fake teacher欄位，也不會进入x。

OWN公開資料可連4456/4458，strict-past book4455/4458；缺值保留，不回填未來。Target484批都可連到保存時鐘上的strict-past公開/book；這不保證所有feature非空，也不代表已驗證original collector端到端時間。

76495筆重複候選拒絕被按自身decision聚合，分布於1745個decision，不當作76495筆獨立老師經驗。2686個decision在role之前被既有時間等入口擋住，保留其來源標記，不教成Target HOLD。

## 時間與份額：不能回到舊錯誤
本批Target event_ms全為整秒值。同秒多成交合為一批，不猜毫秒先後。各市場observed_at-event_ms中位差為5715、5888、5901ms；這是此資料的觀測記錄延遲，不是Target送單延遲，不能拿來當策略固定等待秒數，也不能把尚未被收集到的Target行動當當下即時輸入。

三場Target保留9/7當時可見成交價格／份額；OUR30/55仍是外部傳值fixture，virtual100/two50cash/110qty-per-side亦為測試授權，不是查得Target規模。沒有將舊Target份額改成近期30/55，故此包**不是近期同尺度完整模仿集**。

Target原始requested qty、placement timestamp、cancel/HOLD意圖仍未知；相應mask全部停用，不填18/30/55。不因部分成交小於18/12就丟掉該觀測。學生資金拒絕不代表Target想HOLD。

## 接續
不再重做份額preflight/資料建置。第一個可執行的訓練範圍僅限小型、明確標成輔助用途的Target observed-fill pretraining，使用TRAIN可辨識四個BID head並按市場評估；不能把它當完整控制策略的替代。OWN稀疏轉移先保留對帳／資料用途。完整骨架控制所需的OUR狀態專家／經濟價值標籤仍需另驗，不允許以輔助分數變好宣布策略收益進步。

本輪未啟動任何模型fit，不自動擴大Stage-A16，後續大量資料/訓練仍第二台優先、max_threads4。

## 專案入口
資料目錄：data/research/lan_worker_returns/minimal-student-training-data-smoke3-20260910-v1/
獨立覆核：data/research/lan_worker_returns/minimal-student-training-data-support-20260910-v1/COMPACT.json
整合判定：data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_TRAINING_DATA_ACCEPTANCE_V1_20260910.json
資料manifest SHA256：3ef1638c642481ce0c390797fdb8ae271b4b1e466d5d7d00c61dfa095fc9ffb0

Loader範例（只讀資料，非訓練）：
python tools/minimal_student_dataset_reader_v1.py --dataset data/research/lan_worker_returns/minimal-student-training-data-smoke3-20260910-v1 --task observed_fill_marks --split TRAIN

讀取器仍輸出原始8欄multi-hot以保存schema；真正trainer必須依整合判定的TRAIN-only head allowlist選四個非constant欄，其他欄不計學習能力。不得直接忽略DATA_CARD或支援限制。
'''
    rp=R/'MINIMAL_STUDENT_TRAINING_DATA_RETURN_V1_20260910.md'
    assert not rp.exists();rp.write_text(report,encoding='utf-8')
    zp=R/'minimal_student_training_data_smoke3_20260910_v1.zip'
    assert not zp.exists()
    # Existing gzip streams are stored, not recompressed; tiny packaging task only.
    with zipfile.ZipFile(zp,'w',compression=zipfile.ZIP_STORED) as z:
        for rel in a['artifact_hashes']:
            z.write(D/rel,rel)
        z.write(D/'COMPACT.json','BUILD_RESULT.json')
        z.write(S/'COMPACT.json','TASK_SUPPORT.json')
        z.write(output,'TRAINING_USE_CONTRACT.json')
        z.write(rp,'RETURN_REPORT.md')
    marker='<!-- MINIMAL_STUDENT_TRAINING_DATA_ACCEPTANCE_V1_20260910 -->'
    note='\n\n'+marker+'\n## 極簡學生資料：本輪已完成，勿重做\n`MINIMAL_STUDENT_TRAINING_DATA_RETURN_V1_20260910.md`及`MINIMAL_STUDENT_TRAINING_DATA_ACCEPTANCE_V1_20260910.json`：第二台完成小型資料建置＋独立覆核並collect；23項建置檢查、4942列隔離覆核PASS。三場12個壓縮資料檔，4458OUR轉移／484Target同秒批次、63orders/42receipts/49zero/3partial；0fit/0HFT/0live。TRAIN前2場，最後1場僅PIPELINE_CHECK，全consumed。Target整秒時戳與觀測延遲不冒充私有placement time。原qty/OUR expert/HOLD/cancel未知mask，舊資料不強套近期30/55。警告：TRAIN own delta2974列只有10非零，不能把99.66%不變當能力；Target4個ASK head全0，不得充accuracy。僅允4個TRAIN有變化的BID observed-fill head作小型輔助預訓練；非完整placement policy或控制/盈利PASS。使用完整資料前讀TRAINING_USE_CONTRACT及TASK_SUPPORT，不重跑preflight、不自動Stage-A16，大運算仍第二台max_threads4。其他root-value主線不覆蓋。\n'
    for name in ['GPT6_MASTER_ENTRYPOINT_MIN_V2_20260910.md','PAIR_CORE_RULE_FIDELITY_CURRENT_20260910.md']:
        p=R/name;text=p.read_text(encoding='utf-8')
        if marker not in text:
            with p.open('a',encoding='utf-8') as f:f.write(note)
    print(json.dumps(dict(acceptance_path=output.relative_to(ROOT).as_posix(),acceptance_sha256=sha(output),
        report_path=rp.relative_to(ROOT).as_posix(),report_sha256=sha(rp),
        zip_path=zp.relative_to(ROOT).as_posix(),zip_bytes=zp.stat().st_size,zip_sha256=sha(zp),
        build_result_sha256=use['build_result_sha256'],support_result_sha256=use['support_result_sha256'],
        totals=use['row_counts'],worker=use['worker']),ensure_ascii=False))


if __name__=='__main__':main()
