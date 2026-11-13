"""ctypes bridge to real C++ Client. No cryptographic protocol is reimplemented."""
import ctypes as C
from contextlib import ExitStack
import os
from pathlib import Path
import shutil


class NativeClient:
    def __init__(self, dll_path):
        path=Path(dll_path).resolve()
        # Python 3.8+ does not use PATH to resolve a loaded DLL's dependencies.
        # This is a toolchain-built test fixture, separate from release binaries.
        with ExitStack() as search:
            if os.name == 'nt':
                search.enter_context(os.add_dll_directory(str(path.parent)))
                compiler=shutil.which('clang++')
                if compiler:
                    search.enter_context(os.add_dll_directory(str(Path(compiler).parent)))
            self.dll=C.CDLL(str(path))
        ptr=C.c_void_p;size=C.c_size_t;integer=C.c_int;u64=C.c_uint64
        self.dll.th_client_new.argtypes=[];self.dll.th_client_new.restype=ptr
        for name in ('free','reset','leave'):
            method=getattr(self.dll,'th_client_'+name);method.argtypes=[ptr];method.restype=None
        self.dll.th_client_begin.argtypes=[ptr,u64,ptr,size,ptr,size,ptr,size]
        self.dll.th_client_begin.restype=integer
        self.dll.th_client_ack.argtypes=[ptr,ptr,size];self.dll.th_client_ack.restype=integer
        self.dll.th_client_send.argtypes=[ptr,ptr,size,ptr,size];self.dll.th_client_send.restype=integer
        self.dll.th_client_receive.argtypes=[ptr,ptr,size,ptr,size,C.POINTER(integer)]
        self.dll.th_client_receive.restype=integer
        self.handle=self.dll.th_client_new()
        if not self.handle:raise RuntimeError('native client allocation failed')

    def close(self):
        if self.handle:self.dll.th_client_free(self.handle);self.handle=None

    def __enter__(self):return self
    def __exit__(self,*_):self.close()

    def begin(self, user, token, challenge):
        token_buffer=C.create_string_buffer(token)
        challenge_buffer=C.create_string_buffer(challenge)
        output=C.create_string_buffer(65536)
        try:
            size=self.dll.th_client_begin(self.handle,user,token_buffer,len(token),challenge_buffer,len(challenge),output,65536)
        finally:C.memset(token_buffer,0,C.sizeof(token_buffer))
        if size<0:raise ValueError('native relay proof rejected')
        return output.raw[:size]

    def ack(self, frame):
        if not self.dll.th_client_ack(self.handle,C.create_string_buffer(frame),len(frame)):
            raise ValueError('native relay acknowledgement rejected')

    def receive(self, frame):
        output=C.create_string_buffer(65536);kind=C.c_int()
        size=self.dll.th_client_receive(self.handle,C.create_string_buffer(frame),len(frame),output,65536,C.byref(kind))
        if size<0:raise ValueError('native relay frame rejected')
        return kind.value,output.raw[:size]

    def send(self, inner):
        output=C.create_string_buffer(65536)
        size=self.dll.th_client_send(self.handle,C.create_string_buffer(inner),len(inner),output,65536)
        if size<0:raise ValueError('native relay send denied')
        return output.raw[:size]
