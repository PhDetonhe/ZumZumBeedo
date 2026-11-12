"""Web monitoring: durable cycles, acoustic comparisons and observation labels."""
import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import uuid
import wave
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from flask import Flask, Response, jsonify, request, send_file
from scipy.signal import stft
from werkzeug.exceptions import HTTPException
from audio_pipeline import RATE, WINDOW, features, quality

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}")
LABELS = {"healthy", "sick", "uncertain"}
RULE_VERSION = "acoustic-comparison-1"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Informe data e hora com fuso horário.")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("A data precisa incluir o fuso horário.")
    if result > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("O horário não pode estar no futuro.")
    return result.astimezone(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Envie um objeto JSON.")
    return data


def text_value(data, key, maximum=120, required=True):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValueError(f"Campo {key}: informe um texto de até {maximum} caracteres.")
    return value.strip()


def number(data, key, default, low, high, integer=False):
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"Campo {key}: número inválido.")
    if not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f"Campo {key}: use um valor entre {low} e {high}.")
    return int(value) if integer else float(value)


class DomainError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class Store:
    """The same transactional schema on local SQLite and hosted PostgreSQL."""
    def __init__(self, database_url=None, sqlite_path=None, cloud=None):
        self.url = database_url if database_url is not None else os.environ.get("DATABASE_URL", "")
        self.path = Path(sqlite_path or os.environ.get("BEEGUARD_DB_PATH", ROOT / "data/beeguard.sqlite3"))
        self.cloud = bool(os.environ.get("VERCEL")) if cloud is None else cloud
        self.ready = False
        self.lock = threading.Lock()

    @property
    def backend(self):
        return "postgresql" if self.url else "sqlite"

    def connect(self):
        if self.url:
            import psycopg
            from psycopg.rows import dict_row
            return psycopg.connect(self.url, row_factory=dict_row, connect_timeout=8)
        if self.cloud:
            raise RuntimeError("Configure DATABASE_URL com um PostgreSQL persistente para usar a Vercel.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path), timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=15000")
        return con

    def sql(self, con, query, params=()):
        return con.execute(query.replace("?", "%s") if self.url else query, params)

    def one(self, con, query, params=()):
        row = self.sql(con, query, params).fetchone()
        return dict(row) if row else None

    def all(self, con, query, params=()):
        return [dict(row) for row in self.sql(con, query, params).fetchall()]

    def initialize(self):
        with self.lock:
            if self.ready:
                return
            con = self.connect()
            try:
                if self.url:
                    self.sql(con, "SELECT pg_advisory_xact_lock(72639211)")
                blob = "BYTEA" if self.url else "BLOB"
                schema = [
                    """CREATE TABLE IF NOT EXISTS bg_hives (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL,
                        species TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL)""",
                    """CREATE TABLE IF NOT EXISTS bg_cycles (
                        id TEXT PRIMARY KEY, hive_id TEXT NOT NULL REFERENCES bg_hives(id),
                        name TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
                        calibration_samples INTEGER NOT NULL, threshold DOUBLE PRECISION NOT NULL,
                        consecutive_samples INTEGER NOT NULL, baseline_json TEXT,
                        baseline_count INTEGER NOT NULL DEFAULT 0, baseline_until TEXT,
                        anomaly_streak INTEGER NOT NULL DEFAULT 0,
                        episode_open INTEGER NOT NULL DEFAULT 0, last_evaluated_at TEXT)""",
                    "CREATE UNIQUE INDEX IF NOT EXISTS bg_one_active_cycle ON bg_cycles(hive_id) WHERE ended_at IS NULL",
                    f"""CREATE TABLE IF NOT EXISTS bg_samples (
                        id TEXT PRIMARY KEY, hive_id TEXT NOT NULL REFERENCES bg_hives(id),
                        cycle_id TEXT NOT NULL REFERENCES bg_cycles(id), captured_at TEXT NOT NULL,
                        received_at TEXT NOT NULL, sha256 TEXT NOT NULL, audio {blob} NOT NULL,
                        quality_json TEXT NOT NULL, features_json TEXT NOT NULL,
                        comparison_json TEXT NOT NULL, status TEXT NOT NULL)""",
                    "CREATE INDEX IF NOT EXISTS bg_cycle_time ON bg_samples(cycle_id, captured_at)",
                    "CREATE INDEX IF NOT EXISTS bg_hive_time ON bg_samples(hive_id, captured_at)",
                    """CREATE TABLE IF NOT EXISTS bg_alerts (
                        id TEXT PRIMARY KEY, hive_id TEXT NOT NULL REFERENCES bg_hives(id),
                        cycle_id TEXT NOT NULL REFERENCES bg_cycles(id),
                        recording_id TEXT NOT NULL REFERENCES bg_samples(id),
                        created_at TEXT NOT NULL, message TEXT NOT NULL,
                        score DOUBLE PRECISION NOT NULL, acknowledged_at TEXT)""",
                    """CREATE TABLE IF NOT EXISTS bg_inspections (
                        id TEXT PRIMARY KEY, hive_id TEXT NOT NULL REFERENCES bg_hives(id),
                        cycle_id TEXT NOT NULL REFERENCES bg_cycles(id), observed_at TEXT NOT NULL,
                        label TEXT NOT NULL, observer TEXT NOT NULL, notes TEXT NOT NULL,
                        created_at TEXT NOT NULL)""",
                ]
                for statement in schema:
                    self.sql(con, statement)
                con.commit()
                self.ready = True
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()

    @contextmanager
    def transaction(self, write=False):
        self.initialize()
        con = self.connect()
        try:
            if write and not self.url:
                con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def hive(self, con, hive_id, lock=False):
        suffix = " FOR UPDATE" if lock and self.url else ""
        row = self.one(con, "SELECT * FROM bg_hives WHERE id=?" + suffix, (hive_id,))
        if not row:
            raise DomainError("Colmeia não encontrada.", 404)
        return row

    def cycle(self, con, cycle_id, lock=False):
        suffix = " FOR UPDATE" if lock and self.url else ""
        row = self.one(con, "SELECT * FROM bg_cycles WHERE id=?" + suffix, (cycle_id,))
        if not row:
            raise DomainError("Ciclo não encontrado.", 404)
        return row


