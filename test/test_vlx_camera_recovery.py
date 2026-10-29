import threading
from types import SimpleNamespace

from scripts import vlx_camera_recovery as recovery


def test_transient_identity_failure_retries_and_only_starts_a_verified_source(tmp_path,monkeypatch):
    attempts,started=[],[]
    config={'id':'one','username':'admin','password_env':'CAMERA_PASSWORD','allow_empty_password':True,
            'rtsp_path':'/live/0/SUB','host':'127.0.0.1','rtsp_port':554}
    def verify(*_):
        attempts.append(True)
        if len(attempts)==1: raise TimeoutError()
        return {'p2p_identity_verified':True}
    monkeypatch.setattr(recovery,'verify_camera',verify)
    monkeypatch.setattr(recovery,'LatestCamera',lambda url:SimpleNamespace(thread=SimpleNamespace(start=lambda:started.append(True))))
    view={'source':None,'camera_config':config}
    assert not recovery.connect_verified_view(view,tmp_path)
    assert started==[] and view['source'] is None and view['preflight_error']=='TimeoutError'
    assert recovery.connect_verified_view(view,tmp_path)
    assert len(started)==1 and 'preflight_error' not in view
    assert recovery.connect_verified_view(view,tmp_path) and len(attempts)==2


def test_identity_mismatch_is_never_bypassed(tmp_path,monkeypatch):
    def mismatch(*_): raise ValueError('wrong identity')
    monkeypatch.setattr(recovery,'verify_camera',mismatch)
    view={'source':None,'camera_config':{'password_env':'CAMERA_PASSWORD','allow_empty_password':True}}
    assert not recovery.connect_verified_view(view,tmp_path)
    assert not recovery.connect_verified_view(view,tmp_path)
    assert view['source'] is None and view['preflight_error']=='ValueError'
