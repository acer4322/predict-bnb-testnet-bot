"""Observed V38/Target path chart; no fitting, replay or interpolated fills."""
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MultipleLocator

from audit_btc5m_target_down_timing_v1 import SOURCE, SOURCE_SHA, START, verify_target
from btc5m_finite_active_service_experiment_v1 import ROOT, R, JOB, STEM, read, sha, same, keyed_legs

OUT = R / 'BTC5M_V38_VS_TARGET_2028352_PATHS_20260913'
BLUE = '#1768B2'
ORANGE = '#C66B12'
PURPLE = '#8465A8'


def points(rows):
    assert all(a['t'] <= b['t'] for a, b in zip(rows, rows[1:]))
    observed = {START: dict(inv=dict(UP=0., DOWN=0.), cost=0.)}
    observed.update({r['t']: r for r in rows if START <= r['t'] <= START+300000})
    last = observed[max(observed)]
    # EOF receipt drain may add timestamps; no economic change is hidden at 300s.
    same(last['inv'], rows[-1]['inv']); same(last['cost'], rows[-1]['cost'])
    observed[START+300000] = last
    times = sorted(observed)
    x = [(t-START)/1000 for t in times]
    data = dict(x=x, up=[observed[t]['inv']['UP'] for t in times],
        down=[observed[t]['inv']['DOWN'] for t in times], cost=[observed[t]['cost'] for t in times])
    for side in ('up', 'down'):
        data[side+'_payoff'] = [q-c for q,c in zip(data[side], data['cost'])]
    assert all(math.isfinite(v) for values in data.values() for v in values)
    return data


