from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader
from pathlib import Path

from app.recommender import detect_area, recommend, tokenize
from app.repository import (
    get_template,
    list_feedback,
    list_templates,
    list_training,
    replace_training,
    save_custom_model,
    save_feedback,
    save_training,
    using_database,
)

from app.motor.models import init_db as init_motor_db
from app.motor.router import router as motor_router


app = FastAPI(title="Codex Coruj IA - MVP", version="0.2.0")
ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ASSETS = ROOT / "public" / "assets"
APP_STATIC = ROOT / "app" / "static"

if PUBLIC_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(PUBLIC_ASSETS)), name="assets")
if APP_STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(APP_STATIC)), name="static")

init_motor_db()
app.include_router(motor_router)

MAX_PDF_BYTES = 120 * 1024 * 1024
MAX_HISTORY_BYTES = 200 * 1024 * 1024
LEGAL_IMPORT_HINTS = {
    "parecer",
    "manifestacao",
    "manifestação",
    "ministerio publico",
    "ministério público",
    "processo",
    "autos",
    "exordial",
    "sentenca",
    "sentença",
    "decisao",
    "decisão",
    "guarda",
    "alimentos",
    "curatela",
    "execucao",
    "execução",
    "mandado de seguranca",
    "mandado de segurança",
    "apelacao",
    "apelação",
    "indenizacao",
    "indenização",
    "inventario",
    "inventário",
    "liminar",
    "tutela",
}

MP_LEGAL_OPINION_PROMPT = """
Elabore, para cada arquivo, resumo em formato de parecer juridico do Ministerio Publico, observando rigorosamente as seguintes diretrizes:

Forma e estilo:
- Redacao em linguagem formal, tecnica e objetiva.
- Utilizar paragrafos curtos, com espacamento entre eles.
- Empregar expressoes como "conforme consta nos autos" e "segundo se verifica", evitando assumir como verdade absoluta alegacoes das partes.

Conteudo:
- Sintetizar os principais fatos, pedidos, manifestacoes e decisoes, sem omitir elementos relevantes.
- Indicar sempre que possivel as folhas correspondentes (fls.), no formato "fls. X" ou "fls. X-Y".
- Destacar eventuais contradicoes, lacunas ou insuficiencia de instrucao.

Fidelidade aos autos:
- O resumo deve ser estritamente fiel ao conteudo do arquivo, sendo vedada qualquer inferencia ou complementacao externa.
- Sempre que nao houver informacao, consignar expressamente: "nao consta nos autos".

Prova de leitura obrigatoria:
- Inserir ao menos 1 trecho literal relevante extraido do arquivo, entre aspas, como forma de demonstrar a efetiva leitura.
- Garantir que o resumo reflita com precisao o conteudo do documento.

Ordem de analise:
- Analisar um arquivo por vez.
- Seguir rigorosamente a ordem cronologica, conforme o intervalo de fls. indicado.
- Iniciar pelo documento mais antigo.

Estrutura obrigatoria:
1. Breve contextualizacao do documento
2. Sintese do conteudo
3. Pontos relevantes/observacoes tecnicas
4. Trecho literal comprobatorio
""".strip()

MP_OPINION_ACTIONS = {
    "summarize_process",
    "generate_report",
    "generate_opinion",
    "explain_decision",
    "identify_pending",
    "check_pages",
}


class RecommendRequest(BaseModel):
    case_text: str = Field(..., min_length=10)
    limit: int = Field(3, ge=1, le=10)


class FeedbackRequest(BaseModel):
    case_text: str = Field(..., min_length=10)
    suggested_code: str = Field(..., min_length=3)
    correct_code: str = Field(..., min_length=3)
    accepted: bool
    note: str = ""


class TrainingAnalyzeRequest(BaseModel):
    text: str = Field(..., min_length=30)


class TrainingSaveRequest(BaseModel):
    text: str = Field(..., min_length=30)
    action: str = Field(..., min_length=3)
    target_code: str = ""
    note: str = ""
    title: str = ""
    analysis: dict[str, object] = Field(default_factory=dict)


class TrainingReviewRequest(BaseModel):
    status: str = Field(..., min_length=3)
    target_code: str = ""
    note: str = ""


class CustomModelRequest(BaseModel):
    code: str = Field(..., min_length=3)
    title: str = Field(..., min_length=5)
    area: str = Field(..., min_length=3)
    prefix: str = ""
    when_to_use: str = ""
    identification_triggers: str = ""
    recommended_structure: str = ""
    body: str = Field(..., min_length=20)
    keywords: list[str] = Field(default_factory=list)
    placeholders: list[str] = Field(default_factory=list)
    source_training_index: int | None = None


class OwlAssistantRequest(BaseModel):
    action: str = Field(..., min_length=3)
    context: dict[str, object] = Field(default_factory=dict)
    selectedText: str = ""


def _extract_pdf_text(data: bytes) -> tuple[str, int]:
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise HTTPException(status_code=400, detail="PDF protegido por senha.") from exc

    pages_text = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if text:
            pages_text.append(f"--- Pagina {index} ---\n{text}")
    return "\n\n".join(pages_text).strip(), len(reader.pages)


def _message_text(message: dict[str, object]) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if isinstance(parts, list):
        chunks = []
        for part in parts:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                chunks.append(json.dumps(part, ensure_ascii=False))
        return "\n".join(chunks).strip()
    text = content.get("text")
    return text.strip() if isinstance(text, str) else ""


def _conversation_chunks(conversation: dict[str, object]) -> list[dict[str, str]]:
    title = str(conversation.get("title") or "Conversa sem titulo")
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return []

    chunks = []
    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        author = message.get("author") or {}
        role = author.get("role") if isinstance(author, dict) else ""
        text = _message_text(message)
        if role != "assistant" or len(text) < 250:
            continue
        chunks.append({"title": title, "text": text})
    return chunks


def _legal_score(text: str) -> int:
    normalized = text.lower()
    return sum(1 for hint in LEGAL_IMPORT_HINTS if hint in normalized)


def _load_history_jsons(filename: str, data: bytes) -> list[object]:
    lower = filename.lower()
    if lower.endswith(".zip"):
        try:
            with ZipFile(BytesIO(data)) as archive:
                names = [
                    name
                    for name in archive.namelist()
                    if name.lower().endswith(".json")
                    and ("conversation" in name.lower() or "chat" in name.lower())
                ]
                if not names:
                    names = [name for name in archive.namelist() if name.lower().endswith(".json")]
                return [json.loads(archive.read(name).decode("utf-8-sig")) for name in names[:5]]
        except BadZipFile as exc:
            raise HTTPException(status_code=400, detail="Arquivo ZIP invalido.") from exc
    if lower.endswith(".json"):
        return [json.loads(data.decode("utf-8-sig"))]
    raise HTTPException(status_code=400, detail="Envie um .zip ou .json da exportacao do ChatGPT.")


def _training_analysis(text: str, limit: int = 5) -> dict[str, object]:
    detected_area, area_hits = detect_area(text)
    results = recommend(text, list_templates(), limit=limit)
    top = results[0] if results else None
    if top and top.score >= 90:
        suggestion = {
            "type": "variation",
            "target_code": top.code,
            "message": f"Provavel variacao do modelo {top.code}. Salve como exemplo se estiver correto.",
        }
    else:
        suggestion = {
            "type": "new_model",
            "target_code": "",
            "message": "Pode ser candidato a novo modelo ou a uma variacao ainda fraca. Revise antes de incorporar.",
        }
    return {
        "detected_area": detected_area,
        "area_hits": area_hits,
        "suggestion": suggestion,
        "tokens": sorted(tokenize(text))[:80],
        "recommendations": [result.__dict__ for result in results],
    }


