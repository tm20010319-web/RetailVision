import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os

import jwt
import json
import time
from pathlib import Path

import cv2
from fastapi.testclient import TestClient
import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect

from scripts.run_toy_guitar_live import atomic_json
from scripts.vlx_item_search_api import ApiFault, SessionStore, create_app, resolve_targets
from scripts.vlx_jwt_auth import JavaJwtAuth
from scripts.vlx_api_bridge import FileSelection, publish_api_frame
from scripts.run_toy_guitar_vlx_live import detections_from_output

BASE = '/api/v1/item-search'
CAMERAS = [{'id':'one','name':'一号'},{'id':'three','name':'三号'}]
KEY = os.urandom(48)
SECRET = base64.b64encode(KEY).decode()

def login_token(uid, **changes):
    now = int(time.time())
    return jwt.encode(dict(uid=uid, sub=str(uid), type='front', exp=now+3600, iat=now) | changes, KEY, algorithm='HS256')

ALICE_TOKEN, BOB_TOKEN = login_token(101), login_token(202)
HEADERS = {'Authorization':'Bearer '+ALICE_TOKEN}


@pytest.fixture
def api(tmp_path):
    store = SessionStore(tmp_path/'control.json',tmp_path/'frames.json',CAMERAS,lambda:'ready')
    app = create_app(store,JavaJwtAuth(SECRET),['http://localhost:5173'],
                     Path(__file__).resolve().parents[1]/'docs/api/vlx-item-search-v1/openapi.yaml')
    with TestClient(app) as client:
        yield client,store


def create(client,query='',token=ALICE_TOKEN):
    return client.post(BASE+'/sessions',headers={'Authorization':'Bearer '+token},json={'query':query,'camera_ids':['one','three']})


def publish(store,session):
    control = json.loads(store.control_file.read_text(encoding='utf-8'))
    now = time.monotonic()
    frame = np.zeros((100,200,3),np.uint8)
    sample = (10,now,datetime.now(timezone.utc).isoformat(),frame)
    views = [dict(id=c['id'],name=c['name'],fresh=True,sample=sample,fps=20,seconds=1.2,interval=3,
        checked_generation=control['generation'],checked_at_monotonic=now,ambiguous=[]) for c in CAMERAS]
    winners = {}
    if control['targets']:
        t = control['targets'][0]
        winners[t['class_id']] = {'camera_id':'one','quality':{'score':90},
            'detection':{'class_id':t['class_id'],'bbox_xyxy':[20,10,80,90]}}
    publish_api_frame(store.bundle_file,FileSelection(store.control_file),control['generation'],views,winners,now)


def test_catalog_auth_and_no_credentials_in_public_state(api):
    client,store = api
    assert client.get(BASE+'/catalog').status_code == 401
    assert client.get(BASE+'/catalog',headers=HEADERS).json()['general_queries'] == 'enabled_unverified'
    payload = client.get(BASE+'/cameras',headers=HEADERS).json()
    assert not any(text in json.dumps(payload) for text in ('rtsp','password','token_sha256'))
    assert client.get('/openapi.json').json()['servers'][0]['url'] == BASE


def test_create_query_group_revision_clear_and_ownership(api):
    client,store = api
    session = create(client).json()
    key = session['session_id']
    assert session['state']=='idle' and session['revision']==0
    url = BASE+'/sessions/'+key+'/query'
    changed = client.put(url,headers=HEADERS,json={'query':'吉他','expected_revision':0}).json()
    assert [t['target_id'] for t in changed['targets']] == ['large_guitar','small_guitar']
    publish(store,changed)
    assert client.get(BASE+'/sessions/'+key,headers=HEADERS).json()['results'][0]['status']=='found'
    assert client.get(BASE+'/sessions/'+key,headers={'Authorization':'Bearer '+BOB_TOKEN}).status_code==403
    conflict = client.put(url,headers=HEADERS,json={'query':'小吉他','expected_revision':0})
    assert conflict.status_code==409 and conflict.json()['error']['code']=='REVISION_CONFLICT'
    cleared = client.put(url,headers=HEADERS,json={'query':'','expected_revision':1}).json()
    assert cleared['state']=='idle' and cleared['revision']==2
    assert store.frame(key,'101','one',store.bundle())['annotations']==[]
    assert client.delete(BASE+'/sessions/'+key,headers=HEADERS).status_code==204
    assert client.get(BASE+'/sessions/'+key,headers=HEADERS).status_code==404