def cycle_json(row):
    if not row:
        return None
    row = dict(row)
    raw = row.pop("baseline_json", None)
    row["baseline"] = json.loads(raw) if raw else None
    row["status"] = "closed" if row["ended_at"] else ("monitoring" if row["baseline"] else "calibrating")
    return row


def sample_json(row):
    row = dict(row)
    row.pop("audio", None)
    for key in ("quality", "features", "comparison"):
        row[key] = json.loads(row.pop(key + "_json"))
    row["audio_url"] = "/api/recordings/" + row["id"] + "/audio"
    return row


def describe(payload):
    try:
        with wave.open(io.BytesIO(payload), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) != (1, 2, RATE, WINDOW):
                raise ValueError("Use WAV PCM16 mono, 16000 Hz, exatamente 10 segundos.")
            pcm = wav.readframes(WINDOW)
            if len(pcm) != WINDOW * 2:
                raise ValueError("O WAV está incompleto.")
    except (wave.Error, EOFError) as exc:
        raise ValueError("Arquivo WAV inválido.") from exc
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768
    desc = features(audio)
    freq, _, spectrum = stft(audio, RATE, nperseg=1024, noverlap=512, boundary=None, padded=False)
    power = (np.abs(spectrum) ** 2).mean(axis=1)
    edges = np.geomspace(40, 8001, 33)
    bands = np.array([power[(freq >= a) & (freq < b)].sum() for a, b in zip(edges, edges[1:])])
    desc["spectrum"] = (bands / max(float(bands.sum()), 1e-20)).tolist()
    return quality(audio), desc


def reference_from(descriptions):
    reference = {key: float(np.median([d[key] for d in descriptions]))
                 for key in descriptions[0] if isinstance(descriptions[0][key], (float, int))}
    spectrum = np.mean([d["spectrum"] for d in descriptions], axis=0)
    reference["spectrum"] = (spectrum / max(float(spectrum.sum()), 1e-20)).tolist()
    reference["feature_version"] = descriptions[0]["feature_version"]
    return reference


def compare(current, reference):
    db = 20 * math.log10(max(current["rms"], 1e-8) / max(reference["rms"], 1e-8))
    p, q = np.asarray(current["spectrum"]), np.asarray(reference["spectrum"])
    spectral = float(np.sqrt(np.sum((np.sqrt(p) - np.sqrt(q)) ** 2) / 2))
    score = 100 * max(min(abs(db) / 12, 1), min(spectral, 1))
    return {"score": round(score, 2), "delta_db": round(db, 3),
            "spectral_distance": round(spectral, 5),
            "frequency_delta_hz": round(current["dominant_frequency_hz"] - reference["dominant_frequency_hz"], 2),
            "rule_version": RULE_VERSION}


