"""Export V31 observed path comparison; no interpolated fills or simulation."""
import math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from prepare_btc5m_held_amplitude_v1 import *


def main():
    report=read(R/(STEM+'_RESULT.json'));assert report['verification']=='PASS'
    b,bt=get(BASE);n,nt=get(RET/JOB)
    target=read(R/'BTC5M_POST_EXPOSURE_RESPONSE_V1_20260913_RESULT.json')['target']['2026085']
    panels=[('UP 條件結算收益',lambda r:r['inv']['UP']-r['cost']),
        ('DOWN 條件結算收益',lambda r:r['inv']['DOWN']-r['cost']),
        ('淨 UP 份額',lambda r:r['inv']['UP']-r['inv']['DOWN']),('累計取得成本',lambda r:r['cost'])]
    plt.rcParams.update({'font.family':'Microsoft JhengHei','axes.unicode_minus':False,'font.size':10})
    fig,axes=plt.subplots(2,2,figsize=(13,8),sharex=True)
    arms=[('V27 原版',bt['states'],'#8391a5'),('V31 保留方向強度',nt['states'],'#1c78c0'),('Target 離線觀察',target['curve'],'#c37419')]
    for ax,(title,fn) in zip(axes.flat,panels):
        for label,rows,color in arms:
            points={START:dict(inv=dict(UP=0.,DOWN=0.),cost=0.)}
            points.update({r['t']:r for r in rows if START<=r['t']<=START+300000})
            times=sorted(points);x=[(t-START)/1000 for t in times];y=[fn(points[t]) for t in times]
            if x[-1]<300:x.append(300);y.append(y[-1])
            assert all(math.isfinite(v) for v in x+y)
            ax.step(x,y,where='post',label=label,color=color,lw=1.65)
            ax.plot(x[-1],y[-1],'o',color=color,ms=3)
        ax.axhline(0,color='#c1c7ce',lw=.7);ax.set_title(title,loc='left',fontweight='bold')
        ax.grid(alpha=.15);ax.set_xlim(0,305);ax.spines[['top','right']].set_visible(False)
    axes[0,0].legend(loc='upper left',frameon=False)
    for ax in axes[-1]:ax.set_xlabel('市場開始後秒數')
    fig.suptitle('方向保住了，修復仍跟不上持續加倉',fontsize=18,fontweight='bold',x=.075,ha='left')
    fig.text(.075,.917,'BTC 2026085 · 單一變因、固定 15 份被動單、現金限制關閉 · V31 native 68.749 秒',color='#536174')
    fig.text(.075,.027,'線條為已觀察狀態延續；Target 使用成交秒桶，OUR 使用 canonical 回報時鐘。條件收益不是實際結算結果。',fontsize=9,color='#536174')
    fig.subplots_adjust(top=.85,bottom=.11,left=.075,right=.975,hspace=.26,wspace=.18)
    for ext in ('png','svg'):fig.savefig(R/(STEM+'_PATHS.'+ext),dpi=160,facecolor='white')
    print(json.dumps(dict(status='PASS',panels=4,arms=3,source_result_sha256=sha(R/(STEM+'_RESULT.json')))))


if __name__=='__main__':main()
