"""Contrato dos dados de campo e dos modelos BeeGuard."""
import math
import re
from datetime import datetime, timezone
from pathlib import Path

FEATURES = ['rms','zero_crossing_rate','dominant_frequency_hz','band_122_515_ratio']
FEATURES += [name+'_'+stat for name in ['centroid_hz','spread_hz','entropy','rolloff_hz','flatness'] for stat in ['mean','std']]
FEATURES += [f'mfcc_{i:02}_{stat}' for i in range(13) for stat in ['mean','std']]
LABELS = ('clean_bee_sound','external_interference','uncertain')
ACTIVITIES = ('high','usual','low','not_observed')
CONTEXTS = ('construction','traffic','weather','handling','other','none','unknown')
ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
SCHEMA = 'beeguard-field-1'


def utc(value):
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def text(data,key,maxlen,required=True):
    value=data.get(key,'')
    if not isinstance(value,str) or len(value.strip())>maxlen or (required and not value.strip()):
        raise ValueError(f'{key}: informe texto de até {maxlen} caracteres')
    return value.strip()


def checked_path(root,name):
    root=Path(root).resolve();path=(root/name).resolve()
    if path.parent!=root or path.suffix.lower()!='.wav':raise ValueError('Caminho de áudio inválido')
    return path


def vector(features):
    result=[float(features[k]) for k in FEATURES]
    if not all(math.isfinite(v) for v in result):raise ValueError('Features não finitas')
    return result


def check_session(session,hive,captured):
    captured=utc(captured)
    if session['hive_id']!=hive:raise ValueError('Sessão e gravação pertencem a colmeias diferentes')
    if captured<utc(session['started_at']):raise ValueError('Gravação anterior ao início da sessão')
    if session.get('ended_at') and captured.timestamp()+10>utc(session['ended_at']).timestamp():
        raise ValueError('Gravação fora do período completo da sessão')