def ingest(store, payload, headers):
    try:
        rid = str(uuid.UUID(headers.get("X-Recording-Id", "")))
    except (ValueError, AttributeError) as exc:
        raise ValueError("X-Recording-Id precisa ser um UUID.") from exc
    hive_id = headers.get("X-Hive-Id", "")
    if not IDENTIFIER.fullmatch(hive_id):
        raise ValueError("X-Hive-Id inválido.")
    captured = timestamp(headers.get("X-Captured-At"))
    explicit_cycle = headers.get("X-Cycle-Id") or headers.get("X-Session-Id")
    digest = hashlib.sha256(payload).hexdigest()
    technical, desc = describe(payload)
    received = now_iso()
    with store.transaction(write=True) as con:
        store.hive(con, hive_id, lock=True)
        existing = store.one(con, "SELECT id,hive_id,cycle_id,captured_at,sha256,comparison_json FROM bg_samples WHERE id=?", (rid,))
        if existing:
            if (existing["hive_id"] != hive_id or existing["sha256"] != digest
                    or existing["captured_at"] != captured or (explicit_cycle and explicit_cycle != existing["cycle_id"])):
                raise DomainError("Este UUID já pertence a outra coleta.")
            return {"id": rid, "cycle_id": existing["cycle_id"], "session_id": existing["cycle_id"],
                    "status": "already_stored", "storage": "ok", "comparison": json.loads(existing["comparison_json"])}, 200
        if explicit_cycle:
            cycle = store.cycle(con, explicit_cycle, lock=True)
            if cycle["hive_id"] != hive_id:
                raise DomainError("O ciclo pertence a outra colmeia.")
        else:
            cycle = store.one(con, """SELECT * FROM bg_cycles WHERE hive_id=? AND started_at<=?
                AND (ended_at IS NULL OR ended_at>=?) ORDER BY started_at DESC LIMIT 1""", (hive_id, captured, captured))
            if not cycle:
                raise DomainError("Inicie um ciclo no painel antes de enviar coletas.")
        if captured < cycle["started_at"] or (cycle["ended_at"] and captured > cycle["ended_at"]):
            raise DomainError("O horário da coleta está fora do ciclo informado.")
        baseline = json.loads(cycle["baseline_json"]) if cycle["baseline_json"] else None
        comparison = {"score": None, "rule_version": RULE_VERSION, "previous": None}
        valid = technical["quality_status"] == "pass"
        status = "calibrating" if valid else "quality_review"
        alert_id = None
        if valid and baseline and captured > cycle["baseline_until"]:
            comparison.update(compare(desc, baseline))
            status = "changed" if comparison["score"] >= cycle["threshold"] else "usual"
            previous = store.one(con, """SELECT features_json FROM bg_samples WHERE cycle_id=?
                AND captured_at<? AND status!='quality_review' ORDER BY captured_at DESC LIMIT 1""", (cycle["id"], captured))
            if previous:
                comparison["previous"] = compare(desc, json.loads(previous["features_json"]))
        elif valid and baseline:
            status = "historical"
        store.sql(con, """INSERT INTO bg_samples
            (id,hive_id,cycle_id,captured_at,received_at,sha256,audio,quality_json,features_json,comparison_json,status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, hive_id, cycle["id"], captured, received, digest, payload, encode(technical), encode(desc), encode(comparison), status))
        if valid and not baseline:
            reference_rows = store.all(con, """SELECT features_json,captured_at FROM bg_samples
                WHERE cycle_id=? AND status='calibrating' ORDER BY captured_at LIMIT ?""", (cycle["id"], cycle["calibration_samples"]))
            count = len(reference_rows)
            store.sql(con, "UPDATE bg_cycles SET baseline_count=? WHERE id=?", (count, cycle["id"]))
            if count >= cycle["calibration_samples"]:
                baseline = reference_from([json.loads(row["features_json"]) for row in reference_rows])
                store.sql(con, "UPDATE bg_cycles SET baseline_json=?,baseline_until=? WHERE id=?",
                          (encode(baseline), max(row["captured_at"] for row in reference_rows), cycle["id"]))
        # A late upload is preserved but never reopens an old alert episode.
        if not cycle["last_evaluated_at"] or captured > cycle["last_evaluated_at"]:
            previous_time = cycle["last_evaluated_at"]
            gap = (datetime.fromisoformat(captured) - datetime.fromisoformat(previous_time)).total_seconds() if previous_time else 0
            streak = (cycle["anomaly_streak"] if gap <= 60 else 0) + 1 if status == "changed" else 0
            episode = cycle["episode_open"]
            if status == "usual":
                episode = 0
            if status == "changed" and streak >= cycle["consecutive_samples"] and not episode:
                alert_id = str(uuid.uuid4())
                hive = store.hive(con, hive_id)
                message = (f"{hive['name']}: alteração acústica de {comparison['score']:.0f}% "
                           f"em {streak} coletas consecutivas no ciclo {cycle['name']}. Verifique a colmeia e o ambiente.")
                store.sql(con, """INSERT INTO bg_alerts
                    (id,hive_id,cycle_id,recording_id,created_at,message,score) VALUES (?,?,?,?,?,?,?)""",
                    (alert_id, hive_id, cycle["id"], rid, received, message, comparison["score"]))
                episode = 1
            store.sql(con, "UPDATE bg_cycles SET anomaly_streak=?,episode_open=?,last_evaluated_at=? WHERE id=?",
                      (streak, episode, captured, cycle["id"]))
        return {"id": rid, "cycle_id": cycle["id"], "session_id": cycle["id"], "status": "stored",
                "storage": "ok", "quality_status": technical["quality_status"],
                "acoustic_status": status, "comparison": comparison, "alert_id": alert_id}, 201


def create_app(store=None):
    app = Flask(__name__, static_folder=str(ROOT / "web/static"), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    store = store or Store()
    app.extensions["beeguard_store"] = store

    @app.errorhandler(ValueError)
    @app.errorhandler(TypeError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(DomainError)
    def domain(exc):
        return jsonify(error=str(exc)), exc.status

    @app.errorhandler(Exception)
    def unavailable(exc):
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        app.logger.exception("BeeGuard request failed")
        message = "Armazenamento indisponível. Tente novamente; preserve a fila de coleta."
        if store.cloud and not store.url:
            message = "Configure DATABASE_URL com um PostgreSQL persistente na Vercel."
        return jsonify(error=message), 503

    @app.after_request
    def no_cache(response):
        if request.path.startswith("/api/") or request.path == "/health":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    @app.get("/monitor")
    def index():
        return send_file(ROOT / "web/templates/beeguard_web.html")

    @app.get("/health")
    @app.get("/api/health")
    def health():
        with store.transaction() as con:
            store.sql(con, "SELECT 1").fetchone()
        return jsonify(status="ok", storage="ok", backend=store.backend, rule_version=RULE_VERSION)

    @app.route("/api/hives", methods=["GET", "POST"])
    def hives():
        if request.method == "POST":
            data = body()
            hive_id = text_value(data, "id", 64)
            if not IDENTIFIER.fullmatch(hive_id):
                raise ValueError("Código da colmeia: use letras, números, _ e -.")
            name = text_value(data, "name")
            location = text_value(data, "location", 160, False)
            species = text_value(data, "species", 120, False)
            notes = text_value(data, "notes", 2000, False)
            with store.transaction(write=True) as con:
                if store.one(con, "SELECT id FROM bg_hives WHERE id=?", (hive_id,)):
                    raise DomainError("Já existe uma colmeia com esse código.")
                try:
                    inserted = store.sql(con, "INSERT INTO bg_hives VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                                         (hive_id, name, location, species, notes, now_iso()))
                    if not inserted.rowcount:
                        raise DomainError("Já existe uma colmeia com esse código.")
                except sqlite3.IntegrityError as exc:
                    raise DomainError("Já existe uma colmeia com esse código.") from exc
            return jsonify(id=hive_id, name=name), 201
        with store.transaction() as con:
            rows = store.all(con, "SELECT * FROM bg_hives ORDER BY created_at")
            for row in rows:
                active = store.one(con, "SELECT * FROM bg_cycles WHERE hive_id=? AND ended_at IS NULL", (row["id"],))
                row["active_cycle"] = cycle_json(active)
                row["recording_count"] = store.one(con, "SELECT COUNT(*) AS n FROM bg_samples WHERE hive_id=?", (row["id"],))["n"]
                latest = store.one(con, """SELECT id,hive_id,cycle_id,captured_at,received_at,sha256,
                    quality_json,features_json,comparison_json,status FROM bg_samples
                    WHERE hive_id=? ORDER BY captured_at DESC LIMIT 1""", (row["id"],))
                row["latest"] = sample_json(latest) if latest else None
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(latest["captured_at"])).total_seconds() if latest else None
                row["online"] = bool(age is not None and -5 <= age <= 60 and active and latest["cycle_id"] == active["id"])
                row["unread_alerts"] = store.one(con, "SELECT COUNT(*) AS n FROM bg_alerts WHERE hive_id=? AND acknowledged_at IS NULL", (row["id"],))["n"]
        return jsonify(hives=rows)

    @app.route("/api/hives/<hive_id>/cycles", methods=["GET", "POST"])
    def cycles(hive_id):
        if request.method == "POST":
            data = body()
            name = text_value(data, "name")
            samples = number(data, "calibration_samples", 6, 3, 60, True)
            threshold = number(data, "threshold", 45, 5, 100)
            consecutive = number(data, "consecutive_samples", 2, 1, 20, True)
            with store.transaction(write=True) as con:
                store.hive(con, hive_id, lock=True)
                if store.one(con, "SELECT id FROM bg_cycles WHERE hive_id=? AND ended_at IS NULL", (hive_id,)):
                    raise DomainError("Encerre o ciclo atual antes de iniciar outro.")
                cid = str(uuid.uuid4())
                store.sql(con, """INSERT INTO bg_cycles
                    (id,hive_id,name,started_at,calibration_samples,threshold,consecutive_samples)
                    VALUES (?,?,?,?,?,?,?)""", (cid, hive_id, name, now_iso(), samples, threshold, consecutive))
                row = store.cycle(con, cid)
            return jsonify(cycle=cycle_json(row)), 201
        with store.transaction() as con:
            store.hive(con, hive_id)
            rows = store.all(con, "SELECT * FROM bg_cycles WHERE hive_id=? ORDER BY started_at DESC", (hive_id,))
            for row in rows:
                row["recording_count"] = store.one(con, "SELECT COUNT(*) AS n FROM bg_samples WHERE cycle_id=?", (row["id"],))["n"]
        return jsonify(cycles=[cycle_json(row) for row in rows])

    @app.get("/api/cycles/<cycle_id>")
    @app.get("/api/field/sessions/<cycle_id>")
    def get_cycle(cycle_id):
        with store.transaction() as con:
            row = cycle_json(store.cycle(con, cycle_id))
        return jsonify(cycle=row, session=row)

    @app.post("/api/cycles/<cycle_id>/end")
    def end_cycle(cycle_id):
        with store.transaction(write=True) as con:
            row = store.cycle(con, cycle_id)
            store.hive(con, row["hive_id"], lock=True)
            row = store.cycle(con, cycle_id)
            if not row["ended_at"]:
                latest = store.one(con, "SELECT MAX(captured_at) AS last FROM bg_samples WHERE cycle_id=?", (cycle_id,))["last"]
                ended = max(now_iso(), latest) if latest else now_iso()
                store.sql(con, "UPDATE bg_cycles SET ended_at=? WHERE id=?", (ended, cycle_id))
            row = cycle_json(store.cycle(con, cycle_id))
        return jsonify(cycle=row)

    @app.post("/api/audio")
    def audio():
        result, status = ingest(store, request.get_data(), request.headers)
        return jsonify(result), status


    def filters(con):
        clauses, params = [], []
        for field in ("hive_id", "cycle_id"):
            value = request.args.get(field)
            if value:
                (store.hive if field == "hive_id" else store.cycle)(con, value)
                clauses.append(field + "=?")
                params.append(value)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    @app.get("/api/recordings")
    def recordings():
        limit = int(request.args.get("limit", 120))
        offset = int(request.args.get("offset", 0))
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("Use limit entre 1 e 1000 e offset maior ou igual a zero.")
        with store.transaction() as con:
            where, params = filters(con)
            rows = store.all(con, "SELECT id,hive_id,cycle_id,captured_at,received_at,sha256,quality_json,features_json,comparison_json,status FROM bg_samples" + where + " ORDER BY captured_at DESC,id DESC LIMIT ? OFFSET ?", (*params, limit, offset))
            count = store.one(con, "SELECT COUNT(*) AS n FROM bg_samples" + where, params)["n"]
        return jsonify(recordings=[sample_json(row) for row in rows], total=count)

    @app.get("/api/recordings/<recording_id>/audio")
    def download_audio(recording_id):
        with store.transaction() as con:
            row = store.one(con, "SELECT audio FROM bg_samples WHERE id=?", (recording_id,))
        if not row:
            raise DomainError("Áudio não encontrado.", 404)
        return send_file(io.BytesIO(bytes(row["audio"])), mimetype="audio/wav", download_name=recording_id + ".wav")

    @app.get("/api/alerts")
    def alerts():
        with store.transaction() as con:
            where, params = filters(con)
            rows = store.all(con, "SELECT * FROM bg_alerts" + where + " ORDER BY created_at DESC LIMIT 200", params)
            unread = store.one(con, "SELECT COUNT(*) AS n FROM bg_alerts" + where + (" AND" if where else " WHERE") + " acknowledged_at IS NULL", params)["n"]
        return jsonify(alerts=rows, unread=unread)

    @app.post("/api/alerts/<alert_id>/acknowledge")
    def acknowledge(alert_id):
        with store.transaction(write=True) as con:
            if not store.one(con, "SELECT id FROM bg_alerts WHERE id=?", (alert_id,)):
                raise DomainError("Alerta não encontrado.", 404)
            store.sql(con, "UPDATE bg_alerts SET acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?", (now_iso(), alert_id))
        return jsonify(status="ok")

    @app.route("/api/inspections", methods=["GET", "POST"])
    def inspections():
        if request.method == "POST":
            data = body()
            cycle_id = text_value(data, "cycle_id", 64)
            observer = text_value(data, "observer", 100)
            notes = text_value(data, "notes", 2000)
            label = data.get("label")
            if label not in LABELS:
                raise ValueError("Condição inválida. Use healthy, sick ou uncertain.")
            observed = timestamp(data.get("observed_at", now_iso()))
            with store.transaction(write=True) as con:
                cycle = store.cycle(con, cycle_id)
                if observed < cycle["started_at"] or (cycle["ended_at"] and observed > cycle["ended_at"]):
                    raise DomainError("A inspeção precisa ocorrer dentro do ciclo selecionado.")
                iid = str(uuid.uuid4())
                store.sql(con, "INSERT INTO bg_inspections VALUES (?,?,?,?,?,?,?,?)",
                          (iid, cycle["hive_id"], cycle_id, observed, label, observer, notes, now_iso()))
            return jsonify(id=iid), 201
        with store.transaction() as con:
            where, params = filters(con)
            rows = store.all(con, "SELECT * FROM bg_inspections" + where + " ORDER BY observed_at DESC", params)
        return jsonify(inspections=rows)

    @app.get("/api/export")
    def export():
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["recording_id", "hive_id", "hive_name", "cycle_id", "captured_at", "status",
                         "rms", "dominant_frequency_hz", "change_score", "delta_db", "spectral_distance",
                         "inspection_label", "inspection_id", "inspection_at", "observer", "rule_version"])
        with store.transaction() as con:
            where, params = filters(con)
            labels = store.all(con, "SELECT * FROM bg_inspections" + where + " ORDER BY observed_at", params)
            names = {row["id"]: row["name"] for row in store.all(con, "SELECT id,name FROM bg_hives")}
            offset = 0
            while True:
                page = store.all(con, "SELECT id,hive_id,cycle_id,captured_at,received_at,sha256,quality_json,features_json,comparison_json,status FROM bg_samples" + where + " ORDER BY captured_at,id LIMIT 500 OFFSET ?", (*params, offset))
                if not page:
                    break
                for raw in page:
                    row = sample_json(raw)
                    # Only a nearby inspection from the same cycle labels this sample.
                    nearby = [item for item in labels if item["cycle_id"] == row["cycle_id"]
                              and abs((datetime.fromisoformat(item["observed_at"]) - datetime.fromisoformat(row["captured_at"])).total_seconds()) <= 1800]
                    label = min(nearby, key=lambda item: abs((datetime.fromisoformat(item["observed_at"]) - datetime.fromisoformat(row["captured_at"])).total_seconds())) if nearby else {}
                    f, c = row["features"], row["comparison"]
                    values = [row["id"], row["hive_id"], names[row["hive_id"]], row["cycle_id"], row["captured_at"], row["status"],
                              f["rms"], f["dominant_frequency_hz"], c.get("score"), c.get("delta_db"), c.get("spectral_distance"),
                              label.get("label", ""), label.get("id", ""), label.get("observed_at", ""), label.get("observer", ""), RULE_VERSION]
                    writer.writerow([("'" + value) if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value for value in values])
                offset += len(page)
        return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=beeguard-dataset.csv"})

    return app


if __name__ == "__main__":
    from waitress import serve
    application = create_app()
    print("BeeGuard: http://127.0.0.1:5000", flush=True)
    serve(application, host="127.0.0.1", port=5000, threads=4)
