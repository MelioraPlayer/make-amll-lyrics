"""Forced alignment candidates, with original text retained and audio-keyed caches."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path


def normalized(text, kind):
    text = text.replace('’', "'")
    if re.search(r'[^\x00-\x7f]', text):
        raise ValueError('English model input contains non-ASCII text; provide validated English modelUnits or choose a language model')
    value = re.sub("[^A-Z']", '', text.upper()) if kind == 'ctc' else re.sub('[^a-z]', '', text.lower())
    if not value:
        raise ValueError(f'No pronounceable characters in unit {text!r}')
    return value


def project_syllables(aligned, plan, kind):
    """Apply an explicit pronunciation plan to model character candidates."""
    for index, parts in plan.items():
        if not str(index).isdigit() or int(index) >= len(aligned):
            raise ValueError(f'Split-plan index outside the row: {index!r}')
        unit = aligned[int(index)]
        if not parts or not all(isinstance(part, str) and part for part in parts):
            raise ValueError('Split plan requires a nonempty array of text parts')
        if ''.join(parts) != unit['text']:
            raise ValueError(f"Split plan changes spelling: {unit['text']!r}")
        if 'chars' not in unit:
            raise ValueError('Character projection is available for English CTC/MMS only')
        sizes = [len(normalized(part, kind)) for part in parts]
        if (sum(sizes) != len(unit['chars']) or
                normalized(unit['text'], kind) != ''.join(c['text'] for c in unit['chars'])):
            raise ValueError('Spelling/modelUnits do not support this character projection')
        boundaries, position = [unit['start']], 0
        for size in sizes[:-1]:
            position += size
            boundaries.append(unit['chars'][position]['start'])
        boundaries.append(unit['end'])
        if any(a >= b for a, b in zip(boundaries, boundaries[1:])):
            raise ValueError('Projected syllable has no duration; inspect audio before splitting')
        unit['syllableCandidates'] = [{'text': part, 'start': boundaries[i], 'end': boundaries[i+1]}
                                      for i, part in enumerate(parts)]
        unit['syllableBoundarySource'] = 'character-projection-needs-acoustic-review'


def select_rows(document, ids=None):
    rows = document.get('rows') if isinstance(document, dict) else document
    if not isinstance(rows, list) or not rows:
        raise ValueError('Expected a nonempty rows array')
    known = set()
    for row in rows:
        if not isinstance(row, dict) or not str(row.get('id', '')).strip():
            raise ValueError('Every row needs an ID')
        key = str(row['id'])
        if key in known:
            raise ValueError(f'Duplicate row ID: {key}')
        known.add(key)
    selected = {part.strip() for part in ids.split(',')} if ids is not None else known
    if not selected or '' in selected or selected - known:
        raise ValueError(f'Unknown or empty row IDs: {sorted(selected - known)}')
    return [row for row in rows if str(row['id']) in selected]


def validate_rows(rows, duration, kind, split_plan):
    for row in rows:
        key = str(row['id'])
        if not isinstance(row.get('text'), str) or not row['text'].strip():
            raise ValueError(f'Review empty text in {key}')
        units = row.get('units', row.get('words'))
        if not isinstance(units, list) or not units or not all(isinstance(x, str) and x.strip() for x in units):
            raise ValueError(f'Review empty/unstructured units in {key}')
        model_units = row.get('modelUnits', units)
        if (not isinstance(model_units, list) or len(model_units) != len(units) or
                not all(isinstance(x, str) and x.strip() for x in model_units)):
            raise ValueError(f'modelUnits must map one-to-one to units in {key}')
        start, end = float(row['cropStart']), float(row['cropEnd'])
        if not all(math.isfinite(x) for x in (start, end)) or not 0 <= start < end <= duration + .001:
            raise ValueError(f'Invalid crop for {key}')
        if end - start > 45:
            raise ValueError('Crop exceeds 45 seconds; review and split it before alignment')
        if kind != 'funasr':
            for unit in model_units:
                normalized(unit, kind)
        plan = split_plan.get(key, {})
        if not isinstance(plan, dict):
            raise ValueError(f'Split plan for {key} must be an object')
        if kind == 'funasr' and plan:
            raise ValueError('Character projection is only supported for English CTC/MMS')
        for index, parts in plan.items():
            if not str(index).isdigit() or int(index) >= len(units):
                raise ValueError(f'Split-plan index outside {key}: {index!r}')
            if (not isinstance(parts, list) or not parts or
                    not all(isinstance(part, str) and part for part in parts) or
                    ''.join(parts) != units[int(index)]):
                raise ValueError(f'Split plan changes spelling in {key}: {index}')
            for part in parts:
                normalized(part, kind)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audio', type=Path, required=True)
    p.add_argument('--rows', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--model', choices=['ctc', 'mms', 'funasr'], default='ctc')
    p.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='auto')
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--ids', help='Comma-separated row IDs; process only these rows')
    p.add_argument('--split-plan', type=Path, help='JSON: row ID -> zero-based unit index -> spelling parts')
    p.add_argument('--force', action='store_true', help='Replace alignment.json; cached emissions are retained')
    args = p.parse_args()
    if args.threads < 1:
        p.error('--threads must be positive')
    report_path = args.out / 'alignment.json'
    if report_path.exists() and not args.force:
        raise FileExistsError('alignment.json exists; use a new directory or explicit --force')
    document = json.loads(args.rows.read_text(encoding='utf-8-sig'))
    rows = select_rows(document, args.ids)
    split_plan = json.loads(args.split_plan.read_text(encoding='utf-8-sig')) if args.split_plan else {}
    if not isinstance(split_plan, dict):
        raise ValueError('Split plan must be an object keyed by row ID')
    known_ids = {str(row['id']) for row in select_rows(document)}
    if set(split_plan) - known_ids:
        raise ValueError('Split plan contains unknown row IDs')
    validate_rows(rows, float('inf'), args.model, split_plan)
    import numpy as np
    import soundfile as sf
    import torch
    torch.set_num_threads(args.threads)
    device = 'cuda' if args.device == 'auto' and torch.cuda.is_available() else ('cpu' if args.device == 'auto' else args.device)
    audio, sr = sf.read(args.audio, dtype='float32')
    if sr != 16000 or audio.ndim != 1:
        raise ValueError('Input must be 16000 Hz mono; use prepare_audio.py or ffmpeg')
    validate_rows(rows, len(audio) / sr, args.model, split_plan)
    args.out.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(args.audio.read_bytes()).hexdigest()
    if args.model == 'funasr':
        from funasr import AutoModel
        model = AutoModel(model='iic/speech_timestamp_prediction-v1-16k-offline',
                          device=device, disable_update=True, ncpu=args.threads)
    else:
        import torchaudio
        if not hasattr(torchaudio.functional, 'forced_align'):
            raise RuntimeError('This helper requires compatible torch/torchaudio 2.8; forced_align is absent')
        bundle = torchaudio.pipelines.MMS_FA if args.model == 'mms' else torchaudio.pipelines.WAV2VEC2_ASR_LARGE_LV60K_960H
        model = bundle.get_model().to(device).eval()
        labels = bundle.get_labels()
        lookup = {c: i for i, c in enumerate(labels)}
    results = []
    for row in rows:
        units = row.get('units', row.get('words'))
        if not units or not all(isinstance(x, str) and x.strip() for x in units):
            raise ValueError(f"Review empty/unstructured units in {row['id']}")
        model_units = row.get('modelUnits', units)
        if len(model_units) != len(units):
            raise ValueError('modelUnits must map one-to-one to units')
        start, end = float(row['cropStart']), float(row['cropEnd'])
        if not 0 <= start < end <= len(audio) / sr + .001:
            raise ValueError(f"Invalid crop for {row['id']}")
        if end - start > 45:
            raise ValueError('Crop exceeds 45 seconds; review and split it before alignment')
        begin_sample, end_sample = round(start * sr), round(end * sr)
        clip = audio[begin_sample:end_sample].copy()
        actual_start = begin_sample / sr
        if args.model == 'funasr':
            prediction = model.generate(input=(clip, ' '.join(model_units)), data_type=('sound', 'text'),
                                        batch_size=1, disable_pbar=True)[0]
            stamps = prediction['timestamp']
            if len(stamps) != len(units):
                raise ValueError(f"FunASR unit count mismatch for {row['id']}; inspect text, do not zip-truncate")
            aligned = [{'text': text, 'start': round(actual_start + a/1000, 3),
                        'end': round(actual_start + b/1000, 3)} for text, (a, b) in zip(units, stamps)]
            decoded = None
        else:
            cache_info = {'audio': digest, 'sampleStart': begin_sample, 'sampleEnd': end_sample,
                          'model': args.model, 'torch': torch.__version__, 'torchaudio': torchaudio.__version__,
                          'postprocess': 'ctc-logsoftmax-mms-native-v1'}
            cache_key = hashlib.sha256(json.dumps(cache_info, sort_keys=True).encode()).hexdigest()
            cache = args.out / (cache_key + '.npz')
            if cache.exists():
                with np.load(cache, allow_pickle=False) as item:
                    emission = torch.from_numpy(item['emission'].copy())
            else:
                with torch.inference_mode():
                    emission, _ = model(torch.from_numpy(clip)[None].to(device))
                    # MMS_FA already emits log probabilities plus the wildcard column.
                    if args.model == 'ctc':
                        emission = emission.log_softmax(-1)
                    emission = emission.cpu()
                np.savez_compressed(cache, emission=emission.numpy())
            tokens = [lookup['*']] if args.model == 'mms' else []
            maps = []
            for text in model_units:
                norm = normalized(text, args.model)
                first = len(tokens)
                tokens.extend(lookup[c] for c in norm)
                maps.append((first, len(tokens)))
                if args.model == 'ctc':
                    tokens.append(lookup['|'])
            if args.model == 'ctc':
                tokens.pop()
            else:
                tokens.append(lookup['*'])
            path, scores = torchaudio.functional.forced_align(emission, torch.tensor([tokens], dtype=torch.int32), blank=0)
            spans = torchaudio.functional.merge_tokens(path[0], scores[0].exp())
            if len(spans) != len(tokens):
                raise ValueError('Forced alignment token mismatch')
            ratio = len(clip) / sr / emission.shape[1]
            aligned = []
            for text, (a, b) in zip(units, maps):
                chars = spans[a:b]
                weight = sum(c.end-c.start for c in chars)
                aligned.append({'text': text, 'start': round(actual_start+chars[0].start*ratio, 3),
                                'end': round(actual_start+chars[-1].end*ratio, 3),
                                'score': round(sum(c.score*(c.end-c.start) for c in chars)/weight, 4),
                                'chars': [{'text': labels[c.token], 'start': round(actual_start+c.start*ratio, 3),
                                           'end': round(actual_start+c.end*ratio, 3), 'score': round(c.score, 4)} for c in chars]})
            decoded = ''.join(labels[i] for i in torch.unique_consecutive(emission[0].argmax(-1)).tolist() if i != 0).replace('|', ' ')
        project_syllables(aligned, split_plan.get(str(row['id']), {}), args.model)
        flags = []
        for i, unit in enumerate(aligned):
            if unit['end'] <= unit['start']:
                flags.append(f'{i}: nonpositive duration')
            if unit.get('score', 1) < .4:
                flags.append(f'{i}: low model score; inspect audio')
            if unit['end']-unit['start'] > 3:
                flags.append(f'{i}: long unit; inspect held vowel/reverb')
        if aligned[0]['start']-start < .08 or end-aligned[-1]['end'] < .08:
            flags.append('Alignment touches crop edge; inspect truncation')
        result = {'id': row['id'], 'text': row['text'], 'cropStart': start, 'cropEnd': end,
                  'model': args.model, 'audioSha256': digest, 'units': aligned,
                  'decode': decoded, 'reviewFlags': flags, 'status': 'candidate-not-final'}
        results.append(result)
        # Write the result only after every requested row succeeds; failed reruns keep the old report.
        print(json.dumps({'id': row['id'], 'units': len(aligned), 'reviewFlags': flags}, ensure_ascii=False), flush=True)
    if not results:
        raise ValueError('No rows selected')
    report_path.write_text(json.dumps({'rows': results}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
