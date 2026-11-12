"""Validate source responses and publish immutable daily batches."""
import hashlib
import json
import logging
import math
import os
import ssl
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import certifi

FILES = {"obstaculos": "obstaculos.json", "geosampa_calcadas": "calcadas.geojson"}
MAX_BYTES = 64 * 1024 * 1024
LOG = logging.getLogger(__name__)


def public_endpoint(variable, schemes):
    url = os.environ[variable]
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError:
        raise ValueError(f"{variable}: endpoint inválido") from None
    if (parsed.scheme not in schemes or not hostname or parsed.username is not None
            or "?" in url or "#" in url):
        raise ValueError(f"{variable}: use um endpoint público sem credenciais, query ou fragmento")
    return url


def source_url(source):
    if source == "obstaculos":
        return public_endpoint("ROTA_LIVRE_API_URL", {"http", "https"})
    base = public_endpoint("GEOSAMPA_WFS_URL", {"https"})
    bbox = os.environ["GEOSAMPA_BBOX"]
    west, south, east, north = map(float, bbox.split(","))
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("GEOSAMPA_BBOX inválida")
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "geoportal:calcada", "outputFormat": "application/json",
        "srsName": "EPSG:4326", "bbox": f"{bbox},urn:ogc:def:crs:OGC::CRS84",
    }
    return f"{base}?{urlencode(params)}"


def fetch(url):
    if urlsplit(url).scheme not in {"http", "https"} or urlsplit(url).username is not None:
        raise ValueError("Use uma URL HTTP(S) sem credenciais")
    request = Request(url, headers={"Accept": "application/json, application/geo+json",
                                    "User-Agent": "ROTA-LIVRE-Airflow/1.0"})
    timeout = float(os.environ.get("INGEST_HTTP_TIMEOUT", "30"))
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    with urlopen(request, timeout=timeout, context=context) as response:
        media_type = response.headers.get_content_type()
        if response.status != 200 or media_type not in {"application/json", "application/geo+json"}:
            raise ValueError(f"Resposta inesperada: HTTP {response.status}, {media_type}")
        payload = response.read(MAX_BYTES + 1)
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError("Resposta vazia ou acima de 64 MiB")
    return payload, media_type


def coordinate(value, limit):
    return type(value) in (int, float) and math.isfinite(value) and -limit <= value <= limit


def validate(source, payload):
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise ValueError(f"{source}: esperado objeto JSON")

    if source == "obstaculos":
        rows = document.get("obstaculos")
        expected = (document.get("total"),)
    else:
        if document.get("type") != "FeatureCollection":
            raise ValueError("GeoSampa: esperado GeoJSON FeatureCollection")
        rows = document.get("features")
        expected = (document.get("numberReturned"), document.get("numberMatched"))

    if not isinstance(rows, list) or not rows or any(
        type(count) is not int or count != len(rows) for count in expected
    ):
        raise ValueError(f"{source}: contagem inválida, vazia ou resposta truncada")

    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{source}: registro inválido")
        identifier = row.get("id")
        if source == "obstaculos":
            valid = (
                type(identifier) is int
                and all(isinstance(row.get(key), str) and row[key].strip()
                        for key in ("tipo", "status"))
                and coordinate(row.get("latitude"), 90)
                and coordinate(row.get("longitude"), 180)
            )
        else:
            properties = row.get("properties")
            geometry = row.get("geometry")
            valid = (
                isinstance(identifier, str) and bool(identifier)
                and row.get("type") == "Feature"
                and isinstance(properties, dict)
                and {"cd_identificador_calcada", "nm_logradouro", "qt_area_calcada",
                     "qt_largura_media_trecho", "pc_declividade_media_trecho"} <= properties.keys()
                and isinstance(geometry, dict)
                and geometry.get("type") in {"Polygon", "MultiPolygon"}
                and isinstance(geometry.get("coordinates"), list)
                and bool(geometry["coordinates"])
            )
        if not valid or identifier in ids:
            raise ValueError(f"{source}: campos inválidos ou identificador duplicado")
        ids.add(identifier)
    return len(rows)


def batch_path(source, day):
    FILES[source]
    return Path(os.environ["INGEST_ROOT"]) / source / f"data={date.fromisoformat(day).isoformat()}"


def verify(source, day):
    folder = batch_path(source, day)
    filename = FILES[source]
    metadata = json.loads((folder / "metadados.json").read_bytes())
    payload = (folder / filename).read_bytes()
    expected = {
        "fonte": source, "data_logica": day, "arquivo": filename,
        "status": "publicado", "http_status": 200, "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "registros": validate(source, payload),
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError(f"{source}: metadados ou integridade divergentes em {folder}")
    timestamp = datetime.fromisoformat(metadata["extraido_em_utc"])
    if timestamp.utcoffset() != timezone.utc.utcoffset(None) or not metadata["url"]:
        raise ValueError(f"{source}: proveniência inválida")
    return metadata


def ingest(source, day, run_id):
    folder = batch_path(source, day)
    url = source_url(source)
    if not folder.exists():
        payload, media_type = fetch(url)
        filename = FILES[source]
        metadata = {
            "fonte": source, "url": url, "data_logica": day, "airflow_run_id": run_id,
            "extraido_em_utc": datetime.now(timezone.utc).isoformat(),
            "status": "publicado", "http_status": 200, "content_type": media_type,
            "arquivo": filename, "bytes": len(payload),
            "registros": validate(source, payload), "sha256": hashlib.sha256(payload).hexdigest(),
        }
        folder.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".staging-", dir=folder.parent) as staging:
            staged = Path(staging) / "lote"
            staged.mkdir()
            (staged / filename).write_bytes(payload)
            (staged / "metadados.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            try:
                staged.rename(folder)
            except OSError:
                if not folder.is_dir():
                    raise
            else:
                LOG.info("Publicado: %s, %d registros, SHA-256 %s",
                         folder, metadata["registros"], metadata["sha256"])
                return metadata

    metadata = verify(source, day)
    if metadata["url"] != url:
        raise ValueError(f"{source}: o lote existente veio de uma URL diferente")
    LOG.info("Reutilizado sem regravação: %s, %d registros, SHA-256 %s",
             folder, metadata["registros"], metadata["sha256"])
    return metadata



