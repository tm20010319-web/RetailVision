"""Authenticated standalone HTTP/WS adapter; no production database imports."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import threading
import time
import uuid

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from scripts.run_toy_guitar_live import atomic_json
from scripts.vlx_target_controls import ALIASES, QUERIES, LABELS
from scripts.vlx_jwt_auth import JwtAuthError

BASE = '/api/v1/item-search'
WS_BASE = '/ws/v1/item-search'
IDS = {0: 'bee_plush', 1: 'large_guitar', 2: 'small_guitar'}
COLORS = {0: '#FFD54A', 1: '#48D7FF', 2: '#FF99DD'}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def public_target(target):
    return {k: target[k] for k in ('target_id', 'label', 'validation_status', 'color')}


class ApiFault(Exception):
    def __init__(self, status, code, message, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}

    def payload(self):
        return {'error': {'code': self.code, 'message': self.message,
                          'request_id': 'req_'+uuid.uuid4().hex, 'details': self.details}}


def resolve_targets(query):
    if len(query) > 80:
        raise ApiFault(400, 'INVALID_REQUEST', '查询最多 80 个字符')
    rows = {}
    for word in query.strip().split('、'):
        word = word.strip()
        if not word:
            continue
        if any(ord(c) < 32 for c in word) or any(c in word for c in '<>;；'):
            raise ApiFault(422, 'QUERY_UNSUPPORTED', '请输入物品名称，多个物品用顿号分隔')
        if word in ALIASES:
            for key in ALIASES[word]:
                rows[IDS[key]] = dict(class_id=key, target_id=IDS[key], label=LABELS[key],
                    phrase=QUERIES[key], validation_status='scene_checked', color=COLORS[key])
        else:
            normalized = word.casefold()
            key = 'query_'+hashlib.sha256(normalized.encode()).hexdigest()[:16]
            rows[key] = dict(target_id=key, label=word, phrase=word,
                             validation_status='unverified', color='#7CFF6B')
    if len(rows) > 3:
        raise ApiFault(422, 'TOO_MANY_TARGETS', '每次最多搜索三个目标')
    dynamic = 3
    for row in sorted(rows.values(), key=lambda t: t['target_id']):
        if 'class_id' not in row:
            row['class_id'] = dynamic
            dynamic += 1
    return sorted(rows.values(), key=lambda t: t['class_id'])


class SessionStore:
    def __init__(self, control_file, bundle_file, cameras, engine_status):
        self.control_file, self.bundle_file = Path(control_file), Path(bundle_file)
        self.camera_names = {c['id']: c['name'] for c in cameras}
        self.engine_status = engine_status
        self.lock = threading.RLock()
        self.sessions = {}
        self.active_id = None
        self.generation = 0
        self.write_control(None)

    def bundle(self):
        try:
            return json.loads(self.bundle_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def write_control(self, session):
        self.generation += 1
        atomic_json(self.control_file, {'generation': self.generation,
            'session_id': session['session_id'] if session else '', 'revision': session['revision'] if session else 0,
            'targets': session['_targets'] if session else [],
            'camera_ids': session['camera_ids'] if session else list(self.camera_names)})
        if session:
            session['_generation'] = self.generation

    def expire(self):
        now = time.monotonic()
        for key in [k for k,v in self.sessions.items() if now-v['_touched'] >= 1800]:
            self.sessions.pop(key)
            if self.active_id == key:
                self.active_id = None
                self.write_control(None)

    def owned(self, key, owner):
        self.expire()
        row = self.sessions.get(key)
        if row is None:
            raise ApiFault(404, 'SESSION_NOT_FOUND', '会话不存在或已过期')
        if row['_owner'] != owner:
            raise ApiFault(403, 'FORBIDDEN', '无权访问该会话')
        row['_touched'] = time.monotonic()
        row['expires_at'] = (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
        return row

    def create(self, owner, query, camera_ids, preview_fps):
        with self.lock:
            self.expire()
            if len(self.sessions) >= 32 or sum(v['_owner'] == owner for v in self.sessions.values()) >= 8:
                raise ApiFault(429, 'RATE_LIMITED', '会话数量过多，请结束不用的会话')
            if not camera_ids or len(camera_ids) > 2 or len(set(camera_ids)) != len(camera_ids) or not set(camera_ids) <= self.camera_names.keys():
                raise ApiFault(400, 'INVALID_REQUEST', '摄像头列表无效')
            targets = resolve_targets(query)
            self.check_search(None, targets)
            key = 'ses_'+secrets.token_hex(16)
            row = {'session_id':key, 'revision':int(bool(targets)), 'query':query.strip(),
                'camera_ids':camera_ids, 'preview_fps':preview_fps,
                'created_at':utcnow(), 'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(),
                'stream_path':WS_BASE+'/sessions/'+key,
                '_owner':owner, '_targets':targets, '_generation':0, '_touched':time.monotonic()}
            self.sessions[key] = row
            if targets:
                self.active_id = key
                self.write_control(row)
            return self.snapshot(row)

    def check_search(self, session_id, targets):
        if not targets:
            return
        if self.active_id not in (None, session_id):
            raise ApiFault(409, 'ENGINE_BUSY', '当前已有活跃搜索，请结束后重试')
        if self.engine_status() not in ('ready','running','waiting_for_target'):
            raise ApiFault(503, 'ENGINE_NOT_READY', '识别引擎正在加载或不可用')

    def set_query(self, key, owner, query, expected_revision):
        targets = resolve_targets(query)
        with self.lock:
            row = self.owned(key, owner)
            if expected_revision != row['revision']:
                raise ApiFault(409, 'REVISION_CONFLICT', '查询版本已变化', {'current_revision':row['revision']})
            self.check_search(key, targets)
            old = {t['target_id'] for t in row['_targets']}
            new = {t['target_id'] for t in targets}
            row['query'] = query.strip()
            if old != new:
                row['revision'] += 1
                row['_targets'] = targets
                if targets:
                    self.active_id = key
                    self.write_control(row)
                elif self.active_id == key:
                    self.active_id = None
                    self.write_control(None)
            return self.snapshot(row)

    def get(self, key, owner):
        with self.lock:
            return self.snapshot(self.owned(key, owner))

    def delete(self, key, owner):
        with self.lock:
            self.owned(key, owner)
            self.sessions.pop(key)
            if self.active_id == key:
                self.active_id = None
                self.write_control(None)

    def cameras(self, bundle=None):
        bundle = self.bundle() if bundle is None else bundle
        rows = {c['camera_id']: c for c in bundle.get('cameras', [])}
        output = []
        for key,name in self.camera_names.items():
            camera = rows.get(key, {'camera_id':key, 'name':name, 'status':'connecting'})
            camera = {k:v for k,v in camera.items() if k in ('camera_id','name','status','width','height')}
            frame = bundle.get('frames',{}).get(key)
            if camera['status'] == 'online' and (not frame or time.monotonic()-frame['received_monotonic'] > 2):
                camera['status'] = 'stale'
            output.append(camera)
        return output

    def snapshot(self, row, bundle=None):
        bundle = self.bundle() if bundle is None else bundle
        result = {k:copy.deepcopy(v) for k,v in row.items() if not k.startswith('_')}
        result['targets'] = [public_target(t) for t in row['_targets']]
        result['results'] = []
        if not row['_targets']:
            result['state'] = 'idle'
            return result
        camera_rows = {c['camera_id']:c for c in self.cameras(bundle)}
        online = all(camera_rows[k]['status'] == 'online' for k in row['camera_ids'])
        current = bundle.get('generation') == row['_generation'] and bundle.get('session_id') == row['session_id']
        checks = {c['camera_id']:c for c in bundle.get('cameras',[])} if current else {}
        all_checked = all(checks.get(k,{}).get('checked_generation') == row['_generation'] and
                          time.monotonic()-checks[k].get('checked_at_monotonic',0) <= 8 for k in row['camera_ids'])
        failed = self.engine_status() in ('failed','stopped')
        result['state'] = 'failed' if failed else ('degraded' if not online else ('active' if all_checked else 'searching'))
        for target in row['_targets']:
            found = None
            if current and not failed:
                for camera_id,frame in bundle.get('frames',{}).items():
                    if camera_id not in row['camera_ids'] or camera_rows[camera_id]['status'] != 'online':
                        continue
                    for annotation in frame['annotations']:
                        if annotation['target_id'] == target['target_id']:
                            found = (camera_id, annotation)
            if found:
                key, annotation = found
                item = {'target_id':target['target_id'],'status':'found','winner_camera_id':key,
                        'view_score':annotation['view_score'],'confirmed_at':annotation['confirmed_at']}
            else:
                ambiguous = any(target['class_id'] in c.get('ambiguous_classes',[]) for c in checks.values())
                state = 'unavailable' if not online or failed else ('ambiguous' if ambiguous else ('not_found' if all_checked else 'searching'))
                item = {'target_id':target['target_id'],'status':state}
            result['results'].append(item)
        return result

    def frame(self, key, owner, camera_id, bundle):
        with self.lock:
            row = self.owned(key, owner)
            if camera_id not in row['camera_ids']:
                return None
            source = bundle.get('frames',{}).get(camera_id)
            if not source or time.monotonic()-source['received_monotonic'] > 2:
                return None
            frame = {k:v for k,v in source.items() if k != 'received_monotonic'}
            matches = self.active_id == key and bundle.get('generation') == row['_generation'] and bundle.get('session_id') == key
            frame['annotations'] = source['annotations'] if matches and self.engine_status() not in ('failed','stopped') else []
            return frame


class CreateSession(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(default='', max_length=80)
    camera_ids: list[str] = Field(min_length=1,max_length=2)
    preview_fps: int = Field(default=10,ge=1,le=15,strict=True)


class SetQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(max_length=80)
    expected_revision: int = Field(ge=0,strict=True)


def create_app(store, auth, allowed_origins, contract_file):
    app = FastAPI(title='VLX 物品搜索联调 API', version='1.1.0', docs_url='/docs', redoc_url=None)
    app.add_middleware(CORSMiddleware,allow_origins=allowed_origins,allow_methods=['GET','POST','PUT','DELETE','OPTIONS'],
                       allow_headers=['Authorization','Content-Type'], expose_headers=['Retry-After'])
    streams = {}
    def verify(token):
        try:
            return auth.verify(token)
        except JwtAuthError as error:
            raise ApiFault(401, error.code, error.message) from None

    def owner(header):
        if not header or not header.strip():
            raise ApiFault(401,'AUTH_TOKEN_MISSING','缺少登录凭据')
        header = header.strip()
        if header[:7].lower() != 'bearer ':
            raise ApiFault(401,'AUTH_TOKEN_INVALID','登录凭据无效')
        return verify(header[7:].strip())

    @app.exception_handler(ApiFault)
    async def fault_handler(_, error):
        return JSONResponse(error.payload(),status_code=error.status,
                            headers={'Retry-After':'3'} if error.status in (429,503) else {})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_, error):
        return JSONResponse(ApiFault(400,'INVALID_REQUEST','请求字段或参数范围不正确').payload(),status_code=400)

    @app.middleware('http')
    async def no_cache(request, call_next):
        try:
            too_large = int(request.headers.get('content-length','0') or 0) > 8192
        except ValueError:
            too_large = True
        if too_large:
            return JSONResponse(ApiFault(400,'INVALID_REQUEST','请求体过大').payload(),status_code=400)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/healthz')
    def health():
        state = store.engine_status()
        return {'status':'ready' if state in ('ready','running','waiting_for_target') else 'loading' if state in ('starting','loading_vlx') else 'unavailable'}

    @app.get('/',response_class=HTMLResponse)
    def landing():
        return '<meta charset="utf-8"><h2>VLX 物品搜索联调服务</h2><p>实时画面和搜索接口需要 Bearer Token。</p><a href="/docs">接口文档 /docs</a>'

    @app.get(BASE+'/catalog')
    def catalog(authorization: str | None = Header(default=None)):
        owner(authorization)
        targets = resolve_targets('小蜜蜂、吉他')
        return {'items':[public_target(t)|{'aliases':[a for a,v in ALIASES.items() if v == (t['class_id'],)]} for t in targets],
            'groups':[{'aliases':[a for a,v in ALIASES.items() if v==(1,2)],'target_ids':['large_guitar','small_guitar']}],
            'general_queries':'enabled_unverified','max_targets':3,'max_active_searches':1,
            'default_preview_fps':10,'recognition_interval_target_ms':3000}

    @app.get(BASE+'/cameras')
    def cameras(authorization: str | None = Header(default=None)):
        owner(authorization)
        return {'cameras':store.cameras()}

    @app.post(BASE+'/sessions',status_code=201)
    def create(body:CreateSession, authorization: str | None = Header(default=None)):
        return store.create(owner(authorization),body.query,body.camera_ids,body.preview_fps)

    @app.get(BASE+'/sessions/{session_id}')
    def get(session_id:str,authorization: str | None = Header(default=None)):
        return store.get(session_id,owner(authorization))

    @app.put(BASE+'/sessions/{session_id}/query')
    def change(session_id:str,body:SetQuery,authorization: str | None = Header(default=None)):
        return store.set_query(session_id,owner(authorization),body.query,body.expected_revision)

    @app.delete(BASE+'/sessions/{session_id}',status_code=204)
    def delete(session_id:str,authorization: str | None = Header(default=None)):
        store.delete(session_id,owner(authorization))
        return Response(status_code=204)

    @app.websocket(WS_BASE+'/sessions/{session_id}')
    async def stream(websocket:WebSocket,session_id:str):
        origin = websocket.headers.get('origin')
        host = websocket.headers.get('host','')
        if origin and origin not in allowed_origins and origin not in ('https://'+host,'http://'+host):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        identity, token, counted = None, None, False
        try:
            message = await asyncio.wait_for(websocket.receive_json(),timeout=5)
            if not isinstance(message,dict) or message.get('type') != 'authenticate':
                raise ApiFault(401,'AUTH_TOKEN_MISSING','缺少登录凭据')
            token = message.get('access_token','')
            identity = verify(token)
            store.get(session_id,identity)
            if streams.get(identity,0) >= 4:
                raise ApiFault(429,'RATE_LIMITED','实时连接过多')
            streams[identity] = streams.get(identity,0)+1
            counted = True
            stream_id, event_id = 'stream_'+uuid.uuid4().hex, 0
            last_state, last_frames, last_sent, last_heartbeat = None, {}, {}, time.monotonic()

            async def send(kind,revision,extra):
                nonlocal event_id
                event_id += 1
                data = {'type':kind,'stream_id':stream_id,'event_id':event_id,'session_id':session_id,
                        'revision':revision,'server_time':utcnow(),**extra}
                await asyncio.wait_for(websocket.send_json(data),timeout=5)

            snapshot = store.get(session_id,identity)
            await send('ready',snapshot['revision'],{'heartbeat_interval_ms':10000})
            while True:
                verify(token)
                bundle = await asyncio.to_thread(store.bundle)
                snapshot = store.get(session_id,identity)
                camera_list = [c for c in store.cameras(bundle) if c['camera_id'] in snapshot['camera_ids']]
                state = {'session':snapshot,'cameras':camera_list}
                # Expiry renewal alone should not flood state messages.
                signature = json.dumps({**state,'session':{k:v for k,v in snapshot.items() if k!='expires_at'}},sort_keys=True)
                now = time.monotonic()
                if signature != last_state:
                    await send('state',snapshot['revision'],state)
                    last_state = signature
                for camera_id in snapshot['camera_ids']:
                    frame = store.frame(session_id,identity,camera_id,bundle)
                    if frame is None or now-last_sent.get(camera_id,0) < 1/snapshot['preview_fps']:
                        continue
                    frame_key = (snapshot['revision'],frame['frame_id'])
                    if last_frames.get(camera_id) == frame_key:
                        continue
                    # Recheck revision immediately before sending, after any awaits.
                    if store.get(session_id,identity)['revision'] != snapshot['revision']:
                        break
                    frame['metrics'] = {**frame.get('metrics',{}),
                        'preview_fps':round(1/(now-last_sent[camera_id]),2) if camera_id in last_sent else 0.}
                    await send('frame',snapshot['revision'],frame)
                    last_frames[camera_id], last_sent[camera_id] = frame_key, now
                if now-last_heartbeat >= 10:
                    await send('heartbeat',snapshot['revision'],{})
                    last_heartbeat = now
                await asyncio.sleep(.025)
        except ApiFault as error:
            await websocket.close(code=4001 if error.status==401 and identity else 1000 if error.status==404 else 1008)
        except (WebSocketDisconnect,RuntimeError,asyncio.TimeoutError,ValueError,TypeError):
            pass
        finally:
            if counted:
                streams[identity] = max(0,streams[identity]-1)

    def openapi():
        import yaml
        contract = yaml.safe_load(Path(contract_file).read_text(encoding='utf-8'))
        contract['info']['version'] = '1.1.0'
        contract['info']['title'] = 'VLX 物品搜索联调 API'
        contract['info']['description'] = '沿用原 Java 登录 JWT 的联调服务。其他物品查询已开放输入，但识别效果未验证。'
        contract['servers'] = [{'url':BASE,'description':'当前服务地址'}]
        return contract
    app.openapi = openapi
    return app
