"""Immutable complete comparison delivery; preserves earlier published A receipt."""
import hashlib,importlib.util,json,shutil
from pathlib import Path

P=Path(__file__).resolve().parent;ROOT=P.parents[2]
DEST=P/'release/native_v49_stack185_20261002_v2r1'
assert not DEST.exists();DEST.mkdir(parents=True)
BASE=P/'stage/native_engine_stack185_20261002_v1'
VAR=P/'stage/native_engine_stack_variants185_20261002_v1'
STRICT=P/'stage/native_engine_stack_strict185_20261002_v2r1'
OBSERVER=P/'stage/native_engine_platform_order_audit185_20261002_v1'
RET=ROOT/'data/research/lan_worker_returns'
comparison=json.loads((P/'B_STRICT_CLOUD_COMPARISON.json').read_bytes())
metrics=comparison['strategies']
def copy(src,name):
    d=DEST/name;d.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,d)

jobs=[]
for tag,prefix,job in [('FAV_UNDER','STACK_','native-engine-stack-fav-under-185-20261002-v1'),('VARIANTS','VARIANTS_','native-engine-stack-switch-throttle-185-20261002-v1'),('STRICT','STRICT_R1_','native-engine-stack-strict-four185-20261002-v2r1')]:
    r=RET/job;status=json.loads((P/(prefix+'STATUS.json')).read_bytes())
    assert status['state']=='succeeded' and status['return_code']==0
    jobs.append({'id':job,'state':'succeeded','return_code':0,'worker_elapsed_seconds':status['elapsed_seconds'],'submit_attempts':1,'parallel_paths':1,'max_threads':4})
    for name in ('stack_local.json','RESULT.json'):copy(r/name,tag+'/'+name)
    for f in (r/'arms').rglob('*'):
        if f.is_file() and f.name in ('result.json','AUDIT.json','orders_and_fills.json.gz'):copy(f,tag+'/'+f.relative_to(r).as_posix())
    for suffix in ('STATUS.json','LOAD_ONLY.json','SUBMIT_INTENT.json','DISPATCH_PREFLIGHT.json'):copy(P/(prefix+suffix),'worker/'+prefix+suffix)
for name in ('A_CLOUD_COMPARISON.json','A_COMPARE_TOOL_STDOUT.txt','A_COLLECTION_AUDIT.json','A_PUBLISHED.json','B_CLOUD_COMPARISON.json','B_ALL_CLASSIFIED_RESULTS.json','B_PATH_VALIDITY.json','B_COLLECTION_AUDIT.json','B_INDEPENDENT_AUDIT.json','B_RULE_SEMANTICS_AUDIT.json','VARIANTS_COLLECTION_AUDIT.json','VARIANTS_IDENTITY_AUDIT.json','B_STRICT_CLOUD_COMPARISON.json','B_STRICT_ALL_CLASSIFIED_RESULTS.json','B_STRICT_PATH_VALIDITY.json','STRICT_R1_COLLECTION_AUDIT.json','STRICT_VARIANTS_IDENTITY_AUDIT.json','STRICT_RULE_AUDIT.json','STRICT_LOAD_ONLY_FAILURE.json','STRICT_SOURCE_CHECK.json','STRICT_COMPONENT_TEST.json','ORDER_AUDIT_COLLECTION_AUDIT.json','B_ORDER_COMPARISON.json'):
    copy(P/name,name)
for name in ('LABELS.json','INPUTS.json','INPUT_AUDIT.json','CANDIDATE.json','EXPECTED_BACKEND.json','SOURCE_PARENT.json','SPEC_ZH.md'):
    copy(BASE/name,'inputs/'+name)
for f in (BASE/'META').glob('*.json'):copy(f,'inputs/META/'+f.name)
for tag,stage in [('fav_under',BASE),('variants',VAR),('strict',STRICT)]:
    for name in ('stack_replay.py','comparison_policy.py','eof_runtime.py','MANIFEST.json'):copy(stage/name,'source/'+tag+'/'+name)
for name in ('FOLLOW_ON.patch','FOLLOW_ON.json','RV5.json'):copy(VAR/name,'source/variants/'+name)
for name in ('FOLLOW_ON.patch','FOLLOW_ON.json','RULE_CONFORMANCE.patch','RULE_CONFORMANCE.json','RV5.json'):
    copy(STRICT/name,'source/strict/'+name)
