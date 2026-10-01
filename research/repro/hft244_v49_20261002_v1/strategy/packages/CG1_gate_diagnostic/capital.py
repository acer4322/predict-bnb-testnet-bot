"""Peak capital per path = max over decision frames of (cost + pending cash both sides + NEW ops of that plan), from risk_floor_trace."""
import gzip, json, statistics, sys
from pathlib import Path
read = lambda p: json.loads(gzip.decompress(Path(p).read_bytes()) if str(p).endswith('.gz') else Path(p).read_bytes())
def peak(arm):
    rs = read(Path(arm) / 'risk_floor_trace.json.gz'); best = 0.
    for p in rs['plans']:
        s = p['state']; v = float(s.get('cost', 0.)) + sum(float(x) for x in s['pending_cash'].values())
        v += sum(o['qty'] * o['price'] for o in p['operations'] if o['kind'] == 'NEW')
        best = max(best, v)
    return best, read(Path(arm) / 'result.json')['final_cost']
if __name__ == '__main__':
    L = Path(__file__).resolve().parent.parent / 'lan_worker_returns'
    ms = json.loads((Path(__file__).resolve().parent / 'PROTOCOL.json').read_text())['markets']
    for name, fmt in (('CTRL full', 'btc5m-v12g-fresh40-flip200-20260928-v61r1/arms/v12g61r1_CTRL_{}'), ('half size', 'btc5m-v12g-passive-mirror-size-fresh40-20260928-v67/arms/v12g67_S050_{}')):
        pk = [peak(L / fmt.format(m)) for m in ms]
        p = [a for a, _ in pk]; c = [b for _, b in pk]
        print(f"{name:10s} final cost mean {statistics.mean(c):6.0f} max {max(c):6.0f} | peak capital mean {statistics.mean(p):6.0f} p90 {sorted(p)[int(.9*len(p))]:6.0f} max {max(p):6.0f}")
