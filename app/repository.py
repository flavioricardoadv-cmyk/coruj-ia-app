from __future__ import annotations

import json
import os
import hashlib
import re
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parents[1]
JSON_FALLBACK = ROOT / "modelo_db_export" / "modelos_manifestacoes.json"
FEEDBACK_LOG = ROOT / "modelo_db_export" / "avaliacoes_recomendador.jsonl"
TRAINING_LOG = ROOT / "modelo_db_export" / "treinamento_manifestacoes.jsonl"
STYLE_LOG = ROOT / "modelo_db_export" / "aprendizado_estilo.jsonl"
CUSTOM_MODELS = ROOT / "modelo_db_export" / "modelos_customizados.json"
IMPORT_QUEUE_LOG = ROOT / "modelo_db_export" / "fila_aprendizado.jsonl"


def _learning_fingerprint(row: dict[str, Any]) -> str:
    text = str(row.get("text") or row.get("after_text") or row.get("body") or "")
    title = str(row.get("title") or "")
    code = str(row.get("target_code") or row.get("review_target_code") or "")
    autos = str(row.get("source_autos") or "")
    if autos:
        normalized = re.sub(r"\s+", " ", f"autos|{autos}".lower()).strip()
    else:
        normalized = re.sub(r"\s+", " ", f"{code}|{title}|{text}".lower()).strip()
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def _database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if url and url.startswith("sqlite"):
        return None
    return url


def using_database() -> bool:
    return bool(_database_url())


def _normalize_json_model(model: dict[str, Any]) -> dict[str, Any]:
    body = _clean_model_body(model["modelo_saneado"])
    return {
        "code": model["codigo"],
        "title": model["titulo"],
        "area": model["area"],
        "prefix": model["prefixo"],
        "when_to_use": model["quando_usar"],
        "identification_triggers": model["gatilhos_identificacao"],
        "recommended_structure": model["estrutura_recomendada"],
        "body": body,
        "keywords": model["keywords_extraidas"],
        "placeholders": model["placeholders"],
    }


def _clean_model_body(body: str) -> str:
    # The final DOCX section was captured into GEN-01; keep only the actual model.
    for marker in ("\n5. Índice rápido de gatilhos", "\n6. Observações de saneamento realizadas"):
        if marker in body:
            body = body.split(marker, 1)[0]
    return body.strip()


def _json_templates() -> list[dict[str, Any]]:
    data = json.loads(JSON_FALLBACK.read_text(encoding="utf-8"))
    return [_normalize_json_model(model) for model in data["modelos"]] + list_custom_models()


def list_templates() -> list[dict[str, Any]]:
    if not using_database():
        return _json_templates()

    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                  t.code,
                  t.title,
                  a.name as area,
                  t.prefix,
                  t.when_to_use,
                  t.identification_triggers,
                  t.recommended_structure,
                  t.body,
                  coalesce(array_agg(distinct k.keyword) filter (where k.keyword is not null), '{}') as keywords,
                  coalesce(array_agg(distinct p.placeholder) filter (where p.placeholder is not null), '{}') as placeholders
                from templates t
                left join template_areas a on a.id = t.area_id
                left join template_keywords k on k.template_id = t.id
                left join template_placeholders p on p.template_id = t.id
                group by t.id, a.name
                order by t.code
                """
            )
            return [_clean_db_template(row) for row in cur.fetchall()] + list_custom_models()


def _clean_db_template(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["body"] = _clean_model_body(row.get("body") or "")
    return row


def get_template(code: str) -> dict[str, Any] | None:
    normalized = code.upper()
    for template in list_templates():
        if template["code"].upper() == normalized:
            return template
    return None


def save_feedback(feedback: dict[str, Any]) -> None:
    FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_LOG.open("a", encoding="utf-8") as file:
        file.write(json.dumps(feedback, ensure_ascii=False) + "\n")


def list_feedback() -> list[dict[str, Any]]:
    if not FEEDBACK_LOG.exists():
        return []
    rows = []
    for line in FEEDBACK_LOG.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def save_training(training: dict[str, Any]) -> None:
    TRAINING_LOG.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = _learning_fingerprint(training)
    for row in list_training():
        if _learning_fingerprint(row) == fingerprint:
            return
    with TRAINING_LOG.open("a", encoding="utf-8") as file:
        file.write(json.dumps(training, ensure_ascii=False) + "\n")


def list_training() -> list[dict[str, Any]]:
    if not TRAINING_LOG.exists():
        return []
    rows = []
    for line in TRAINING_LOG.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def replace_training(rows: list[dict[str, Any]]) -> None:
    TRAINING_LOG.parent.mkdir(parents=True, exist_ok=True)
    unique_rows = []
    seen = set()
    for row in rows:
        fingerprint = _learning_fingerprint(row)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique_rows.append(row)
    with TRAINING_LOG.open("w", encoding="utf-8") as file:
        for row in unique_rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_style_lesson(lesson: dict[str, Any]) -> None:
    STYLE_LOG.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = _learning_fingerprint(lesson)
    for row in list_style_lessons():
        if _learning_fingerprint(row) == fingerprint:
            return
    with STYLE_LOG.open("a", encoding="utf-8") as file:
        file.write(json.dumps(lesson, ensure_ascii=False) + "\n")


def list_style_lessons() -> list[dict[str, Any]]:
    if not STYLE_LOG.exists():
        return []
    rows = []
    for line in STYLE_LOG.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def replace_style_lessons(rows: list[dict[str, Any]]) -> None:
    STYLE_LOG.parent.mkdir(parents=True, exist_ok=True)
    unique_rows = []
    seen = set()
    for row in rows:
        fingerprint = _learning_fingerprint(row)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique_rows.append(row)
    with STYLE_LOG.open("w", encoding="utf-8") as file:
        for row in unique_rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_import_queue(item: dict[str, Any]) -> None:
    IMPORT_QUEUE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with IMPORT_QUEUE_LOG.open("a", encoding="utf-8") as file:
        file.write(json.dumps(item, ensure_ascii=False) + "\n")


def list_import_queue() -> list[dict[str, Any]]:
    if not IMPORT_QUEUE_LOG.exists():
        return []
    rows = []
    for line in IMPORT_QUEUE_LOG.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def list_custom_models() -> list[dict[str, Any]]:
    if not CUSTOM_MODELS.exists():
        return []
    data = json.loads(CUSTOM_MODELS.read_text(encoding="utf-8-sig") or "[]")
    return [_clean_db_template(dict(model)) for model in data]


def save_custom_model(model: dict[str, Any]) -> dict[str, Any]:
    CUSTOM_MODELS.parent.mkdir(parents=True, exist_ok=True)
    rows = list_custom_models()
    normalized_code = model["code"].upper()
    rows = [row for row in rows if row.get("code", "").upper() != normalized_code]
    item = dict(model)
    item["code"] = normalized_code
    item["body"] = _clean_model_body(item.get("body") or "")
    rows.append(item)
    rows.sort(key=lambda row: row.get("code", ""))
    CUSTOM_MODELS.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return item
