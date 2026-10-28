"""Export reviewed timing data, inspect TTML, and compare semantic content."""
import argparse
import copy
import json
import math
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

TT = 'http://www.w3.org/ns/ttml'
TM = 'http://www.w3.org/ns/ttml#metadata'
IT = 'http://music.apple.com/lyric-ttml-internal'
AM = 'http://www.example.com/ns/amll'
XL = 'http://www.w3.org/XML/1998/namespace'
for prefix, uri in [('', TT), ('ttm', TM), ('itunes', IT), ('amll', AM)]:
    ET.register_namespace(prefix, uri)


def local(tag):
    return tag.rsplit('}', 1)[-1]


def attr(element, name, default=None):
    return next((v for k, v in element.attrib.items() if local(k) == name), default)


def milliseconds(value):
    if value is None:
        raise ValueError('Missing timestamp')
    if (not re.fullmatch(r'\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?s?', value)
            or (':' in value and value.endswith('s'))):
        raise ValueError(f'Invalid timestamp: {value!r}')
    parts = value.removesuffix('s').split(':')
    if any(float(p) >= 60 for p in parts[1:]):
        raise ValueError(f'Invalid clock field: {value!r}')
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return round(total * 1000)


def stamp(seconds):
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Time must be finite and nonnegative')
    ms = round(seconds * 1000)
    mins, rest = divmod(ms, 60000)
    sec, frac = divmod(rest, 1000)
    if mins >= 60:
        hours, mins = divmod(mins, 60)
        return f'{hours}:{mins:02d}:{sec:02d}.{frac:03d}'
    return f'{mins}:{sec:02d}.{frac:03d}' if mins else f'{sec}.{frac:03d}'


def serialize(root):
    root = copy.deepcopy(root)
    for block in root.iter(f'{{{IT}}}iTunesMetadata'):
        for element in block.iter():
            if element.tag.startswith('{' + IT + '}'):
                element.tag = local(element.tag)
        block.set('xmlns', IT)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def plain(element):
    if element is None:
        return ''
    return (element.text or '') + ''.join(''.join(c.itertext()) + (c.tail or '') for c in element if attr(c, 'role') != 'x-bg')


def track(container, errors, warnings, label, background=False):
    text = container.text or ''
    units = []
    previous = None
    for child in container:
        if attr(child, 'role') in ('x-bg', 'x-translation', 'x-roman'):
            text += child.tail or ''
            continue
        value = ''.join(child.itertext())
        text += value + (child.tail or '')
        if value.strip():
            if child.get('begin') is None or child.get('end') is None:
                errors.append(f'{label}: visible unit has no complete timing')
                continue
            start, end = milliseconds(child.get('begin')), milliseconds(child.get('end'))
            if start >= end:
                errors.append(f'{label}: nonpositive unit duration for {value!r}')
            if previous is not None and start < previous:
                errors.append(f'{label}: sequential units overlap/backtrack at {value!r}')
            previous = end
            if end - start > 8000:
                warnings.append(f'{label}: held unit >8 seconds; inspect voice/reverb, do not auto-trim')
            units.append({'text': value, 'start': start, 'end': end})
    if background:
        text = text.strip()
        if text.startswith('(') and text.endswith(')'):
            text = text[1:-1]
            if units:
                units[0]['text'] = units[0]['text'].removeprefix('(')
                units[-1]['text'] = units[-1]['text'].removesuffix(')')
        if text.startswith('(') or text.endswith(')'):
            warnings.append(f'{label}: check doubled/unbalanced background parentheses')
    if not text.strip():
        errors.append(f'{label}: empty lyric track')
    if '\n' in text or '\r' in text or '\t' in text:
        warnings.append(f'{label}: formatting whitespace in mixed text; inspect display')
    translations = {attr(c, 'lang', ''): ''.join(c.itertext()) for c in container if attr(c, 'role') == 'x-translation'}
    romanizations = {attr(c, 'lang', ''): ''.join(c.itertext()) for c in container if attr(c, 'role') == 'x-roman'}
    return {'text': text, 'units': units, 'translations': translations,
            'romanizations': romanizations, 'agent': attr(container, 'agent')}


