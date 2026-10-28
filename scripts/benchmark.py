"""Reproducible offline synthetic workload measurements; not a live-data SLA."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liqmap import __version__
from liqmap.service import load_heatmap

# Declared before measurement; local engineering checks, not service guarantees.
HTTP_BUDGETS = {
    'warm_request_max_ms': 5000,
    'observed_server_rss_and_hwm_max_bytes': 512 * 1024 * 1024,
    'warm_requests_must_all_succeed': True,
    'scope': 'Offline BTC demo 12h/model1 on this host only; no live-data SLA or hard memory bound.',
}
HTTP_WARM_SAMPLES = 5
HTTP_BURST_CONCURRENCY = 8
HTTP_CLIENT_TIMEOUT_S = 30
# Environment HTTP proxies must not turn a loopback workload into external traffic.
_LOOPBACK_HTTP = build_opener(ProxyHandler({}))

_SERVER_CODE = """
import json
from pathlib import Path
import sys
from liqmap.server import (BoundedThreadingHTTPServer, Handler, MAX_EXPENSIVE_REQUESTS,
                           MAX_HTTP_WORKERS, REQUEST_DEADLINE_S, SOCKET_IDLE_TIMEOUT_S)
server = BoundedThreadingHTTPServer(('127.0.0.1', 0), Handler)
path = Path(sys.argv[1])
temporary = path.with_suffix('.part')
temporary.write_text(json.dumps({'port': server.server_port, 'limits': {
    'expensive_requests': MAX_EXPENSIVE_REQUESTS, 'http_workers': MAX_HTTP_WORKERS,
    'request_deadline_seconds': REQUEST_DEADLINE_S, 'socket_idle_timeout_seconds': SOCKET_IDLE_TIMEOUT_S}}))
temporary.replace(path)
try:
    server.serve_forever()
finally:
    server.server_close()
