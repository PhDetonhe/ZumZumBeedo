"""Painel e regras acústicas experimentais. Usa o servidor/coletor existentes."""
import argparse
import json
import math
import re
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import jsonify, request, send_file
from beeguard_server import MySQLStore, create_app, read_config, serialize

ROOT = Path(__file__).resolve().parents[1]
HIVE = re.compile(r'[A-Za-z0-9_-]{1,64}')


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def usable(row):
    q = row.get('quality', {})
    rms = q.get('rms', row.get('features', {}).get('rms'))
    return (isinstance(rms, (float, int)) and math.isfinite(rms) and rms >= 0
            and q.get('clipping_ratio', 0) <= 0.01)


def minute_series(rows):
    groups = {}
    for r in rows:
        if not usable(r):
            continue
        t = utc(r['captured_at']).replace(second=0, microsecond=0)
        groups.setdefault(t, []).append(r['quality']['rms'])
    return [{'at': t.isoformat(), 'rms': statistics.median(values), 'samples': len(values)}
            for t, values in sorted(groups.items())]


def calibrate(rows, now):
    """Referência só de 30 min recentes com cobertura e sinal utilizável."""
    rows = [r for r in rows if now - timedelta(minutes=30) <= utc(r['captured_at']) <= now]
    series = minute_series(rows)
    if len(series) < 25 or sum(p['samples'] for p in series) < 120:
        raise ValueError('Colete 30 minutos de referência: ao menos 25 minutos cobertos e 120 gravações sem clipping.')
    times = [utc(r['captured_at']) for r in rows if usable(r)]
    times.sort()
    if now - times[-1] > timedelta(seconds=60) or any(b-a > timedelta(seconds=60) for a,b in zip(times,times[1:])):
        raise ValueError('Referência com interrupções maiores que 60 segundos. Verifique a captura e tente novamente.')
    base = statistics.median(p['rms'] for p in series)
    if base < 1e-4:
        raise ValueError('Referência quase silenciosa. Verifique microfone/colmeia antes de calibrar.')
    return base


def assess(rows, reference, now=None):
    now = now or datetime.now(timezone.utc)
    rows = [r for r in rows if utc(r['captured_at']) <= now]
    series = minute_series(rows)
    result = {'status': 'learning', 'message': 'Defina uma referência durante um período observado como habitual.',
              'series': series, 'reference': reference, 'delta_db': None,
              'rule_version': 'acoustic-rules-1', 'model': 'not_enabled'}
    if not rows or now - max(utc(r['captured_at']) for r in rows) > timedelta(seconds=60):
        result.update(status='offline', message='Sem áudio recente. Verifique USB, coletor e alimentação; não indica silêncio da colmeia.')
        return result
    recent = [r for r in rows if now-timedelta(minutes=5) <= utc(r['captured_at']) <= now]
    valid = [r for r in recent if usable(r)]
    if not valid or len(valid) < 0.8 * len(recent):
        result.update(status='quality', message='Sinal saturado ou inválido. Verifique posicionamento e ganho do microfone.')
        return result
    current = statistics.median(r['quality']['rms'] for r in valid)
    result['current_rms'] = current
    if not reference:
        return result
    base = float(reference['rms'])
    db = 20*math.log10(max(current, 1e-8)/base)
    result['delta_db'] = db
    # Persistência exige 30 minutos completos, cobertura >=80% e nenhum intervalo >60s.
    window = [r for r in rows if now-timedelta(minutes=30) <= utc(r['captured_at']) <= now]
    good = sorted([r for r in window if usable(r)], key=lambda r: utc(r['captured_at']))
    points = minute_series(good)
    after_ref = utc(reference['created_at']) <= now-timedelta(minutes=30)
    continuous = (bool(good) and len(points) >= 30 and len(good) >= 120
                  and len(good) >= 0.8*len(window)
                  and now-utc(good[-1]['captured_at']) <= timedelta(seconds=60)
                  and all(utc(b['captured_at'])-utc(a['captured_at']) <= timedelta(seconds=60)
                          for a,b in zip(good,good[1:])))
    low = continuous and after_ref and all(p['rms'] <= base*10**(-6/20) for p in points)
    if low:
        result.update(status='low', message='Intensidade reduzida por 30 min. Inspecione a colmeia e o sensor; abandono não confirmado.')
    elif db >= 6:
        result.update(status='high', message='Intensidade acima da referência. Investigue ruído externo, manejo e atividade das abelhas.')
    elif db <= -6:
        result.update(status='lower', message='Intensidade abaixo da referência. Acompanhando duração e continuidade.')
    else:
        result.update(status='usual', message='Intensidade próxima da referência escolhida. Não equivale a diagnóstico de saúde.')
    return result