def inspect(path, duration=None):
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError('Audio duration must be finite and positive')
    raw = Path(path).read_bytes()
    root = ET.fromstring(raw)
    errors, warnings = [], []
    if raw.startswith(b'\xef\xbb\xbf'):
        errors.append('UTF-8 BOM present')
    if root.tag != f'{{{TT}}}tt':
        errors.append('Root is not TTML tt')
    if b'<!--' in raw:
        warnings.append('XML comments present; exclude non-lyric working notes from delivery')
    metadata = {}
    for element in root.iter():
        if local(element.tag) == 'meta' and 'key' in element.attrib:
            metadata.setdefault(element.get('key'), []).append(element.get('value', ''))
    for key in ('musicName', 'artists', 'album'):
        if not metadata.get(key) or not all(metadata[key]):
            errors.append(f'Missing metadata: {key}')
    if not any(metadata.get(k) for k in ('ncmMusicId', 'qqMusicId', 'spotifyId', 'appleMusicId')):
        errors.append('Missing supported platform ID')
    if bool(metadata.get('ttmlAuthorGithub')) != bool(metadata.get('ttmlAuthorGithubLogin')):
        errors.append('GitHub attribution needs both numeric ID and login')
    for key, values in metadata.items():
        if len(values) != len(set(values)):
            errors.append(f'Duplicate metadata value: {key}')
    agent_nodes = [e for e in root.iter() if e.tag == f'{{{TM}}}agent']
    agents = {attr(e, 'id'): {'type': e.get('type'), 'name': ''.join(e.itertext())} for e in agent_nodes}
    if len(agents) != len(agent_nodes) or None in agents:
        errors.append('Duplicate or missing agent ID')
    body = root.find(f'{{{TT}}}body')
    if body is None:
        raise ValueError('No TTML body')
    declared = milliseconds(body.get('dur')) if body.get('dur') else None
    maximum = round(duration * 1000) if duration is not None else declared
    parents = {c: p for p in root.iter() for c in p}
    for element in body.iter():
        if 'begin' not in element.attrib and 'end' not in element.attrib:
            continue
        start, end = milliseconds(element.get('begin')), milliseconds(element.get('end'))
        if (local(element.tag) == 'span' and start == end and
                ''.join(element.itertext()).isspace()):
            # Zero-length space spans may be anchored at zero outside the parent lyric range.
            continue
        if start > end or (start == end and ''.join(element.itertext()).strip()):
            errors.append(f'Invalid range: {local(element.tag)} {start}..{end}')
        if maximum is not None and end > maximum + 1:
            errors.append(f'Timestamp beyond audio duration: {end} > {maximum}')
        if declared is not None and end > declared:
            errors.append(f'Timestamp beyond body dur: {end} > {declared}')
        parent = parents.get(element)
        while parent is not None and 'begin' not in parent.attrib:
            parent = parents.get(parent)
        if parent is not None:
            a, b = milliseconds(parent.get('begin')), milliseconds(parent.get('end'))
            if start < a or end > b:
                errors.append(f'Child range {start}..{end} outside parent {a}..{b}')
    lines, keys, last_by_agent = [], set(), {}
    for index, element in enumerate(body.iter(f'{{{TT}}}p'), 1):
        key = attr(element, 'key')
        if key != f'L{index}':
            errors.append(f'Expected continuous line ID L{index}, found {key!r}')
        key = key or f'L{index}'  # Continue inspection without masking the recorded missing-ID error.
        if key in keys:
            errors.append(f'Duplicate line ID: {key}')
        keys.add(key)
        agent = attr(element, 'agent')
        if not agent or agent not in agents:
            errors.append(f'{key}: undefined or missing agent')
        row = track(element, errors, warnings, key)
        row.update(id=key, agent=agent, start=milliseconds(element.get('begin')),
                   end=milliseconds(element.get('end')), part=None)
        parent = parents.get(element)
        while parent is not None and parent is not body:
            row['part'] = attr(parent, 'songPart', attr(parent, 'song-part', row['part']))
            parent = parents.get(parent)
        backgrounds = [c for c in element if attr(c, 'role') == 'x-bg']
        if len(backgrounds) > 1:
            warnings.append(f'{key}: multiple background containers; verify target parser support')
        row['backgrounds'] = [track(c, errors, warnings, key+' background', True) for c in backgrounds]
        if agent in last_by_agent and row['units'] and row['units'][0]['start'] < last_by_agent[agent]:
            warnings.append(f'{key}: same-agent overlap; verify mixing or accidental backtrack')
        if row['units']:
            last_by_agent[agent] = max(last_by_agent.get(agent, 0), row['units'][-1]['end'])
        lines.append(row)
    by_id = {x['id']: x for x in lines}
    for translation in root.iter():
        kind = local(translation.tag)
        if kind not in ('translation', 'transliteration'):
            continue
        field = 'translations' if kind == 'translation' else 'romanizations'
        language = attr(translation, 'lang', '')
        if not language:
            warnings.append('Translation has no language code')
        for text in translation:
            if local(text.tag) != 'text':
                continue
            key = text.get('for')
            if key not in by_id:
                errors.append(f'Translation refers to missing line: {key}')
                continue
            row = by_id[key]
            if language in row[field]:
                errors.append(f'{key}: duplicate {field} for {language!r}')
            row[field][language] = plain(text)
            backgrounds = [c for c in text if attr(c, 'role') == 'x-bg']
            if len(backgrounds) > len(row['backgrounds']):
                errors.append(f'{key}: background translation without background track')
            for bg, bg_text in zip(row['backgrounds'], backgrounds):
                value = ''.join(bg_text.itertext()).strip()
                if value.startswith('(') and value.endswith(')'):
                    value = value[1:-1]
                bg[field][language] = value
    if not lines:
        errors.append('No lyric lines')
    report = {'ok': not errors, 'errors': errors, 'warnings': warnings,
              'counts': {'mainLines': len(lines), 'timedUnits': sum(len(x['units']) for x in lines),
                         'translations': sum(len(x['translations']) for x in lines),
                         'backgrounds': sum(len(x['backgrounds']) for x in lines),
                         'backgroundTranslations': sum(len(bg['translations']) for x in lines for bg in x['backgrounds'])}}
    return {'metadata': metadata, 'agents': agents, 'lines': lines}, report


