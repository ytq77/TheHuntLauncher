"""Regenerate official PSN text from the pinned BBport source, without game images."""
import argparse
import importlib.util
import json
from pathlib import Path


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def catalog(port):
    source = Path(port) / 'launcher'
    trophies = module(source / 'bbport_trophies.py', 'catalog_trophies').TROPHIES
    zh = module(source / 'bbport_lang_trophies.py', 'catalog_language').TRANSLATIONS['zh']
    return [dict(id=n, name=zh.get(name, name), description=zh.get(desc, desc), grade=grade,
                 group='base' if n < 34 else 'dlc') for n, name, desc, grade, group in trophies]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', required=True, type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / 'launcher/trophy-catalog.json'
    result = catalog(args.port)
    if args.check:
        if json.loads(target.read_text('utf-8')) != result:
            raise SystemExit('Trophy catalog differs from pinned upstream')
        print('Official trophy catalog matches all 40 pinned upstream records.')
    else:
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