def test_single_gpu_slot_is_atomic_and_released(api):
    client,store = api
    a,b = create(client).json(),create(client,token=BOB_TOKEN).json()
    def activate(pair):
        try:
            return store.set_query(pair[0]['session_id'],pair[1],'小吉他',0)['state']
        except ApiFault as e:
            return e.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(activate,[(a,'101'),(b,'202')]))
    assert results.count('ENGINE_BUSY')==1
    store.delete(store.active_id, '101' if store.active_id==a['session_id'] else '202')
    assert store.active_id is None


def test_general_names_preserve_spaces_and_are_unverified(api):
    client,store = api
    session = create(client,'white coffee mug').json()
    assert len(session['targets'])==1 and session['targets'][0]['label']=='white coffee mug'
    assert session['targets'][0]['validation_status']=='unverified'
    assert create(client,'甲、乙、丙、丁').status_code==422
    rows = resolve_targets('小蜜蜂、鼠标')
    aliases = {t['phrase'].casefold():t['class_id'] for t in rows}
    names = {t['class_id']:t['label'] for t in rows}
    output = {'result_bbox_list':[{'label':'鼠标','xmin':1,'ymin':2,'xmax':30,'ymax':40}]}
    assert detections_from_output(output,(100,100),aliases,names)[0]['label']=='鼠标'


def test_websocket_auth_origin_frame_sync_and_clear(api):
    client,store = api
    session = create(client,'小吉他').json()
    publish(store,session)
    url = '/ws/v1/item-search/sessions/'+session['session_id']
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url,headers={'Origin':'https://untrusted.invalid'}) as ws:
            ws.receive_json()
    with client.websocket_connect(url,headers={'Origin':'http://localhost:5173'}) as ws:
        ws.send_json({'type':'authenticate','access_token':ALICE_TOKEN})
        assert ws.receive_json()['type']=='ready'
        assert ws.receive_json()['type']=='state'
        message = ws.receive_json()
        assert message['type']=='frame' and message['revision']==1
        decoded=cv2.imdecode(np.frombuffer(base64.b64decode(message['jpeg_base64']),np.uint8),cv2.IMREAD_COLOR)
        assert decoded.shape[:2]==(message['height'],message['width'])
        assert message['annotations'][0]['bbox']==[.1,.1,.4,.9]
        assert 'received_monotonic' not in message
        client.put(BASE+'/sessions/'+session['session_id']+'/query',headers=HEADERS,json={'query':'','expected_revision':1})
        while True:
            message=ws.receive_json()
            if message['type']=='frame' and message['revision']==2:
                assert message['annotations']==[]
                break


def test_other_owner_cannot_read_websocket_and_expired_token_fails(api):
    client,store = api
    session=create(client).json()
    with client.websocket_connect('/ws/v1/item-search/sessions/'+session['session_id']) as ws:
        ws.send_json({'type':'authenticate','access_token':BOB_TOKEN})
        with pytest.raises(WebSocketDisconnect): ws.receive_json()
    response=client.get(BASE+'/catalog', headers={'Authorization':'Bearer '+login_token(101, exp=1)})
    assert response.status_code==401
    assert response.json()['error']['code']=='AUTH_TOKEN_EXPIRED'


def test_http_java_login_errors_and_header_compatibility(api):
    client,_ = api
    assert client.get(BASE+'/catalog').json()['error']['code']=='AUTH_TOKEN_MISSING'
    assert client.get(BASE+'/catalog', headers={'Authorization':'Basic abc'}).status_code==401
    assert client.get(BASE+'/catalog', headers={'Authorization':'bEaReR '+ALICE_TOKEN}).status_code==200
    assert client.get(BASE+'/catalog', headers={'Authorization':'Bearer test-alice'}).status_code==401
    bad=client.get(BASE+'/catalog', headers={'Authorization':'Bearer '+login_token(101, type='admin')})
    assert bad.json()['error']['code']=='AUTH_TOKEN_TYPE_INVALID'


