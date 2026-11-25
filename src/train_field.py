"""Baseline experimental de interferência sonora; validação por colmeia."""
import argparse
import csv
import json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn import __version__ as sklearn_version
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score,f1_score,confusion_matrix
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from audio_pipeline import VERSION
from field_dataset import FEATURES,LABELS,SCHEMA,vector


def train(csv_path,out):
    out=Path(out)
    if out.exists():raise ValueError('Saída já existe; use nova versão')
    with open(csv_path,encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    if len(rows)<20:raise ValueError('Colete e revise pelo menos 20 gravações elegíveis; isso ainda é apenas um piloto')
    if any(r['kind']!='field' or r['training_eligible']!='True' or r['feature_version']!=VERSION for r in rows):
        raise ValueError('CSV precisa conter apenas dados reais elegíveis da versão atual')
    if any(r['sound_label'] not in LABELS[:2] for r in rows):raise ValueError('Rótulos incompatíveis')
    if len({r['recording_id'] for r in rows})!=len(rows) or len({r['audio_sha256'] for r in rows})!=len(rows):
        raise ValueError('Gravações/conteúdo duplicados no CSV')
    X=np.asarray([vector(r) for r in rows]);y=np.asarray([r['sound_label'] for r in rows]);groups=np.asarray([r['hive_group'] for r in rows])
    if len(set(groups))<3:raise ValueError('São necessárias pelo menos 3 colmeias para avaliação separada')
    if set(y)!=set(LABELS[:2]) or min(Counter(y).values())<5:raise ValueError('Revise ao menos 5 gravações de cada classe')
    def model():return make_pipeline(StandardScaler(),LogisticRegression(C=1,class_weight='balanced',max_iter=1000,random_state=42))
    predictions=np.empty(len(y),dtype=object);baseline=np.empty(len(y),dtype=object);folds=[]
    for tr,te in LeaveOneGroupOut().split(X,y,groups):
        if len(set(y[tr]))<2:raise ValueError('Uma classe só aparece em uma colmeia. Colete essa classe em outras colmeias antes de treinar.')
        fitted=model().fit(X[tr],y[tr]);dummy=DummyClassifier(strategy='most_frequent').fit(X[tr],y[tr])
        predictions[te]=fitted.predict(X[te]);baseline[te]=dummy.predict(X[te])
        folds.append(dict(held_out_hive=str(groups[te][0]),recordings=len(te),
                          macro_f1=f1_score(y[te],predictions[te],labels=list(LABELS[:2]),average='macro',zero_division=0),
                          baseline_macro_f1=f1_score(y[te],baseline[te],labels=list(LABELS[:2]),average='macro',zero_division=0)))
    model_f1=f1_score(y,predictions,labels=list(LABELS[:2]),average='macro',zero_division=0)
    baseline_f1=f1_score(y,baseline,labels=list(LABELS[:2]),average='macro',zero_division=0)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    report=dict(stage='experimental_field_sound',target='sound_label',feature_version=VERSION,
                recordings=len(rows),hives=len(set(groups)),sessions=len({r['session_id'] for r in rows}),
                class_counts=dict(Counter(y)),evaluation='leave_one_hive_out',
                macro_f1=model_f1,baseline_macro_f1=baseline_f1,
                balanced_accuracy=balanced_accuracy_score(y,predictions),
                confusion_matrix=confusion_matrix(y,predictions,labels=list(LABELS[:2])).tolist(),class_order=list(LABELS[:2]),
                macro_hive_f1=float(np.mean([f['macro_f1'] for f in folds])),
                baseline_macro_hive_f1=float(np.mean([f['baseline_macro_f1'] for f in folds])),folds=folds,
                beats_baseline_on_both=bool(model_f1>baseline_f1 and np.mean([f['macro_f1'] for f in folds])>np.mean([f['baseline_macro_f1'] for f in folds])),
                sklearn_version=sklearn_version,model_version=stamp,
                limitations=['Rótulos humanos do som; não diagnóstico de colmeia.',
                             'Colmeias separadas podem compartilhar clima, apiário e dispositivo.',
                             'Janelas próximas no tempo são correlacionadas; quantidade de gravações não equivale a independência.',
                             'Sem teste final prospectivo independente; não há calibração de confiança.',
                             'Métricas da validação cruzada; modelo final usa todos os dados.'])
    out.mkdir(parents=True)
    bundle=dict(schema=SCHEMA,target='sound_label',features=FEATURES,feature_version=VERSION,
                model_version=stamp,model=model().fit(X,y),training_report=report)
    joblib.dump(bundle,out/'sound_model.joblib')
    (out/'training_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    with (out/'oof_predictions.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['recording_id','hive_group','truth','prediction','baseline']);w.writeheader()
        for i,r in enumerate(rows):w.writerow(dict(recording_id=r['recording_id'],hive_group=r['hive_group'],truth=y[i],prediction=predictions[i],baseline=baseline[i]))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    try:print(json.dumps(train(a.dataset,a.output),indent=2,ensure_ascii=False))
    except Exception as exc:raise SystemExit(f'Treinamento interrompido: {exc}')
