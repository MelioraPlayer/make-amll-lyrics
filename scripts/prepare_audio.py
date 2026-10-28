"""Preserve LRC evidence, create provisional row windows and an aligned PCM copy."""
import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

STAMP = re.compile(r'\[(\d+):(\d{1,2})(?:\.(\d{1,3}))?\]')


def parse_lrc(text, duration, offset_ms=0):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Audio duration must be finite and positive')
    rows, other, declared_offsets = [], [], []
    for number, line in enumerate(text.splitlines(), 1):
        offset = re.fullmatch(r'\[offset:([+-]?\d+)\]', line.strip(), re.I)
        if offset:
            declared_offsets.append(int(offset[1]))
            continue
        matches = list(STAMP.finditer(line))
        if not matches:
            if line.strip():
                other.append({'sourceLine': number, 'text': line})
            continue
        value = STAMP.sub('', line).strip()
        for m in matches:
            if int(m[2]) >= 60:
                raise ValueError(f'Invalid LRC seconds at source line {number}')
            t = 60 * int(m[1]) + int(m[2]) + float('0.' + (m[3] or '0')) + offset_ms / 1000
            if not 0 <= t <= duration:
                raise ValueError(f'LRC timestamp outside audio at source line {number}: {t}')
            rows.append({'sourceLine': number, 'sourceStart': round(t, 3), 'text': value,
                         'units': value.split(), 'needsTextReview': True})
    rows.sort(key=lambda x: (x['sourceStart'], x['sourceLine']))
    for i, row in enumerate(rows):
        row['id'] = f'L{i+1}'
        row['cropStart'] = round(max(0, row['sourceStart'] - .5), 3)
        later = next((r['sourceStart'] for r in rows[i+1:] if r['sourceStart'] > row['sourceStart']), duration)
        row['cropEnd'] = round(min(duration, later + .15), 3)
        row['needsWindowReview'] = row['cropEnd'] - row['cropStart'] > 30 or not row['text']
    return {'rows': rows, 'unparsedLines': other, 'declaredLrcOffsetsMs': declared_offsets,
            'appliedOffsetMs': offset_ms,
            'offsetNote': 'Declared LRC offsets are recorded, not applied automatically. Positive --offset-ms delays lyrics.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', required=True, type=Path)
    parser.add_argument('--lrc', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--offset-ms', type=int, default=0)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / 'mix-16k.wav'
    rows_path = args.out / 'rows.json'
    if not args.force and (target.exists() or rows_path.exists()):
        raise FileExistsError('Output exists; use a new directory or explicit --force')
    info = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json',
                           str(args.audio)], check=True, capture_output=True, text=True)
    probe = json.loads(info.stdout)
    if not any(s.get('codec_type') == 'audio' for s in probe['streams']):
        raise ValueError('No audio stream')
    duration = float(probe['format']['duration'])
    document = parse_lrc(args.lrc.read_text(encoding='utf-8-sig'), duration, args.offset_ms)
    document.update(audio=str(args.audio.resolve()), duration=duration,
                    audioSha256=hashlib.sha256(args.audio.read_bytes()).hexdigest())
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y' if args.force else '-n',
                    '-i', str(args.audio), '-vn', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(target)], check=True)
    rows_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'rows': len(document['rows']), 'duration': duration, 'audio': str(target),
                      'rowsFile': str(rows_path), 'textReviewRequired': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
