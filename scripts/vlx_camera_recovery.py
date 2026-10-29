"""Retry transient camera preflight failures without bypassing identity pins."""
from scripts.run_toy_guitar_live import LatestCamera, atomic_json, camera_password, make_source, verify_camera


def connect_verified_view(view, folder):
    if view['source'] is not None:
        return True
    config = view['camera_config']
    try:
        password = camera_password(config)
        identity = verify_camera(config, password)
        atomic_json(folder/'camera_identity.json', identity)
        source = LatestCamera(make_source(config, password))
        source.thread.start()
        view['source'] = source
        view.pop('preflight_error', None)
        return True
    except Exception as error:
        view['preflight_error'] = type(error).__name__
        return False


def retry_missing_cameras(stop, views, runtime, interval=10):
    while not stop.wait(interval):
        for view in views:
            if stop.is_set():
                return
            if view['source'] is None:
                connect_verified_view(view, runtime/view['id'])
