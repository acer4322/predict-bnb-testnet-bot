from __future__ import annotations
import argparse, os
from http.server import ThreadingHTTPServer
from . import unified_controller_paper_v2 as base


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument('--shares',type=float,required=True)
    p.add_argument('--min-price',type=float,required=True)
    p.add_argument('--port',type=int,required=True)
    p.add_argument('--version',required=True)
    args=p.parse_args()
    if os.environ.get('PREDICT_LIVE_ENABLED','false').lower() not in {'0','false','no','off',''}:
        raise RuntimeError('scaled R2 dream paper refuses to start while PREDICT_LIVE_ENABLED is true')
    base.SHARES=float(args.shares)
    base.MIN_PRICE=float(args.min_price)
    base.VERSION=str(args.version)
    base.PORT=int(args.port)
    runtime=base.UnifiedControllerPaperV2()
    runtime.start()
    handler=type('R2ScaledDreamHandler',(base.Handler,),{'runtime':runtime})
    server=ThreadingHTTPServer((base.HOST,base.PORT),handler)
    print(f"{base.VERSION} listening on http://{base.HOST}:{base.PORT}/state; shares={base.SHARES}; minPrice={base.MIN_PRICE}; dreamPaper=true; liveOrdersAffected=false",flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown(); server.server_close(); runtime.stop()
    return 0

if __name__=='__main__':
    raise SystemExit(main())
