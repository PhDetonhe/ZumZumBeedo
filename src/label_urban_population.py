"""Rótulos exploratórios de população por data publicada e interpolação limitada.

Inspirado no notebook main_benchmarks.ipynb dos autores UrBAN.
Não converte fusos, não extrapola, não infere saúde/rainha e não treina modelos.
"""
import argparse
import csv
import json
import math
from collections import Counter
from datetime import date, datetime
from pathlib import Path

REFERENCE = 'https://github.com/mahsa-abdollahi/UrBAN/blob/main/code/main_benchmarks.ipynb'


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return rows, reader.fieldnames or []


def write_csv(path, rows, fields):
    with Path(path).open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(fields)))
        w.writeheader()
        w.writerows(rows)


def observations(path):
    rows, fields = read_csv(path)
    required = {'hive_tag','inspection_timestamp','frames_bees','inspection_id','label_source'}
    if not rows or not required.issubset(fields):
        raise ValueError('Inspeções vazias ou formato incompatível')
    hives = {}
    seen = set()
    for r in rows:
        day = datetime.fromisoformat(r['inspection_timestamp']).date()
        value = float(r['frames_bees'])
        if not math.isfinite(value) or value < 0:
            raise ValueError('População inválida')
        k = (r['hive_tag'], day)
        if k in seen:
            raise ValueError(f'Inspeções duplicadas da mesma colmeia/data: {k}; revisar manualmente')
        seen.add(k)
        hives.setdefault('urban:'+r['hive_tag'], []).append((day, value, r))
    return {h: sorted(points, key=lambda p:p[0]) for h,points in hives.items()}


def estimate(points, day, max_gap_days):
    # Igualdade de data é associação diária, não evidência de que o áudio foi
    # gravado no instante exato da inspeção.
    for p in points:
        if p[0] == day:
            return p[1], p, p, 0.0, 'derived_same_published_date'
    for left, right in zip(points, points[1:]):
        if left[0] < day < right[0]:
            gap = (right[0]-left[0]).days
            if gap > max_gap_days:
                return None
            alpha = (day-left[0]).days / gap
            value = left[1] + alpha*(right[1]-left[1])
            return value, left, right, alpha, 'derived_linear_by_published_date'
    return None


def label(features, inspections, output, max_gap_days=21):
    output = Path(output)
    if output.exists():
        raise ValueError('Pasta de saída já existe; use outro nome')
    if max_gap_days <= 0:
        raise ValueError('max_gap_days deve ser positivo')
    rows, fields = read_csv(features)
    required = {'source_file','hive_id','timestamp','frames_bees','feature_version'}
    if not rows or not required.issubset(fields):
        raise ValueError('Use urban_features_identified.csv produzido por import_urban.py')
    hives = observations(inspections)
    labeled, recordings, valid_recordings = [], {}, {}
    for old in rows:
        if old['frames_bees'].strip():
            raise ValueError('Entrada já contém rótulos. Use a tabela identificada sem rótulos')
        hive = old['hive_id']
        if hive not in hives:
            raise ValueError(f'Colmeia sem inspeções: {hive}')
        day = datetime.fromisoformat(old['timestamp']).date()
        rec_id = old.get('recording_id') or old['source_file'].replace('\\','/')
        signature = (hive, day, old['source_file'])
        if rec_id in recordings and recordings[rec_id] != signature:
            raise ValueError('Metadados inconsistentes entre janelas da mesma gravação')
        recordings[rec_id] = signature
        info = estimate(hives[hive], day, max_gap_days)
        if info is None:
            continue
        value, left, right, alpha, kind = info
        provenance = f"{left[2]['label_source']}|{right[2]['label_source']};{kind};max_gap_days={max_gap_days}"
        updated = {**old, 'frames_bees':value, 'label_type':kind,
            'label_source':provenance, 'inspection_id':left[2]['inspection_id']+'|'+right[2]['inspection_id'],
            'association_status':'derived_date_label',
            'inspection_timestamp':'', 'inspection_distance_hours':'',
            'label_time_basis':'published_calendar_date_no_timezone_conversion',
            'left_inspection_id':left[2]['inspection_id'], 'right_inspection_id':right[2]['inspection_id'],
            'left_inspection_date':left[0].isoformat(), 'right_inspection_date':right[0].isoformat(),
            'left_frames_bees':left[1], 'right_frames_bees':right[1],
            'inspection_gap_days':(right[0]-left[0]).days, 'interpolation_weight':alpha,
            'recording_group':rec_id}
        labeled.append(updated)
        valid_recordings[rec_id] = {c:updated[c] for c in ['recording_group','source_file','hive_id','timestamp',
            'frames_bees','label_type','left_inspection_id','right_inspection_id','inspection_gap_days','interpolation_weight']}
    counts = Counter(r['hive_id'] for r in valid_recordings.values())
    summary = {'input_windows':len(rows), 'input_recordings':len(recordings),
        'labeled_windows':len(labeled), 'excluded_windows':len(rows)-len(labeled),
        'labeled_recordings':len(valid_recordings), 'labeled_hives':len(counts),
        'recordings_by_hive':dict(sorted(counts.items())), 'max_gap_days':max_gap_days,
        'method_reference':REFERENCE,
        'time_basis':'Datas escritas nos nomes WAV e datas escritas nas inspeções, conforme convenção diária do notebook; sem afirmar equivalência de instantes/fusos.',
        'limitations':['Rótulos derivados; precisão contra eles não comprova precisão contra população real.',
            'Limite de 21 dias é escolha conservadora inicial BeeGuard, não um limiar validado pelos autores.',
            'Não extrapola além da primeira/última inspeção; intervalos longos são excluídos.',
            'Fuso do áudio continua não confirmado; possível desalinhamento de um dia perto de limites.',
            'Separar colmeias e agrupar gravações na avaliação; janelas não são observações independentes.'],
        'training_ready':'experimental_only' if len(counts)>=3 else 'insufficient_hives'}
    extra = list(labeled[0]) if labeled else fields+['label_type','recording_group']
    output.mkdir(parents=True)
    write_csv(output/'population_features.csv', labeled, extra)
    rec_fields = list(next(iter(valid_recordings.values()))) if valid_recordings else ['recording_group']
    write_csv(output/'recording_labels.csv',list(valid_recordings.values()),rec_fields)
    (output/'population_label_report.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--features',default='data/metadata/urban_import_v1/urban_features_identified.csv')
    p.add_argument('--inspections',default='data/metadata/urban_inspections/population_inspections_2022.csv')
    p.add_argument('--output',default='data/metadata/urban_population_v1')
    p.add_argument('--max-gap-days',type=int,default=21)
    a=p.parse_args()
    try:
        result=label(a.features,a.inspections,a.output,a.max_gap_days)
        print(json.dumps(result,indent=2,ensure_ascii=False))
        if not result['labeled_windows']:
            raise SystemExit(1)
    except (ValueError,KeyError,OSError) as exc:
        p.exit(1,f'ERRO: {exc}\n')
