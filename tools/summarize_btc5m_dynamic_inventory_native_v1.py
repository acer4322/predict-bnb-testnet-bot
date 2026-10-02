"""Close the completed V48 cohort from collected evidence; never dispatch."""
import collections
from pathlib import Path
import btc5m_dynamic_inventory_native_v1 as d


def main():
    audits=[];traces=[];results=[]
    for job in d.jobs():
        w=d.worker(job);a=d.read(w.artifact(job,'AUDIT'));assert a['execution_status']=='PASS'
        f=d.R/'lan_worker_returns'/job['job_id'];tr=d.read(f/'clock_trace.json.gz');n=d.read(f/'result.json')
        audits.append(a);traces.append(tr);results.append(n)
    pairs=[]
    for left,right in ((1,2),(3,4)):
        a,b=audits[left],audits[right];ta,tb=traces[left],traces[right]
        equality={k:ta[k]==tb[k] for k in ('plans','states','native_actions','demand_final','banks','birth_provenance')}
        assert all(equality.values()) and a['grace_frames']==b['grace_frames']==0
        switches=[]
        for row in a['switches']:
            frame=next(f for f in ta['bridge_frames'] if f['index']==row['index'])
            after=[dict(t=p['t'],**o) for p in ta['plans'] if p['t']>=row['t'] for o in p['operations'] if o['kind']=='NEW']
            switches.append(dict(seconds=row['seconds'],side=row['side'],inventory=row['inv'],fills=row['increments'],
                net_gap=abs(row['inv']['UP']-row['inv']['DOWN']),pending=frame['state']['pending_qty'],payoff=frame['state']['payoff'],
                owners_at_switch=len(frame['state']['owners']),new_after_switch=after))
        pairs.append(dict(market=a['market'],exact_path_equality=equality,up=a['terminal']['up'],down=a['terminal']['down'],
            cost=a['cost'],active=a['active'],grace_frames=0,switches=switches,
            opportunity_reasons=dict(collections.Counter(r['reason'] for r in ta['opportunity_rows'])),
            commitment_reasons=dict(collections.Counter(r['reason'] for r in ta['commitment_repair_rows'])),
            coordination_reasons=dict(collections.Counter(r['reason'] for r in ta['coordination_rows'])),
            minimum_observed_weak_ask=min(r['active_ask'] for r in ta['opportunity_rows'] if r['active_ask'] is not None)))
    elapsed=round(sum(a['native_elapsed_seconds'] for a in audits),3)
    state=d.worker().idle()
    out=dict(status='COMPLETE',execution_audit='PASS',strategy_core_learned=False,grace_effect='NOT_IDENTIFIED_ZERO_ACTIVATIONS',
        native_jobs=5,total_native_elapsed_seconds=elapsed,max_threads=4,pairs=pairs,
        first_engineering_parity=d.read(d.R/(d.STEM+'_PARITY.json')),
        native_switch_coverage='1977248 switches only after both payoffs positive and owners empty; no subsequent NEW. 2127218 never switches. Live-owner switch branch remains component-tested only.',
        findings=['Dual neutral opening removes the hard-coded asymmetric initial quoting but does not discover Target direction belief.',
            'One-ticket grace had zero activations and exact path equality on both consumed markets; no evidence of benefit or harm.',
            '1977248 repair burst is 30 shares, overtake 27.687 shares. The switch creates no subsequent acquisition.',
            '2127218 has 1456 passive-price-available opportunity rejections, zero Active, and dependent services waiting for the first Active receipt.'],
        next_hypothesis='Preserve inventory-based role selection. Separate the authority to add after a repair-only overtake from the accounting role itself; prioritize finite active repair eligibility when legal passive service has not kept pace with confirmed new exposure. Do not simply increase a reversal threshold.',
        limits=['Two consumed diagnostic markets, not out of sample.','No Target runtime data or new direction signal.','Global five-Active research ceiling preserved.','Native drain terminal time is not exactly persisted for every owner.'],
        model_fits=0,parameter_search=0,local_native_jobs=0,live_changes=0,plots=0,pending_jobs=[])
    d.dump('RESULT',out)
    d.dump('PROGRESS',dict(status='COMPLETE_ALL_FIVE_AUDITED',submissions=5,pending_jobs=[],native_seconds=elapsed,do_not_resubmit=True,V46='DEFERRED_DO_NOT_SUBMIT'))
    packages=(d.PARENT,d.ORIGINAL_PACKAGE,d.PACKAGE)
    pinrows=[]
    for p in packages:
        m=d.read(p/'manifest.json');assert all(d.sha(p/n)==h for n,h in m['files'].items())
        pinrows.append(dict(package=p.name,manifest_sha256=d.sha(p/'manifest.json'),files=len(m['files']),status='PASS'))
    tools=['btc5m_inventory_direction_bridge_v1.py','btc5m_dynamic_inventory_native_v1.py','verify_btc5m_dynamic_inventory_native_v1.py','verify_btc5m_dynamic_producer_component_v1.py','summarize_btc5m_dynamic_inventory_native_v1.py']
    d.dump('FINAL_VALIDATION',dict(status='PASS',native_jobs=5,terminal_audits=5,packages=pinrows,tools={n:d.sha(d.ROOT/'tools'/n) for n in tools},
        worker_state=state,model_fits=0,parameter_search=0,local_native_jobs=0,live_changes=0,pending_jobs=[]))
    text=f'''# V48：動態持倉方向完整接線與原生配對已完成

接續 [V47](BTC5M_MICROWORLD_CORE_LOOP_HANDOFF_V47_20260914.md)。本輪已完成 gateway 接線、雙側狀態銀行及完整原生回放，**5 個 job 全部執行／帳務／機制核對 PASS，累計原生時間 {elapsed:.3f} 秒**。策略核心仍未學會；兩場不能宣稱泛化或已識別 Target 信念。全部已收回，沒有待跑 job，勿重送。

## 使用者規則與接線

基準組按已確認持倉較多側動態加倉，平手保留前向；初始平手中立，雙側各合法 Passive15。雙側開局已走 canonical draft reserve、原 envelope／gateway／native。CANCEL_PENDING／UNKNOWN 保留所有承諾，取消不提供同計畫釋放權限。Active 仍為可變份額，NEW 至少 1 元。現有 theta、qref、首次 OWN 持倉幅度、V44 200 秒起禁止方向側被動 NEW 全保留。

需求／加倉 anchor／growth-hold／opportunity／commitment／coordination（內含 renewal）依實際 strong UP、DOWN 保存兩組狀態。所有有限修復工作每個來源 frame 都以原側別觀察；舊 repair owner 的特殊維護也綁原出生側。inactive bank 不產生新單，返回該側後續用原記憶及當前回執。共用實際 ledger、parent UP1／DOWN2、owner IDs、atomic responsibility。

主動服務為全局 opportunity≤1、原 coordination≤2、renewed≤2、合計≤5，不因兩側各有銀行而倍增。此為既有研究限制，不能當成 Target 策略特徵。V46 固定強度對照仍停用未送，沒有採用其幅度干預。

## 加測修復反超緩衝

NEW 時記錄不可重分類的 NEUTRAL／ADD／REPAIR 出生用途。候選 REPAIR_GRACE：若反超側的新增確認成交都來自 REPAIR，且差额≤15 份，暫留原方向；差額>15 或反超側另有 ADD／NEUTRAL 成交才切換。pending 不算成交。緩衝不含時間／價格參數，15 來自原固定份額。

|市場|動態基準 UP 條件損益|DOWN 條件損益|反轉|Active|緩衝候選|
|---|---:|---:|---|---:|---|
|1977248|+94.299273|+66.612272|DOWN→UP 一次|3|0 次觸發，整條路徑完全相同|
|2127218|+5.605648|−400.653883|始終 UP|0|0 次觸發，整條路徑完全相同|

以上是 UP／DOWN 兩種結算条件的損益，非同時實現的兩筆獲利。兩對的 plans、states、native_actions、demand_final、banks、birth_provenance 完全相同。不能說緩衝有效，也不能用 0 觸發推論此設計普遍無效。

1977248 的 216.414 秒，兩張修復 UP 單同批成交 30 份，UP 1260.895573／DOWN 1233.208571，反超 27.687002 份，超過 15 緩衝。當下兩種 payoff 都正、pending=0、沒有 live owner，此後 0 NEW。這是帳面方向變化，未造成追買新方向；不應為了消除這次切換而任意調大門檻。V47 曾在舊已保存路徑重算的反超是 7.687002，不能混為此新路徑的反超數。

2127218 在 3.777 秒首見中立 UP 單成交 15 份，之後始終 UP 多，最後淨 UP 406.259531。整局無雙正，最差瞬間 floor −538.306176，終局 DOWN −400.653883。opportunity 共 1456 次 FIXED15_PASSIVE_PRICE_STILL_AVAILABLE、15 次 NO_UNCOVERED_DOWN_REPAIR；所見弱側 ask 最低 .25。commitment 的 1471 列全等第一筆 Active 終態回執，coordination 的 1471 列全等確認再曝險情節。這支持應優先處理「被動可合法掛單，但實際修復跟不上」的主動服務入口。此瓶頸與先前 V37 的一般 Active 需求問題相接，並非宣稱新發現了一個 Target 規則。

舊 V45 2127218 不給方向路徑為 +64.339948／−437.629583；新雙側開局改善 DOWN 36.975700、減少 UP 58.734300，仍不是成功循環。V45 給最終 DOWN 為 +29.896989／+36.994804，屬帶離線最終方向的條件診斷。新組不能把差異單獨歸因於動態切向：雙側起步也改變首次成交量及保留幅度。

## 執行與覆蓋邊界

先跑 LEGACY 1977248 接線對照，與原 V44 的完整 trace 欄位及關鍵 result 完全一致，保留 +76.999273／+69.312272。首次封存 v1 已執行此一對照；在動態組派送前查到 native book 只有 bids／asks，與 V47 摘要 best_bid／best_ask 介面不同，另封存 v2 只修當前 book 價格轉接。v1 不覆寫，v2 LEGACY 生成 source 與 v1 正規化路徑後完全相同，不另重跑 parity。總量仍為 1+4=5，沒有 native failure。

局部檢查含 5 模式編譯、30 個方向／用途／差額配對、6 組實際類別的獨立銀行、單調持倉舊修復進度、全局機會上限、實際 canonical draft 與 pending／UNKNOWN；直接抽取生成 Policy 執行兩種動態開局及確認回執換 bank。原 V47 108／4850 元件結果仍保留，不重跑。

完整審核包括來源 frame 逐筆覆蓋、封存與生成程式 hash、全部 raw receipts、實際價格與份額、金額與庫存守恆、所有 owner 終態與預留歸零、own-cross（含 pending）、動態方向／出生用途、尾段 NEW、分側 finite work／growth／hold／opportunity／commitment／coordination／renewal／維護。來源同一毫秒可有兩個 frame；本輪 auditor 已以 source index／同時刻出現順序對齐，沒有改動或重跑 native。

覆蓋限制：1977248 唯一切向發生在空 owner、雙正且後續無 NEW；2127218 無切向。因此「有舊 live repair owner 時切向並繼續服務」程式已接好，但這個分支本輪 native 尚未觸及，不能稱為已在市場充分驗證。終態帳務已核對，但舊 runner 未保存每筆 owner 的精確 drain 終态時間，未以 EOF 假造。

## 接續主線

保留使用者指定的動態持倉角色。优先準備一个有辨識力的小實驗：當被動修復尚未完成、已確認新加倉又加重弱側損失時，是否能依有限金額工作而啟動 Active，不必先等低價機會型 Active 成功。仍計入當前深度、滑價實際成本、pending、own-cross；避免任意預算或放寬為無限追單。

修復反超方面，較好的後續比較是分開「帳面角色切換」與「新側追加倉位授權」：保留真實持倉較多側，但對完全由舊修復造成的切換，先檢查是否真的要新增該側風險。此候選本輪未實作、未派送。不要掃更大的緩衝份額來追這兩個結果。

## 檔案

- [完整結果]({d.STEM}_RESULT.json)
- [協定]({d.STEM}_PROTOCOL.json)、[五個已完成 job]({d.STEM}_WAVE.json)
- [封存转接修正]({d.STEM}_BOOK_ADAPTER_REPAIR.json)、[來源 index 核對修正]({d.STEM}_AUDIT_FRAME_INDEX_CORRECTION.json)
- [最終驗證]({d.STEM}_FINAL_VALIDATION.json)
- [接線程式](../../tools/btc5m_inventory_direction_bridge_v1.py)、[準備／派送](../../tools/btc5m_dynamic_inventory_native_v1.py)、[完整審核](../../tools/verify_btc5m_dynamic_inventory_native_v1.py)

0 fit、0 參數搜尋、0 主機 HFT、0 圖、0 外掛、0 live 變更。第二台單 job、max_threads=4；5 個新 job 均已完成、勿重送；V46 未送、V45 勿重送。下一步沒有預先排程或尚在背景跑的工作。
'''
    path=d.R/'BTC5M_MICROWORLD_CORE_LOOP_HANDOFF_V48_20260914.md';assert not path.exists()
    path.write_text(text,encoding='utf-8')
    full=d.R/'BTC5M_MICROWORLD_CORE_LOOP_FULL_HANDOFF_V1_20260912.md'
    prefix=f'> 最新進度：[V48](BTC5M_MICROWORLD_CORE_LOOP_HANDOFF_V48_20260914.md) 已完成雙側 Passive15 開局與動態持倉方向完整接線；5 個 native／{elapsed:.3f} 秒，全量執行與帳務核對 PASS。1977248 +94.2993／+66.6123；2127218 +5.6056／−400.6539。15 份修復反超緩衝兩場皆 0 觸發、與基準路徑完全相同，未驗出效益。核心未學會，下一主線是被動合法但修復落後時的有限 Active 入口；帳面換向與新側加倉授權可另分離。有 live owner 的切向分支本輪尚無 native 覆蓋。五組全完成勿重送，無待跑 job；V46 仍停用。0 fit／搜尋／主機 HFT／圖／live 變更。\n\n'
    full.write_text(prefix+full.read_text(encoding='utf-8'),encoding='utf-8')
    old=d.R/'BTC5M_MICROWORLD_CORE_LOOP_HANDOFF_V47_20260914.md'
    old.write_text('> 接續已完成：請先讀 [V48](BTC5M_MICROWORLD_CORE_LOOP_HANDOFF_V48_20260914.md)。完整接線與五次 native 已完成；下文保留 V47 當時元件階段狀態。\n\n'+old.read_text(encoding='utf-8'),encoding='utf-8')
    print(dict(status='COMPLETE',native_jobs=5,native_seconds=elapsed,pairs=[{k:p[k] for k in ('market','up','down','active','grace_frames')} for p in pairs],handoff=str(path)))


if __name__=='__main__':main()