copy(STRICT/'runtime/tools/open_funding_native_active_adapter_v1.py','source/strict/runtime/tools/open_funding_native_active_adapter_v1.py')
for name in ('prepare_stack.py','prepare_variants.py','prepare_strict.py','prepare_ioc_importfix.py','prepare_order_audit.py','compare_order_metrics.py','analyze_complete.py','verify_variants_collection.py','lan_actions.py','audit_comparison.py','publish_packet.py','build_complete_packet.py'):
    copy(P/name,'source/helpers/'+name)
for name in ('CLOUD_LAB_RESULTS_20261002.json','SPOT_RV5_TABLE_20261002.json'):
    copy(ROOT/'docs/research_specs/results'/name,'reference/'+name)
copy(RET/'native-engine-platform-185-20261002-v1/local.json','A/local.json')
copy(RET/'native-engine-platform-185-20261002-v1/ENGINE_READBACK.json','A/ENGINE_READBACK.json')
oreturn=RET/'native-engine-platform-order-audit185-20261002-v1'
for name in ('A_ORDER_SUMMARIES.json','local.json','RESULT.json','ENGINE_READBACK.json'):copy(oreturn/name,'A_order_observer/'+name)
for name in ('platform_worker.py','strategy_lab.py','MANIFEST.json','FOLLOW_ON.patch','FOLLOW_ON.json'):copy(OBSERVER/name,'source/A_order_observer/'+name)
for name in ('STATUS.json','LOAD_ONLY.json','SUBMIT_INTENT.json','DISPATCH_PREFLIGHT.json'):copy(P/('ORDER_AUDIT_'+name),'worker/ORDER_AUDIT_'+name)
(DEST/'A_REFERENCE_RESOLUTION.json').write_text(json.dumps({'status':'RESOLVED_PLATFORM_PASS','previous_packet':'../native_platform185_20261002_v1','previous_commit':'0fcb3703f3dee37398fbf9b604732521123249da','resolution_commit':'72095db1','final_comparison':'A_CLOUD_COMPARISON.json','earlier_missing_input_receipts':'Historical, preserved unchanged. Both required files now available under docs/research_specs/results.','rows':370,'max_absolute_difference':0.0},indent=2))