def main():
    validation = read(R/(STEM+'_FINAL_VALIDATION.json'))
    assert validation['status'] == 'PASS'
    result = read(R/(STEM+'_RESULT.json')); assert result['job'] == JOB
    assert sha(SOURCE) == SOURCE_SHA
    source = read(SOURCE); _, target_curve, target_check = verify_target(source)
    folder = R/'lan_worker_returns'/JOB
    native = read(folder/'result.json'); trace = read(folder/'clock_trace.json.gz')
    audit = read(R/(STEM+'_2028352_finite_service_AUDIT.json'))
    assert sha(folder/'result.json') == audit['result_sha256']
    assert sha(folder/'clock_trace.json.gz') == audit['trace_sha256']
    assert native['execution_accounting_valid'] and native['unresolved_owners'] == 0
    data = dict(model=points(trace['states']), target=points(target_curve))
    for side in ('UP', 'DOWN'):
        same(data['model'][side.lower()][-1], native['final_inventory'][side])
        same(data['model'][side.lower()+'_payoff'][-1], result['candidate_terminal'][side])
    service = result['service_owner']; service_sec = (service['first_canonical_t']-START)/1000
    assert service['filled'] == 16.74 and service_sec == 176.25
    news = {o['key']: dict(t=p['t'], **o) for p in trace['plans'] for o in p['operations'] if o['kind']=='NEW'}
    legs = keyed_legs(native, trace, news)
    last_up = max((l['t']-START)/1000 for l in legs if l['side']=='UP')
    target_last_up = max((l['event_ms']-START)/1000 for l in source['targetActions'] if l['side']=='UP')

    plt.rcParams.update({'font.family':['Microsoft JhengHei','DejaVu Sans'],
        'axes.unicode_minus':False,'font.size':11,'axes.labelcolor':'#485566',
        'text.color':'#1E2E40','axes.edgecolor':'#CCD5DF','xtick.color':'#617083',
        'ytick.color':'#617083','svg.fonttype':'path'})
    fig, axes = plt.subplots(2, 2, figsize=(14, 9.4), sharex=True, facecolor='white')
    fig.subplots_adjust(left=.073, right=.967, top=.805, bottom=.15, wspace=.18, hspace=.29)
    titles = [('up','UP 累計成交份額','份'), ('down','DOWN 累計成交份額','份'),
        ('up_payoff','UP 結算條件損益','元'), ('down_payoff','DOWN 結算條件損益','元')]
    styles = [('model',BLUE,'目前模型 V38',2.0), ('target',ORANGE,'Target 離線觀察',2.0)]
    for ax,(field,title,unit) in zip(axes.flat,titles):
        ax.set_facecolor('#FBFCFE')
        ax.axvspan(100,150,color='#DEE8F0',alpha=.38,zorder=0)
        ax.axvline(service_sec,color=PURPLE,lw=1.0,linestyle=(0,(3,3)),alpha=.75,zorder=1)
        for label,color,legend,lw in styles:
            d = data[label]
            ax.step(d['x'],d[field],where='post',color=color,lw=lw,label=legend,zorder=3)
            end = d[field][-1]
            ax.plot(300,end,'o',color=color,ms=4,clip_on=False,zorder=5)
            text = f'{end:,.2f}' if unit=='份' else f'{end:+,.2f}'
            other = 'target' if label=='model' else 'model'
            label_offset = 8 if end >= data[other][field][-1] else -17
            ax.annotate(text,xy=(300,end),xytext=(-8,label_offset),
                textcoords='offset points',ha='right',color=color,fontsize=10.7,fontweight='bold',
                bbox=dict(boxstyle='round,pad=.18',facecolor='white',edgecolor='none',alpha=.9),zorder=6)
        ax.set_title(title,loc='left',fontsize=13,fontweight='bold',pad=11)
        ax.set_ylabel(unit,rotation=0,labelpad=13)
        ax.set_xlim(0,300); ax.xaxis.set_major_locator(MultipleLocator(50))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x,pos:f'{x:,.0f}'))
        ax.grid(axis='y',alpha=.32,lw=.6,color='#B7C3D0')
        ax.spines[['top','right']].set_visible(False)
        if unit=='份': ax.set_ylim(-90,4250)
        else:
            ax.set_ylim(-900,380)
            ax.axhline(0,color='#8392A4',lw=.8,linestyle=(0,(3,3)),zorder=2)
    for ax in axes[1]: ax.set_xlabel('市場開始後秒數',labelpad=9)
    axes[0,0].annotate('模型最後 UP 成交：188.843 秒',
        xy=(last_up,native['final_inventory']['UP']),xytext=(85,2140),
        fontsize=9.7,color=BLUE,arrowprops=dict(arrowstyle='-',color=BLUE,lw=.8),
        bbox=dict(facecolor='white',edgecolor='none',alpha=.85,pad=2))
    axes[0,1].text(125,4000,'100–150 秒：便宜 DOWN 取得期',ha='center',
        fontsize=9.5,color='#6C7E90')
    fig.suptitle('BTC 2028352｜目前模型與 Target 的市場路線',
        x=.073,y=.97,ha='left',fontsize=21,fontweight='bold')
    fig.text(.073,.925,'V38 已完成原生回放 · 模型已給定 Target 最終淨持倉方向 UP · 原始規模',
        fontsize=11,color='#586A7C')
    fig.text(.073,.897,f"期末取得成本：模型 {native['final_cost']:,.2f} 元  ／  Target {target_curve[-1]['cost']:,.2f} 元",
        fontsize=11,color='#586A7C')
    handles, labels = axes[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='upper left',bbox_to_anchor=(.066,.88),
        frameon=False,ncol=2,fontsize=11,handlelength=2.8,columnspacing=2.2)
    fig.text(.51,.85,'紫色虛線：新增 Active 成交 176.250 秒｜DOWN 16.74 份 @ 0.14',
        fontsize=9.7,color=PURPLE)
    fig.text(.073,.062,'條件損益＝該側累計份額－兩側累計取得成本；不是實際 winner 的結算結果。',
        fontsize=9.5,color='#617083')
    fig.text(.073,.036,'Target 為成交秒桶重建（期初 0、未知 fees/rebates 未計）；模型使用 canonical 成交回報時鐘、回放費用為 0。',
        fontsize=9,color='#617083')
    fig.text(.073,.013,'階梯線只延續已觀察狀態，未平滑；原始成交價格與時鐘精度不同，不能由圖反推私人掛單時刻。',
        fontsize=9,color='#617083')
    for ext in ('png','svg'):
        fig.savefig(str(OUT)+'.'+ext,dpi=170,facecolor='white')
    plt.close(fig)
    artifact = dict(status='PASS',market_id=2028352,model_version='V38',job=JOB,
        source_sha256=SOURCE_SHA,model_trace_sha256=audit['trace_sha256'],target_checks=target_check,
        paths=data,model_last_up_fill_seconds=last_up,target_last_up_fill_seconds=target_last_up,
        service_fill_seconds=service_sec,terminal={name:{k:v[-1] for k,v in d.items() if k!='x'} for name,d in data.items()},
        chart=dict(png=str(OUT)+'.png',svg=str(OUT)+'.svg',panels=4,series=2,raw_scale=True,
            step_mode='post',zero_start=True,after_300_economic_state_unchanged=True),
        native_jobs=0,model_fit=0,source_files_modified=False)
    Path(str(OUT)+'.json').write_text(json.dumps(artifact,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({k:artifact[k] for k in ('status','market_id','model_version','terminal','chart')}))


if __name__=='__main__':main()
