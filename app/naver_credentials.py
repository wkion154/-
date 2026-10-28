"""User-local Windows DPAPI storage; credentials never enter library/settings JSON."""
import ctypes
import json
import os
from pathlib import Path
import platform
import uuid


def _crypt(data,decrypt=False):
    if platform.system()!='Windows':raise RuntimeError('API 발급 정보의 앱 내 보안 저장은 Windows에서 지원합니다.')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    function=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    buffer=ctypes.create_string_buffer(data);source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));destination=Blob()
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(destination)):
        raise RuntimeError('Windows 보안 저장소에서 API 발급 정보를 처리하지 못했습니다.')
    try:return ctypes.string_at(destination.data,destination.size)
    finally:kernel.LocalFree(ctypes.cast(destination.data,ctypes.c_void_p))


def _values(client_id,secret):
    if not all(isinstance(value,str) and 1<=len(value.strip())<=256 and not any(ord(char)<32 for char in value) for value in (client_id,secret)):
        raise ValueError('네이버 Client ID와 Client Secret을 확인하세요.')
    return client_id.strip(),secret.strip()


def save(root,client_id,secret):
    client_id,secret=_values(client_id,secret)
    encrypted=_crypt(json.dumps(dict(client_id=client_id,secret=secret)).encode())
    path=Path(root)/'credentials'/'naver.dpapi';path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as file:file.write(encrypted)
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def load(root):
    client_id,secret=os.environ.get('NAVER_CLIENT_ID'),os.environ.get('NAVER_CLIENT_SECRET')
    if client_id or secret:return _values(client_id,secret)
    path=Path(root)/'credentials'/'naver.dpapi'
    if not path.is_file():raise RuntimeError('검색 출처에서 네이버 API 발급 정보를 먼저 입력하세요.')
    try:
        data=json.loads(_crypt(path.read_bytes(),decrypt=True))
        return _values(data.get('client_id'),data.get('secret'))
    except Exception:raise RuntimeError('저장된 네이버 API 발급 정보를 읽지 못했습니다. 이 Windows 계정에서 다시 입력하세요.') from None