class MonitorStore(MySQLStore):
    def query(self, sql, args=(), write=False):
        conn = self.connect()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(sql, args)
            rows = [] if write else cur.fetchall()
            if write:
                conn.commit()
            cur.close()
            return rows
        finally:
            conn.close()

    def init_monitor(self):
        self.query('''CREATE TABLE IF NOT EXISTS beeguard_observations (
            id CHAR(36) PRIMARY KEY, hive_id VARCHAR(64) NOT NULL,
            observed_at DATETIME(6) NOT NULL, category VARCHAR(32) NOT NULL,
            activity VARCHAR(32) NOT NULL, notes TEXT NOT NULL,
            created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            INDEX idx_obs_hive_time (hive_id, observed_at)
        ) ENGINE=InnoDB''', write=True)
        self.query('''CREATE TABLE IF NOT EXISTS beeguard_references (
            id CHAR(36) PRIMARY KEY, hive_id VARCHAR(64) NOT NULL,
            rms DOUBLE NOT NULL, created_at DATETIME(6) NOT NULL,
            INDEX idx_ref_hive_time (hive_id, created_at)
        ) ENGINE=InnoDB''', write=True)

    def hives(self):
        return [r['hive_id'] for r in self.query('SELECT DISTINCT hive_id FROM beeguard_recordings ORDER BY hive_id')]

    def history(self, hive, start, end):
        return [serialize(r) for r in self.query('''SELECT captured_at, quality_json, features_json,
            quality_status FROM beeguard_recordings WHERE hive_id=%s AND captured_at BETWEEN %s AND %s
            ORDER BY captured_at''', (hive, start.replace(tzinfo=None), end.replace(tzinfo=None)))]

    def reference(self, hive):
        rows = self.query('SELECT rms,created_at FROM beeguard_references WHERE hive_id=%s ORDER BY created_at DESC LIMIT 1', (hive,))
        if rows:
            rows[0]['created_at'] = utc(rows[0]['created_at']).isoformat()
        return rows[0] if rows else None

    def save_reference(self, hive, rms, now):
        self.query('INSERT INTO beeguard_references (id,hive_id,rms,created_at) VALUES (%s,%s,%s,%s)',
                   (str(uuid.uuid4()), hive, rms, now.replace(tzinfo=None)), write=True)

    def observations(self, hive, start=None, end=None):
        sql = 'SELECT * FROM beeguard_observations WHERE hive_id=%s'
        args = [hive]
        if start is not None:
            sql += ' AND observed_at BETWEEN %s AND %s'
            args.extend([start.replace(tzinfo=None), end.replace(tzinfo=None)])
        rows = self.query(sql+' ORDER BY observed_at DESC LIMIT 100', tuple(args))
        for r in rows:
            for k in ('observed_at','created_at'):
                r[k] = utc(r[k]).isoformat()
        return rows

    def save_observation(self, row):
        self.query('''INSERT INTO beeguard_observations
            (id,hive_id,observed_at,category,activity,notes) VALUES (%s,%s,%s,%s,%s,%s)''',
            (row['id'],row['hive_id'],row['observed_at'],row['category'],row['activity'],row['notes']), write=True)


