"""BeeGuard: piloto Random Forest de população, avaliação por colmeia/gravação.

Treino: python train_population.py
Inferência: python train_population.py --audio caminho.wav
Não realiza diagnóstico e não estima probabilidade de saúde.
"""
import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import LeaveOneGroupOut

from audio_pipeline import VERSION, RATE, WINDOW, load_audio, quality, features

FEATURES = ['rms','zero_crossing_rate','dominant_frequency_hz','band_122_515_ratio']
FEATURES += [name+'_'+stat for name in ['centroid_hz','spread_hz','entropy','rolloff_hz','flatness'] for stat in ['mean','std']]
FEATURES += [f'mfcc_{i:02}_{stat}' for i in range(13) for stat in ['mean','std']]
PARAMS = dict(n_estimators=200, max_depth=6, min_samples_leaf=2, random_state=42, n_jobs=1)


def load_dataset(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        fields=reader.fieldnames or []
        required=set(FEATURES+['frames_bees','hive_id','recording_group','feature_version','label_type','audio_id'])
        if not required.issubset(fields):
            raise ValueError('Faltam colunas: '+', '.join(sorted(required-set(fields))))
        rows=list(reader)
    if not rows:
        raise ValueError('Dataset sem exemplos rotulados')
    seen=set()
    signatures={}
    for row in rows:
        if row['audio_id'] in seen:
            raise ValueError('audio_id duplicado; não contar a mesma janela duas vezes')
        seen.add(row['audio_id'])
        if row['feature_version'] != VERSION:
            raise ValueError('Versão de features incompatível com audio_pipeline.py')
        if not row['label_type'].startswith('derived_'):
            raise ValueError('Este piloto espera rótulos derivados rastreáveis')
        if not row['hive_id'] or not row['recording_group']:
            raise ValueError('Colmeia/gravação sem identidade')
        values=[float(row[c]) for c in FEATURES+['frames_bees']]
        if not all(math.isfinite(v) for v in values) or values[-1]<0:
            raise ValueError('Features ou população inválidas')
        signature=(row['hive_id'],values[-1])
        if row['recording_group'] in signatures and signatures[row['recording_group']]!=signature:
            raise ValueError('Uma gravação possui colmeias/rótulos inconsistentes')
        signatures[row['recording_group']]=signature
    if len(signatures)<10 or len({s[0] for s in signatures.values()})<3:
        raise ValueError('Piloto requer pelo menos 10 gravações e três colmeias')
    X=np.asarray([[float(r[c]) for c in FEATURES] for r in rows])
    y=np.asarray([float(r['frames_bees']) for r in rows])
    hives=np.asarray([r['hive_id'] for r in rows])
    recs=np.asarray([r['recording_group'] for r in rows])
    return rows,X,y,hives,recs


def weights(recs):
    count=Counter(recs)
    # Total de peso = 1 por gravação, independente da quantidade de janelas.
    return np.asarray([1/count[r] for r in recs])


def score(records):
    truth=np.asarray([r['target_frames_derived'] for r in records])
    pred=np.asarray([r['predicted_frames'] for r in records])
    base=np.asarray([r['baseline_frames'] for r in records])
    return {'recordings':len(records),'rf_mae_frames':float(np.abs(pred-truth).mean()),
        'rf_rmse_frames':float(np.sqrt(((pred-truth)**2).mean())),
        'baseline_mae_frames':float(np.abs(base-truth).mean()),
        'baseline_rmse_frames':float(np.sqrt(((base-truth)**2).mean()))}


def train(data, output):
    output=Path(output)
    if output.exists():
        raise ValueError('Pasta de saída já existe; use outro nome')
    rows,X,y,hives,recs=load_dataset(data)
    predictions=[]
    folds=[]
    splits=[]
    for train_idx,test_idx in LeaveOneGroupOut().split(X,y,groups=hives):
        assert not set(hives[train_idx]) & set(hives[test_idx])
        assert not set(recs[train_idx]) & set(recs[test_idx])
        model=RandomForestRegressor(**PARAMS)
        model.fit(X[train_idx],y[train_idx],sample_weight=weights(recs[train_idx]))
        inferred=model.predict(X[test_idx])
        # Baseline média dos rótulos por gravação do treino; não usa o teste.
        train_rec_values={r:float(value) for r,value in zip(recs[train_idx],y[train_idx])}
        baseline=float(np.mean(list(train_rec_values.values())))
        records=[]
        for rec in sorted(set(recs[test_idx])):
            mask=recs[test_idx]==rec
            first=int(test_idx[np.flatnonzero(mask)[0]])
            records.append({'recording_group':rec,'hive_id':str(hives[first]),
                'source_file':rows[first].get('source_file',''),
                'target_frames_derived':float(y[first]),
                'predicted_frames':float(inferred[mask].mean()),'baseline_frames':baseline,
                'windows':int(mask.sum())})
        held_hive=str(hives[test_idx][0])
        folds.append({'held_out_hive':held_hive, **score(records)})
        splits.append({'held_out_hive':held_hive,'train_hives':sorted(set(hives[train_idx])),
            'train_recordings':len(set(recs[train_idx])),'test_recordings':len(records)})
        predictions.extend(records)
    metrics=score(predictions)
    macro_rf=float(np.mean([f['rf_mae_frames'] for f in folds]))
    macro_base=float(np.mean([f['baseline_mae_frames'] for f in folds]))
    report={'stage':'experimental_population_pilot','evaluation':'leave_one_hive_out; médias de previsão por gravação',
        'feature_version':VERSION,'feature_count':len(FEATURES),
        'windows':len(rows),'independent_recording_groups':len(set(recs)),
        'hives':len(set(hives)),'recordings_by_hive':dict(Counter(r['hive_id'] for r in predictions)),
        **metrics,'macro_hive_rf_mae_frames':macro_rf,'macro_hive_baseline_mae_frames':macro_base,
        'beats_baseline_on_both_mae':bool(metrics['rf_mae_frames']<metrics['baseline_mae_frames'] and macro_rf<macro_base),
        'folds':folds,'forest_parameters':PARAMS,'sklearn_version':sklearn.__version__,
        'limitations':['Avaliação contra rótulos derivados, não contra inspeções diretas do instante do áudio.',
            'Colmeias são separadas, mas clima e eventos no mesmo apiário podem ser compartilhados.',
            'Não há teste final independente; esta validação cruzada é exploratória.',
            '29 gravações (na base atual) são poucas; 348 janelas não equivalem a 348 gravações independentes.',
            'Fuso continua pendente; não há calibração de confiança nem diagnóstico.',
            'Modelo final usa todos os dados; as métricas vêm de modelos distintos por fold.']}
    final=RandomForestRegressor(**PARAMS)
    final.fit(X,y,sample_weight=weights(recs))
    bundle={'model':final,'features':FEATURES,'feature_version':VERSION,
        'sample_rate':RATE,'window_samples':WINDOW,'sklearn_version':sklearn.__version__,
        'stage':'experimental_population_pilot','target_range':[float(y.min()),float(y.max())],
        'training_recordings':len(set(recs)),'training_hives':sorted(set(hives))}
    output.mkdir(parents=True)
    joblib.dump(bundle,output/'population_rf.joblib')
    (output/'training_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    (output/'cv_splits.json').write_text(json.dumps(splits,indent=2),encoding='utf-8')
    with (output/'oof_recording_predictions.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(predictions[0]))
        writer.writeheader()
        writer.writerows(predictions)
    return report


def predict(audio, model_path):
    bundle=joblib.load(model_path)
    if bundle['feature_version']!=VERSION or bundle['features']!=FEATURES or bundle['window_samples']!=WINDOW:
        raise ValueError('Modelo incompatível com extrator atual')
    x,source=load_audio(audio)
    if len(x)<WINDOW:
        raise ValueError('Áudio precisa ter pelo menos 10 s')
    n=min(12,len(x)//WINDOW)
    windows=[]
    rejected=[]
    for i in range(n):
        chunk=x[i*WINDOW:(i+1)*WINDOW]
        qc=quality(chunk)
        if qc['quality_status']!='pass' or source['source_clipping_ratio']>0.01:
            rejected.append({'window':i,'reason':qc['quality_reasons'] or 'source_clipping'})
            continue
        # Mesma quantização PCM16 utilizada por prepare_dataset.py.
        pcm=np.round(np.clip(chunk,-1,32767/32768)*32768).astype(np.int16)
        desc=features(pcm.astype(np.float64)/32768)
        windows.append([desc[name] for name in FEATURES])
    if not windows:
        raise ValueError('Nenhuma janela passou nas verificações técnicas')
    values=bundle['model'].predict(np.asarray(windows))
    return {'audio':str(audio),'stage':bundle['stage'],
        'estimated_frames_experimental':float(values.mean()),
        'window_estimates':[float(v) for v in values], 'accepted_windows':len(windows),
        'rejected_windows':rejected,'target_range_training':bundle['target_range'],
        'note':'Estimativa exploratória de quadros de abelhas. Não é diagnóstico, porcentagem de saúde ou confiança calibrada. Dados locais podem diferir do UrBAN.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',default='data/metadata/urban_population_v1/population_features.csv')
    p.add_argument('--output',default='models/population_v1')
    p.add_argument('--audio',help='Se informado, executa inferência em vez de treinamento')
    p.add_argument('--model',default='models/population_v1/population_rf.joblib')
    a=p.parse_args()
    try:
        result=predict(a.audio,a.model) if a.audio else train(a.data,a.output)
        print(json.dumps(result,indent=2,ensure_ascii=False))
    except (ValueError,KeyError,OSError) as exc:
        p.exit(1,f'ERRO: {exc}\n')
