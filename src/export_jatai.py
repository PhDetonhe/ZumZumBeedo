"""Exporta uma espécie por vez; inspeções ficam separadas dos rótulos do áudio."""
import argparse
import csv
import json
from pathlib import Path

from beeguard_server import read_config
from beeguard_jatai import JataiStore,SPECIES,META,ROOT
from export_field import export


def write_csv(path,rows,fields):
    with Path(path).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)


def export_species(store,out,species,hive=None,copy_audio=False):
    if species not in SPECIES:raise ValueError('Espécie inválida')
    rows=[r for r in store.records() if r['species_id']==species and (not hive or r['hive_id']==hive)]
    if not rows:raise ValueError('Nenhuma gravação corresponde à espécie/colmeia selecionada')
    report=export(rows,out,ROOT/'data/live/audio',copy_audio)
    out=Path(out)
    # Acrescenta a identificação da espécie nos dois CSVs, sem modificar o exportador anterior.
    metadata={r['id']:{k:r.get(k,'') for k in META} for r in rows}
    for filename in ('manifest.csv','training_features.csv'):
        with (out/filename).open(encoding='utf-8',newline='') as f:
            reader=csv.DictReader(f);fields=list(reader.fieldnames);items=list(reader)
        for item in items:item.update(metadata[item['recording_id']])
        write_csv(out/filename,items,fields+META)
    sessions={r['session_id']:r for r in rows}
    write_csv(out/'sessions.csv',sessions.values(),['session_id','apiary_id','hive_id','kind','device_id','mic_model','gain','placement','session_started_at','session_ended_at']+META)
    inspections=[]
    for name in sorted({r['hive_id'] for r in rows}):inspections.extend(store.inspections(name))
    write_csv(out/'inspections.csv',inspections,['id','hive_id','observed_at','condition_observed','observer','observer_role','context_tag','noise_observed','temperature_c','humidity_pct','measurement_location','notes','created_at'])
    report.update(species_id=species,species_identification_pending=species in ('unknown','jatai_unconfirmed'),
                  inspections=len(inspections),health_labels_assigned=False,
                  note='Inspeções são eventos datados, não rótulos automáticos de cada áudio. Não há modelo de saúde de jataí validado.')
    (out/'dataset_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--species',choices=list(SPECIES),required=True)
    p.add_argument('--hive');p.add_argument('--output',required=True);p.add_argument('--copy-audio',action='store_true');a=p.parse_args()
    try:print(json.dumps(export_species(JataiStore(read_config()['mysql']),a.output,a.species,a.hive,a.copy_audio),ensure_ascii=False,indent=2))
    except Exception as exc:raise SystemExit(f'Exportação interrompida: {exc}')