def test_refreshed_login_keeps_ownership_for_http_and_websocket(api):
    client,_ = api
    session = create(client).json()
    key = session['session_id']
    refreshed = login_token(101, jti='refreshed')
    assert client.get(BASE+'/sessions/'+key, headers={'Authorization':'Bearer '+refreshed}).status_code==200
    with client.websocket_connect('/ws/v1/item-search/sessions/'+key) as ws:
        ws.send_json({'type':'authenticate','access_token':refreshed})
        assert ws.receive_json()['type']=='ready'


def test_other_java_user_cannot_mutate_or_delete_session(api):
    client,_ = api
    session = create(client).json()
    url = BASE+'/sessions/'+session['session_id']
    headers = {'Authorization':'Bearer '+BOB_TOKEN}
    assert client.put(url+'/query', headers=headers, json={'query':'小吉他','expected_revision':0}).status_code==403
    assert client.delete(url, headers=headers).status_code==403
    assert client.get(url, headers=HEADERS).status_code==200


@pytest.mark.parametrize('token', ['legacy-local-token', login_token(101, exp=1), login_token(101, sub='202')])
def test_invalid_java_websocket_never_receives_ready_or_frames(api, token):
    client,_ = api
    session = create(client).json()
    with client.websocket_connect('/ws/v1/item-search/sessions/'+session['session_id']) as ws:
        ws.send_json({'type':'authenticate','access_token':token})
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_json()
        assert caught.value.code==1008


def test_websocket_token_expiry_closes_live_connection(api, monkeypatch):
    client,_ = api
    session = create(client).json()
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(time.time()+7200, tz=tz)
    with client.websocket_connect('/ws/v1/item-search/sessions/'+session['session_id']) as ws:
        ws.send_json({'type':'authenticate','access_token':ALICE_TOKEN})
        assert ws.receive_json()['type']=='ready'
        monkeypatch.setattr(jwt.api_jwt, 'datetime', Clock)
        with pytest.raises(WebSocketDisconnect) as caught:
            while True:
                ws.receive_json()
        assert caught.value.code==4001


def test_stale_camera_cannot_produce_false_found_or_absence(api):
    client,store=api
    session=create(client,'小吉他').json()
    publish(store,session)
    bundle=store.bundle()
    bundle['frames']['one']['received_monotonic']-=20
    bundle['frames']['three']['received_monotonic']-=20
    atomic_json(store.bundle_file,bundle)
    current=store.get(session['session_id'],'101')
    assert current['state']=='degraded' and current['results'][0]['status']=='unavailable'
    assert store.frame(session['session_id'],'101','one',bundle) is None


def test_engine_not_ready_does_not_allocate_active_slot(tmp_path):
    store=SessionStore(tmp_path/'control',tmp_path/'frames',CAMERAS,lambda:'loading_vlx')
    idle=store.create('101','',['one'],10)
    with pytest.raises(ApiFault) as e: store.set_query(idle['session_id'],'101','小吉他',0)
    assert e.value.status==503 and store.active_id is None
    assert store.get(idle['session_id'],'101')['revision']==0


def test_expiry_releases_slot_and_old_file_selection_cannot_be_accepted(api):
    client,store=api
    session=create(client,'小吉他').json()
    selection=FileSelection(store.control_file)
    old,_=selection.snapshot()
    store.sessions[session['session_id']]['_touched']-=1900
    store.expire()
    assert store.active_id is None and not selection.accepts(old)


def test_held_frame_preserves_its_actual_positive_confirmation_metadata(api):
    client,store=api
    session=create(client,'小吉他').json()
    control=json.loads(store.control_file.read_text(encoding='utf-8'))
    now=time.monotonic()
    sample=(99,now,datetime.now(timezone.utc).isoformat(),np.zeros((100,200,3),np.uint8))
    view=dict(id='one',name='一号',fresh=True,sample=sample,fps=20,seconds=1,interval=3,
              model_frame_id=98,confirmed_at='2026-10-27T03:01:20+00:00')
    old='2026-10-27T03:01:15+00:00'
    winner={2:{'camera_id':'one','quality':{'score':85},'detection':{
        'class_id':2,'bbox_xyxy':[20,10,80,90],'model_frame_id':80,
        'model_confirmed_at_utc':old,'recognition_support':'held_after_miss'}}}
    publish_api_frame(store.bundle_file,FileSelection(store.control_file),control['generation'],[view],winner,now)
    item=store.frame(session['session_id'],'101','one',store.bundle())['annotations'][0]
    assert item['model_frame_id']==80 and item['confirmed_at']==old
