"""Auditoria e comparação exploratória, sem substituir modelos existentes.

Compara baseline, RF por janela, RF por gravação e Ridge alpha=10.
Parâmetros fixos; avaliação LeaveOneHiveOut, sem escolha automática de vencedor.
"""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
import sklearn
from sklearn.base import clone
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from train_population import FEATURES, PARAMS, load_dataset, weights


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def audit(data, oof, output):
    output=Path(output)
    if output.exists():raise ValueError('Saída já existe; use outro nome')
    rows,X,y,hives,recs=load_dataset(data)
    ids=sorted(set(recs))
    grouped_X=np.asarray([X[recs==r].mean(axis=0) for r in ids])
    grouped_y=np.asarray([y[recs==r][0] for r in ids])
    grouped_hives=np.asarray([hives[recs==r][0] for r in ids])
    configurations=[('rf_windows',RandomForestRegressor(**PARAMS)),
                    ('rf_recording_mean',RandomForestRegressor(**PARAMS)),
                    ('ridge_recording_alpha10',make_pipeline(StandardScaler(),Ridge(alpha=10)))]
    result_rows=[]
    predictions={}
    baseline_predictions=np.zeros(len(ids))
    fold_rows=[]
    split_rows=[]
    for name,template in configurations:
        pred=np.zeros(len(ids))
        for train_idx,test_idx in LeaveOneGroupOut().split(grouped_X,grouped_y,groups=grouped_hives):
            train_hives=set(grouped_hives[train_idx]);test_hives=set(grouped_hives[test_idx])
            if train_hives & test_hives:raise ValueError('Vazamento de colmeia')
            baseline_predictions[test_idx]=grouped_y[train_idx].mean()
            model=clone(template)
            if name=='rf_windows':
                train_mask=np.isin(recs,np.asarray(ids)[train_idx])
                model.fit(X[train_mask],y[train_mask],sample_weight=weights(recs[train_mask]))
                for j in test_idx:pred[j]=model.predict(X[recs==ids[j]]).mean()
            else:
                model.fit(grouped_X[train_idx],grouped_y[train_idx])
                pred[test_idx]=model.predict(grouped_X[test_idx])
            fold_rows.append({'method':name,'held_out_hive':str(grouped_hives[test_idx][0]),
                'recordings':len(test_idx),'mae_frames':float(np.abs(pred[test_idx]-grouped_y[test_idx]).mean())})
            if name=='rf_windows':
                split_rows.append({'test_hive':str(grouped_hives[test_idx][0]),'train_hives':sorted(train_hives),
                    'test_recordings':[ids[j] for j in test_idx],'train_recordings':[ids[j] for j in train_idx]})
        predictions[name]=pred
    predictions['constant_baseline']=baseline_predictions
    for name,pred in predictions.items():
        errors=np.abs(pred-grouped_y)
        macro=np.mean([errors[grouped_hives==h].mean() for h in sorted(set(grouped_hives))])
        result_rows.append({'method':name,'mae_frames':float(errors.mean()),
            'macro_hive_mae_frames':float(macro),'rmse_frames':float(np.sqrt(((pred-grouped_y)**2).mean()))})
    with Path(oof).open(encoding='utf-8-sig',newline='') as f:
        original=list(csv.DictReader(f))
    lookup={r['recording_group']:r for r in original}
    if len(lookup)!=len(original) or set(lookup)!=set(ids):raise ValueError('OOF não corresponde ao conjunto de gravações')
    for j,rec in enumerate(ids):
        if abs(float(lookup[rec]['target_frames_derived'])-grouped_y[j])>1e-8:
            raise ValueError('Alvo OOF não corresponde ao alvo das features')
        if lookup[rec]['hive_id']!=grouped_hives[j]:raise ValueError('Colmeia OOF inconsistente')
    delta=max(abs(float(lookup[rec]['predicted_frames'])-predictions['rf_windows'][j]) for j,rec in enumerate(ids))
    summaries=[]
    for hive in sorted(set(grouped_hives)):
        mask=grouped_hives==hive
        recordings=[rows[np.flatnonzero(recs==ids[j])[0]] for j in np.flatnonzero(mask)]
        summaries.append({'hive_id':str(hive),'recordings':int(mask.sum()),
            'target_min':float(grouped_y[mask].min()),'target_max':float(grouped_y[mask].max()),
            'rms_recording_mean_median':float(np.median(grouped_X[mask,FEATURES.index('rms')])),
            'recording_dates':[r['timestamp'] for r in recordings]})
    interval_ids={r.get('left_inspection_id','')+'|'+r.get('right_inspection_id','') for r in rows}
    report={'recordings':len(ids),'windows':len(rows),'hives':len(set(grouped_hives)),
        'feature_count':len(FEATURES),'feature_values_finite':bool(np.isfinite(X).all()),
        'duplicates_audio_id':0,'oof_targets_consistent':True,'original_rf_max_prediction_difference':delta,
        'matches_original_rf_with_tolerance_1e_minus8':bool(delta<1e-8),
        'label_types':dict(Counter(rows[np.flatnonzero(recs==r)[0]]['label_type'] for r in ids)),
        'distinct_inspection_anchor_pairs':len(interval_ids),
        'comparisons':result_rows,'by_hive':summaries,'sklearn_version':sklearn.__version__,
        'data_sha256':file_hash(data),'oof_sha256':file_hash(oof),
        'evaluation':'LeaveOneHiveOut; métrica por gravação, features acústicas apenas; escalonamento Ridge ajustado somente no treino.',
        'status':'exploratory_not_validated',
        'limitations':['Comparações realizadas após observar o resultado do primeiro piloto; não são teste final independente.',
            'Rótulos interpolados e fuso pendente; resultados não medem precisão contra população real contemporânea.',
            'Poucas gravações por colmeia e várias ligadas ao mesmo par de inspeções.',
            'Todos os resultados são registrados; nenhum modelo é promovido ou sobrescrito automaticamente.',
            'RMS diferente entre colmeias não permite distinguir ganho de microfone, população e contexto sem dados adicionais.']}
    output.mkdir(parents=True)
    (output/'audit_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    (output/'cv_splits.json').write_text(json.dumps(split_rows,indent=2),encoding='utf-8')
    for filename,values in [('comparisons.csv',result_rows),('fold_metrics.csv',fold_rows)]:
        with (output/filename).open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(values[0]));w.writeheader();w.writerows(values)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',default='data/metadata/urban_population_v1/population_features.csv')
    p.add_argument('--oof',default='models/population_v1/oof_recording_predictions.csv')
    p.add_argument('--output',default='reports/population_audit_v1')
    a=p.parse_args()
    try:
        result=audit(a.data,a.oof,a.output)
        print(json.dumps({k:result[k] for k in ['recordings','windows','hives','oof_targets_consistent',
             'original_rf_max_prediction_difference','comparisons','status']},indent=2,ensure_ascii=False))
    except (ValueError,KeyError,OSError) as exc:p.exit(1,f'ERRO: {exc}\n')