def install_monitor(app, store):
    def hive_arg():
        hive = request.args.get('hive_id', 'BG01')
        if not HIVE.fullmatch(hive):
            raise ValueError('Colmeia inválida')
        return hive

    def period():
        raw = request.args.get('date')
        if raw:
            # Dia local São Paulo, UTC-3; data atual/histórica pode ser explorada.
            start = datetime.strptime(raw, '%Y-%m-%d').replace(tzinfo=timezone(timedelta(hours=-3))).astimezone(timezone.utc)
            return start, start+timedelta(days=1)
        end = datetime.now(timezone.utc)
        return end-timedelta(hours=24), end

    app.view_functions['index'] = lambda: send_file(ROOT/'web/templates/beeguard_dashboard.html')

    @app.get('/api/hives')
    def hives():
        try:
            return jsonify(hives=store.hives())
        except Exception:
            return jsonify(error='MySQL indisponível'), 503

    @app.get('/api/monitor')
    def monitor():
        try:
            hive = hive_arg(); start,end = period()
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        try:
            now = datetime.now(timezone.utc)
            rows = store.history(hive,start,end)
            live_rows = rows if not request.args.get('date') else store.history(hive,now-timedelta(minutes=31),now)
            state = assess(live_rows,store.reference(hive),now)
            state['series'] = minute_series(rows)
            state['observations'] = store.observations(hive,start,end)
            state['hive_id'] = hive
            state['recordings'] = len(rows)
            state['review_recordings'] = sum(r.get('quality_status') != 'pass' for r in rows)
            state['period_start'] = start.isoformat(); state['period_end'] = end.isoformat()
            return jsonify(state)
        except Exception:
            app.logger.exception('Consulta do monitor')
            return jsonify(error='Não foi possível consultar o monitor/MySQL'), 503

    @app.post('/api/reference')
    def reference():
        try:
            hive = hive_arg()
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        try:
            now = datetime.now(timezone.utc)
            rms = calibrate(store.history(hive,now-timedelta(minutes=30),now),now)
            store.save_reference(hive,rms,now)
            return jsonify(rms=rms,created_at=now.isoformat()), 201
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception:
            return jsonify(error='Não foi possível salvar a referência/MySQL'), 503

    @app.post('/api/observations')
    def observation():
        try:
            data = request.get_json(silent=True)
            if not isinstance(data,dict):
                raise ValueError('Envie um objeto JSON')
            hive = data.get('hive_id','')
            if not isinstance(hive,str) or not HIVE.fullmatch(hive):
                raise ValueError('Colmeia inválida')
            category = data.get('category')
            if category not in ('obra','manejo','clima','inspecao','outro'):
                raise ValueError('Categoria inválida')
            activity = data.get('activity')
            if activity not in ('alta','habitual','baixa','nao_observada'):
                raise ValueError('Atividade inválida')
            notes = data.get('notes','')
            if not isinstance(notes,str) or not 1 <= len(notes.strip()) <= 2000:
                raise ValueError('Descreva a observação em até 2000 caracteres')
            raw = data.get('observed_at')
            if not isinstance(raw,str):
                raise ValueError('Horário inválido')
            timestamp = datetime.fromisoformat(raw.replace('Z','+00:00'))
            if timestamp.tzinfo is None:
                raise ValueError('Horário precisa de fuso')
            timestamp = utc(timestamp)
            if timestamp > datetime.now(timezone.utc)+timedelta(minutes=5):
                raise ValueError('Observação não pode estar no futuro')
            row = dict(id=str(uuid.uuid4()),hive_id=hive,category=category,activity=activity,
                       notes=notes.strip(),observed_at=timestamp.replace(tzinfo=None))
        except (ValueError,TypeError) as exc:
            return jsonify(error=str(exc)), 400
        try:
            store.save_observation(row)
            return jsonify(id=row['id'],status='stored'), 201
        except Exception:
            return jsonify(error='Não foi possível salvar a observação/MySQL'), 503

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init-db',action='store_true')
    args = parser.parse_args()
    try:
        store = MonitorStore(read_config()['mysql'])
        if args.init_db:
            store.init(); store.init_monitor()
            print('Tabelas de áudio, observações e referências prontas.'); raise SystemExit(0)
        store.healthy()
        app = install_monitor(create_app(store,ROOT/'data/live/audio'),store)
        from waitress import serve
        print('BeeGuard Monitor disponível em http://127.0.0.1:5000',flush=True)
        serve(app,host='127.0.0.1',port=5000,threads=4)
    except Exception as exc:
        raise SystemExit(f'Não foi possível iniciar o monitor/MySQL: {exc}')
