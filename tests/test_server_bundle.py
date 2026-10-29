import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from package import check_public
from server_bundle import install_package


class EmbeddedServer(unittest.TestCase):
    def package(self,folder,extra=None,version='0.1.0-preview.15',corrupt=False):
        files={'Server.exe':b'server-program','server.json':b'{"address":"127.0.0.1"}',
               'bbsc/shadnet.exe':b'shadnet-program','runtime/bin/python.exe':b'python-runtime',
               'runtime/bin/shadnet-admin.exe':b'account-tool','bbsc-game/bbsc_gameserver.py':b'# fixed server',
               'Apply-Update.ps1':b'# installer'}
        files.update(extra or {})
        info={'label':version,'features':['seamless-only'],'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}}
        files['BUILD-INFO.json']=json.dumps(info).encode()
        if corrupt:files['Server.exe']=b'tampered-program'
        path=folder/'server.zip'
        with zipfile.ZipFile(path,'w') as z:
            for name,raw in files.items():z.writestr('TheHuntServer/'+name,raw)
        return path

    def test_clean_matching_server_is_embedded_with_verified_programs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=self.package(root);dest=root/'embedded'
            info=install_package(archive,dest,'0.1.0-preview.15',check_public)
            self.assertEqual(info['label'],'0.1.0-preview.15')
            self.assertEqual((dest/'Server.exe').read_bytes(),b'server-program')
            self.assertFalse((dest/'normal').exists())

    def test_state_and_unsafe_names_are_rejected_before_any_extraction(self):
        for name in ('bbsc/db/shadnet.db','bbsc/shadnet.cfg','bbsc/online-data.json',
                     'bbsc/.server.lock','normal/shadnet.exe','../escape','/escape','runtime/../../escape'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);archive=self.package(root,{name:b'private-or-unsafe'});dest=root/'embedded'
                with self.assertRaises(ValueError):install_package(archive,dest,'0.1.0-preview.15',check_public)
                self.assertFalse(dest.exists())

    def test_wrong_version_or_initialized_listener_or_tampering_is_refused(self):
        for values in ({'version':'0.1.0-preview.14'}, {'extra':{'server.json':b'{"address":"100.64.1.2"}'}},{'corrupt':True}):
            with self.subTest(values=values),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);archive=self.package(root,**values);dest=root/'embedded'
                with self.assertRaises(ValueError):install_package(archive,dest,'0.1.0-preview.15',check_public)
                self.assertFalse(dest.exists())

if __name__=='__main__':unittest.main()
