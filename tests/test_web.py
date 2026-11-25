"""End-to-end behavior of the new dashboard and real durable SQLite storage."""
import csv
import io
import json
import base64
import re
import struct
import zlib
import tempfile
import threading
import unittest
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import numpy as np
from werkzeug.serving import make_server
from beeguard_web import Store, create_app
from capture_field import enqueue, upload


@lru_cache(maxsize=12)
def tone(amplitude=.1, frequency=250):
    pcm = np.round(amplitude * 32767 * np.sin(2 * np.pi * frequency * np.arange(160000) / 16000)).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm.tobytes())
    return output.getvalue()


class WebFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(database_url="", sqlite_path=self.root / "monitor.db", cloud=False)
        self.app = create_app(self.store)
        self.client = self.app.test_client()
        self.time = datetime.now(timezone.utc) - timedelta(minutes=10)
        self.register("BG01", "Jataí do jardim")
        self.cid = self.cycle("BG01")
        self.index = 0

    def tearDown(self):
        self.temp.cleanup()

    def register(self, hid, name):
        response = self.client.post("/api/hives", json={"id": hid, "name": name})
        self.assertEqual(response.status_code, 201, response.json)

    def cycle(self, hid):
        response = self.client.post("/api/hives/" + hid + "/cycles",
                                    json={"name": "Ciclo observado", "calibration_samples": 3})
        self.assertEqual(response.status_code, 201, response.json)
        cid = response.json["cycle"]["id"]
        with self.store.transaction(write=True) as con:
            self.store.sql(con, "UPDATE bg_cycles SET started_at=? WHERE id=?", (self.time.isoformat(), cid))
        return cid

    def send(self, amplitude=.1, frequency=250, hid="BG01", cid=None, at=None, rid=None, data=None):
        self.index += 1
        headers = {"X-Hive-Id": hid, "X-Cycle-Id": cid or self.cid,
                   "X-Recording-Id": rid or str(uuid.uuid4()),
                   "X-Captured-At": at or (self.time + timedelta(seconds=self.index * 10)).isoformat()}
        response = self.client.post("/api/audio", data=tone(amplitude, frequency) if data is None else data,
                                    headers=headers, content_type="audio/wav")
        return response, headers

    def baseline(self):
        for _ in range(3):
            response, _ = self.send()
            self.assertEqual(response.status_code, 201, response.json)

    def recordings(self):
        return self.client.get("/api/recordings?cycle_id=" + self.cid).json["recordings"]

    def test_page_assets_and_health(self):
        for path in ("/", "/monitor", "/static/beeguard.css", "/static/beeguard.js"):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/health").json["backend"], "sqlite")
        self.assertEqual(self.client.get("/api/hives").headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/missing").status_code, 404)

    def test_hive_validation_and_duplicate(self):
        self.assertEqual(self.client.post("/api/hives", json={"id": "BG01", "name": "Outra"}).status_code, 409)
        for payload in ([], {"id": "../x", "name": "a"}, {"id": "B", "name": ""}):
            self.assertEqual(self.client.post("/api/hives", json=payload).status_code, 400)

    def test_edit_hive_preserves_identifier_and_history(self):
        response = self.client.patch("/api/hives/BG01", json={
            "name": "Jataí revisada", "location": "Jardim", "species": "Jataí", "notes": "Observação atualizada"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["id"], "BG01")
        self.assertEqual(self.client.get("/api/hives").json["hives"][0]["name"], "Jataí revisada")
        self.assertEqual(self.client.get("/api/hives/BG01/cycles").status_code, 200)

    def test_archive_hive_preserves_history_and_pauses_device(self):
        response, _ = self.send()
        self.assertEqual(response.status_code, 201)
        archived = self.client.post("/api/hives/BG01/archive", json={"archived": True})
        self.assertEqual(archived.status_code, 200)
        self.assertTrue(archived.json["archived"])
        self.assertEqual(self.client.get("/api/hives").json["hives"], [])
        listed = self.client.get("/api/hives?include_archived=true").json["hives"]
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0]["archived_at"])
        self.assertEqual(listed[0]["recording_count"], 1)
        self.assertEqual(self.client.post("/api/hives/BG01/cycles", json={"name": "Blocked"}).status_code, 409)
        config = self.client.get("/api/device/config", headers={"X-Hive-Id": "BG01"}).json
        self.assertFalse(config["recording_enabled"])
        self.assertIsNone(config["cycle_id"])
        restored = self.client.post("/api/hives/BG01/archive", json={"archived": False})
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(self.client.get("/api/recordings?cycle_id=" + self.cid).json["total"], 1)

    def test_permanent_hive_delete_requires_exact_confirmation_and_no_history(self):
        self.register("EMPTY", "Colmeia vazia")
        self.assertEqual(self.client.post("/api/hives/EMPTY/delete", json={"confirm_id": "wrong"}).status_code, 400)
        deleted = self.client.post("/api/hives/EMPTY/delete", json={"confirm_id": "EMPTY"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/hives/EMPTY/cycles").status_code, 404)
        blocked = self.client.post("/api/hives/BG01/delete", json={"confirm_id": "BG01"})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(self.client.get("/api/recordings?cycle_id=" + self.cid).json["total"], 0)

    def test_one_active_cycle_and_cycle_isolation(self):
        self.assertEqual(self.client.post("/api/hives/BG01/cycles", json={"name": "Outro"}).status_code, 409)
        self.baseline()
        self.assertEqual(self.client.post("/api/cycles/" + self.cid + "/end").status_code, 200)
        second = self.client.post("/api/hives/BG01/cycles", json={"name": "Novo"}).json["cycle"]
        self.assertIsNone(second["baseline"])
        self.assertEqual(second["baseline_count"], 0)
        self.assertEqual(len(self.recordings()), 3)

    def test_quality_does_not_contaminate_reference(self):
        response, _ = self.send(amplitude=0)
        self.assertEqual(response.json["acoustic_status"], "quality_review")
        self.send(amplitude=1)
        self.assertEqual(self.client.get("/api/cycles/" + self.cid).json["cycle"]["baseline_count"], 0)
        self.baseline()
        self.assertIsNotNone(self.client.get("/api/cycles/" + self.cid).json["cycle"]["baseline"])

    def test_stable_reference_and_previous_comparison(self):
        self.baseline()
        response, _ = self.send()
        self.assertEqual(response.json["acoustic_status"], "usual")
        self.assertLess(response.json["comparison"]["score"], 1)
        self.assertIsNotNone(response.json["comparison"]["previous"])
        self.assertEqual(self.client.get("/api/alerts").json["unread"], 0)

    def test_persistent_change_one_alert_and_acknowledgment(self):
        self.baseline()
        response, _ = self.send(amplitude=.5)
        self.assertIsNone(response.json["alert_id"])
        response, headers = self.send(amplitude=.5)
        self.assertIsNotNone(response.json["alert_id"])
        self.send(amplitude=.5)
        retry = self.client.post("/api/audio", data=tone(.5), headers=headers)
        self.assertEqual(retry.status_code, 200)
        alerts = self.client.get("/api/alerts").json
        self.assertEqual(len(alerts["alerts"]), 1)
        self.assertIn("Jataí do jardim", alerts["alerts"][0]["message"])
        aid = alerts["alerts"][0]["id"]
        self.assertEqual(self.client.post("/api/alerts/" + aid + "/acknowledge").status_code, 200)
        self.assertEqual(self.client.get("/api/alerts").json["unread"], 0)

    def test_frequency_change_at_equal_volume(self):
        self.baseline()
        response, _ = self.send(frequency=1200)
        self.assertEqual(response.json["acoustic_status"], "changed")
        self.assertGreater(response.json["comparison"]["spectral_distance"], .8)
        self.assertLess(abs(response.json["comparison"]["delta_db"]), .1)

    def test_alert_rearms_after_recovery(self):
        self.baseline()
        for _ in range(3):
            self.send(amplitude=.5)
        self.send()
        for _ in range(2):
            self.send(amplitude=.5)
        self.assertEqual(len(self.client.get("/api/alerts").json["alerts"]), 2)

    def test_other_hive_cannot_receive_cycle_audio(self):
        self.register("BG02", "Outra")
        response, _ = self.send(hid="BG02")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.recordings(), [])

    def test_idempotency_conflict_and_audio_roundtrip(self):
        response, headers = self.send()
        rid = response.json["id"]
        self.assertEqual(self.client.post("/api/audio", data=tone(), headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/audio", data=tone(.3), headers=headers).status_code, 409)
        with self.client.get("/api/recordings/" + rid + "/audio") as audio:
            self.assertEqual(audio.data, tone())
        self.assertEqual(len(self.recordings()), 1)

    def test_delayed_closed_cycle_and_timestamp_resolution(self):
        self.baseline()
        self.client.post("/api/cycles/" + self.cid + "/end")
        response, headers = self.send(at=(self.time + timedelta(seconds=45)).isoformat())
        self.assertEqual(response.status_code, 201, response.json)
        headers.pop("X-Cycle-Id")
        self.assertEqual(self.client.post("/api/audio", data=tone(), headers=headers).status_code, 200)
        headers["X-Recording-Id"] = str(uuid.uuid4())
        headers["X-Captured-At"] = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
        self.assertEqual(self.client.post("/api/audio", data=tone(), headers=headers).status_code, 409)

    def test_out_of_order_sample_is_preserved_without_alert(self):
        self.baseline()
        response, _ = self.send(amplitude=.5, at=(self.time + timedelta(seconds=15)).isoformat())
        self.assertEqual(response.json["acoustic_status"], "historical")
        self.assertEqual(len(self.recordings()), 4)
        self.assertEqual(self.client.get("/api/alerts").json["unread"], 0)

    def test_long_gap_breaks_consecutive_alerts(self):
        self.baseline()
        self.send(amplitude=.5)
        self.send(amplitude=.5, at=(self.time + timedelta(seconds=180)).isoformat())
        self.assertEqual(self.client.get("/api/alerts").json["unread"], 0)

    def test_invalid_wave_and_times_write_nothing(self):
        for data in (b"invalid", tone()[:1000]):
            self.assertEqual(self.send(data=data)[0].status_code, 400)
        for at in ("2026-01-01T10:00:00", "invalid", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()):
            self.assertEqual(self.send(at=at)[0].status_code, 400)
        self.assertEqual(self.recordings(), [])
        self.assertEqual(self.client.post("/api/audio", data=b"x" * (1024 * 1024 + 1)).status_code, 413)

    def test_inspection_export_and_durable_restart(self):
        self.baseline()
        payload = {"cycle_id": self.cid, "label": "healthy", "observer": "Equipe",
                   "notes": "Atividade observada no jardim.", "observed_at": (self.time + timedelta(seconds=20)).isoformat()}
        self.assertEqual(self.client.post("/api/inspections", json=payload).status_code, 201)
        self.assertEqual(self.client.post("/api/inspections", json={**payload, "label": "bad"}).status_code, 400)
        data = self.client.get("/api/export?cycle_id=" + self.cid).data.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(data)))
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["inspection_label"] == "healthy" for row in rows))
        restarted = create_app(Store(database_url="", sqlite_path=self.root / "monitor.db", cloud=False)).test_client()
        self.assertEqual(restarted.get("/api/recordings").json["total"], 3)
        self.assertEqual(len(restarted.get("/api/inspections").json["inspections"]), 1)

    def test_export_does_not_truncate_at_ui_limit(self):
        self.send()
        with self.store.transaction(write=True) as con:
            row = self.store.one(con, "SELECT * FROM bg_samples LIMIT 1")
            keys = list(row)
            for i in range(1003):
                duplicate = {**row, "id": str(uuid.uuid4()), "audio": b"test-fixture"}
                self.store.sql(con, "INSERT INTO bg_samples (" + ",".join(keys) + ") VALUES (" + ",".join("?" for _ in keys) + ")", tuple(duplicate[k] for k in keys))
        self.assertEqual(self.client.get("/api/recordings?limit=1000").json["total"], 1004)
        rows = list(csv.DictReader(io.StringIO(self.client.get("/api/export").data.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 1004)

    def test_cloud_requires_durable_database(self):
        app = create_app(Store(database_url="", sqlite_path=self.root / "should-not-exist.db", cloud=True))
        app.logger.disabled = True
        with app.test_client() as client:
            self.assertEqual(client.get("/").status_code, 200)
            response = client.get("/health")
            self.assertEqual(response.status_code, 503)
            self.assertIn("DATABASE_URL", response.json["error"])
        self.assertFalse((self.root / "should-not-exist.db").exists())

    def test_concurrent_retry_only_stores_once(self):
        _, headers = self.send()
        headers["X-Recording-Id"] = str(uuid.uuid4())
        def retry(_):
            with self.app.test_client() as client:
                return client.post("/api/audio", data=tone(), headers=headers).status_code
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(retry, range(4)))
        self.assertEqual(sorted(results), [200, 200, 200, 201])
        self.assertEqual(len(self.recordings()), 2)

    def test_transaction_rolls_back_audio_if_alert_save_fails(self):
        self.baseline()
        self.send(amplitude=.5)
        original = self.store.sql
        def fail(con, query, params=()):
            if "INSERT INTO bg_alerts" in query:
                raise OSError("simulated database failure")
            return original(con, query, params)
        self.app.logger.disabled = True
        with patch.object(self.store, "sql", side_effect=fail):
            response, headers = self.send(amplitude=.5)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(self.recordings()), 4)
        self.assertEqual(self.client.post("/api/audio", data=tone(.5), headers=headers).status_code, 201)
        self.assertEqual(self.client.get("/api/alerts").json["unread"], 1)

    def test_real_http_collector_accepts_storage_confirmation(self):
        server = make_server("127.0.0.1", 0, self.app)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with wave.open(io.BytesIO(tone()), "rb") as wav:
                marker = enqueue(wav.readframes(160000), {"id": self.cid, "hive_id": "BG01"},
                                 self.root / "pending", (self.time + timedelta(seconds=10)).isoformat())
            upload(marker, "http://127.0.0.1:" + str(server.server_port))
            self.assertFalse(marker.exists())
            self.assertEqual(len(self.recordings()), 1)
        finally:
            server.shutdown()
            worker.join()
            server.server_close()

    def device_headers(self, hive="BG01"):
        return {"X-Hive-Id": hive,
                "X-Cycle-Id": self.cid, "X-Recording-Id": str(uuid.uuid4()),
                "X-Captured-At": (self.time + timedelta(seconds=10)).isoformat()}

    def test_device_config_starts_and_stops_with_dashboard_cycle(self):
        headers = self.device_headers()
        response = self.client.get("/api/device/config", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["recording_enabled"])
        self.assertEqual(response.json["cycle_id"], self.cid)
        self.assertEqual(response.json["sample_rate"], 16000)
        self.client.post("/api/cycles/" + self.cid + "/end")
        response = self.client.get("/api/device/config", headers=headers)
        self.assertFalse(response.json["recording_enabled"])
        self.assertIsNone(response.json["cycle_id"])

    def test_device_routes_validate_hive_and_accept_upload_without_credentials(self):
        good = self.device_headers()
        for hive, status in (("", 400), ("../invalid", 400), ("UNKNOWN", 404)):
            headers = {**good, "X-Hive-Id": hive}
            self.assertEqual(self.client.get("/api/device/config", headers=headers).status_code, status)
            self.assertEqual(self.client.post("/api/device/audio", data=tone(), headers=headers).status_code, status)
        self.assertEqual(self.recordings(), [])
        self.assertEqual(self.client.post("/api/device/audio", data=tone(), headers=good).status_code, 201)
        self.assertEqual(self.send()[0].status_code, 201)

    def test_recording_origin_is_persisted_for_manual_and_esp32(self):
        device_headers = self.device_headers()
        response = self.client.post("/api/device/audio", data=tone(), headers=device_headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["source"], "esp32")
        self.assertEqual(self.client.post("/api/audio", data=tone(), headers={**device_headers,
                         "X-Recording-Id": str(uuid.uuid4()),
                         "X-Captured-At": (self.time + timedelta(seconds=20)).isoformat()}).status_code, 201)
        page = self.client.get("/api/recordings?cycle_id=" + self.cid + "&limit=20&recent_limit=120").json
        self.assertEqual({row["source"] for row in page["recordings"]}, {"manual", "esp32"})
        self.assertEqual(len(page["recent_recordings"]), 2)

    def test_device_retry_after_restart_and_cycle_end_preserves_original_cycle(self):
        headers = self.device_headers()
        response = self.client.post("/api/device/audio", data=tone(), headers=headers)
        self.assertEqual(response.status_code, 201, response.json)
        self.client.post("/api/cycles/" + self.cid + "/end")
        fresh = create_app(Store(database_url="", sqlite_path=self.root / "monitor.db", cloud=False))
        with fresh.test_client() as client:
            retry = client.post("/api/device/audio", data=tone(), headers=headers)
            self.assertEqual(retry.status_code, 200)
            self.assertEqual(retry.json["status"], "already_stored")
            self.assertEqual(retry.json["cycle_id"], self.cid)
            self.assertEqual(client.get("/api/recordings").json["total"], 1)
        # An unsent WAV captured before closure can still be delivered afterwards.
        headers["X-Recording-Id"] = str(uuid.uuid4())
        self.assertEqual(self.client.post("/api/device/audio", data=tone(), headers=headers).status_code, 201)
        headers["X-Recording-Id"] = str(uuid.uuid4())
        headers["X-Captured-At"] = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
        self.assertEqual(self.client.post("/api/device/audio", data=tone(), headers=headers).status_code, 409)

    def test_device_requires_explicit_cycle_and_rejects_cross_hive_cycle(self):
        headers = self.device_headers()
        no_cycle = {key: value for key, value in headers.items() if key != "X-Cycle-Id"}
        self.assertEqual(self.client.post("/api/device/audio", data=tone(), headers=no_cycle).status_code, 400)
        self.register("BG02", "Outra colmeia")
        headers["X-Cycle-Id"] = self.cycle("BG02")
        self.assertEqual(self.client.post("/api/device/audio", data=tone(), headers=headers).status_code, 409)
        self.assertEqual(self.recordings(), [])

    def test_spectrogram_is_derived_from_durable_wav_and_has_frequency_axis(self):
        _, headers = self.send(frequency=250)
        rid = headers["X-Recording-Id"]
        row = self.recordings()[0]
        self.assertEqual(row["spectrogram_url"], "/api/recordings/" + rid + "/spectrogram")
        fresh = create_app(Store(database_url="", sqlite_path=self.root / "monitor.db", cloud=False)).test_client()
        response = fresh.get(row["spectrogram_url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/svg+xml")
        png = base64.b64decode(re.search(rb"base64,([^\"]+)", response.data).group(1))
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", png[16:24])
        self.assertEqual((width, height), (200, 257))
        compressed = b""
        position = 8
        while position < len(png):
            length = struct.unpack(">I", png[position:position + 4])[0]
            if png[position + 4:position + 8] == b"IDAT":
                compressed += png[position + 8:position + 8 + length]
            position += 12 + length
        scanlines = np.frombuffer(zlib.decompress(compressed), dtype=np.uint8).reshape(height, 1 + width * 3)
        pixels = scanlines[:, 1:].reshape(height, width, 3)
        brightest_row = pixels.astype(float).sum(axis=2).mean(axis=1).argmax()
        self.assertAlmostEqual((height - 1 - brightest_row) * 16000 / 512, 250, delta=32)
        self.assertEqual(fresh.get("/api/recordings/missing/spectrogram").status_code, 404)

if __name__ == "__main__":
    unittest.main()