def _import_chatgpt_history(filename: str, data: bytes) -> dict[str, object]:
    imported = 0
    ignored = 0
    candidates = []
    for parsed in _load_history_jsons(filename, data):
        if isinstance(parsed, list):
            conversations = parsed
        elif isinstance(parsed, dict):
            conversations = parsed.get("conversations", [])
        else:
            conversations = []

        for conversation in conversations:
            if not isinstance(conversation, dict):
                continue
            for chunk in _conversation_chunks(conversation):
                score = _legal_score(chunk["title"] + "\n" + chunk["text"])
                if score < 2:
                    ignored += 1
                    continue

                text = chunk["text"][:8000]
                analysis = _training_analysis(text, limit=3)
                recs = analysis.get("recommendations") or []
                target_code = recs[0]["code"] if recs else ""
                training = {
                    "text": text,
                    "action": "chatgpt_import",
                    "target_code": target_code,
                    "status": "pending",
                    "note": f"Importado do historico ChatGPT: {chunk['title']}",
                    "title": chunk["title"],
                    "analysis": {**analysis, "legal_score": score},
                    "source": "chatgpt_export",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                save_training(training)
                imported += 1
                candidates.append(
                    {
                        "title": chunk["title"],
                        "score": score,
                        "target_code": target_code,
                        "preview": text[:220],
                    }
                )
                if imported >= 80:
                    return {"imported": imported, "ignored": ignored, "candidates": candidates}
    return {"imported": imported, "ignored": ignored, "candidates": candidates}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return r"""
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Codex Coruj IA - Sistema Juridico</title>
  <style>
    :root { color-scheme: dark; font-family: Garamond, "EB Garamond", "Adobe Garamond Pro", Georgia, serif; }
    body { margin: 0; background: #07111f; color: #e7edf7; }
    main { display: grid; grid-template-columns: 360px 1fr 420px; height: 100vh; overflow: hidden; }
    aside, section { border-right: 1px solid #24324a; padding: 18px; overflow: auto; }
    h1 { font-size: 22px; margin: 0 0 14px; }
    h2 { font-size: 16px; margin: 0 0 10px; color: #f6a21a; }
    input, textarea { width: 100%; box-sizing: border-box; background: #0c1a2e; border: 1px solid #2d4162; color: #e7edf7; border-radius: 6px; padding: 10px; }
    textarea { min-height: 250px; resize: vertical; line-height: 1.45; }
    button { background: #0f52ba; color: white; border: 0; padding: 10px 12px; border-radius: 6px; cursor: pointer; }
    button:hover { background: #1768e5; }
    .secondary { background: #1f3150; }
    .ok { background: #087b3a; }
    .bad { background: #8a2638; }
    .area-group { margin-top: 14px; border-top: 1px solid #24324a; padding-top: 12px; }
    .area-title { color: #f6a21a; font-size: 12px; font-weight: 700; text-transform: uppercase; display: flex; justify-content: space-between; gap: 8px; }
    .count { background: #1e3354; color: #dce8ff; border-radius: 999px; padding: 1px 8px; font-size: 11px; }
    .model { padding: 10px; border-bottom: 1px solid #1e2b40; cursor: pointer; border-left: 3px solid transparent; }
    .model:hover, .model.active { background: #122644; border-left-color: #f6a21a; }
    .code { color: #8db7ff; font-weight: 700; font-size: 12px; }
    .title { margin-top: 4px; font-size: 14px; }
    .meta { color: #aab5c5; font-size: 12px; margin-top: 4px; }
    .detail-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 12px 0; }
    .detail-card, .result, .feedback-panel, .training-panel, .use-panel { background: #0b1a2f; border: 1px solid #273a59; border-radius: 8px; padding: 12px; }
    .detail-label, .field label { color: #f6a21a; font-size: 11px; font-weight: 700; text-transform: uppercase; }
    .detail-text { margin-top: 6px; color: #dbe6f7; font-size: 13px; line-height: 1.35; }
    .chips, .actions, .feedback, .training-actions, .final-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .chips { margin: 10px 0 12px; }
    .chip { border: 1px solid #2d4162; background: #10233d; border-radius: 999px; padding: 4px 8px; font-size: 12px; color: #c7d7ef; }
    pre { white-space: pre-wrap; line-height: 1.55; background: #09182b; border: 1px solid #24324a; padding: 16px; border-radius: 8px; max-height: 360px; overflow: auto; }
    #preview.is-empty { display: none; }
    .preview-idle {
      width: min(840px, calc(100% - 30px));
      min-height: 390px;
      height: min(560px, calc(100vh - 280px));
      margin: 12px auto;
      border: 1px solid #24324a;
      border-radius: 8px;
      overflow: hidden;
      background: #0b1a2f center/contain no-repeat;
      box-shadow: 0 18px 42px rgba(0,0,0,.24);
      position: relative;
      transition: opacity .38s ease, filter .38s ease;
    }
    .preview-idle::before {
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, rgba(8,22,39,.05), rgba(8,22,39,.20));
      pointer-events: none;
    }
    .preview-idle::after {
      content: attr(data-caption);
      position: absolute;
      left: 18px;
      bottom: 16px;
      max-width: min(360px, calc(100% - 36px));
      color: rgba(255,255,255,.92);
      background: rgba(6,22,38,.52);
      border: 1px solid rgba(255,255,255,.24);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 12px;
      font-family: "Segoe UI", Tahoma, Arial, sans-serif;
      box-shadow: 0 12px 24px rgba(0,0,0,.22);
      backdrop-filter: blur(4px);
    }
    .preview-idle.is-fading { opacity: .72; filter: saturate(.9); }
    .preview-idle.hidden { display: none; }
    .result { margin-top: 10px; }
    .score { color: #62d26f; font-weight: 700; }
    .feedback input, .training-actions input { flex: 1 1 120px; padding: 8px; }
    .toast { margin-top: 10px; color: #62d26f; min-height: 18px; font-size: 13px; }
    .feedback-panel, .training-panel, .use-panel { display: none; margin-top: 12px; }
    .feedback-row { border-top: 1px solid #1d2c43; padding-top: 8px; margin-top: 8px; font-size: 12px; }
    .feedback-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .metric { background: #0d2038; border-radius: 6px; padding: 8px; }
    .training-panel textarea { min-height: 170px; }
    .training-note, .auto-note { border: 1px solid #274c75; background: #0b2038; border-radius: 8px; padding: 10px; margin: 10px 0; font-size: 13px; color: #c8dcff; }
    .auto-note { display: none; }
    .motor-status-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }
    .motor-status-card { background: #0d2038; border: 1px solid #273a59; border-radius: 6px; padding: 8px; }
    .motor-status-card b { display: block; color: #e7edf7; font-size: 16px; }
    .placeholder-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 10px 0; }
    .field { margin-top: 10px; }
    .field label { display: block; margin-bottom: 5px; }
    .field input { min-height: 38px; }
    .filled-preview { max-height: 390px; margin-top: 12px; }
    .final-panel { margin-top: 14px; border-top: 1px solid #273a59; padding-top: 12px; }
    .warning { display: none; margin-top: 10px; border: 1px solid #8a6d20; background: #2c2309; color: #ffd978; border-radius: 6px; padding: 10px; font-size: 13px; }
    .mode-toggle { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px; }
    .mode-toggle label { border: 1px solid #2d4162; background: #0b1a2f; border-radius: 8px; padding: 9px; cursor: pointer; font-size: 13px; }
    .mode-toggle input { width: auto; margin-right: 6px; }
    .pdf-drop { border: 1px dashed #3d5f91; background: #0a1c32; color: #c8dcff; border-radius: 8px; padding: 12px; margin-bottom: 10px; cursor: pointer; font-size: 13px; display: block; }
    .pdf-drop:hover, .pdf-drop.dragging { border-color: #f6a21a; background: #102640; }
    .pdf-drop input { display: none; }
    .pdf-drop b { color: #f6a21a; }
    .owl-assistant { position: fixed; right: 16px; bottom: 14px; z-index: 30; transition: right .38s cubic-bezier(.2,.8,.2,1), bottom .38s cubic-bezier(.2,.8,.2,1), transform .38s cubic-bezier(.2,.8,.2,1); }
    .owl-assistant.is-menu-open { right: 50%; bottom: 50%; z-index: 48; transform: translate(50%, 34%) scale(1.22); }
    .owl-button { position: relative; width: 120px; height: 120px; border-radius: 8px; padding: 0; border: 0; background: transparent; box-shadow: none; display: grid; place-items: center; overflow: visible; }
    .owl-button:hover { background: transparent; }
    .owl-button img { width: 116px; height: 116px; object-fit: contain; pointer-events: none; filter: drop-shadow(0 14px 22px rgba(0,0,0,.46)); transform-origin: center bottom; transition: transform .16s ease, filter .16s ease; animation: owlFloat 3.8s ease-in-out infinite; }
    .owl-button:hover img { transform: translateY(-3px) scale(1.03); filter: drop-shadow(0 18px 26px rgba(0,0,0,.5)); }
    .owl-button.is-awake img { animation: owlFloat 3.8s ease-in-out infinite, owlAttention 1.3s ease-in-out infinite; }
    .owl-button.is-working img { animation: owlFloat 2.2s ease-in-out infinite, owlWorking 1s ease-in-out infinite; }
    .owl-assistant.is-menu-open .owl-button img { animation: owlArrive .42s ease-out, owlFloat 3.4s ease-in-out infinite .42s; }
    .owl-button::after { content: ""; position: absolute; width: 46px; height: 14px; right: 35px; bottom: 0; border-radius: 50%; background: rgba(0,0,0,.28); filter: blur(6px); animation: owlShadow 3.8s ease-in-out infinite; }
    .owl-menu, .owl-context-menu, .owl-panel { position: fixed; z-index: 40; background: #0b1a2f; border: 1px solid #273a59; box-shadow: 0 18px 44px rgba(0,0,0,.38); border-radius: 8px; color: #e7edf7; }
    .owl-menu { right: 50%; bottom: calc(50% - 326px); width: min(660px, calc(100vw - 48px)); display: none; padding: 16px; transform: translateX(50%) translateY(14px) scale(.96); opacity: 0; border-color: #74501b; background: linear-gradient(180deg, #10233d 0%, #071625 100%); box-shadow: 0 24px 70px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.08); }
    .owl-context-menu { min-width: 260px; display: none; padding: 10px; border-color: #74501b; background: #0b1a2f; }
    .owl-menu.open, .owl-context-menu.open, .owl-panel.open { display: block; }
    .owl-menu.open { animation: owlMenuReveal .28s ease-out forwards; }
    .owl-menu-title { padding: 4px 8px 14px; border-bottom: 1px solid rgba(246,162,26,.28); margin-bottom: 12px; text-align: center; }
    .owl-menu-title b { display: block; font-size: 18px; color: #fff6df; letter-spacing: .2px; }
    .owl-menu-title span { color: #c9d7e8; font-size: 12px; }
    .owl-action-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
    .owl-context-menu .owl-action-grid { display: block; }
    .owl-action { width: 100%; min-height: 62px; display: grid; grid-template-columns: 34px 1fr; gap: 10px; align-items: center; background: rgba(255,255,255,.045); border: 1px solid rgba(141,183,255,.18); border-radius: 8px; color: #dbe6f7; padding: 10px; text-align: left; }
    .owl-context-menu .owl-action { min-height: 0; margin-top: 7px; }
    .owl-action:hover { background: rgba(246,162,26,.12); border-color: rgba(246,162,26,.62); box-shadow: inset 0 0 0 1px rgba(246,162,26,.14); }
    .owl-action-mark { width: 32px; height: 32px; border-radius: 8px; display: grid; place-items: center; color: #ffd978; background: #132b4a; border: 1px solid rgba(246,162,26,.45); font-size: 14px; font-weight: 800; }
    .owl-action-text { display: grid; gap: 3px; min-width: 0; }
    .owl-action span { font-weight: 700; line-height: 1.15; }
    .owl-action small { color: #9fbde7; font-size: 11px; }
    .owl-panel { right: 24px; bottom: 122px; width: min(390px, calc(100vw - 48px)); max-height: min(620px, calc(100vh - 150px)); display: none; overflow: hidden; }
    .owl-panel-header { display: grid; grid-template-columns: 54px 1fr auto; gap: 10px; align-items: center; padding: 12px; border-bottom: 1px solid #24324a; background: #0d2038; }
    .owl-panel-header img { width: 52px; height: 52px; object-fit: contain; }
    .owl-panel-title { font-weight: 700; font-size: 15px; }
    .owl-panel-subtitle { color: #aab5c5; font-size: 12px; margin-top: 2px; }
    .owl-close { background: #1f3150; width: 32px; height: 32px; padding: 0; border-radius: 6px; }
    .owl-panel-body { padding: 14px; overflow: auto; max-height: 520px; font-size: 13px; line-height: 1.45; }
    .owl-section { margin-top: 12px; }
    .owl-section:first-child { margin-top: 0; }
    .owl-section-label { color: #f6a21a; font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 6px; }
    .owl-list { margin: 0; padding-left: 18px; color: #dbe6f7; }
    .owl-draft { white-space: pre-wrap; background: #09182b; border: 1px solid #24324a; border-radius: 8px; padding: 10px; color: #dbe6f7; max-height: 150px; overflow: auto; }
    @keyframes owlFloat {
      0%, 100% { transform: translateY(0) rotate(-.5deg); }
      50% { transform: translateY(-7px) rotate(.8deg); }
    }
    @keyframes owlShadow {
      0%, 100% { transform: scaleX(1); opacity: .32; }
      50% { transform: scaleX(.82); opacity: .2; }
    }
    @keyframes owlAttention {
      0%, 100% { filter: drop-shadow(0 14px 22px rgba(0,0,0,.46)); }
      50% { filter: drop-shadow(0 18px 28px rgba(246,162,26,.32)); }
    }
    @keyframes owlWorking {
      0%, 100% { scale: 1; }
      50% { scale: 1.045; }
    }
    @keyframes owlArrive {
      0% { transform: translateY(10px) rotate(-6deg) scale(.82); }
      72% { transform: translateY(-8px) rotate(2deg) scale(1.08); }
      100% { transform: translateY(0) rotate(0) scale(1); }
    }
    @keyframes owlMenuReveal {
      from { opacity: 0; transform: translateX(50%) translateY(18px) scale(.96); }
      to { opacity: 1; transform: translateX(50%) translateY(0) scale(1); }
    }
    @media (prefers-reduced-motion: reduce) {
      .owl-button img, .owl-button.is-awake img, .owl-button.is-working img, .owl-button::after { animation: none; }
    }
    .owl-pointing * { cursor: crosshair !important; }
    .owl-target-halo { position: fixed; z-index: 35; pointer-events: none; border: 2px solid #f6a21a; box-shadow: 0 0 0 4px rgba(246, 162, 26, .18), 0 12px 26px rgba(0,0,0,.24); border-radius: 6px; display: none; transition: all .12s ease; }
    .saj-shell { height: 100vh; display: grid; grid-template-rows: 24px 24px 40px 24px 1fr 18px; background: #e6ebf1; color: #111; font-family: "Segoe UI", Tahoma, Arial, sans-serif; overflow: hidden; }
    .saj-titlebar { background: #0b4f7d; color: #dcecf8; display: flex; align-items: center; gap: 7px; padding: 0 8px; font-size: 12px; }
    .saj-titlebar img { width: 18px; height: 18px; border-radius: 2px; object-fit: cover; object-position: center 18%; }
    .saj-titlebar strong { font-weight: 600; }
    .saj-clock { margin-left: auto; display: flex; gap: 8px; align-items: center; font-weight: 600; }
    .saj-menubar { background: #0f5f96; color: #fff; display: flex; align-items: center; gap: 16px; padding: 0 8px; font-size: 12px; }
    .saj-menubar span { white-space: nowrap; }
    .saj-toolbar { background: #17689e; border-bottom: 1px solid #0b3f65; display: flex; align-items: center; gap: 4px; padding: 4px 8px; color: #fff; }
    .saj-tool { background: #0f3d66; color: #fff; border: 1px solid rgba(255,255,255,.28); border-radius: 2px; padding: 6px 9px; font-size: 12px; font-weight: 600; box-shadow: inset 0 1px 0 rgba(255,255,255,.12); }
    .saj-tool:hover { background: #1d75ae; }
    .saj-search { margin-left: auto; width: 250px; height: 22px; border: 0; border-radius: 1px; padding: 0 8px; background: #fff; color: #102033; font-family: "Segoe UI", Tahoma, Arial, sans-serif; font-size: 12px; }
    .saj-modulebar { background: #303030; color: #d9d9d9; display: flex; align-items: center; gap: 8px; padding: 0 8px; font-size: 12px; border-bottom: 1px solid #6b6b6b; }
    .saj-modulebar strong { font-weight: 500; }
    .saj-modulebar .window-actions { margin-left: auto; color: #bfbfbf; letter-spacing: 8px; }
    .saj-statusbar { background: #eef3f8; border-top: 1px solid #b8c7d7; color: #16466d; display: flex; align-items: center; gap: 18px; padding: 0 8px; font-size: 11px; }
    .saj-shell main { height: 100%; min-height: 0; overflow: hidden; grid-template-columns: 286px minmax(460px, 1fr) 398px; background: #e8edf3; color: #111; font-family: "Segoe UI", Tahoma, Arial, sans-serif; }
    .saj-shell aside, .saj-shell section { min-height: 0; background: #f8fafc; border-right: 1px solid #aeb9c5; padding: 8px; overflow: auto; }
    .saj-shell aside { background: #fff; }
    .saj-shell section:nth-of-type(1) { background: #e8edf3; }
    .saj-shell section:nth-of-type(2) { background: #f2f6fa; }
    .saj-shell h1 { color: #123a60; font-size: 13px; margin: 0 0 6px; font-family: "Segoe UI", Tahoma, Arial, sans-serif; }
    .saj-shell h2 { color: #003f6b; font-size: 13px; margin: 0 0 7px; font-family: "Segoe UI", Tahoma, Arial, sans-serif; }
    .saj-shell input, .saj-shell textarea { background: #fff; color: #111; border: 1px solid #9fb4c8; border-radius: 2px; font-family: Consolas, "Courier New", monospace; }
    .saj-shell textarea { min-height: 210px; }
    .saj-shell button { background: #176aa4; border: 1px solid #0f578a; color: #fff; border-radius: 2px; padding: 7px 10px; font-weight: 600; }
    .saj-shell button:hover { background: #0f578a; }
    .saj-shell .secondary { background: #dce7f2; color: #12395d; border-color: #b5c6d7; }
    .saj-shell .ok { background: #15914a; border-color: #0c7639; color: #fff; }
    .saj-shell .bad { background: #b9354b; border-color: #982b3d; color: #fff; }
    .saj-shell .area-group { position: relative; border-top: 0; margin-top: 1px; padding-top: 0; }
    .saj-shell .area-title { position: relative; background: transparent; color: #111; border-bottom: 0; margin: 0; padding: 2px 4px 2px 28px; font-size: 12px; text-transform: none; font-weight: 400; display: flex; justify-content: flex-start; gap: 4px; line-height: 16px; cursor: pointer; }
    .saj-shell .area-title::before { content: "+"; position: absolute; left: 2px; top: 3px; width: 11px; height: 11px; border: 1px solid #7f7f7f; background: #fff; color: #111; font: 11px/10px Consolas, monospace; text-align: center; }
    .saj-shell .area-group.expanded .area-title::before { content: "-"; }
    .saj-shell .area-title::after { content: ""; position: absolute; left: 18px; top: 2px; width: 12px; height: 14px; border-left: 1px solid #9c9c9c; border-bottom: 1px solid #9c9c9c; }
    .saj-shell .count { display: none; }
    .saj-shell .model-list { display: none; margin-left: 18px; border-left: 1px dotted #9c9c9c; }
    .saj-shell .area-group.expanded .model-list { display: block; }
    .saj-shell .model { position: relative; border-bottom: 0; color: #111; padding: 2px 4px 2px 28px; border-left: 0; background: transparent; font-size: 12px; line-height: 16px; }
    .saj-shell .model::before { content: ""; position: absolute; left: 0; top: 9px; width: 12px; border-top: 1px dotted #9c9c9c; }
    .saj-shell .model::after { content: ""; position: absolute; left: 14px; top: 3px; width: 11px; height: 13px; border: 1px solid #7f7f7f; background: #fff; box-sizing: border-box; }
    .saj-shell .model:hover { background: #e8f1fb; }
    .saj-shell .model.active { background: #c7dcf2; outline: 1px dotted #333; outline-offset: -1px; }
    .saj-shell .code { display: none; }
    .saj-shell .title { color: #111; font-size: 12px; line-height: 16px; font-weight: 400; }
    .saj-shell .meta { color: #526579; }
    .saj-shell .detail-card, .saj-shell .result, .saj-shell .feedback-panel, .saj-shell .training-panel, .saj-shell .use-panel, .saj-shell .training-note, .saj-shell .auto-note { background: #fff; border: 1px solid #c4d2e1; color: #102033; border-radius: 3px; }
    .saj-shell .detail-label, .saj-shell .field label { color: #003f6b; }
    .saj-shell .chip { background: #eaf3ff; border-color: #a8c1dc; color: #003f6b; }
    .saj-shell pre { background: #fff; color: #111; border: 1px solid #c7c7c7; border-radius: 2px; box-shadow: 0 2px 8px rgba(0,0,0,.10); font-family: Garamond, "Times New Roman", serif; font-size: 17px; line-height: 1.65; max-height: 520px; padding: 42px 58px; width: min(760px, calc(100% - 30px)); margin: 12px auto; box-sizing: border-box; }
    .saj-shell .filled-preview { max-height: 420px; }
    .saj-shell .pdf-drop { background: #edf6ff; color: #163a5c; border-color: #9fbad7; }
    .saj-shell .pdf-drop b { color: #003f6b; }
    .saj-shell .toast { color: #087b3a; }
    .saj-shell .warning { background: #fff4cf; color: #694b00; border-color: #d6a800; }
    .saj-shell .feedback-row { border-top-color: #d7e0ea; }
    .saj-shell .score { color: #0b8c3d; }
    .saj-shell .mode-toggle label { background: #fff; border-color: #b5c6d7; color: #12395d; }
    .saj-shell .motor-status-card { background: #fff; border-color: #c4d2e1; }
    .saj-shell .motor-status-card b { color: #003f6b; }
    .saj-shell .preview-idle {
      border-color: #c4d2e1;
      border-radius: 2px;
      box-shadow: 0 2px 8px rgba(0,0,0,.12);
      min-height: 420px;
      height: min(600px, calc(100vh - 255px));
    }
    .saj-shell .owl-menu, .saj-shell .owl-context-menu, .saj-shell .owl-panel { background: #0b1a2f; color: #e7edf7; }
    .saj-shell .owl-button { background: transparent; border: 0; box-shadow: none; }
    .saj-shell .owl-button:hover { background: transparent; }
    @media (max-width: 900px) {
      .saj-shell { grid-template-rows: 24px 24px auto 24px 1fr 18px; }
      .saj-toolbar { flex-wrap: wrap; height: auto; }
      .saj-search { width: 100%; margin-left: 0; }
      main, .saj-shell main { grid-template-columns: 1fr; overflow: auto; height: auto; }
      aside, section { min-height: auto; border-right: 0; border-bottom: 1px solid #24324a; }
      .owl-assistant.is-menu-open { transform: translate(50%, 32%) scale(1); }
      .owl-menu { bottom: calc(50% - 300px); }
      .owl-action-grid { grid-template-columns: 1fr; }
      .owl-button { width: 88px; height: 88px; }
      .owl-button img { width: 84px; height: 84px; }
    }
  </style>
</head>
<body>
<div class="saj-shell">
<header class="saj-titlebar">
  <img src="/static/coruj_assets/coruj-analisar.png" alt="" />
  <strong>Codex Coruj IA - Sistema de Automacao da Justica - Ministerio Publico</strong>
  <div class="saj-clock"><span>Padrao II</span><span>Codex MP Assistente</span></div>
</header>
<nav class="saj-menubar">
  <span>Cadastro</span>
  <span>Editor</span>
  <span>Andamento</span>
  <span>Carga</span>
  <span>Consulta</span>
  <span>Relatorios</span>
  <span>Apoio</span>
  <span>Ajuda</span>
</nav>
<div class="saj-toolbar">
  <button class="saj-tool" type="button" data-saj-action="import">Carga e Importacao</button>
  <button class="saj-tool" type="button" data-saj-action="flow">Fluxo de Trabalho</button>
  <button class="saj-tool" type="button" data-saj-action="files">Gerenciador de Arquivos</button>
  <button class="saj-tool" type="button" data-saj-action="search">Consulta Avancada</button>
  <button class="saj-tool" type="button" data-saj-action="deadlines">Agenda de Compromissos</button>
  <input class="saj-search" id="sajSearch" placeholder="Qual funcionalidade voce busca?" />
</div>
<div class="saj-modulebar">
  <strong>Fluxo de Trabalho</strong>
  <span>Legenda</span>
  <span>Estilo da visualizacao</span>
  <span>Padrao II</span>
  <span class="window-actions">-_x</span>
</div>
<main>
  <aside>
    <h1>Modelos</h1>
    <input id="search" placeholder="Buscar modelos..." />
    <div id="models"></div>
  </aside>
  <section>
    <h2 id="selectedTitle">Selecione um modelo</h2>
    <div class="meta" id="selectedMeta"></div>
    <div class="chips" id="selectedChips"></div>
    <div class="detail-grid" id="selectedDetails"></div>
    <div class="actions" id="modelActions" style="display:none">
      <button onclick="toggleUseModel()">Usar modelo</button>
      <button class="secondary" onclick="copyOriginalText()">Copiar texto</button>
    </div>
    <div class="preview-idle" id="previewIdle" aria-label="Paisagem de descanso da area de previa"></div>
    <pre id="preview" class="is-empty"></pre>
    <div class="use-panel" id="usePanel">
      <h2>Preencher modelo</h2>
      <div class="placeholder-grid" id="placeholderFields"></div>
      <p>
        <button onclick="generateFilledText()">Gerar texto</button>
        <button class="secondary" onclick="clearPlaceholderFields()">Limpar campos</button>
      </p>
      <div class="warning" id="pendingWarning"></div>
      <div class="final-panel">
        <h2>Texto final</h2>
        <div class="final-actions"><button class="secondary" onclick="copyFilledText()">Copiar texto final</button></div>
        <pre class="filled-preview" id="filledPreview">Preencha os campos e gere o texto.</pre>
      </div>
    </div>
  </section>
  <section>
    <h2>Recomendar modelo</h2>
    <div class="mode-toggle">
      <label><input type="radio" name="workMode" value="auto" checked />Automatico</label>
      <label><input type="radio" name="workMode" value="semi" />Semi-automatico</label>
    </div>
    <label class="pdf-drop" id="pdfDrop">
      <input id="pdfInput" type="file" accept="application/pdf,.pdf" />
      <b>PDF</b>: solte aqui ou clique para extrair o texto do processo.
    </label>
    <textarea id="caseText" placeholder="Cole aqui o texto do caso, andamento, decisao ou resumo do processo..."></textarea>
    <p>
      <button onclick="recommendModel()">Recomendar</button>
      <button class="secondary" onclick="newCase()">Novo caso</button>
      <button class="secondary" onclick="toggleFeedback()">Ver avaliacoes</button>
      <button class="secondary" onclick="toggleTraining()">Treinar</button>
    </p>
    <div class="toast" id="toast"></div>
    <div class="auto-note" id="autoNote"></div>
    <div class="feedback-panel" id="feedbackPanel"></div>
    <div class="training-panel" id="trainingPanel">
      <h2>Motor de aprendizado</h2>
      <label class="pdf-drop" id="motorDrop">
        <input id="motorInput" type="file" accept=".rtf,.zip,.pdf,.docx,.txt,application/rtf,application/zip,application/x-zip-compressed,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" />
        <b>Corpus juridico</b>: envie RTF, ZIP, PDF, DOCX ou TXT para o motor aprender padroes.
      </label>
      <div class="training-actions">
        <button onclick="loadMotorStatus()">Status do motor</button>
        <button class="secondary" onclick="processMotorQueue()">Processar fila</button>
      </div>
      <div id="motorStatus"></div>
      <h2>Analisar manifestacao</h2>
      <label class="pdf-drop" id="chatHistoryDrop">
        <input id="chatHistoryInput" type="file" accept=".zip,.json,application/json,application/zip" />
        <b>Documento/arquivo</b>: solte aqui ou clique para importar parecer, processo, texto, JSON ou historico.
      </label>
      <textarea id="trainingText" placeholder="Cole uma manifestacao real ou minuta para classificar e salvar como exemplo de treinamento."></textarea>
      <div class="training-actions">
        <button onclick="analyzeTrainingText()">Analisar</button>
        <button class="secondary" onclick="loadTraining()">Ver salvos</button>
        <button class="secondary" onclick="saveTrainingExample('review')">Salvar para revisao</button>
      </div>
      <div id="trainingResult"></div>
    </div>
    <div id="results"></div>
  </section>
</main>
<footer class="saj-statusbar"><span>0 objetos selecionados</span><span>Base local de modelos e aprendizagem ativa</span></footer>
</div>
<div class="owl-assistant" id="owlAssistantRoot"></div>
<div class="owl-menu" id="owlAssistantMenu" aria-hidden="true"></div>
<div class="owl-context-menu" id="owlContextMenu" aria-hidden="true"></div>
<div class="owl-panel" id="owlAssistantPanel" aria-hidden="true"></div>
<div class="owl-target-halo" id="owlTargetHalo"></div>
<script>
let allModels = [];
let selectedModelCode = null;
let selectedModel = null;
let filledText = '';
let readyToReplaceCase = false;
let lastTrainingAnalysis = {};

const owlAssistantService = {
  async run(action, context = {}, selectedText = '') {
    const res = await fetch('/api/owl-assistant', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action, context, selectedText})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Nao foi possivel acionar a Coruj IA.');
    return data.response;
  }
};

const OwlAssistant = (() => {
  const primaryImages = {
    idle: '/assets/owl/owl-idle.png',
    writing: '/assets/owl/owl-writing.png',
    investigate: '/assets/owl/owl-investigate.png',
    deadline: '/assets/owl/owl-deadline.png',
    warning: '/assets/owl/owl-warning.png',
    checklist: '/assets/owl/owl-checklist.png',
    success: '/assets/owl/owl-success.png',
    teacher: '/assets/owl/owl-teacher.png'
  };
  const fallbackImages = {
    idle: '/static/coruj_assets/coruj-explicar.png',
    writing: '/static/coruj_assets/coruj-minuta.png',
    investigate: '/static/coruj_assets/coruj-analisar.png',
    deadline: '/static/coruj_assets/coruj-prazos.png',
    warning: '/static/coruj_assets/coruj-inconsistencia.png',
    checklist: '/static/coruj_assets/coruj-analisar.png',
    success: '/static/coruj_assets/coruj-explicar.png',
    teacher: '/static/coruj_assets/coruj-explicar.png'
  };
  const assetVersion = 'transparent-export-20260508';
  const actions = [
    {id: 'summarize_process', label: 'Resumir processo', state: 'investigate', hint: 'autos', mark: 'R'},
    {id: 'analyze_urgency', label: 'Analisar urgencia', state: 'deadline', hint: 'prazo', mark: '!'},
    {id: 'check_pages', label: 'Conferir fls.', state: 'checklist', hint: 'fls.', mark: 'F'},
    {id: 'identify_pending', label: 'Identificar pendencias', state: 'warning', hint: 'risco', mark: '?'},
    {id: 'generate_report', label: 'Gerar relatorio', state: 'writing', hint: 'texto', mark: 'D'},
    {id: 'generate_opinion', label: 'Gerar parecer', state: 'writing', hint: 'minuta', mark: 'P'},
    {id: 'rewrite_excerpt', label: 'Reescrever trecho', state: 'writing', hint: 'editor', mark: 'E'},
    {id: 'explain_decision', label: 'Explicar decisao', state: 'teacher', hint: 'ajuda', mark: 'J'},
    {id: 'suggest_next_act', label: 'Sugerir proximo ato', state: 'success', hint: 'fluxo', mark: 'A'}
  ];
  let state = 'idle';
  let currentContext = {};
  let pointingMode = false;

  function imageFor(nextState) {
    return `${primaryImages[nextState] || primaryImages.idle}?v=${assetVersion}`;
  }

  function setState(nextState) {
    state = nextState || 'idle';
    document.querySelectorAll('[data-owl-image]').forEach(img => {
      img.dataset.fallback = fallbackImages[state] || fallbackImages.idle;
      img.src = imageFor(state);
    });
  }

  function setAnimationMode(mode = 'idle') {
    const button = document.querySelector('.owl-button');
    if (!button) return;
    button.classList.toggle('is-awake', mode === 'awake');
    button.classList.toggle('is-working', mode === 'working');
  }

  function imageTag(extra = '') {
    return `<img data-owl-image ${extra} src="${imageFor(state)}" data-fallback="${fallbackImages[state] || fallbackImages.idle}" alt="Codex Coruj IA" onerror="this.onerror=null; this.src=this.dataset.fallback;" />`;
  }

  function renderActions(targetId) {
    return actions.map(action =>
      `<button class="owl-action" type="button" data-owl-action="${action.id}" data-owl-state="${action.state}">
        <span class="owl-action-mark">${escapeHtml(action.mark)}</span>
        <span class="owl-action-text"><span>${escapeHtml(action.label)}</span><small>${escapeHtml(action.hint)}</small></span>
      </button>`
    ).join('');
  }

  function renderMenus() {
    const menuBody = `<div class="owl-menu-title"><b>Codex Coruj IA</b><span>Assistente juridica contextual</span></div>
      <div class="owl-action-grid">
        <button class="owl-action" type="button" data-owl-point="true">
          <span class="owl-action-mark">+</span>
          <span class="owl-action-text"><span>Apontar na tela</span><small>clique onde quer ajuda</small></span>
        </button>
        ${renderActions()}
      </div>`;
    document.getElementById('owlAssistantMenu').innerHTML = menuBody;
    document.getElementById('owlContextMenu').innerHTML = menuBody;
  }

  function selectedText() {
    const active = document.activeElement;
    if (active && ['TEXTAREA', 'INPUT'].includes(active.tagName)) {
      return active.value.substring(active.selectionStart || 0, active.selectionEnd || 0);
    }
    return String(window.getSelection ? window.getSelection() : '').trim();
  }

  function contextFromElement(element) {
    const modelNode = element?.closest?.('.model');
    const resultNode = element?.closest?.('.result');
    const base = {
      selectedModelCode,
      selectedModelTitle: selectedModel?.title || '',
      caseText: document.getElementById('caseText')?.value || '',
      filledText,
      source: element?.id || element?.className || 'screen'
    };
    if (modelNode) {
      base.type = 'model';
      base.text = modelNode.textContent.trim();
      base.modelCode = modelNode.dataset.modelCode || '';
    } else if (resultNode) {
      base.type = 'process';
      base.text = resultNode.textContent.trim();
      base.processCode = resultNode.dataset.processCode || '';
    } else if (element?.id === 'preview') {
      base.type = 'document_preview';
      base.text = document.getElementById('preview').textContent;
    } else if (element?.id === 'caseText') {
      base.type = 'editor';
      base.text = document.getElementById('caseText').value;
    } else {
      base.type = 'screen';
    }
    return base;
  }

  function closeMenus() {
    document.getElementById('owlAssistantMenu').classList.remove('open');
    document.getElementById('owlContextMenu').classList.remove('open');
    document.getElementById('owlAssistantRoot').classList.remove('is-menu-open');
    setAnimationMode('idle');
  }

  function showHalo(element) {
    const halo = document.getElementById('owlTargetHalo');
    if (!element || !element.getBoundingClientRect) {
      halo.style.display = 'none';
      return;
    }
    const rect = element.getBoundingClientRect();
    halo.style.left = `${Math.max(0, rect.left - 4)}px`;
    halo.style.top = `${Math.max(0, rect.top - 4)}px`;
    halo.style.width = `${Math.max(24, rect.width + 8)}px`;
    halo.style.height = `${Math.max(24, rect.height + 8)}px`;
    halo.style.display = 'block';
  }

  function hideHalo() {
    document.getElementById('owlTargetHalo').style.display = 'none';
  }

  function startPointing() {
    closeMenus();
    pointingMode = true;
    document.body.classList.add('owl-pointing');
    setState('teacher');
    setAnimationMode('awake');
    renderPanel({
      title: 'Coruj IA solta na tela',
      summary: 'Clique em um campo, botao, modelo, resultado ou texto. Eu identifico a area e ofereco a acao mais util.',
      items: ['Use para perguntar sobre qualquer pedaco do app.', 'Depois de clicar, a area fica marcada em amarelo.'],
      draft: '',
      nextSteps: ['Clique no local do app onde voce quer minha ajuda.']
    });
  }

  function stopPointing() {
    pointingMode = false;
    document.body.classList.remove('owl-pointing');
  }

  function openGuideForTarget(target) {
    stopPointing();
    const focus = target.closest('.model, .result, #caseText, #preview, #filledPreview, #trainingPanel, button, input, textarea, pre') || target;
    currentContext = contextFromElement(focus);
    showHalo(focus);

    const text = focus.textContent || focus.value || '';
    const isCaseText = focus.id === 'caseText';
    const isModel = !!focus.closest('.model');
    const isResult = !!focus.closest('.result');
    const isFinal = focus.id === 'filledPreview';
    const isTraining = !!focus.closest('#trainingPanel');
    const suggested = isCaseText ? 'Recomendar modelo' : isModel ? 'Abrir/usar modelo' : isResult ? 'Avaliar resultado' : isFinal ? 'Copiar texto final' : isTraining ? 'Importar aprendizado' : 'Analisar contexto';

    renderPanel({
      title: 'Area identificada',
      summary: `Estou olhando para: ${suggested}.`,
      items: [
        text.trim().slice(0, 180) || 'Area sem texto direto.',
        'Posso agir a partir deste ponto ou voce pode apontar outro lugar.'
      ],
      draft: '',
      nextSteps: ['Escolha uma acao da Coruj IA ou continue usando o app normalmente.']
    });
  }

  function toggleMenu() {
    const menu = document.getElementById('owlAssistantMenu');
    const isOpen = menu.classList.toggle('open');
    menu.setAttribute('aria-hidden', String(!isOpen));
    document.getElementById('owlContextMenu').classList.remove('open');
    document.getElementById('owlAssistantRoot').classList.toggle('is-menu-open', isOpen);
    currentContext = contextFromElement(document.body);
    setAnimationMode(isOpen ? 'awake' : 'idle');
  }

  function showContextMenu(event) {
    const target = event.target.closest('.result, .model, #preview, #caseText, textarea, pre');
    if (!target) return;
    event.preventDefault();
    currentContext = contextFromElement(target);
    const menu = document.getElementById('owlContextMenu');
    menu.style.left = `${Math.min(event.clientX, window.innerWidth - 260)}px`;
    menu.style.top = `${Math.min(event.clientY, window.innerHeight - 360)}px`;
    menu.classList.add('open');
    menu.setAttribute('aria-hidden', 'false');
    document.getElementById('owlAssistantMenu').classList.remove('open');
    document.getElementById('owlAssistantRoot').classList.remove('is-menu-open');
    setAnimationMode('awake');
  }

  function renderPanel(response, pending = false) {
    const panel = document.getElementById('owlAssistantPanel');
    const title = pending ? 'Coruj IA em trabalho' : response.title;
    const body = pending
      ? '<div class="owl-section"><div class="owl-section-label">Status</div><div>Organizando o contexto e preparando a resposta...</div></div>'
      : `<div class="owl-section"><div class="owl-section-label">Sintese</div><div>${escapeHtml(response.summary)}</div></div>
        <div class="owl-section"><div class="owl-section-label">Achados</div><ul class="owl-list">${(response.items || []).map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>
        <div class="owl-section"><div class="owl-section-label">Rascunho</div><div class="owl-draft">${escapeHtml(response.draft || '')}</div></div>
        <div class="owl-section"><div class="owl-section-label">Proximos passos</div><ul class="owl-list">${(response.nextSteps || []).map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>`;
    panel.innerHTML = `<div class="owl-panel-header">
        ${imageTag()}
        <div><div class="owl-panel-title">${escapeHtml(title)}</div><div class="owl-panel-subtitle">Resposta contextual da assistente</div></div>
        <button class="owl-close" type="button" aria-label="Fechar Coruj IA" onclick="OwlAssistant.closePanel()">x</button>
      </div><div class="owl-panel-body">${body}</div>`;
    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
  }

  async function handleAction(actionId, nextState) {
    closeMenus();
    setState(nextState);
    setAnimationMode('working');
    renderPanel({title: 'Coruj IA em trabalho'}, true);
    try {
      const response = await owlAssistantService.run(actionId, currentContext, selectedText());
      setState(response.state || nextState);
      setAnimationMode('awake');
      renderPanel(response);
    } catch (error) {
      setState('warning');
      setAnimationMode('awake');
      renderPanel({
        title: 'Nao foi possivel concluir',
        summary: error.message,
        items: ['Verifique a conexao com o backend e tente novamente.'],
        draft: '',
        nextSteps: ['A rota /api/owl-assistant ja esta preparada para receber a requisicao.']
      });
    }
  }

  function bindEvents() {
    document.getElementById('owlAssistantRoot').innerHTML = `<button class="owl-button" type="button" aria-label="Abrir Codex Coruj IA" title="Codex Coruj IA">${imageTag()}</button>`;
    document.querySelector('.owl-button').addEventListener('click', toggleMenu);
    document.addEventListener('contextmenu', showContextMenu);
    document.addEventListener('click', event => {
      if (pointingMode && !event.target.closest('.owl-assistant, .owl-menu, .owl-context-menu, .owl-panel')) {
        event.preventDefault();
        event.stopPropagation();
        openGuideForTarget(event.target);
        return;
      }
      const point = event.target.closest('[data-owl-point]');
      if (point) {
        event.preventDefault();
        event.stopPropagation();
        startPointing();
        return;
      }
      const action = event.target.closest('[data-owl-action]');
      if (action) {
        handleAction(action.dataset.owlAction, action.dataset.owlState);
        return;
      }
      if (!event.target.closest('.owl-menu, .owl-context-menu, .owl-button')) closeMenus();
    });
  }

  function init() {
    renderMenus();
    bindEvents();
    setState('idle');
    setAnimationMode('idle');
  }

  return {
    init,
    setState,
    runAction: handleAction,
    closePanel: () => {
      document.getElementById('owlAssistantPanel').classList.remove('open');
      hideHalo();
      stopPointing();
      setAnimationMode('idle');
    }
  };
})();

function escapeHtml(value) {
  return String(value || '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

function currentMode() {
  const selected = document.querySelector('input[name="workMode"]:checked');
  return selected ? selected.value : 'auto';
}

const PREVIEW_LANDSCAPES = [
  { src: '/assets/landscapes/landscape-fantasy-01.jpg', caption: 'Ilhas suspensas' },
  { src: '/assets/landscapes/landscape-fantasy-02.jpg', caption: 'Prado nas nuvens' },
  { src: '/assets/landscapes/landscape-fantasy-03.jpg', caption: 'Bosque encantado' },
  { src: '/assets/landscapes/landscape-fantasy-04.jpg', caption: 'Deserto ao poente' },
  { src: '/assets/landscapes/landscape-fantasy-05.jpg', caption: 'Arvore luminosa' },
  { src: '/assets/landscapes/landscape-fantasy-06.jpg', caption: 'Vale de cristais' },
  { src: '/assets/landscapes/landscape-fantasy-07.jpg', caption: 'Aurora violeta' },
  { src: '/assets/landscapes/landscape-fantasy-08.jpg', caption: 'Cataratas celestes' },
  { src: '/assets/landscapes/landscape-fantasy-09.jpg', caption: 'Lago lunar' }
];
let previewLandscapeIndex = 0;
let previewLandscapeTimer = null;

function applyPreviewLandscape(index) {
  const idle = document.getElementById('previewIdle');
  if (!idle) return;
  const landscape = PREVIEW_LANDSCAPES[index % PREVIEW_LANDSCAPES.length];
  idle.style.backgroundImage = `url("${landscape.src}")`;
  idle.dataset.caption = landscape.caption;
}

function startPreviewLandscapes() {
  const idle = document.getElementById('previewIdle');
  const preview = document.getElementById('preview');
  if (!idle || !preview) return;
  idle.classList.remove('hidden');
  preview.classList.add('is-empty');
  applyPreviewLandscape(previewLandscapeIndex);
  if (previewLandscapeTimer) return;
  previewLandscapeTimer = window.setInterval(() => {
    idle.classList.add('is-fading');
    window.setTimeout(() => {
      previewLandscapeIndex = (previewLandscapeIndex + 1) % PREVIEW_LANDSCAPES.length;
      applyPreviewLandscape(previewLandscapeIndex);
      idle.classList.remove('is-fading');
    }, 380);
  }, 6500);
}

function stopPreviewLandscapes() {
  const idle = document.getElementById('previewIdle');
  const preview = document.getElementById('preview');
  if (idle) idle.classList.add('hidden');
  if (preview) preview.classList.remove('is-empty');
  if (previewLandscapeTimer) {
    window.clearInterval(previewLandscapeTimer);
    previewLandscapeTimer = null;
  }
}

async function loadModels() {
  const res = await fetch('/modelos');
  allModels = await res.json();
  renderModels(filteredModels());
}

function renderModels(models) {
  const box = document.getElementById('models');
  box.innerHTML = '';
  const grouped = models.reduce((acc, model) => {
    if (!acc[model.area]) acc[model.area] = [];
    acc[model.area].push(model);
    return acc;
  }, {});
  Object.keys(grouped).sort().forEach(area => {
    const group = document.createElement('div');
    const isOpen = selectedModel ? grouped[area].some(model => model.code === selectedModelCode) : area === Object.keys(grouped).sort()[0];
    group.className = 'area-group' + (isOpen ? ' expanded' : '');
    group.innerHTML = `<div class="area-title"><span>${escapeHtml(area)}</span><span class="count">${grouped[area].length}</span></div><div class="model-list"></div>`;
    group.querySelector('.area-title').onclick = () => group.classList.toggle('expanded');
    const list = group.querySelector('.model-list');
    grouped[area].forEach(model => {
      const item = document.createElement('div');
      item.className = 'model' + (model.code === selectedModelCode ? ' active' : '');
      item.dataset.modelCode = model.code;
      item.innerHTML = `<div class="title">${escapeHtml(model.title)}</div>`;
      item.onclick = () => selectModel(model.code);
      list.appendChild(item);
    });
    box.appendChild(group);
  });
}

async function selectModel(code) {
  const res = await fetch('/modelos/' + encodeURIComponent(code));
  selectedModel = await res.json();
  selectedModelCode = selectedModel.code;
  renderModels(filteredModels());
  document.getElementById('selectedTitle').textContent = selectedModel.title;
  document.getElementById('selectedMeta').textContent = selectedModel.area;
  document.getElementById('selectedChips').innerHTML = (selectedModel.keywords || []).slice(0, 8).map(k => `<span class="chip">${escapeHtml(k)}</span>`).join('');
  document.getElementById('selectedDetails').innerHTML = `
    <div class="detail-card"><div class="detail-label">Quando usar</div><div class="detail-text">${escapeHtml(selectedModel.when_to_use || '')}</div></div>
    <div class="detail-card"><div class="detail-label">Estrutura</div><div class="detail-text">${escapeHtml(selectedModel.recommended_structure || '')}</div></div>
    <div class="detail-card"><div class="detail-label">Campos variaveis</div><div class="detail-text">${escapeHtml((selectedModel.placeholders || []).join(', ') || 'sem campos')}</div></div>`;
  stopPreviewLandscapes();
  document.getElementById('preview').textContent = selectedModel.body || '';
  document.getElementById('modelActions').style.display = 'flex';
  renderPlaceholderFields(selectedModel.placeholders || []);
}

function normalizeKey(value) {
  return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function firstMatch(text, patterns) {
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match && match[1]) return match[1].trim().replace(/[.;,]+$/, '');
  }
  return '';
}

function extractCaseFields(text) {
  const fields = {};
  const t = text || '';
  fields['x y'] = firstMatch(t, [/fls?\.?\s*([0-9]+(?:\s*[-/]\s*[0-9]+)?)/i]);
  fields['x'] = fields['x y'];
  fields['classe'] = firstMatch(t, [/trata-se de ([^.]{8,90})/i, /em ([a-z\s]+?) proposta/i]);
  fields['parte autora'] = firstMatch(t, [/proposta por ([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,.]+?) em face/i, /intentada por ([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,.]+?) em face/i]);
  fields['parte requerida'] = firstMatch(t, /em face de ([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,.]+?)(?:,|\.| em favor| nos termos)/i);
  fields['menor'] = firstMatch(t, [/menor ([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,.]+?)(?:\.|,)/i, /em favor d[eoa] (?:menor )?([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,.]+?)(?:\.|,)/i]);
  fields['executado'] = firstMatch(t, [/executado ([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,.]+?)(?:\s+foi|,|\.)/i]);
  fields['exequente'] = firstMatch(t, [/exequente ([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,.]+?)(?:\s+requereu|,|\.)/i]);
  fields['impetrante'] = firstMatch(t, [/impetrante ([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,.]+?)(?:\s+pretende|,|\.)/i]);
  fields['autoridade coatora'] = firstMatch(t, [/autoridade coatora ([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,.]+?)(?:,|\.)/i]);
  fields['indicar'] = firstMatch(t, [/acometid[ao] de ([^.]+?)(?:,|\.)/i, /apresenta quadro de ([^.]+?)(?:,|\.)/i]);
  fields['descrever'] = firstMatch(t, [/alegando ([^.]+?)(?:\.|;)/i, /afirmando ([^.]+?)(?:\.|;)/i]);
  fields['motivo certificado'] = firstMatch(t, [/executado nao foi encontrado ([^.]+?)(?:\.|,)/i, /mandado retornou negativo[^.]*?([^.]+?)(?:\.|,)/i]);
  fields['genitor(a)'] = firstMatch(t, [/guarda unilateral pela ([^,.]+?)(?:,|\.| e)/i]);
  fields['modalidade'] = firstMatch(t, [/(unilateral|compartilhada|alternada)/i]);
  fields['valor'] = firstMatch(t, [/valor de ([^.]+?)(?:\.|,)/i, /alimentos? (?:em|no importe de|de) ([^.]+?)(?:\.|,)/i]);
  fields['percentual quantia'] = fields['valor'];
  fields['indicar prova tecnica ou documental'] = firstMatch(t, [/(laudo social[^.]+?)(?:\.|,)/i, /(estudo psicossocial[^.]+?)(?:\.|,)/i]);
  fields['indicar renda vinculo ausencia de comprovacao ou sinais de capacidade laborativa'] = firstMatch(t, [/(requerido trabalha[^.]+?)(?:\.|,)/i, /(renda aproximada[^.]+?)(?:\.|,)/i]);
  return fields;
}

function renderPlaceholderFields(placeholders) {
  const box = document.getElementById('placeholderFields');
  const suggestions = extractCaseFields(document.getElementById('caseText').value || '');
  if (!placeholders.length) {
    box.innerHTML = '<div class="meta">Este modelo nao possui campos variaveis.</div>';
    return;
  }
  box.innerHTML = placeholders.map(name => {
    const value = suggestions[normalizeKey(name)] || '';
    return `<div class="field"><label>${escapeHtml(name)}</label><input data-placeholder="${escapeHtml(name)}" value="${escapeHtml(value)}" placeholder="Preencher ${escapeHtml(name)}" /></div>`;
  }).join('');
}

function generateFilledText() {
  if (!selectedModel) return;
  let text = selectedModel.body || '';
  const pending = [];
  document.querySelectorAll('[data-placeholder]').forEach(input => {
    const name = input.dataset.placeholder;
    const value = input.value.trim();
    if (value) {
      text = text.replaceAll(`[${name}]`, value);
    } else {
      pending.push(name);
    }
  });
  filledText = text;
  document.getElementById('filledPreview').textContent = text;
  const warning = document.getElementById('pendingWarning');
  warning.style.display = pending.length ? 'block' : 'none';
  warning.textContent = pending.length ? `Atencao: ainda existem campos pendentes: ${pending.join(', ')}.` : '';
}

function toggleUseModel() {
  const panel = document.getElementById('usePanel');
  panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
  if (panel.style.display === 'block') renderPlaceholderFields(selectedModel?.placeholders || []);
}

function clearPlaceholderFields() {
  document.querySelectorAll('[data-placeholder]').forEach(input => input.value = '');
  generateFilledText();
}

async function copyOriginalText() {
  await navigator.clipboard.writeText(selectedModel?.body || '');
  document.getElementById('toast').textContent = 'Texto do modelo copiado.';
}

async function copyFilledText() {
  await navigator.clipboard.writeText(filledText || document.getElementById('filledPreview').textContent);
  document.getElementById('toast').textContent = 'Texto final copiado.';
}

async function runAutomaticMode(topRecommendation) {
  await selectModel(topRecommendation.code);
  document.getElementById('usePanel').style.display = 'block';
  renderPlaceholderFields(selectedModel.placeholders || []);
  generateFilledText();
  const pending = Array.from(document.querySelectorAll('[data-placeholder]')).filter(input => !input.value.trim()).map(input => input.dataset.placeholder);
  const note = document.getElementById('autoNote');
  note.style.display = 'block';
  note.textContent = pending.length
    ? `Modo automatico: ${topRecommendation.code} selecionado. Revise os campos pendentes: ${pending.join(', ')}.`
    : `Modo automatico: ${topRecommendation.code} selecionado e previa final gerada.`;
}

function resetAnalysisForNewCase() {
  readyToReplaceCase = false;
  document.getElementById('autoNote').style.display = 'none';
  document.getElementById('toast').textContent = '';
}

function prepareCaseTextForNewPaste() {
  const field = document.getElementById('caseText');
  if (readyToReplaceCase && field.value.trim()) {
    field.value = '';
    document.getElementById('results').innerHTML = '';
    document.getElementById('autoNote').style.display = 'none';
    readyToReplaceCase = false;
  }
}

function newCase() {
  const field = document.getElementById('caseText');
  field.value = '';
  document.getElementById('results').innerHTML = '';
  resetAnalysisForNewCase();
  field.focus();
}

async function recommendModel() {
  const caseText = document.getElementById('caseText').value;
  document.getElementById('toast').textContent = 'Analisando novo texto...';
  const res = await fetch('/recomendar', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({case_text: caseText, limit: 3})
  });
  const data = await res.json();
  const box = document.getElementById('results');
  box.innerHTML = '';
  data.recommendations.forEach(item => {
    const div = document.createElement('div');
    const safeCode = item.code.replace(/[^a-zA-Z0-9_-]/g, '');
    div.className = 'result';
    div.dataset.processCode = item.code;
    div.innerHTML = `<div><b>${escapeHtml(item.title)}</b></div>
      <div class="score">${item.score} pontos</div>
      <div class="meta">${escapeHtml(item.area)}</div>
      <div>${escapeHtml(item.reasons.join('; '))}</div>
      <div class="meta">Gatilhos: ${escapeHtml(item.matched_keywords.join(', '))}</div>
      <div class="feedback">
        <button class="ok" onclick="sendFeedback(event, '${item.code}', '${item.code}', true)">Acertou</button>
        <input id="correct-${safeCode}" placeholder="Modelo correto, ex.: EXE-02" />
        <input id="note-${safeCode}" placeholder="Observacao" />
        <button class="bad" onclick="sendCorrection(event, '${item.code}', '${safeCode}')">Salvar correcao</button>
      </div>`;
    div.onclick = event => {
      if (event.target.tagName !== 'BUTTON' && event.target.tagName !== 'INPUT') selectModel(item.code);
    };
    box.appendChild(div);
  });
  if (currentMode() === 'auto' && data.recommendations.length) {
    await runAutomaticMode(data.recommendations[0]);
  } else {
    document.getElementById('autoNote').style.display = 'none';
  }
  readyToReplaceCase = true;
}

async function uploadPdfFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    document.getElementById('toast').textContent = 'Selecione um arquivo PDF.';
    return;
  }
  document.getElementById('toast').textContent = `Extraindo texto de ${file.name}...`;
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/extrair-pdf', { method: 'POST', body: form });
  const data = await res.json();
  if (!res.ok) {
    document.getElementById('toast').textContent = data.detail || 'Nao foi possivel extrair o PDF.';
    return;
  }
  document.getElementById('caseText').value = data.text;
  resetAnalysisForNewCase();
  document.getElementById('toast').textContent = `Texto extraido: ${data.pages} pagina(s), ${data.characters} caracteres. Clique em Recomendar.`;
}

async function uploadChatHistory(file) {
  if (!file) return;
  const lower = file.name.toLowerCase();
  if (!lower.endsWith('.zip') && !lower.endsWith('.json')) {
    document.getElementById('toast').textContent = 'Envie o .zip ou .json exportado do ChatGPT.';
    return;
  }
  document.getElementById('trainingResult').innerHTML = '<div class="training-note">Importando historico e filtrando conteudo juridico...</div>';
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/importar-chatgpt', { method: 'POST', body: form });
  const data = await res.json();
  if (!res.ok) {
    document.getElementById('trainingResult').innerHTML = `<div class="training-note">${escapeHtml(data.detail || 'Nao foi possivel importar.')}</div>`;
    return;
  }
  const rows = data.candidates.slice(0, 8).map(item =>
    `<div class="feedback-row"><b>${escapeHtml(item.title)}</b><div class="meta">sugestao: ${escapeHtml(item.target_code || 'sem modelo')} | score juridico: ${item.score}</div><div>${escapeHtml(item.preview)}...</div></div>`
  ).join('');
  document.getElementById('trainingResult').innerHTML = `<div class="training-note"><b>${data.imported}</b> trechos juridicos importados para treinamento. Ignorados: ${data.ignored}. Revise em Ver salvos.</div>${rows}`;
}

async function uploadMotorFile(file) {
  if (!file) return;
  const lower = file.name.toLowerCase();
  if (!lower.endsWith('.rtf') && !lower.endsWith('.zip') && !lower.endsWith('.pdf') && !lower.endsWith('.docx') && !lower.endsWith('.txt')) {
    document.getElementById('motorStatus').innerHTML = '<div class="training-note">Envie RTF, ZIP, PDF, DOCX ou TXT para o motor de aprendizado.</div>';
    return;
  }
  document.getElementById('motorStatus').innerHTML = `<div class="training-note">Enviando ${escapeHtml(file.name)} para o motor...</div>`;
  const form = new FormData();
  form.append('arquivo', file);
  form.append('categoria', 'AUTO');
  const res = await fetch('/motor/upload', { method: 'POST', body: form });
  const data = await res.json();
  if (!res.ok) {
    document.getElementById('motorStatus').innerHTML = `<div class="training-note">${escapeHtml(data.detail || 'Nao foi possivel enviar ao motor.')}</div>`;
    return;
  }
  const detail = data.status === 'enfileirado_zip'
    ? `${data.documentos || 0} documento(s) enfileirado(s), ${data.duplicatas || 0} duplicata(s), ${data.ignorados || 0} ignorado(s).`
    : `Documento ${data.documento_id || ''} | Categoria ${escapeHtml(data.categoria || 'AUTO')}`;
  document.getElementById('motorStatus').innerHTML = `<div class="training-note"><b>${escapeHtml(data.status)}</b>: ${escapeHtml(data.mensagem || 'Documento recebido pelo motor.')}<br>${detail}</div>`;
  await loadMotorStatus();
}

async function loadMotorStatus() {
  const [statusRes, filaRes, grafoRes] = await Promise.all([
    fetch('/motor/status'),
    fetch('/motor/fila'),
    fetch('/motor/grafo/status')
  ]);
  const status = await statusRes.json();
  const fila = await filaRes.json();
  const grafo = await grafoRes.json();
  const top = (Array.isArray(status) ? status : []).slice(0, 6).map(item =>
    `<div class="feedback-row"><b>${escapeHtml(item.nome || item.categoria)}</b><div class="meta">${Number(item.percentual || 0).toFixed(1)}% | ${item.total_docs || 0} documento(s)</div><div>${escapeHtml(item.proxima_necessidade || '')}</div></div>`
  ).join('');
  document.getElementById('motorStatus').innerHTML = `
    <div class="motor-status-grid">
      <div class="motor-status-card"><b>${fila.aguardando || 0}</b><span class="meta">na fila</span></div>
      <div class="motor-status-card"><b>${grafo.total_entidades || 0}</b><span class="meta">entidades KG</span></div>
      <div class="motor-status-card"><b>${grafo.total_documentos_grafo || 0}</b><span class="meta">docs no grafo</span></div>
    </div>
    ${top || '<div class="training-note">Motor pronto. Envie documentos para iniciar o aprendizado.</div>'}`;
}

async function processMotorQueue() {
  document.getElementById('motorStatus').innerHTML = '<div class="training-note">Processando ate 5 documentos da fila...</div>';
  const res = await fetch('/motor/processar-agora', { method: 'POST' });
  const data = await res.json();
  document.getElementById('motorStatus').innerHTML = `<div class="training-note">${escapeHtml(data.mensagem || `Processados: ${data.processados || 0}`)}</div>`;
  await loadMotorStatus();
}

async function sendCorrection(event, suggestedCode, safeCode) {
  event.stopPropagation();
  const correct = document.getElementById('correct-' + safeCode).value.trim().toUpperCase();
  const note = document.getElementById('note-' + safeCode).value.trim();
  if (!correct) {
    document.getElementById('toast').textContent = 'Informe o modelo correto antes de salvar.';
    return;
  }
  await sendFeedback(event, suggestedCode, correct, false, note);
}

async function sendFeedback(event, suggestedCode, correctCode, accepted, note = '') {
  event.stopPropagation();
  const caseText = document.getElementById('caseText').value;
  const res = await fetch('/avaliacoes', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({case_text: caseText, suggested_code: suggestedCode, correct_code: correctCode, accepted, note})
  });
  document.getElementById('toast').textContent = res.ok ? (accepted ? 'Avaliacao salva: acertou.' : 'Correcao salva.') : 'Nao foi possivel salvar.';
  if (res.ok && document.getElementById('feedbackPanel').style.display === 'block') loadFeedback();
}

async function toggleFeedback() {
  const panel = document.getElementById('feedbackPanel');
  panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
  if (panel.style.display === 'block') await loadFeedback();
}

async function loadFeedback() {
  const res = await fetch('/avaliacoes');
  const data = await res.json();
  const accepted = data.items.filter(item => item.accepted).length;
  const corrected = data.items.length - accepted;
  const last = data.items.slice(-8).reverse();
  const panel = document.getElementById('feedbackPanel');
  panel.innerHTML = `<h2>Avaliacoes salvas</h2>
    <div class="feedback-summary">
      <div class="metric"><b>${data.total}</b><br><span class="meta">total</span></div>
      <div class="metric"><b>${accepted}</b><br><span class="meta">acertos</span></div>
      <div class="metric"><b>${corrected}</b><br><span class="meta">correcoes</span></div>
    </div>`;
  last.forEach(item => {
    const row = document.createElement('div');
    row.className = 'feedback-row';
    row.innerHTML = `<div><b>${item.accepted ? 'Acertou' : 'Corrigido'}</b>: ${escapeHtml(item.suggested_code)} -> ${escapeHtml(item.correct_code)}</div><div class="meta">${escapeHtml(item.note || 'Sem observacao')}</div>`;
    panel.appendChild(row);
  });
}

async function toggleTraining() {
  const panel = document.getElementById('trainingPanel');
  panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
  const source = document.getElementById('caseText').value.trim();
  if (panel.style.display === 'block' && source && !document.getElementById('trainingText').value.trim()) {
    document.getElementById('trainingText').value = source;
  }
  if (panel.style.display === 'block') {
    await loadMotorStatus();
  }
}

async function analyzeTrainingText() {
  const text = document.getElementById('trainingText').value.trim();
  if (text.length < 30) {
    document.getElementById('trainingResult').innerHTML = '<div class="training-note">Cole ao menos 30 caracteres para analisar.</div>';
    return;
  }
  const res = await fetch('/treinamento/analisar', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text})
  });
  const data = await res.json();
  lastTrainingAnalysis = data;
  const top = data.recommendations[0];
  const rows = data.recommendations.map(item => `<div class="result"><b>${escapeHtml(item.code)}</b> - ${escapeHtml(item.title)}<div class="score">${item.score} pontos</div><div class="meta">${escapeHtml(item.area)}</div></div>`).join('');
  document.getElementById('trainingResult').innerHTML = `
    <div class="training-note">${escapeHtml(data.suggestion.message)}<br>Area provavel: <b>${escapeHtml(data.detected_area || 'nao identificada')}</b>. ${top ? `Modelo mais provavel: <b>${escapeHtml(top.code)}</b>.` : ''}</div>
    <div class="training-actions">
      ${top ? `<button onclick="saveTrainingExample('accepted', '${top.code}')">Salvar como ${top.code}</button>` : ''}
      <button class="bad" onclick="saveTrainingExample('new_model')">Candidato a novo modelo</button>
      <input id="trainingTargetCode" placeholder="Codigo correto, ex.: FAM-03" />
      <button class="secondary" onclick="saveTrainingExample('corrected')">Salvar codigo informado</button>
    </div>${rows}`;
}

async function saveTrainingExample(action, targetCode = '') {
  const text = document.getElementById('trainingText').value.trim();
  const informedCode = document.getElementById('trainingTargetCode')?.value.trim().toUpperCase() || '';
  const finalCode = targetCode || informedCode;
  if (text.length < 30) {
    document.getElementById('toast').textContent = 'Cole uma manifestacao antes de salvar.';
    return;
  }
  if (!finalCode && action !== 'review' && action !== 'new_model') {
    document.getElementById('toast').textContent = 'Informe o codigo correto antes de salvar.';
    return;
  }
  const res = await fetch('/treinamento', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text, action, target_code: finalCode, note: '', title: '', analysis: lastTrainingAnalysis || {}})
  });
  document.getElementById('toast').textContent = res.ok ? 'Exemplo de treinamento salvo.' : 'Nao foi possivel salvar o treinamento.';
}

async function loadTraining() {
  const res = await fetch('/treinamento');
  const data = await res.json();
  const last = data.items.slice(-12).reverse();
  const rows = last.map(item => {
    const code = item.target_code || item.review_target_code || 'sem codigo';
    const action = item.action === 'new_model' ? 'novo modelo' : item.action;
    const status = item.status || 'pending';
    return `<div class="feedback-row">
      <b>${escapeHtml(action)}</b>: ${escapeHtml(code)} <span class="meta">(${escapeHtml(status)})</span>
      <div class="meta">${escapeHtml((item.note || '').slice(0, 160))}</div>
      <div>${escapeHtml((item.text || '').slice(0, 220))}...</div>
      <div class="training-actions">
        <button class="ok" onclick="reviewTraining(${item.index}, 'approved_variation', '${code === 'sem codigo' ? '' : code}')">Aprovar variacao</button>
        <button class="secondary" onclick="reviewTraining(${item.index}, 'approved_new_model')">Virar modelo novo</button>
        <button class="bad" onclick="reviewTraining(${item.index}, 'discarded')">Descartar</button>
      </div>
    </div>`;
  }).join('');
  document.getElementById('trainingResult').innerHTML = `<div class="training-note"><b>${data.total}</b> exemplos de treinamento salvos.</div>${rows || '<div class="meta">Nenhum treinamento salvo ainda.</div>'}`;
}

async function reviewTraining(index, status, targetCode = '') {
  const res = await fetch(`/treinamento/${index}/revisar`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status, target_code: targetCode, note: ''})
  });
  document.getElementById('toast').textContent = res.ok ? 'Treinamento revisado.' : 'Nao foi possivel revisar o treinamento.';
  if (!res.ok) return;
  const data = await res.json();
  if (status === 'approved_new_model') showCustomModelEditor(data.training, index);
  else loadTraining();
}

function nextCodeForArea(area) {
  const prefixMap = {
    'Execução de Alimentos': 'EXE',
    'Família e Sucessões': 'FAM',
    'Curatela e Capacidade Civil': 'CUR',
    'Mandado de Segurança': 'MS',
    'Patrimônio / Inventário / Contas': 'PAT',
    'Procedimento Comum / Indenização': 'PC',
    'Recursos': 'REC',
    'Saúde': 'SAU',
  };
  const prefix = prefixMap[area] || 'NOV';
  const used = allModels.map(model => model.code || '').filter(code => code.startsWith(prefix + '-')).map(code => Number(code.split('-')[1])).filter(Number.isFinite);
  return `${prefix}-${String(Math.max(0, ...used) + 1).padStart(2, '0')}`;
}

function showCustomModelEditor(training, index) {
  const analysis = training.analysis || {};
  const top = (analysis.recommendations || [])[0] || {};
  const area = analysis.detected_area || top.area || 'Geral';
  const title = training.title || `${area} - novo modelo`;
  const keywords = (analysis.tokens || []).slice(0, 10).join(', ');
  const body = training.text || '';
  document.getElementById('trainingResult').innerHTML = `
    <div class="training-note">Revise antes de incorporar. O modelo novo so entra na arvore depois de salvar aqui.</div>
    <div class="field"><label>Codigo</label><input id="customCode" value="${escapeHtml(nextCodeForArea(area))}" /></div>
    <div class="field"><label>Titulo</label><input id="customTitle" value="${escapeHtml(title)}" /></div>
    <div class="field"><label>Area</label><input id="customArea" value="${escapeHtml(area)}" /></div>
    <div class="field"><label>Quando usar</label><input id="customWhen" placeholder="Hipotese de uso do modelo" /></div>
    <div class="field"><label>Gatilhos</label><input id="customTriggers" value="${escapeHtml(keywords)}" /></div>
    <div class="field"><label>Estrutura</label><input id="customStructure" placeholder="Relatorio; fundamentacao; conclusao" /></div>
    <div class="field"><label>Campos variaveis</label><input id="customPlaceholders" placeholder="parte autora, x-y, valor" /></div>
    <div class="field"><label>Texto-base</label><textarea id="customBody">${escapeHtml(body)}</textarea></div>
    <div class="training-actions"><button onclick="saveCustomModel(${index})">Incorporar ao banco</button><button class="secondary" onclick="loadTraining()">Voltar</button></div>`;
}

function splitCsv(value) {
  return value.split(',').map(item => item.trim()).filter(Boolean);
}

async function saveCustomModel(index) {
  const payload = {
    code: document.getElementById('customCode').value.trim().toUpperCase(),
    title: document.getElementById('customTitle').value.trim(),
    area: document.getElementById('customArea').value.trim(),
    prefix: document.getElementById('customCode').value.trim().split('-')[0].toUpperCase(),
    when_to_use: document.getElementById('customWhen').value.trim(),
    identification_triggers: document.getElementById('customTriggers').value.trim(),
    recommended_structure: document.getElementById('customStructure').value.trim(),
    body: document.getElementById('customBody').value.trim(),
    keywords: splitCsv(document.getElementById('customTriggers').value),
    placeholders: splitCsv(document.getElementById('customPlaceholders').value),
    source_training_index: index
  };
  const res = await fetch('/modelos-customizados', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok) {
    document.getElementById('toast').textContent = data.detail || 'Nao foi possivel incorporar o modelo.';
    return;
  }
  document.getElementById('toast').textContent = `Modelo ${data.model.code} incorporado ao banco.`;
  await loadModels();
  selectModel(data.model.code);
}

function filteredModels() {
  const q = document.getElementById('search').value.toLowerCase();
  return allModels.filter(model => `${model.code} ${model.title} ${model.area} ${(model.keywords || []).join(' ')}`.toLowerCase().includes(q));
}

function showSajStatus(message) {
  const status = document.querySelector('.saj-statusbar span:first-child');
  if (status) status.textContent = message;
  const toast = document.getElementById('toast');
  if (toast) toast.textContent = message;
}

function handleSajAction(action) {
  if (action === 'import') {
    document.getElementById('pdfInput').click();
    showSajStatus('Carga e importacao: selecione um PDF do processo.');
    return;
  }
  if (action === 'flow') {
    document.getElementById('caseText').focus();
    showSajStatus('Fluxo de trabalho ativo: cole ou revise o texto do processo.');
    return;
  }
  if (action === 'files') {
    toggleTraining();
    showSajStatus('Gerenciador de arquivos: painel de treinamento/importacao aberto.');
    return;
  }
  if (action === 'search') {
    document.getElementById('search').focus();
    showSajStatus('Consulta avancada: busque modelos por codigo, area ou palavra-chave.');
    return;
  }
  if (action === 'deadlines') {
    OwlAssistant.runAction('analyze_urgency', 'deadline');
    showSajStatus('Agenda de compromissos: Coruj IA analisando urgencia.');
  }
}

function handleSajSearch(value) {
  const normalized = normalizeKey(value);
  if (!normalized) return;
  const aliases = [
    {terms: ['pdf', 'importacao', 'carga', 'arquivo'], action: 'import'},
    {terms: ['fluxo', 'processo', 'texto', 'recomendar'], action: 'flow'},
    {terms: ['treinar', 'aprendizado', 'historico', 'gerenciador'], action: 'files'},
    {terms: ['consulta', 'modelo', 'buscar'], action: 'search'},
    {terms: ['prazo', 'urgencia', 'agenda'], action: 'deadlines'},
  ];
  const match = aliases.find(item => item.terms.some(term => normalized.includes(term)));
  if (match) handleSajAction(match.action);
  else showSajStatus('Funcionalidade nao encontrada. Tente: PDF, fluxo, modelo, prazo ou treinar.');
}

document.getElementById('search').addEventListener('input', () => renderModels(filteredModels()));
document.querySelectorAll('[data-saj-action]').forEach(button => {
  button.addEventListener('click', () => handleSajAction(button.dataset.sajAction));
});
document.getElementById('sajSearch').addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    handleSajSearch(event.target.value);
  }
});
document.getElementById('caseText').addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    recommendModel();
  }
});
document.getElementById('caseText').addEventListener('input', resetAnalysisForNewCase);
document.getElementById('caseText').addEventListener('focus', prepareCaseTextForNewPaste);
document.getElementById('caseText').addEventListener('click', prepareCaseTextForNewPaste);
document.getElementById('pdfInput').addEventListener('change', event => uploadPdfFile(event.target.files[0]));
document.getElementById('chatHistoryInput').addEventListener('change', event => uploadChatHistory(event.target.files[0]));
document.getElementById('motorInput').addEventListener('change', event => uploadMotorFile(event.target.files[0]));

for (const dropId of ['pdfDrop', 'chatHistoryDrop', 'motorDrop']) {
  const drop = document.getElementById(dropId);
  drop.addEventListener('dragover', event => {
    event.preventDefault();
    drop.classList.add('dragging');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragging'));
  drop.addEventListener('drop', event => {
    event.preventDefault();
    drop.classList.remove('dragging');
    if (dropId === 'pdfDrop') uploadPdfFile(event.dataTransfer.files[0]);
    else if (dropId === 'motorDrop') uploadMotorFile(event.dataTransfer.files[0]);
    else uploadChatHistory(event.dataTransfer.files[0]);
  });
}

OwlAssistant.init();
startPreviewLandscapes();
loadModels();
</script>
</body>
</html>
"""


def _owl_mock_response(action: str, context: dict[str, object], selected_text: str) -> dict[str, object]:
    labels = {
        "summarize_process": "Resumo do processo",
        "analyze_urgency": "Analise de urgencia",
        "check_pages": "Conferencia de fls.",
        "identify_pending": "Pendencias identificadas",
        "generate_report": "Relatorio preliminar",
        "generate_opinion": "Parecer preliminar",
        "rewrite_excerpt": "Trecho reescrito",
        "explain_decision": "Explicacao da decisao",
        "suggest_next_act": "Proximo ato sugerido",
    }
    states = {
        "summarize_process": "investigate",
        "analyze_urgency": "deadline",
        "check_pages": "checklist",
        "identify_pending": "warning",
        "generate_report": "writing",
        "generate_opinion": "writing",
        "rewrite_excerpt": "writing",
        "explain_decision": "teacher",
        "suggest_next_act": "success",
    }
    title = labels.get(action, "Analise da Coruj IA")
    source = selected_text.strip() or str(context.get("text") or context.get("title") or "").strip()
    preview = source[:360] if source else "Nenhum trecho especifico foi enviado; usei o contexto disponivel na tela."
    process_ref = context.get("processCode") or context.get("modelCode") or "contexto atual"
    uses_mp_prompt = action in MP_OPINION_ACTIONS

    if uses_mp_prompt:
        items = [
            "Estrutura aplicada: contextualizacao, sintese, observacoes tecnicas e trecho literal comprobatorio.",
            "A resposta real devera indicar fls. sempre que o arquivo trouxer essa informacao.",
            "Na ausencia de dado nos autos, a resposta devera consignar: nao consta nos autos.",
            "Contradicoes, lacunas e insuficiencia de instrucao deverao ser destacadas sem inferencia externa.",
        ]
        draft = "\n\n".join([
            "1. Breve contextualizacao do documento",
            "Conforme consta nos autos, a Coruj IA estruturara a leitura do arquivo em formato de parecer juridico do Ministerio Publico.",
            "2. Sintese do conteudo",
            preview,
            "3. Pontos relevantes/observacoes tecnicas",
            "Nao consta nos autos, nesta resposta mockada, indicacao segura de folhas, contradicoes ou lacunas especificas.",
            "4. Trecho literal comprobatorio",
            '"Trecho literal sera extraido do arquivo quando a integracao real de IA estiver ativa."',
        ])
    else:
        items = [
            "Contexto recebido e normalizado pelo frontend.",
            "Pontos juridicos separados em achados, riscos e providencias.",
            "Saida preparada para futura integracao com OpenAI Responses API.",
        ]
        draft = preview

    return {
        "title": title,
        "state": states.get(action, "teacher"),
        "summary": f"Resposta mockada para {process_ref}. O prompt institucional do MP {'ja foi aplicado' if uses_mp_prompt else 'esta disponivel'} para futura chamada real a IA.",
        "items": items,
        "draft": draft,
        "nextSteps": [
            "Revisar os dados do processo antes de usar a sugestao.",
            "Conferir prazos, documentos essenciais e movimentacoes recentes.",
        ],
        "integrationReady": {
            "provider": "OpenAI Responses API",
            "systemPrompt": MP_LEGAL_OPINION_PROMPT if uses_mp_prompt else "Voce e uma assistente juridica institucional.",
            "suggestedPayload": {
                "model": "gpt-4.1-mini",
                "input": [
                    {"role": "system", "content": MP_LEGAL_OPINION_PROMPT if uses_mp_prompt else "Voce e uma assistente juridica institucional."},
                    {"role": "user", "content": {"action": action, "context": context, "selectedText": selected_text}},
                ],
            },
        },
    }


@app.post("/api/owl-assistant")
def owl_assistant(payload: OwlAssistantRequest) -> dict[str, object]:
    return {"ok": True, "response": _owl_mock_response(payload.action, payload.context, payload.selectedText)}


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "source": "postgresql" if using_database() else "json"}