table='\n'.join('|'+ '|'.join([s,f"{m['pnl_correlation']:.9f}",f"{m['B_minus_cloud_mean_pnl']:.9f}",f"{m['B_minus_cloud_pnl_population_std']:.9f}",f"{m['mean_average_fill_price_difference']:.9f}",str(m['B_total_orders']),f"{m['B_order_weighted_mean_seconds']:.9f}",m['numerical_acceptance']])+'|' for s,m in metrics.items())
eof='；'.join(f"{s} {m['EOF_unresolved_paths']} 條／{m['EOF_unresolved_owners']} 張" for s,m in metrics.items())
maker='；'.join(f"{s} maker {m['B_maker_fill_qty']:.6f}、taker {m['B_taker_fill_qty']:.6f} 股" for s,m in metrics.items())
readme=f'''# 185 場原生平台與 V49 執行層對照

A 平台對照完成：185 場 × FAV／UNDER，PnL、UP 股數、DOWN 股數與 cost 的最大絕對差全部 **0**。`A_CLOUD_COMPARISON.json` 含完整逐場比對；不是只比共同子集。先前 A 包的缺檔記錄保留，`A_REFERENCE_RESOLUTION.json` 記錄新 commit 72095db1 提供兩個輸入後的結案。

B 最終修正版四臂各 185 場實際 native 重播，共 740 筆，job `native-engine-stack-strict-four185-20261002-v2r1` 一次提交。另保留先前 GTC 診斷兩個 job 的 740 筆原始結果。各 job 依序執行、各一次提交，native 使用相同 pyd SHA、250/250ms latency、risk-adverse queue、partial fill、tick/lot .01、零 fee。所有事件 NPZ 與官方結算標籤固定為 A 那 185 場，沒有篩損益或替補。

|策略|損益相關|B−cloud 平均差|差的 population std|平均成交價差|B 下單數|B 下單加權平均秒|兩項數值門檻|
|---|---:|---:|---:|---:|---:|---:|---|
{table}

`B_STRICT_CLOUD_COMPARISON.json` 是最終修正版逐場配對，`B_CLOUD_COMPARISON.json` 是原 GTC 診斷。包括 population／sample std、平均成交價（每場 cost / shares；雙方有成交才入價差平均）、最大差異、下單數與下單時間、分類與 1%／2% notional fee 敏感度。上表仍列完整 185 場的描述數字，含作廢零交易列，不能把作廢列當作符合 FAV 規格的經濟結果。另附有效上下文子集的描述數字，但不替代預登記的完整 cohort。cloud 的 v1／v2 參考是依同一個 frozen RV 值組合其 FAV／UNDER 列，v2 低 RV 為零，並非另有雲端 v1／v2 native run。

雲端JSON沒有下單序列。為補齊 requested metrics，用 bytecode／SHA 不改的 A 原策略再重播一次，在 run return frame 唯讀觀察 orders，新增 `A_order_observer/A_ORDER_SUMMARIES.json`。該新job一次 submit、23.425秒 succeeded，所有結果與原A完全相同、cloud全部原始8欄也精確相同（cloud額外set為cohort metadata）。`B_ORDER_COMPARISON.json` 比 actual LOCAL_A 与 strict B 的每場下單數／平均下單秒數，统一用正式META時基；不是從股數反推，也不是聲稱已讀到雲端未提供的 orders。

波動表 185/185 對上 META 的 window_start，114 高 RV、71 低 RV。閾值 3.1834e-05 未改。表裡未提供原始現貨 bars 或完整查詢窗，不能宣稱已獨立重建事前 RV 的因果時間；使用使用者預登記的外生表。`STRICT_VARIANTS_IDENTITY_AUDIT.json` 證明最終修正版 370 個 v1／v2 實際重播與對應修正版母策略的 orders／receipts 完全相同，或低 RV v2 零交易。原 GTC identity 另保留。

執行範圍是 **新凍結規則 action producer + V49 mixed whole-plan／TrainingPlanGateway／OpenFunding owner ledger／native ACTIVE adapter／canonical receipt accounting／EOF guard**。原碼在 branch `research/hft244-v49-repro-20261002` commit `3bf09cf645e1d83284825c32196e483294fcd182` 的 `research/repro/hft244_v49_20261002_v1/strategy/runtime_scratch_CG1AT_2671717`，`inputs/SOURCE_PARENT.json` 固定原始 44 SHA。GTC 診斷包44檔未改；最終修正版僅在新後續包把 ACTIVE adapter 的两處 GTC 改 IOC，43 個既有 runtime 檔不變，原 frozen source／base 不修改。本包提供實際新 producer、runner、改版 adapter 与全部 follow-on diff。**沒有重播原 CG1AT alpha／theta progression／修補／被動轉主動／governor 的決策**，不能把這個結果當原 CG1AT 完整策略等價證明。

最終修正版在 `STRICT/arms/`；先前診斷在 `FAV_UNDER/arms/` 与 `VARIANTS/arms/`。各有原始 result、AUDIT 和 `orders_and_fills.json.gz`，含完整 NEW 時間／side 自己價格／qty、native receipt 的 exchange／receive ms、qty／maker／fee、逐秒決策及 plans。只含匿名 simulator counter，不含私人帳戶／實際訂單識別。Feed V1 的公開簿／逐筆交易時序含轉換規則，不能當真實網路或交易所延遲。

觀察到的 receipts／inventory／cost reconciliation 通過，但 EOF 尚未 canonical terminal：{eof}。保留 pending／UNKNOWN／右設限，沒有為追求 terminal 擅加撤單；不能稱全部 owners 終局或所有舊閘門通过。`active_matches_opportunity` 既有失敗原樣保留，新 producer 未重測該 opportunity policy；`all_old_gates_passed=false`。

最終修正版 IOC 實際 maker 股數均為零：{maker}。原 A lab 及原 V49 ACTIVE 是 ask 的 marketable GTC LIMIT，250ms 延遲後能留簿變 maker；該原始診斷不符合嚴格 only-taker，所以完整保留並改在新包使用既有引擎 IOC。沒有重建或修改 Python wrapper／Rust binary。第一版只因 Python 頂層未匯出 IOC 而 load-only FAIL、從未 submit；v2r1 用 `hftbacktest.order.IOC`，ABI=3 与凍結 Rust enum一致，失敗證據在 `STRICT_LOAD_ONLY_FAILURE.json`。

修正版持股 predicate 仅計 confirmed inventory<300，pending 仍由原 canonical ledger 保留 cash／quantity／self-cross 義務直到 terminal，沒有將 pending 假裝成交或釋放。fixed F 必須在正式 +12s 的有效簿初始化。2807895／2809188 首盤口received在+19.696s／+97.415s，缺12s上下文，因此 FAV 與低波動 v1 共四條作廢、零下單；没有使用+20s／+98s的價格補造 F，也沒替補場次。`B_STRICT_PATH_VALIDITY.json` 詳列有效性，原GTC延後選F之結果僅診斷。

原 lab 与 B 原碼差異包括 checkpoint 起點与正式 META 窗、1Hz/int(sec) 与精確 12+2k grid、UNDER 的 12s 下限、FAV 原始 F band 和 flip 撤單規則、direct native balance 与 canonical owner receipt accounting。`B_RULE_SEMANTICS_AUDIT.json` 有行號證據。未做逐元件因果消融，因此哪個元件造成多少 PnL 差仍 UNKNOWN；沒為通過門檻調參。

零費率 native 結果之外，1%／2% 只是對實際買入 cost 的離線敏感度，並非場地費率。0 fit；没有改 DB、凍結 source、live／armed／stake／服務／排程／collector。三個 B job 成功且逐個 hash 回收驗證；完整審計及新資料由本包單獨記錄。
'''
(DEST/'README_ZH.md').write_text(readme,encoding='utf-8')
(DEST/'.gitattributes').write_text('* -text\n')
index={'id':DEST.name,'status':'COMPLETE_RULE_CORRECTED_FOUR_ARM_COMPARISON_WITH_FOUR_INVALID_INITIAL_CONTEXT_PATHS','markets':185,'rows':740,'diagnostic_GTC_rows':740,'jobs':jobs,'A_platform_acceptance':'PASS_MAX_DIFFERENCE_0','A_final_comparison':'A_CLOUD_COMPARISON.json','A_order_observation_job':'native-engine-platform-order-audit185-20261002-v1','B_comparison':'B_STRICT_CLOUD_COMPARISON.json','B_order_count_time_comparison':'B_ORDER_COMPARISON.json','B_classified_results':'B_STRICT_ALL_CLASSIFIED_RESULTS.json','B_path_validity':'B_STRICT_PATH_VALIDITY.json','strategies':list(metrics),'numeric_gates':{s:m['numerical_acceptance'] for s,m in metrics.items()},'full_spec_acceptance':{s:m['full_spec_acceptance'] for s,m in metrics.items()},'strict_taker_only':'PASS_ZERO_OBSERVED_MAKER_FILLS_IOC','holdings_predicate':'CONFIRMED_INVENTORY_ONLY; pending ownership ledger preserved','original_CG1AT_alpha_replayed':False,'cloud_order_sequence_comparison':'UNKNOWN_NOT_RECORDED; local A order sequence now observed','private_data':'ONLY_PUBLIC_INPUT_IDS_AND_SYNTHETIC_SIMULATOR_COUNTERS','engine_native_sha256':'033469835b44f94f1a022e419e79be61be72a9f2501ceea11b8e255b4824d145','all_old_gates_passed':False,'model_fits':0,'live_changes':0}
(DEST/'INDEX.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
s=importlib.util.spec_from_file_location('scan',ROOT/'tools/sync_research_pack.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
flagged=[{'file':f.relative_to(DEST).as_posix(),'matches':m.scan(f)} for f in DEST.rglob('*') if f.is_file() and m.scan(f)]
assert all(x['file']=='B_INDEPENDENT_AUDIT.json' and x['matches']==[m.FLAG[1].pattern] for x in flagged),flagged
privacy={'status':'PASS_MANUALLY_REVIEWED' if flagged else 'PASS','FLAGGED':len(flagged),'scan':'sync_research_pack FLAG patterns including gzip decompression','reviewed_flags':[{'file':x['file'],'sha256':hashlib.sha256((DEST/x['file']).read_bytes()).hexdigest(),'pattern_indices':[1],'line':8,'disposition':'Generic credential vocabulary in privacy-scan scope prose only. Exact line manually examined; no credential value or private identifier. Original independent report preserved unchanged.'} for x in flagged],'private_identifiers':0,'source_scope':'new research code, public metadata, simulated orders and native receipts; unchanged runtime source remains available in the pinned repro branch','allowed_ids':'public market_id and synthetic order_sequence; no private account address, wallet, observed order hash, or transaction hash'}
(DEST/'PRIVATE_DATA_CHECK.json').write_text(json.dumps(privacy,indent=2),encoding='utf-8')
files={f.relative_to(DEST).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(DEST.rglob('*')) if f.is_file()}
(DEST/'SHA256SUMS.json').write_text(json.dumps({'files':files},indent=2),encoding='utf-8')
print(json.dumps({'packet':DEST.name,'files':len(files)+1,'bytes':sum(f.stat().st_size for f in DEST.rglob('*') if f.is_file()),'FLAGGED':len(flagged),'reviewed_prose_only':len(flagged)}))
