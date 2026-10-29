"""Java front contract and fail-closed deployment checks; no real credentials."""
import base64
import hashlib
import hmac
import json
import os
import time

import jwt
import pytest

from scripts.vlx_jwt_auth import JavaJwtAuth, JwtAuthError, configured_auth


@pytest.fixture
def signing():
    key = os.urandom(48)
    return key, JavaJwtAuth(base64.b64encode(key).decode())


def claims(**changes):
    now = int(time.time())
    return dict(uid=123, sub='123', type='front', iat=now, exp=now+3600) | changes


def test_jjwt_string_key_semantics_and_stable_identity(signing):
    key, auth = signing
    # Construct JWS independently of PyJWT's encoder, with JJWT's decoded key.
    def segment(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=')
    content = segment({'alg':'HS256','typ':'JWT'}) + b'.' + segment(claims(uid='00123'))
    signature = base64.urlsafe_b64encode(hmac.digest(key, content, hashlib.sha256)).rstrip(b'=')
    assert auth.verify((content+b'.'+signature).decode()) == '123'
    assert auth.verify(jwt.encode(claims(jti='new-login-token'), key, algorithm='HS256')) == '123'


@pytest.mark.parametrize('changes,code', [
    ({'exp':1}, 'AUTH_TOKEN_EXPIRED'),
    ({'exp':'9999999999'}, 'AUTH_TOKEN_INVALID'),
    ({'iat':'bad'}, 'AUTH_TOKEN_INVALID'),
    ({'type':'admin'}, 'AUTH_TOKEN_TYPE_INVALID'),
    ({'uid':0}, 'AUTH_UID_INVALID'),
    ({'uid':True}, 'AUTH_UID_INVALID'),
    ({'uid':123.0}, 'AUTH_UID_INVALID'),
    ({'uid':'１２３'}, 'AUTH_UID_INVALID'),
    ({'uid':'1'*21}, 'AUTH_UID_INVALID'),
    ({'sub':123}, 'AUTH_SUBJECT_MISMATCH'),
    ({'sub':'456'}, 'AUTH_SUBJECT_MISMATCH'),
    ({'nbf':int(time.time())+3600}, 'AUTH_TOKEN_INVALID'),
])
def test_invalid_signed_claims(signing, changes, code):
    key, auth = signing
    with pytest.raises(JwtAuthError) as caught:
        auth.verify(jwt.encode(claims(**changes), key, algorithm='HS256'))
    assert caught.value.code == code


@pytest.mark.parametrize('field', ['uid','sub','exp','type'])
def test_required_claims(signing, field):
    key, auth = signing
    payload = claims()
    del payload[field]
    with pytest.raises(JwtAuthError):
        auth.verify(jwt.encode(payload, key, algorithm='HS256'))


@pytest.mark.parametrize('algorithm', ['none','HS384','HS512','wrong_key','raw_string_key','hex_key'])
def test_only_expected_algorithm_and_jjwt_key(signing, algorithm):
    key, auth = signing
    signing_key = None if algorithm == 'none' else key
    if algorithm == 'wrong_key':
        signing_key = os.urandom(48)
    elif algorithm == 'raw_string_key':
        signing_key = base64.b64encode(key)
    elif algorithm == 'hex_key':
        signing_key = key.hex().encode()
    token = jwt.encode(claims(), signing_key, algorithm=algorithm if algorithm in ('none','HS384','HS512') else 'HS256')
    with pytest.raises(JwtAuthError) as caught:
        auth.verify(token)
    assert caught.value.code == 'AUTH_TOKEN_INVALID'


@pytest.mark.parametrize('token', [None, 1, '', 'expired-local-token', 'x.y.z', 'a'*8193, 'a b'])
def test_malformed_token_never_falls_back(signing, token):
    with pytest.raises(JwtAuthError):
        signing[1].verify(token)


@pytest.mark.parametrize('secret', ['', 'bad!', base64.b64encode(b'short').decode()])
def test_bad_secret_fails_configuration(secret):
    with pytest.raises(RuntimeError, match='^AUTH_CONFIG_INVALID$'):
        JavaJwtAuth(secret)


def test_configuration_file_environment_and_no_legacy_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv('JAVA_FRONT_JWT_SECRET', raising=False)
    path = tmp_path/'secret.txt'
    config = {'auth_mode':'java_jwt','java_front_jwt_secret_file':str(path)}
    with pytest.raises(RuntimeError, match='^AUTH_CONFIG_INVALID$'):
        configured_auth(config)
    key = os.urandom(48)
    path.write_text(base64.b64encode(key).decode(), encoding='utf-8')
    token = jwt.encode(claims(), key, algorithm='HS256')
    assert configured_auth(config).verify(token) == '123'
    monkeypatch.setenv('JAVA_FRONT_JWT_SECRET', '')
    with pytest.raises(RuntimeError, match='^AUTH_CONFIG_INVALID$'):
        configured_auth(config)
    monkeypatch.setenv('JAVA_FRONT_JWT_SECRET', base64.b64encode(key).decode())
    assert configured_auth({'auth_mode':'java_jwt'}).verify(token) == '123'
    with pytest.raises(RuntimeError, match='^AUTH_CONFIG_INVALID$'):
        configured_auth({'auth_mode':'local_token'})
