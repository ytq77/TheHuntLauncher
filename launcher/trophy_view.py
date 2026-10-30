"""Read-only desktop trophy snapshot. Never instantiate the upstream Tracker."""
import argparse
import base64
import json
from pathlib import Path
import sys
import time

from trophy_sync import read


def snapshot(root, game=None, icons=False):
    root = Path(root).resolve()
    path = root / 'data' / 'user' / 'trophies.log'
    for attempt in range(3):
        try:
            state, raw = read(path)
            if raw and not raw.endswith((b'\n', b'\r')):
                raise ValueError('奖杯记录末行尚未写完，请稍后刷新。')
            break
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(0.03)
    catalog = json.loads((root / 'trophy-catalog.json').read_text('utf-8'))
    records = state['records']
    rows = [dict(item, unlocked=records.get(str(item['id']))) for item in catalog]
    images = {}
    if icons and game:
        # Fixed BBport reader extracts unencrypted PNGs in memory only.
        sys.path.insert(0, str(root / 'engine' / 'launcher'))
        try:
            from bbport_trophy_icons import TrophyIcons
        except ImportError:
            TrophyIcons = None
        if TrophyIcons is not None:
            source = TrophyIcons(game)
            for item in rows:
                raw = source.get(item['id'])
                if raw:
                    images[str(item['id'])] = base64.b64encode(raw).decode('ascii')
    return dict(ok=True, environment=str(root / 'data' / 'user'), path=str(path),
                sha256=state['sha256'], missing=state['sha256'] is None,
                ignored=state['ignored'], trophies=rows, images=images,
                platinum_due=all(str(n) in records for n in range(1, 34)) and '0' not in records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--icons', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    try:
        profile = root / 'profile.json'
        game = json.loads(profile.read_text('utf-8-sig')).get('game') if profile.exists() else None
        result = snapshot(root, game, args.icons)
    except Exception as error:
        result = dict(ok=False, error='奖杯记录暂不可用：' + str(error))
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
