"""Translate only catalogued Windows wide-string literals; retain C++ and filters."""
import ast
from collections import Counter
import json
from pathlib import Path
import re

LITERAL = r'L"(?:[^"\\\r\n]|\\.)*"'
GROUP = re.compile(LITERAL + r'(?:\s*' + LITERAL + r')*')


def wide(value):
    return 'L' + json.dumps(value, ensure_ascii=True).replace(r'\u0000', r'\0')


def translate_source(source, catalog):
    used = Counter()

    def replace(match):
        text = ''.join(ast.literal_eval(token[1:])
                       for token in re.findall(LITERAL, match[0]))
        # Windows file filters are NUL-delimited pairs of label and pattern.
        # Translate labels only; patterns and the trailing NULs must survive.
        parts = text.split('\0')
        changed = False
        for index, part in enumerate(parts):
            if part in catalog and (len(parts) == 1 or index % 2 == 0):
                parts[index] = catalog[part]
                used[part] += 1
                changed = True
        return wide('\0'.join(parts)) if changed else match[0]

    return GROUP.sub(replace, source), used


def generate(directory, catalog_path):
    catalog = json.loads(Path(catalog_path).read_text(encoding='utf-8'))
    used = Counter()
    outputs = []
    for name in ('bbsc_launch', 'bbsc_mod'):
        path = Path(directory) / (name + '.cpp')
        translated, occurrences = translate_source(path.read_text(encoding='utf-8'), catalog)
        used.update(occurrences)
        outputs.append((path.with_name(name + '_zh_cn.cpp'), translated))
    if set(used) != set(catalog):
        raise ValueError('Windows text changed upstream; unused catalog keys: ' +
                         repr(sorted(set(catalog) - set(used))))
    for path, source in outputs:
        path.write_text(source, encoding='utf-8')
    return {'catalog_entries': len(catalog), 'translated_occurrences': sum(used.values()),
            'outputs': [path.name for path, _ in outputs]}
