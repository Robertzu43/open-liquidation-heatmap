"""Reproducible offline synthetic workload measurements; not a live-data SLA."""
from __future__ import annotations
import argparse
import gzip
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liqmap import __version__
from liqmap.service import load_heatmap


def measure(samples=7):
    rows = []
    for range_key in ('24h', '7d', '30d'):
        for model in (1, 2, 3):
            load_heatmap('demo', 'BTC', range_key, model, 'pair')
            elapsed = []
            for _ in range(samples):
                start = time.perf_counter()
                payload = load_heatmap('demo', 'BTC', range_key, model, 'pair')
                elapsed.append((time.perf_counter() - start) * 1000)
            wire = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
            rows.append(dict(range=range_key, model=model, bars=len(payload['times']),
                             minimum_ms=min(elapsed), median_ms=statistics.median(elapsed),
                             maximum_ms=max(elapsed), samples_ms=elapsed,
                             json_bytes=len(wire), gzip_bytes=len(gzip.compress(wire, compresslevel=5))))
    return {'version': __version__, 'recorded_at': datetime.now(timezone.utc).isoformat(),
            'commit': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
            'working_tree_dirty': bool(subprocess.check_output(['git','status','--porcelain'], cwd=ROOT, text=True).strip()),
            'python': platform.python_version(), 'platform': platform.platform(), 'cpu_count': os.cpu_count(),
            'scope': 'Seeded synthetic BTC; cached source, freshly computed model and payload. No HTTP or exchange latency. Samples are repeated in one process. Not a live-data SLA or empirical model validation.',
            'rows': rows}


def plot(report, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':10,'figure.facecolor':'#101510','axes.facecolor':'#171e18',
                        'axes.edgecolor':'#657565','text.color':'#e8ede5','axes.labelcolor':'#e8ede5',
                        'xtick.color':'#bdcbb8','ytick.color':'#bdcbb8'})
    fig, axes = plt.subplots(1,2,figsize=(11,4.3),layout='constrained')
    colors=['#e4b653','#58b8aa','#b2a0da']
    for model, color in zip((1,2,3),colors):
        rows=[r for r in report['rows'] if r['model']==model]
        x=[i+(model-2)*.24 for i in range(3)]
        med=[r['median_ms'] for r in rows]
        axes[0].bar(x,med,width=.23,color=color,label=f'Model {model}',
                    yerr=[[m-r['minimum_ms'] for m,r in zip(med,rows)],
                          [r['maximum_ms']-m for m,r in zip(med,rows)]],capsize=2,
                    error_kw={'ecolor':'#e8ede5','elinewidth':.8})
        axes[1].bar(x,[r['gzip_bytes']/1024 for r in rows],width=.23,color=color)
    for ax in axes:
        ax.set_xticks(range(3),['24h','7d','30d'])
        ax.spines[['top','right']].set_visible(False)
        ax.grid(axis='y',alpha=.12)
        ax.set_axisbelow(True)
    axes[0].set_title('Model + payload • median and observed range')
    axes[0].set_ylabel('Milliseconds (7 repeated runs)')
    axes[0].legend(frameon=False,labelcolor='#e8ede5')
    axes[1].set_title('Compressed response size')
    axes[1].set_ylabel('KiB • gzip level 5')
    fig.suptitle(f"Open Liq {report['version']} · offline synthetic workload",fontsize=15,fontweight='bold')
    fig.supxlabel('Cached source; no HTTP/exchange latency. Full environment and samples in benchmark.json.',fontsize=9)
    fig.savefig(path,dpi=160)
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/assets')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    report=measure()
    assert all(r['minimum_ms']<=r['median_ms']<=r['maximum_ms'] for r in report['rows'])
    assert all(r['gzip_bytes']>0 and len(r['samples_ms'])==7 for r in report['rows'])
    (args.output/'benchmark.json').write_text(json.dumps(report,indent=2)+'\n')
    plot(report,args.output/'benchmark.png')
    print(json.dumps({'measurements':len(report['rows']), 'output':str(args.output), 'max_median_ms':max(r['median_ms'] for r in report['rows'])}))

if __name__=='__main__':
    main()
