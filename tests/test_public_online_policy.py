"""Missing ss.info policy regression; synthetic loopback only, no real accounts."""
import base64
from pathlib import Path
import sys
import unittest
from urllib.request import build_opener, ProxyHandler
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import public_online as online
from test_public_online import endpoint

POLICY = {
    'SummonDataCreateInterval2': '2',
    'SummonDataGetListInterval2': '2',
    'SummonDataMatchingRate2': '1.0',
    'Playlog_ChrDead2': '0',
    'PlayLog_ChrLevel2': '0',
}
PUBLIC_OPTIONS = {'BloodMessGetListInterval2': '90', 'TombMessGetListInterval2': '120'}


def metadata(base, options=None, regions=10):
    root = ET.Element('root')
    ET.SubElement(root, 'ss').text = '0'
    for i in range(regions):
        block = ET.SubElement(root, 'gameurl' + str(i))
        for name in sorted(online.SUMMON | {'api_Login', 'api_BloodMessGetList', 'api_TombMessGetList'}):
            ET.SubElement(block, name).text = base
    for tag, value in (options or {}).items():
        ET.SubElement(root, tag).text = value
    return base64.b64encode(''.join(ET.tostring(e, encoding='unicode') for e in root).encode())


def decode(raw):
    return ET.fromstring('<root>' + base64.b64decode(raw).decode() + '</root>')


class MissingPolicy(unittest.TestCase):
    def merge(self, public_options=None, private_options=None):
        return decode(online.hybrid_info(
            metadata(online.PUBLIC + ':443', public_options),
            metadata('http://127.0.0.1:31415', POLICY if private_options is None else private_options),
            'http://127.0.0.1:12000/fixture'))

    def assert_policy(self, root, options=POLICY):
        for tag, value in options.items():
            nodes = root.findall(tag)
            self.assertEqual(len(nodes), 1, tag)
            self.assertEqual(nodes[0].text, value, tag)

    def test_public_with_no_policy_tags_gets_private_policy(self):
        # Actual upstream shape is ss + gameurl0..9, without any policy fields.
        root = self.merge()
        self.assert_policy(root)
        self.assertEqual(root.find('ss').text, '0')
        self.assertEqual(len([e for e in root if e.tag.startswith('gameurl')]), 10)
        for node in root.iter():
            if node.tag in online.SUMMON:
                self.assertEqual(node.text, 'http://127.0.0.1:31415')
            elif node.tag.startswith('api_'):
                self.assertEqual(node.text, 'http://127.0.0.1:12000/fixture')

    def test_partial_public_policy_updates_existing_and_adds_missing_once(self):
        root = self.merge({'SummonDataGetListInterval2': '35', 'Playlog_ChrDead2': '1'})
        self.assert_policy(root)

    def test_existing_public_content_options_stay_public(self):
        root = self.merge(PUBLIC_OPTIONS, {**POLICY, 'BloodMessGetListInterval2': '2',
                                          'TombMessGetListInterval2': '3', 'PrivateOnlyFlag': '1'})
        self.assert_policy(root)
        for tag, value in PUBLIC_OPTIONS.items():
            self.assertEqual(root.find(tag).text, value)
        self.assertIsNone(root.find('PrivateOnlyFlag'))

    def test_unrelated_private_options_are_not_added(self):
        root = self.merge({}, {**POLICY, 'BloodMessGetListInterval2': '2', 'PrivateOnlyFlag': '1'})
        self.assert_policy(root)
        self.assertIsNone(root.find('BloodMessGetListInterval2'))
        self.assertIsNone(root.find('PrivateOnlyFlag'))

    def test_public_only_policy_is_retained(self):
        root = self.merge({'SummonPublicOnly2': '7', **PUBLIC_OPTIONS})
        self.assert_policy(root)
        self.assertEqual(root.find('SummonPublicOnly2').text, '7')

    def test_no_private_policy_does_not_invent_values(self):
        root = self.merge({'SummonDataCreateInterval2': '30', **PUBLIC_OPTIONS}, {})
        self.assertEqual(root.find('SummonDataCreateInterval2').text, '30')
        self.assertIsNone(root.find('SummonDataGetListInterval2'))

    def test_existing_policy_is_not_duplicated(self):
        root = self.merge({**POLICY, **PUBLIC_OPTIONS})
        self.assert_policy(root)
        self.assertEqual(len(list(root)), 1 + 10 + len(POLICY) + len(PUBLIC_OPTIONS))

    def test_real_loopback_bridge_serves_missing_policy_and_private_routes(self):
        class Identity:
            user_id = 41
            connected = True
        public_requests = []
        def transport(path, raw=None, headers=None):
            public_requests.append(path)
            self.assertEqual(path, '/bb-eu/ss.info')
            return 200, {}, metadata(online.PUBLIC + ':443', PUBLIC_OPTIONS)
        with endpoint(lambda: metadata(private, {**POLICY, 'BloodMessGetListInterval2': '2'})) as (private, private_requests):
            with online.Bridge(Identity(), private, transport) as bridge:
                opener = build_opener(ProxyHandler({}))
                with opener.open(bridge.origin + '/bb-eu/ss.info') as response:
                    self.assertEqual(response.status, 200)
                    root = decode(response.read())
                self.assert_policy(root)
                for node in root.iter():
                    if node.tag in online.SUMMON: self.assertEqual(node.text, private)
                    elif node.tag.startswith('api_'): self.assertEqual(node.text, bridge.url)
                self.assertEqual(root.find('BloodMessGetListInterval2').text, '90')
        self.assertEqual(public_requests, ['/bb-eu/ss.info'])
        self.assertEqual(private_requests, [])


if __name__ == '__main__':
    unittest.main()
