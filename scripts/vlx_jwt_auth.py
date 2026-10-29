"""Verify the existing Java front JWT contract without account/database imports."""
import base64
import binascii
import os
from pathlib import Path

import jwt


class JwtAuthError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(code)


class JavaJwtAuth:
    def __init__(self, secret):
        # Match JJWT signWith(HS256, String): STANDARD Base64, not UTF-8 or hex.
        try:
            key = base64.b64decode(secret, validate=True)
        except (ValueError, TypeError, binascii.Error):
            raise RuntimeError('AUTH_CONFIG_INVALID') from None
        if len(key) < 32:
            raise RuntimeError('AUTH_CONFIG_INVALID')
        self._key = key

    @staticmethod
    def normalize_uid(value):
        if type(value) is int:
            valid = value > 0 and len(str(value)) <= 20
        elif isinstance(value, str) and 0 < len(value) <= 20 and value.isascii() and value.isdigit():
            value = int(value)
            valid = value > 0
        else:
            valid = False
        if not valid:
            raise JwtAuthError('AUTH_UID_INVALID', '用户身份无效')
        return str(value)

    def verify(self, token):
        if not isinstance(token, str) or not token or len(token) > 8192 or any(c.isspace() for c in token):
            raise JwtAuthError('AUTH_TOKEN_INVALID', '登录凭据无效')
        try:
            payload = jwt.decode(token, self._key, algorithms=['HS256'], options={
                'require': ['exp'], 'verify_aud': False, 'verify_iss': False, 'verify_sub': False,
            })
        except jwt.ExpiredSignatureError:
            raise JwtAuthError('AUTH_TOKEN_EXPIRED', '登录状态已过期') from None
        except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
            raise JwtAuthError('AUTH_TOKEN_INVALID', '登录凭据无效') from None
        if type(payload['exp']) is not int or ('iat' in payload and type(payload['iat']) is not int):
            raise JwtAuthError('AUTH_TOKEN_INVALID', '登录凭据无效')
        if payload.get('type') != 'front':
            raise JwtAuthError('AUTH_TOKEN_TYPE_INVALID', '登录凭据类型无效')
        uid = self.normalize_uid(payload.get('uid'))
        if payload.get('sub') != uid:
            raise JwtAuthError('AUTH_SUBJECT_MISMATCH', '登录凭据身份不一致')
        return uid


def configured_auth(config):
    if config.get('auth_mode') != 'java_jwt':
        raise RuntimeError('AUTH_CONFIG_INVALID')
    secret = os.environ.get('JAVA_FRONT_JWT_SECRET')
    if secret is None:
        try:
            secret = Path(config['java_front_jwt_secret_file']).read_text(encoding='utf-8').strip()
        except (OSError, KeyError, ValueError):
            raise RuntimeError('AUTH_CONFIG_INVALID') from None
    return JavaJwtAuth(secret)
