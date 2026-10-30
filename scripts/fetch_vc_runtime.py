"""Fetch the pinned Microsoft runtime installer without installing it."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def signature(path):
    code = """$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1')
$file=Get-Item -LiteralPath $env:THEHUNT_VCREDIST_FILE
$signature=Get-AuthenticodeSignature -LiteralPath $file.FullName
if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation'){throw 'Invalid Microsoft installer signature'}
@{signature='Valid';signer=$signature.SignerCertificate.Subject;version=$file.VersionInfo.FileVersion}|ConvertTo-Json -Compress
"""
    environment = {key:value for key,value in os.environ.items() if key.upper() != 'PSMODULEPATH'}
    environment['THEHUNT_VCREDIST_FILE'] = str(path)
    result = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand',
        base64.b64encode(code.encode('utf-16le')).decode('ascii')],
        capture_output=True, text=True, env=environment)
    if result.returncode:
        raise RuntimeError('Microsoft installer signature verification failed: ' + result.stderr[-1200:])
    return json.loads(result.stdout)


def fetch(folder, spec):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'VC_redist.x64.exe'
    def verify(path):
        if path.stat().st_size != spec['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != spec['sha256']:
            raise ValueError('Microsoft runtime installer hash/size mismatch')
    if target.exists():
        verify(target)
    else:
        temporary = folder / 'VC_redist.download.exe'
        try:
            with urllib.request.urlopen(spec['url'], timeout=60) as response, temporary.open('xb') as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
                    if out.tell() > spec['size']:
                        raise ValueError('Microsoft runtime installer exceeds pinned size')
            verify(temporary)
            signed = signature(temporary)
            if signed['version'] != spec['version']:
                raise ValueError('Unexpected Microsoft runtime version')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    signed = signature(target)
    if signed['version'] != spec['version']:
        raise ValueError('Unexpected Microsoft runtime version')
    (folder / 'source.json').write_text(json.dumps({**spec, **signed}, indent=2), 'utf-8')
    return target


if __name__ == '__main__':
    spec = json.loads(Path(__file__).with_name('vc-runtime.lock.json').read_text('utf-8'))
    fetch(ROOT / '.build/vc-runtime', spec)
    print('Microsoft VC++ x64 installer verified; not installed.')