@app.get("/modelos")
def modelos() -> list[dict[str, object]]:
    return [
        {
            "code": item["code"],
            "title": item["title"],
            "area": item["area"],
            "keywords": item.get("keywords", []),
            "placeholders": item.get("placeholders", []),
        }
        for item in list_templates()
    ]


@app.get("/modelos/{code}")
def modelo(code: str) -> dict[str, object]:
    item = get_template(code)
    if not item:
        raise HTTPException(status_code=404, detail="Modelo nao encontrado")
    return item


@app.post("/modelos-customizados")
def criar_modelo_customizado(payload: CustomModelRequest) -> dict[str, object]:
    code = payload.code.strip().upper()
    if get_template(code):
        raise HTTPException(status_code=409, detail=f"Ja existe modelo com o codigo {code}.")

    model = {
        "code": code,
        "title": payload.title.strip(),
        "area": payload.area.strip(),
        "prefix": payload.prefix.strip().upper() or code.split("-")[0],
        "when_to_use": payload.when_to_use.strip(),
        "identification_triggers": payload.identification_triggers.strip(),
        "recommended_structure": payload.recommended_structure.strip(),
        "body": payload.body.strip(),
        "keywords": [item.strip() for item in payload.keywords if item.strip()],
        "placeholders": [item.strip() for item in payload.placeholders if item.strip()],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_training_index": payload.source_training_index,
    }
    saved = save_custom_model(model)

    if payload.source_training_index is not None:
        rows = list_training()
        index = payload.source_training_index
        if 0 <= index < len(rows):
            row = dict(rows[index])
            row["status"] = "incorporated_model"
            row["incorporated_code"] = code
            row["incorporated_at"] = datetime.now(timezone.utc).isoformat()
            rows[index] = row
            replace_training(rows)

    return {"ok": True, "model": saved}


