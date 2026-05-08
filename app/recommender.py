from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


STOPWORDS = {
    "a",
    "ao",
    "aos",
    "as",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "no",
    "nos",
    "na",
    "nas",
    "o",
    "os",
    "ou",
    "para",
    "por",
    "que",
    "se",
    "um",
    "uma",
}


AREA_HINTS = {
    "Família e Sucessões": [
        "guarda",
        "convivencia",
        "alimentos",
        "menor",
        "crianca",
        "adolescente",
        "genitor",
        "genitora",
    ],
    "Execução de Alimentos": [
        "execucao de alimentos",
        "execucao",
        "prisao civil",
        "inadimplemento",
        "exequente",
        "executado",
        "debito alimentar",
        "parcelamento",
        "parcelas",
        "parcelas vincendas",
        "cumprimento",
        "penhora",
        "sisbaJud".lower(),
    ],
    "Curatela e Capacidade Civil": [
        "curatela",
        "interdicao",
        "curador",
        "pericia",
        "incapaz",
        "entrevista",
    ],
    "Mandado de Segurança": [
        "mandado de seguranca",
        "impetrante",
        "autoridade coatora",
        "direito liquido",
    ],
    "Saúde": ["saude", "medicamento", "tratamento", "nat", "tutela cumprida"],
    "Patrimônio / Inventário / Contas": [
        "inventario",
        "alvara",
        "prestacao de contas",
        "alienacao",
        "patrimonio",
    ],
    "Recursos": ["apelacao", "recurso", "admissibilidade"],
}


@dataclass(frozen=True)
class Recommendation:
    code: str
    title: str
    area: str
    score: int
    reasons: list[str]
    matched_keywords: list[str]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> set[str]:
    normalized = normalize(text)
    words = re.findall(r"[a-z0-9]{3,}", normalized)
    return {word for word in words if word not in STOPWORDS}


def detect_area(case_text: str) -> tuple[str | None, list[str]]:
    normalized = normalize(case_text)
    best_area = None
    best_hits: list[str] = []
    for area, hints in AREA_HINTS.items():
        hits = [hint for hint in hints if normalize(hint) in normalized]
        if len(hits) > len(best_hits):
            best_area = area
            best_hits = hits
    return best_area, best_hits


def recommend(case_text: str, templates: list[dict[str, Any]], limit: int = 3) -> list[Recommendation]:
    case_norm = normalize(case_text)
    case_tokens = tokenize(case_text)
    detected_area, area_hits = detect_area(case_text)
    scored: list[Recommendation] = []

    for template in templates:
        code = template.get("code") or template.get("codigo") or ""
        title = template.get("title") or template.get("titulo") or ""
        area = template.get("area") or ""
        when_to_use = template.get("when_to_use") or template.get("quando_usar") or ""
        triggers = template.get("identification_triggers") or template.get("gatilhos_identificacao") or ""
        body = template.get("body") or template.get("modelo_saneado") or ""
        keywords = template.get("keywords") or template.get("keywords_extraidas") or []

        score = 0
        reasons: list[str] = []
        matched_keywords: list[str] = []

        if detected_area and area == detected_area:
            score += 35
            reasons.append(f"área provável coincide: {area}")

        if "execucao de alimentos" in case_norm and area == "Execução de Alimentos":
            score += 25
            reasons.append("contexto específico de execução de alimentos")

        if "execucao de alimentos" in case_norm and area == "Família e Sucessões":
            score -= 25

        has_execution_agreement = (
            "execucao" in case_norm
            and "alimento" in case_norm
            and ("acordo" in case_norm or "homologacao" in case_norm)
            and ("parcelamento" in case_norm or "parcelas vincendas" in case_norm or "debito alimentar" in case_norm)
        )
        if has_execution_agreement and normalize(code) == "exe-05":
            score += 55
            reasons.append("acordo de parcelamento dentro de execução de alimentos")

        if has_execution_agreement and normalize(code) == "fam-04":
            score -= 45
            reasons.append("acordo familiar penalizado porque o caso está em execução")

        has_incapacity_justification = (
            ("avc" in case_norm or "incapacidade" in case_norm or "incapacidade laborativa" in case_norm or "doenca" in case_norm)
            and ("justificativa" in case_norm or "aleg" in case_norm or "documentos medicos" in case_norm or "laudo" in case_norm)
        )
        if has_incapacity_justification and normalize(code) == "exe-03":
            score += 75
            reasons.append("justificativa por incapacidade/doenca no rito da prisao")

        if has_incapacity_justification and normalize(code) == "exe-01":
            score -= 45
            reasons.append("prisao imediata penalizada porque ha justificativa medica")

        has_unanswered_social_report = (
            ("laudo social" in case_norm or "estudo psicossocial" in case_norm)
            and (
                "alegacoes finais" in case_norm
                or ("alega" in case_norm and "finais" in case_norm)
                or "contraditorio" in case_norm
                or "manifestar sobre" in case_norm
            )
            and (
                "sem apresentacao" in case_norm
                or "sem apresenta" in case_norm
                or "nao foi oportunizado" in case_norm
                or "ainda nao" in case_norm
                or "necessidade" in case_norm
            )
        )
        if has_unanswered_social_report and normalize(code) == "fam-03":
            score += 65
            reasons.append("laudo social juntado sem alegacoes finais")

        if has_unanswered_social_report and normalize(code) == "fam-02":
            score -= 35
            reasons.append("parecer final penalizado porque ainda falta contraditorio do laudo")

        for hit in area_hits:
            if normalize(hit) in normalize(title + " " + when_to_use + " " + triggers):
                score += 4

        for keyword in keywords:
            if normalize(keyword) in case_norm:
                score += 12
                matched_keywords.append(keyword)

        title_tokens = tokenize(title)
        trigger_tokens = tokenize(triggers)
        when_tokens = tokenize(when_to_use)
        body_tokens = tokenize(body)

        title_hits = case_tokens & title_tokens
        trigger_hits = case_tokens & trigger_tokens
        when_hits = case_tokens & when_tokens
        body_hits = case_tokens & body_tokens

        score += min(len(title_hits) * 5, 25)
        score += min(len(trigger_hits) * 4, 32)
        score += min(len(when_hits) * 3, 24)
        score += min(len(body_hits), 20)

        if title_hits:
            reasons.append("termos do título aparecem no caso")
        if trigger_hits or matched_keywords:
            reasons.append("gatilhos de identificação encontrados")
        if when_hits:
            reasons.append("hipótese de uso compatível")

        if "gen-01" == normalize(code) and score > 0:
            score -= 18
            reasons.append("modelo genérico penalizado para priorizar modelos específicos")

        if score > 0:
            scored.append(
                Recommendation(
                    code=code,
                    title=title,
                    area=area,
                    score=max(score, 0),
                    reasons=reasons[:4],
                    matched_keywords=matched_keywords[:8],
                )
            )

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]
