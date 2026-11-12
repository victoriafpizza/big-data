import json
import os
import tempfile
import unittest
from unittest.mock import patch

from ingestao import batch_path, ingest, source_url, validate, verify


class IngestaoTest(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {
            "INGEST_ROOT": temp,
            "ROTA_LIVRE_API_URL": "http://api:8000/api/obstacles",
        }))
        self.day = "2026-11-12"
        self.source = "obstaculos"
        self.row = {"id": 1, "tipo": "teste", "latitude": -23.5,
                    "longitude": -46.6, "status": "teste"}
        self.payload = json.dumps({"total": 1, "obstaculos": [self.row]}).encode()

    def test_reexecucao_preserva_arquivos_e_metadados(self):
        with patch("ingestao.fetch", return_value=(self.payload, "application/json")) as fetch:
            first = ingest(self.source, self.day, "primeira")
            folder = batch_path(self.source, self.day)
            before = {p.name: p.stat().st_mtime_ns for p in folder.iterdir()}
            second = ingest(self.source, self.day, "reexecucao")
            self.assertEqual(first, second)
            self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in folder.iterdir()})
            self.assertEqual(list(folder.parent.iterdir()), [folder])
            fetch.assert_called_once()

    def test_lote_corrompido_falha_sem_regravar(self):
        with patch("ingestao.fetch", return_value=(self.payload, "application/json")) as fetch:
            ingest(self.source, self.day, "primeira")
            data = batch_path(self.source, self.day) / "obstaculos.json"
            data.write_bytes(self.payload + b" ")
            with self.assertRaisesRegex(ValueError, "integridade"):
                verify(self.source, self.day)
            with self.assertRaisesRegex(ValueError, "integridade"):
                ingest(self.source, self.day, "reexecucao")
            self.assertEqual(data.read_bytes(), self.payload + b" ")
            fetch.assert_called_once()

    def test_resposta_vazia_nao_publica_lote(self):
        with patch("ingestao.fetch", return_value=(b'{"total":0,"obstaculos":[]}', "application/json")):
            with self.assertRaisesRegex(ValueError, "contagem"):
                ingest(self.source, self.day, "falha")
        self.assertFalse(batch_path(self.source, self.day).exists())

    def test_obstaculos_duplicados_sao_rejeitados(self):
        payload = json.dumps({"total": 2, "obstaculos": [self.row, self.row]})
        with self.assertRaisesRegex(ValueError, "duplicado"):
            validate(self.source, payload)

    def test_endpoint_rejeita_credenciais_query_e_fragmento(self):
        for url in (
            "http://user:valor-de-teste@api:8000/api/obstacles",
            "http://api:8000/api/obstacles?parametro=valor-de-teste",
            "http://api:8000/api/obstacles#valor-de-teste",
        ):
            with self.subTest(url=url), patch.dict(os.environ, {"ROTA_LIVRE_API_URL": url}):
                with self.assertRaisesRegex(ValueError, "endpoint público") as error:
                    ingest(self.source, self.day, "falha")
                self.assertNotIn("valor-de-teste", str(error.exception))
                self.assertFalse(batch_path(self.source, self.day).exists())

    def test_wfs_exige_https(self):
        with patch.dict(os.environ, {"GEOSAMPA_WFS_URL": "http://example.invalid/wfs"}):
            with self.assertRaisesRegex(ValueError, "GEOSAMPA_WFS_URL"):
                source_url("geosampa_calcadas")

    def test_geosampa_truncado_e_rejeitado(self):
        payload = '{"type":"FeatureCollection","numberReturned":1,"numberMatched":2,"features":[{}]}'
        with self.assertRaisesRegex(ValueError, "truncada"):
            validate("geosampa_calcadas", payload)


if __name__ == "__main__":
    unittest.main()