def export(data, output):
    root = ET.Element(f'{{{TT}}}tt', {f'{{{XL}}}lang': data['language'], f'{{{IT}}}timing': 'Word'})
    metadata = ET.SubElement(ET.SubElement(root, f'{{{TT}}}head'), f'{{{TT}}}metadata')
    for agent in data['agents']:
        node = ET.SubElement(metadata, f'{{{TM}}}agent', {f'{{{XL}}}id': agent['id'], 'type': agent.get('type', 'person')})
        if agent.get('name'):
            ET.SubElement(node, f'{{{TM}}}name', {'type': 'full'}).text = agent['name']
    for key, values in data['metadata'].items():
        for value in values if isinstance(values, list) else [values]:
            ET.SubElement(metadata, f'{{{AM}}}meta', {'key': key, 'value': str(value)})
    apple = ET.SubElement(metadata, f'{{{IT}}}iTunesMetadata')
    translation = ET.SubElement(ET.SubElement(apple, f'{{{IT}}}translations'), f'{{{IT}}}translation',
                                {f'{{{XL}}}lang': data.get('translationLanguage', 'zh-Hans'), 'type': 'subtitle'})
    body = ET.SubElement(root, f'{{{TT}}}body', {'dur': stamp(data['duration'])})
    current_part, div = object(), None
    def append_units(parent, units, background=False):
        if not units:
            raise ValueError('Cannot export an empty voice track')
        for i, unit in enumerate(units):
            text = unit['text']
            if text != text.strip() or not text:
                raise ValueError('Unit text must be nonempty without edge spaces; use spaceAfter')
            if unit['start'] >= unit['end']:
                raise ValueError('Nonpositive unit range')
            if background and (text.startswith('(') or text.endswith(')')):
                raise ValueError('Supply background text without outer parentheses')
            if background and i == 0:
                text = '(' + text
            if background and i == len(units)-1:
                text += ')'
            node = ET.SubElement(parent, f'{{{TT}}}span', {'begin': stamp(unit['start']), 'end': stamp(unit['end'])})
            node.text = text
            if unit.get('spaceAfter', False) and i < len(units)-1:
                node.tail = ' '
    for row in data['lines']:
        part = row.get('part', 'Verse')
        if part != current_part:
            current_part = part
            div = ET.SubElement(body, f'{{{TT}}}div', {f'{{{IT}}}songPart': part})
        all_units = row['units'] + row.get('background', {}).get('units', [])
        begin, end = min(x['start'] for x in all_units), max(x['end'] for x in all_units)
        line = ET.SubElement(div, f'{{{TT}}}p', {'begin': stamp(begin), 'end': stamp(end),
                            f'{{{IT}}}key': row['id'], f'{{{TM}}}agent': row['agent']})
        append_units(line, row['units'])
        if row.get('background'):
            bg = row['background']
            bg_node = ET.SubElement(line, f'{{{TT}}}span', {f'{{{TM}}}role': 'x-bg',
                                     'begin': stamp(min(u['start'] for u in bg['units'])),
                                     'end': stamp(max(u['end'] for u in bg['units']))})
            append_units(bg_node, bg['units'], True)
        if row.get('translation') is not None or row.get('background', {}).get('translation') is not None:
            text = ET.SubElement(translation, f'{{{IT}}}text', {'for': row['id']})
            text.text = row.get('translation', '')
            if row.get('background', {}).get('translation') is not None:
                ET.SubElement(text, f'{{{IT}}}span', {f'{{{TM}}}role': 'x-bg'}).text = row['background']['translation']
    for div in body:
        div.set('begin', stamp(min(milliseconds(p.get('begin')) for p in div)/1000))
        div.set('end', stamp(max(milliseconds(p.get('end')) for p in div)/1000))
    payload = serialize(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + '.',
                                         suffix='.checking', delete=False) as handle:
            temp = Path(handle.name)
            handle.write(payload)
        _, report = inspect(temp, data['duration'])
        if not report['ok']:
            raise ValueError(json.dumps(report, ensure_ascii=False))
        temp.replace(output)
        return report
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    check = sub.add_parser('check')
    check.add_argument('file', type=Path)
    check.add_argument('--duration', type=float)
    check.add_argument('--report', type=Path)
    build = sub.add_parser('export')
    build.add_argument('json', type=Path)
    build.add_argument('file', type=Path)
    build.add_argument('--force', action='store_true')
    compare = sub.add_parser('compare')
    compare.add_argument('before', type=Path)
    compare.add_argument('after', type=Path)
    args = parser.parse_args()
    if args.action == 'export':
        if args.file.exists() and not args.force:
            raise FileExistsError('Destination exists; use another file or explicit --force')
        report = export(json.loads(args.json.read_text(encoding='utf-8-sig')), args.file)
    elif args.action == 'check':
        _, report = inspect(args.file, args.duration)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        a, ar = inspect(args.before)
        b, br = inspect(args.after)
        report = {'ok': a == b and ar['ok'] and br['ok'], 'semanticMatch': a == b,
                  'before': ar, 'after': br}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report['ok'] else 1)


if __name__ == '__main__':
    main()
