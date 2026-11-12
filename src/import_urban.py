"""Identifica áudios UrBAN e propõe associações; não inventa rótulos."""
import argparse
import csv
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PATTERN = re.compile(r'^(\d{2}-\d{2}-\d{4})_(\d{2})h(\d{2})_HIVE-(\d+)\.wav$', re.I)


def key(value):
    return value.replace('\\', '/').strip()


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f'CSV sem cabeçalho: {path}')
        return list(reader), reader.fieldnames


def write_csv(path, rows, fields):
    with Path(path).open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(fields)))
        w.writeheader()
        w.writerows(rows)


def parse_name(value):
    m = PATTERN.fullmatch(key(value).split('/')[-1])
    if not m:
        raise ValueError(f'Nome UrBAN não reconhecido: {value}')
    local = datetime.strptime(f'{m[1]} {m[2]}:{m[3]}', '%d-%m-%Y %H:%M')
    return local, m[4]


def import_data(manifest, inspections, features, output, audio_timezone=None, max_hours=6):
    if not math.isfinite(max_hours) or max_hours <= 0:
        raise ValueError('max_hours deve ser finito e positivo')
    manifest, inspections, output = Path(manifest), Path(inspections), Path(output)
    if output.exists():
        raise ValueError('Pasta de saída já existe; use outro nome')
    records, fields = read_csv(manifest)
    if not records or not {'file', 'source', 'hive_id'}.issubset(fields):
        raise ValueError('Manifesto vazio ou sem file/source/hive_id')
    observed, _ = read_csv(inspections)
    timezone = ZoneInfo(audio_timezone) if audio_timezone else None
    by_hive = {}
    for row in observed:
        time = datetime.fromisoformat(row['inspection_timestamp'])
        if time.tzinfo is None or not math.isfinite(float(row['frames_bees'])) or float(row['frames_bees']) < 0:
            raise ValueError('Inspeção sem fuso ou população inválida')
        by_hive.setdefault(row['hive_tag'], []).append((time, row))
    enriched, mapping, candidates = [], {}, []
    for record in records:
        if record['source'] != 'urban':
            raise ValueError('Manifesto deve conter somente source=urban')
        if record.get('frames_bees', '').strip():
            raise ValueError('Manifesto já contém rótulos; confira antes de reaplicar associação')
        local, hive = parse_name(record['file'])
        if hive not in by_hive:
            raise ValueError(f'Colmeia sem referência nas inspeções: {hive}')
        time = local.replace(tzinfo=timezone) if timezone else local
        row = {**record, 'hive_id': 'urban:' + hive, 'timestamp': time.isoformat(),
               'audio_timezone': audio_timezone or 'unconfirmed',
               'frames_bees': '', 'label_source': '', 'inspection_id': '',
               'label_type': 'unknown', 'association_status': 'unmatched',
               'inspection_timestamp': '', 'inspection_distance_hours': ''}
        # Sem fuso confirmado: apenas candidatos por data escrita, sem rótulo.
        if timezone is None:
            options = [(t, r) for t, r in by_hive[hive] if t.date() == local.date()]
            if options:
                row['association_status'] = 'candidate_timezone_unconfirmed'
            for t, inspection in options:
                candidates.append({'file': record['file'], 'hive_id': row['hive_id'],
                    'audio_timestamp': time.isoformat(), 'inspection_id': inspection['inspection_id'],
                    'inspection_timestamp': t.isoformat(), 'candidate_frames_bees': inspection['frames_bees'],
                    'status': 'candidate_only_timezone_unconfirmed'})
        else:
            options = sorted([(abs((t-time).total_seconds())/3600, t, r)
                              for t, r in by_hive[hive]], key=lambda item:item[0])
            distance, t, inspection = options[0]
            # Não escolher arbitrariamente entre inspeções equidistantes.
            tied = len(options)>1 and abs(options[1][0]-distance)<1e-9
            if distance <= max_hours and not tied:
                row.update(frames_bees=inspection['frames_bees'],
                    label_source=f"{inspection['label_source']};nearest_inspection;max_hours={max_hours};timezone={audio_timezone}",
                    inspection_id=inspection['inspection_id'], label_type='derived_near_inspection',
                    association_status='matched', inspection_timestamp=t.isoformat(),
                    inspection_distance_hours=round(distance, 6))
                candidates.append({'file': record['file'], 'hive_id':row['hive_id'],
                    'audio_timestamp':time.isoformat(), 'inspection_id':inspection['inspection_id'],
                    'inspection_timestamp':t.isoformat(), 'candidate_frames_bees':inspection['frames_bees'],
                    'status':'derived_near_inspection'})
        k = key(record['file'])
        if k in mapping:
            raise ValueError('Caminho de áudio duplicado no manifesto')
        mapping[k] = row
        enriched.append(row)
    merged = []
    feature_fields = []
    if features:
        feature_rows, feature_fields = read_csv(features)
        for old in feature_rows:
            k = key(old['source_file'])
            if k not in mapping:
                raise ValueError(f'Feature não corresponde ao manifesto: {old["source_file"]}')
            info = mapping[k]
            # file nas features é o WAV da janela, não o WAV de origem.
            cols = ['hive_id','timestamp','audio_timezone','frames_bees','label_source',
                    'inspection_id','label_type','association_status','inspection_timestamp','inspection_distance_hours']
            merged.append({**old, **{c:info[c] for c in cols}})
    summary = {'recordings':len(enriched), 'hives':dict(sorted(Counter(r['hive_id'] for r in enriched).items())),
        'filename_format':'DD-MM-YYYY_HHhMM_HIVE-tag.wav',
        'audio_timezone':audio_timezone or 'unconfirmed',
        'recordings_with_candidate':sum(r['association_status']=='candidate_timezone_unconfirmed' for r in enriched),
        'labeled_recordings':sum(bool(r['frames_bees']) for r in enriched),
        'labeled_hives':sorted({r['hive_id'] for r in enriched if r['frames_bees']}),
        'labeled_inspections':len({r['inspection_id'] for r in enriched if r['frames_bees']}),
        'feature_windows':len(merged),
        'labeled_feature_windows':sum(bool(r['frames_bees']) for r in merged),
        'note':'Janelas da mesma gravação não são observações independentes. Sem fuso confirmado, nenhum rótulo é atribuído. Associação próxima à inspeção é rótulo derivado, não diagnóstico.'}
    output.mkdir(parents=True)
    extra = ['audio_timezone','frames_bees','label_source','inspection_id','label_type',
             'association_status','inspection_timestamp','inspection_distance_hours']
    manifest_rows = [{**r, 'file': os.path.relpath((manifest.parent / key(r['file'])).resolve(), output.resolve())} for r in enriched]
    write_csv(output/'urban_identified.csv', manifest_rows, fields + ['timestamp'] + extra)
    write_csv(output/'association_candidates.csv', candidates,
              ['file','hive_id','audio_timestamp','inspection_id','inspection_timestamp','candidate_frames_bees','status'])
    write_csv(output/'inspection_targets.csv', observed, list(observed[0]))
    if features:
        write_csv(output/'urban_features_identified.csv', merged, feature_fields + ['hive_id','timestamp'] + extra)
    (output/'import_report.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    return summary


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',default='data/metadata/urban.csv')
    p.add_argument('--inspections',default='data/metadata/urban_inspections/population_inspections_2022.csv')
    p.add_argument('--features')
    p.add_argument('--output',default='data/metadata/urban_import_v1')
    p.add_argument('--audio-timezone',help='Somente após confirmar o fuso nos dados/documentação da fonte')
    p.add_argument('--max-hours',type=float,default=6,help='Limite de proximidade para rótulos derivados, se fuso informado')
    a=p.parse_args()
    try:
        print(json.dumps(import_data(a.manifest,a.inspections,a.features,a.output,a.audio_timezone,a.max_hours),indent=2,ensure_ascii=False))
    except (OSError,ValueError,KeyError) as exc:
        p.exit(1,f'ERRO: {exc}\n')
