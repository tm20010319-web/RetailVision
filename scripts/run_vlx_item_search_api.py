"""Start one authenticated API and one shared headless VLX worker."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import yaml
from scripts.run_toy_guitar_live import atomic_json, resolve_path
from scripts.vlx_item_search_api import SessionStore, create_app
from scripts.vlx_jwt_auth import configured_auth


def stop_preview(dual):
    pointer = resolve_path(dual['runtime_root'])/'current.json'
    if not pointer.exists():
        return
    import psutil
    try:
        old = json.loads(pointer.read_text(encoding='utf-8'))
        process = psutil.Process(old['pid'])
        if not any(Path(arg).name == 'run_toy_guitar_vlx_dual.py' for arg in process.cmdline()):
            return
        folder = Path(old['runtime'])
        (folder/'stop.request').write_text('API takes over shared GPU worker',encoding='utf-8')
        process.wait(timeout=25)
    except (psutil.NoSuchProcess,FileNotFoundError):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'config/vlx_item_search_api.yaml')
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding='utf-8'))
    # Validate configuration before touching the current GPU/model process.
    auth = configured_auth(config)
    # Fail before touching the current GPU process if the API port is occupied.
    with socket.socket() as probe:
        probe.bind((config['host'],config['port']))
    dual_path = resolve_path(config['dual_config'])
    dual = yaml.safe_load(dual_path.read_text(encoding='utf-8'))
    stop_preview(dual)
    runtime_root = resolve_path(config['runtime_root'])
    runtime = runtime_root/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    runtime.mkdir(parents=True)
    atomic_json(runtime_root/'current.json',{'runtime':str(runtime),'pid':os.getpid(),'port':config['port']})
    log = (runtime/'api.log').open('a',encoding='utf-8',buffering=1)
    sys.stdout = sys.stderr = log
    worker_dir = runtime/'engine'
    control, bundle = runtime/'control.json', runtime/'frames.json'

    child_ref = []
    def engine_status():
        if child_ref and child_ref[0].poll() is not None:
            return 'failed'
        try:
            return json.loads((worker_dir/'status.json').read_text(encoding='utf-8'))['status']
        except (OSError,ValueError,KeyError):
            return 'starting'

    store = SessionStore(control,bundle,dual['cameras'],engine_status)
    app = create_app(store,auth,config['allowed_origins'],ROOT/'docs/api/vlx-item-search-v1/openapi.yaml')
    child_log = (runtime/'worker_boot.log').open('a',encoding='utf-8')
    child = subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts/run_toy_guitar_vlx_dual.py'),
        '--config',str(dual_path),'--runtime-dir',str(worker_dir),'--api-control',str(control),'--api-state',str(bundle)],
        cwd=ROOT,stdout=child_log,stderr=child_log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    child_ref.append(child)
    atomic_json(runtime/'service.json',{'api_pid':os.getpid(),'worker_pid':child.pid,'listen':f"http://{config['host']}:{config['port']}",
        'auth_mode':'java_jwt','started_at':datetime.now(timezone.utc).isoformat()})
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app,host=config['host'],port=config['port'],access_log=False,
                                          proxy_headers=False,ws_max_size=16384,timeout_graceful_shutdown=10))
    done = threading.Event()
    def watch():
        while not done.wait(.5):
            with store.lock:
                store.expire()
            if (runtime/'stop.request').exists():
                server.should_exit = True
    thread = threading.Thread(target=watch,daemon=True)
    thread.start()
    try:
        server.run()
    finally:
        done.set()
        if child.poll() is None:
            (worker_dir/'stop.request').write_text('API stopped',encoding='utf-8')
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
        child_log.close()


if __name__ == '__main__':
    main()
