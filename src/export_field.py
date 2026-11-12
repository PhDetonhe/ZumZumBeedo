"""Exporta dados auditáveis sem atribuir rótulos a partir de alertas."""
import argparse
import csv
import hashlib
import json
import os
import shutil
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

from audio_pipeline import VERSION
from beeguard_server import read_config
from beeguard_field import FieldStore
from field_dataset import FEATURES,LABELS,SCHEMA,checked_path,vector

ROOT=Path(__file__).resolve().parents[1]
META=['recording_id','session_id','hive_id','apiary_id','recording_group','hive_group','session_day',
      'captured_at','audio_path','audio_sha256','kind','device_id','mic_model','gain','placement',
      'operator_name','session_notes','quality_status','feature_version','review_id','reviewed_at',
      'sound_label','bee_activity','context_tag','reviewer','review_notes','exclude_training','training_eligible','exclusion_reason']


def export(rows,out,audio_root,copy_audio=False,include_bench=False):
    out=Path(out).resolve()
    if out.exists():raise ValueError('Pasta de saída já existe. Use outro nome para preservar versões.')
    out.mkdir(parents=True)
    if copy_audio:(out/'audio').mkdir()
    manifest=[];training=[];issues=[];counts=Counter()
    for row in rows:
        reasons=[];desc=row['features']
        r={k:row.get(k,'') for k in META}
        r.update(recording_id=row['id'],recording_group=row['id'],
                 hive_group=row['apiary_id']+'::'+row['hive_id'],session_day=str(row['captured_at'])[:10],
                 feature_version=desc.get('feature_version',''),audio_path='')
        if row['kind']!='field' and not include_bench:reasons.append('bench_session')
        if row['quality_status']!='pass':reasons.append('quality_review')
        if row.get('review_id') is None:reasons.append('unreviewed')
        elif row.get('sound_label') not in LABELS[:2]:reasons.append('uncertain_label')
        if row.get('exclude_training'):reasons.append('excluded_by_reviewer')
        if desc.get('feature_version')!=VERSION:reasons.append('feature_version_mismatch')
        try:vector(desc)
        except (KeyError,ValueError,TypeError):reasons.append('invalid_features')
        try:
            path=checked_path(audio_root,row['audio_path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=row['audio_sha256']:raise ValueError('SHA256 diferente')
            if copy_audio:
                target=out/'audio'/(row['id']+'.wav');shutil.copyfile(path,target)
                r['audio_path']='audio/'+target.name
            else:r['audio_path']=os.path.relpath(path,out).replace(os.sep,'/')
        except (OSError,ValueError) as exc:
            reasons.append('missing_or_invalid_audio');issues.append(dict(recording_id=row['id'],error=str(exc)))
        r['exclude_training']=bool(row.get('exclude_training'))
        r['training_eligible']=not reasons;r['exclusion_reason']=';'.join(reasons)
        manifest.append(r)
        for reason in reasons:counts[reason]+=1
        if not reasons:training.append({**r,**{k:desc[k] for k in FEATURES}})
    # Duplicatas de conteúdo não devem aparecer como exemplos independentes.
    duplicate_counts=Counter(r['audio_sha256'] for r in training)
    hashes={h for h,n in duplicate_counts.items() if n>1}
    if hashes:
        for r in manifest:
            if r['training_eligible'] and r['audio_sha256'] in hashes:
                r['training_eligible']=False;r['exclusion_reason']='duplicate_audio';counts['duplicate_audio']+=1
        training=[r for r in training if r['audio_sha256'] not in hashes]
    for name,items,fields in [('manifest.csv',manifest,META),('training_features.csv',training,META+FEATURES)]:
        with (out/name).open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(items)
    report=dict(schema=SCHEMA,created_at=datetime.now(timezone.utc).isoformat(),feature_version=VERSION,
                recordings=len(manifest),training_recordings=len(training),
                label_counts=dict(Counter(r['sound_label'] for r in training)),
                training_hives=len({r['hive_group'] for r in training}),
                training_sessions=len({r['session_id'] for r in training}),excluded_by_reason=dict(counts),
                issues=issues,audio_copied=copy_audio,bench_included=include_bench,
                note='Rótulos humanos do som; atividade visual/contexto não equivalem a diagnóstico. Use grupos por colmeia para avaliar generalização.')
    (out/'dataset_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True);p.add_argument('--session');p.add_argument('--copy-audio',action='store_true')
    p.add_argument('--include-bench',action='store_true',help='Somente teste do exportador; treinador rejeita bancada')
    a=p.parse_args()
    try:
        store=FieldStore(read_config()['mysql'])
        report=export(store.records(a.session),a.output,ROOT/'data/live/audio',a.copy_audio,a.include_bench)
        print(json.dumps(report,ensure_ascii=False,indent=2))
    except Exception as exc:raise SystemExit(f'Exportação interrompida: {exc}')