"""


def hardware():
    cpu_model = platform.processor() or None
    physical_memory = None
    memory_source = None
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                cpu_model = line.split(':', 1)[1].strip()
                break
    except OSError:
        pass
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal:'):
                physical_memory = int(line.split()[1]) * 1024
                memory_source = '/proc/meminfo MemTotal'
                break
    except OSError:
        try:
            physical_memory = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
            memory_source = 'os.sysconf physical pages'
        except (AttributeError, OSError, ValueError):
            pass
    return {'cpu_model': cpu_model, 'physical_memory_bytes': physical_memory,
            'physical_memory_source': memory_source,
            'notes': 'CPU/RAM are OS-reported and may describe a VM or host rather than a container allocation. Shared-host contention and virtualization can affect samples; no CPU isolation was applied.'}


def server_memory(pid, point):
    result = {'point': point, 'pid': pid, 'recorded_at': datetime.now(timezone.utc).isoformat(),
              'rss_bytes': None, 'hwm_bytes': None, 'supported': False,
              'source': '/proc/PID/status VmRSS/VmHWM' if sys.platform.startswith('linux') else None}
    if not sys.platform.startswith('linux'):
        result['reason'] = 'Linux /proc process memory is unavailable on this platform.'
        return result
    try:
        fields = {}
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith(('VmRSS:', 'VmHWM:')):
                key, value = line.split(':', 1)
                fields[key] = int(value.split()[0]) * 1024
        result.update(rss_bytes=fields['VmRSS'], hwm_bytes=fields['VmHWM'], supported=True)
    except (OSError, KeyError, ValueError) as exc:
        result['reason'] = f'Process memory could not be read ({type(exc).__name__}).'
    return result


def http_request(url, index):
    start = time.perf_counter()
    sample = {'index': index, 'status': None, 'error_code': None,
              'wire_bytes': 0, 'decoded_json_bytes': 0, 'content_encoding': None}
    try:
        try:
            response = _LOOPBACK_HTTP.open(Request(url, headers={'Accept-Encoding': 'gzip', 'Connection': 'close'}), timeout=HTTP_CLIENT_TIMEOUT_S)
        except HTTPError as exc:
            response = exc
        with response:
            sample['status'] = response.status
            wire = response.read()
            sample['wire_bytes'] = len(wire)
            encoding = response.headers.get('Content-Encoding')
            body = gzip.decompress(wire) if encoding == 'gzip' else wire
            sample['decoded_json_bytes'] = len(body)
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError('Expected a JSON response object.')
            sample.update(status=response.status, wire_bytes=len(wire), decoded_json_bytes=len(body),
                          content_encoding=encoding, error_code=payload.get('code'),
                          source=payload.get('source'), quality_status=payload.get('quality', {}).get('status'),
                          schema_version=payload.get('schema_version'), model_version=payload.get('model_version'),
                          data_fingerprint=payload.get('data_fingerprint'), bars=len(payload.get('times', [])))
            if response.status == 200 and (sample['source'] != 'demo' or sample['quality_status'] != 'synthetic'):
                sample['error_code'] = 'unexpected_source'
    except (OSError, URLError, ValueError, HTTPException) as exc:
        sample.update(error_code='transport_or_decode_error', error_type=type(exc).__name__, error=str(exc))
    sample['latency_ms'] = (time.perf_counter() - start) * 1000
    return sample


def request_summary(samples):
    elapsed = [sample['latency_ms'] for sample in samples]
    by_status = {}
    for sample in samples:
        key = str(sample['status']) if sample['status'] is not None else 'no_http_response'
        by_status.setdefault(key, []).append(sample['latency_ms'])
    return {'requests': len(samples),
            'latency_by_status': {key: {'requests': len(values), 'minimum_ms': min(values),
                                        'median_ms': statistics.median(values), 'maximum_ms': max(values)}
                                  for key, values in by_status.items()},
            'status_counts': dict(Counter(str(sample['status']) if sample['status'] is not None else 'no_http_response' for sample in samples)),
            'error_counts': dict(Counter(sample['error_code'] for sample in samples if sample['error_code'] is not None)),
            'minimum_ms': min(elapsed), 'median_ms': statistics.median(elapsed), 'maximum_ms': max(elapsed),
            'wire_bytes_total': sum(sample['wire_bytes'] for sample in samples), 'samples': samples}


def measure_http():
    """One fresh server, serial cold/warm requests, then one synchronized burst."""
    report = {'budgets_declared_before_run': dict(HTTP_BUDGETS),
              'workload': {'exchange': 'demo', 'symbol': 'BTC', 'range': '12h', 'model': 1,
                           'warm_repeats': HTTP_WARM_SAMPLES, 'burst_requests': HTTP_BURST_CONCURRENCY,
                           'burst_concurrency': HTTP_BURST_CONCURRENCY, 'client_timeout_seconds': HTTP_CLIENT_TIMEOUT_S,
                           'connection': 'New loopback TCP connection per request; environment proxies disabled; gzip requested.',
                           'latency': 'Client wall time including connect, response transfer, gzip decode and JSON parse; startup measured separately.'},
              'scope': 'Fresh production BoundedThreadingHTTPServer in a child interpreter. Demo only; no external network. One small local workload, not a load certification. Memory belongs to the server process only; samples and Linux reported high-water mark are evidence, not a universal peak upper bound.'}
    with tempfile.TemporaryDirectory(prefix='open-liq-benchmark-') as directory:
        temporary = Path(directory)
        ready = temporary / 'server.json'
        env = os.environ.copy()
        env['LIQMAP_CACHE_DIR'] = str(temporary / 'cache')
        env['PYTHONPATH'] = str(ROOT)
        started = time.perf_counter()
        with (temporary / 'server.log').open('w+') as output:
            child = subprocess.Popen([sys.executable, '-u', '-c', _SERVER_CODE, str(ready)], cwd=ROOT, env=env,
                                     stdout=output, stderr=subprocess.STDOUT)
            report['server_pid'] = child.pid
            try:
                startup_deadline = time.monotonic() + 15
                while not ready.exists():
                    if child.poll() is not None:
                        raise RuntimeError(f'Benchmark server exited during startup (exit={child.returncode}).')
                    if time.monotonic() >= startup_deadline:
                        raise RuntimeError('Benchmark server did not start within 15 seconds.')
                    time.sleep(.02)
                server_info = json.loads(ready.read_text())
                port = server_info['port']
                report['server_limits'] = server_info['limits']
                report['workload']['burst_exceeds_expensive_limit'] = HTTP_BURST_CONCURRENCY > server_info['limits']['expensive_requests']
                report['server_startup_ms'] = (time.perf_counter() - started) * 1000
                url = f'http://127.0.0.1:{port}/api/v1/heatmap?exchange=demo&symbol=BTC&range=12h&model=1'
                report['request_path'] = '/api/v1/heatmap?exchange=demo&symbol=BTC&range=12h&model=1'
                report['memory_observations'] = [server_memory(child.pid, 'started_before_first_request')]
                report['cold'] = request_summary([http_request(url, 0)])
                report['memory_observations'].append(server_memory(child.pid, 'after_cold_request'))
                report['warm'] = request_summary([http_request(url, index) for index in range(HTTP_WARM_SAMPLES)])
                report['memory_observations'].append(server_memory(child.pid, 'after_warm_before_burst'))
                barrier = threading.Barrier(HTTP_BURST_CONCURRENCY + 1)
                def burst_request(index):
                    barrier.wait(timeout=15)
                    return http_request(url, index)
                with ThreadPoolExecutor(max_workers=HTTP_BURST_CONCURRENCY) as pool:
                    futures = [pool.submit(burst_request, index) for index in range(HTTP_BURST_CONCURRENCY)]
                    burst_start = time.perf_counter()
                    barrier.wait(timeout=15)
                    samples = [future.result(timeout=HTTP_CLIENT_TIMEOUT_S + 5) for future in futures]
                    burst_elapsed = (time.perf_counter() - burst_start) * 1000
                report['burst'] = request_summary(samples)
                report['burst']['batch_elapsed_ms'] = burst_elapsed
                report['memory_observations'].append(server_memory(child.pid, 'after_burst'))
                all_samples = report['cold']['samples'] + report['warm']['samples'] + samples
                report['total'] = request_summary(all_samples)
                # Avoid repeating every raw sample in the aggregate; phase samples remain intact.
                report['total'].pop('samples')
                memory = report['memory_observations']
                rss = [point['rss_bytes'] for point in memory if point['rss_bytes'] is not None]
                hwm = [point['hwm_bytes'] for point in memory if point['hwm_bytes'] is not None]
                report['max_observed_rss_bytes'] = max(rss) if rss else None
                report['max_reported_hwm_bytes'] = max(hwm) if hwm else None
                warm = report['warm']['samples']
                report['budget_results'] = {
                    'warm_success': all(sample['status'] == 200 and sample['error_code'] is None for sample in warm),
                    'warm_latency': max(sample['latency_ms'] for sample in warm) < HTTP_BUDGETS['warm_request_max_ms'],
                    'server_memory': max(rss + hwm) < HTTP_BUDGETS['observed_server_rss_and_hwm_max_bytes'] if len(rss) == len(memory) and len(hwm) == len(memory) else None,
                }
            finally:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
                report['child_cleanup'] = {'reaped': child.poll() is not None, 'exit_code': child.returncode,
                                           'method': 'terminate; kill fallback after 5 seconds'}
    return report


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
            'hardware': hardware(),
            'scope': 'Rows: seeded synthetic BTC, cached source, freshly computed model and payload, repeated in one process without HTTP/exchange latency. The separate http section measures a fresh loopback subprocess. Not a live-data SLA or empirical model validation.',
            'rows': rows, 'http': measure_http()}


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
    print(json.dumps({'measurements':len(report['rows']), 'output':str(args.output), 'max_median_ms':max(r['median_ms'] for r in report['rows']),
                      'http_requests': report['http']['total']['requests'], 'http_status_counts': report['http']['total']['status_counts'],
                      'http_budget_results': report['http']['budget_results'], 'server_cleanup': report['http']['child_cleanup']}))

if __name__=='__main__':
    main()