@app.post("/recomendar")
def recomendar(payload: RecommendRequest) -> dict[str, object]:
    results = recommend(payload.case_text, list_templates(), payload.limit)
    return {"recommendations": [result.__dict__ for result in results]}


@app.post("/extrair-pdf")
async def extrair_pdf(file: UploadFile = File(...)) -> dict[str, object]:
    filename = file.filename or "arquivo.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Envie um arquivo PDF.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="PDF vazio.")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF muito grande para este MVP.")
    try:
        text, pages = _extract_pdf_text(data)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Nao foi possivel ler este PDF.") from exc
    if len(text) < 20:
        raise HTTPException(status_code=422, detail="Nao encontrei texto selecionavel no PDF. Ele pode estar escaneado como imagem.")
    return {"filename": filename, "pages": pages, "characters": len(text), "text": text}


@app.post("/importar-chatgpt")
async def importar_chatgpt(file: UploadFile = File(...)) -> dict[str, object]:
    filename = file.filename or "historico"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")
    if len(data) > MAX_HISTORY_BYTES:
        raise HTTPException(status_code=413, detail="Historico muito grande para este MVP.")
    try:
        return _import_chatgpt_history(filename, data)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Nao foi possivel processar o historico.") from exc


@app.post("/avaliacoes")
def avaliacoes(payload: FeedbackRequest) -> dict[str, object]:
    feedback = {
        "case_text": payload.case_text,
        "suggested_code": payload.suggested_code.upper(),
        "correct_code": payload.correct_code.upper(),
        "accepted": payload.accepted,
        "note": payload.note,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    save_feedback(feedback)
    return {"ok": True, "feedback": feedback}


@app.get("/avaliacoes")
def listar_avaliacoes() -> dict[str, object]:
    rows = list_feedback()
    return {"total": len(rows), "items": rows}


@app.post("/treinamento/analisar")
def analisar_treinamento(payload: TrainingAnalyzeRequest) -> dict[str, object]:
    return _training_analysis(payload.text)


@app.post("/treinamento")
def salvar_treinamento(payload: TrainingSaveRequest) -> dict[str, object]:
    training = {
        "text": payload.text,
        "action": payload.action,
        "target_code": payload.target_code.upper(),
        "status": "pending",
        "note": payload.note,
        "title": payload.title,
        "analysis": payload.analysis,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    save_training(training)
    return {"ok": True, "training": training}


@app.get("/treinamento")
def listar_treinamento() -> dict[str, object]:
    items = []
    for index, row in enumerate(list_training()):
        item = dict(row)
        item["index"] = index
        item.setdefault("status", "pending")
        items.append(item)
    return {"total": len(items), "items": items}


@app.post("/treinamento/{index}/revisar")
def revisar_treinamento(index: int, payload: TrainingReviewRequest) -> dict[str, object]:
    rows = list_training()
    if index < 0 or index >= len(rows):
        raise HTTPException(status_code=404, detail="Treinamento nao encontrado")
    row = dict(rows[index])
    row["status"] = payload.status
    row["review_target_code"] = payload.target_code.upper()
    row["review_note"] = payload.note
    row["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    rows[index] = row
    replace_training(rows)
    return {"ok": True, "training": row}
