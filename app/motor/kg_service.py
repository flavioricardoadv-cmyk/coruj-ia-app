"""
kg_service.py â€” Grafo de Conhecimento
======================================
Substitui ferramentas externas como Microsoft GraphRAG por um grafo prÃ³prio,
100% local, sem mensalidade, rodando em PostgreSQL.

Arquitetura:
  KgEntidade  â†’ nÃ³s do grafo (argumentos, citaÃ§Ãµes, expressÃµes, estruturas)
  KgRelacao   â†’ arestas (co-ocorrÃªncia, resultado, similaridade)
  KgDocumento â†’ rastreia quais entidades vieram de quais documentos

Funcionamento:
  1. Documento Ã© processado pelo motor â†’ entidades extraÃ­das via Claude Haiku
  2. Entidades sÃ£o adicionadas/atualizadas no grafo
  3. RelaÃ§Ãµes entre entidades co-ocorrentes sÃ£o criadas/reforÃ§adas
  4. Score de eficÃ¡cia Ã© calculado por resultado (favorÃ¡vel/parcial/desfavorÃ¡vel)
  5. Peso temporal decai exponencialmente (docs antigos valem menos)
  6. Na geraÃ§Ã£o, o grafo fornece argumentos e citaÃ§Ãµes ranqueados por eficÃ¡cia

Custo IA: ~$0.0003 por documento (Claude Haiku).
"""

import anthropic, os, json, re, unicodedata, math
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.motor.models import SessionLocal, KgEntidade, KgRelacao, KgDocumento, MotorMinuta
from app.motor.config import (
    ANTHROPIC_API_KEY, MODELO_RAPIDO, LAMBDA_DECAIMENTO,
    CATEGORIAS, TIPOS_ENTIDADE,
)

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", ""))


# â”€â”€ UtilitÃ¡rios â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _slugify(texto: str) -> str:
    """Normaliza texto para usar como chave Ãºnica no grafo."""
    nfkd = unicodedata.normalize("NFKD", texto.lower().strip())
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", sem_acento).strip("_")[:250]


def _peso_temporal(data: datetime) -> float:
    """
    Peso entre 0.0 e 1.0.
    - Documento de hoje       â†’ 1.0
    - Documento de 18 meses   â†’ ~0.5
    - Documento de 3 anos     â†’ ~0.25
    """
    if data is None:
        return 0.5
    agora = datetime.now(timezone.utc)
    if data.tzinfo is None:
        data = data.replace(tzinfo=timezone.utc)
    dias = max(0, (agora - data).days)
    return round(math.exp(-LAMBDA_DECAIMENTO * dias), 4)


def _chamar_haiku(prompt: str, max_tokens: int = 800) -> dict:
    """Chama Claude Haiku e parseia o JSON retornado."""
    resp = _client.messages.create(
        model=MODELO_RAPIDO,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = resp.content[0].text.strip()
    texto = re.sub(r"^```\w*\n?", "", texto)
    texto = re.sub(r"\n?```$", "", texto)
    try:
        return json.loads(texto)
    except Exception:
        return {}


# â”€â”€ ExtraÃ§Ã£o de entidades â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def extrair_entidades(texto: str, categoria: str) -> dict:
    """
    Extrai entidades do documento usando Claude Haiku.

    Retorna dict com listas:
      argumentos  â€” argumentos/teses principais (max 8)
      citacoes    â€” leis, artigos, sÃºmulas, jurisprudÃªncia
      expressoes  â€” frases caracterÃ­sticas do estilo
      estrutura   â€” padrÃµes de organizaÃ§Ã£o
      tipo_pedido â€” nome curto do pedido principal

    Custo: ~$0.0003 por chamada (Claude Haiku).
    Adapte o prompt para o seu domÃ­nio alterando o texto abaixo.
    """
    nome_cat = CATEGORIAS.get(categoria, categoria)
    prompt = f"""Analise este documento de {nome_cat} e extraia as entidades principais.
Retorne APENAS JSON vÃ¡lido:
{{
  "argumentos": [
    "argumento/tese principal 1 (especÃ­fico, max 15 palavras)",
    "argumento/tese principal 2"
  ],
  "citacoes": [
    "lei, artigo, sÃºmula ou precedente mencionado 1",
    "lei, artigo, sÃºmula ou precedente mencionado 2"
  ],
  "expressoes": [
    "expressÃ£o caracterÃ­stica do estilo do autor 1",
    "expressÃ£o caracterÃ­stica 2"
  ],
  "estrutura": [
    "padrÃ£o estrutural 1 (ex: 'abre com narrativa cronolÃ³gica')",
    "padrÃ£o estrutural 2"
  ],
  "tipo_pedido": "nome curto e especÃ­fico do pedido/aÃ§Ã£o principal"
}}

Regras:
- Argumentos: mÃ¡ximo 8, especÃ­ficos e acionÃ¡veis
- CitaÃ§Ãµes: apenas referÃªncias explicitamente mencionadas no texto
- ExpressÃµes: frases que caracterizam o estilo deste autor
- Evite itens genÃ©ricos como "argumentaÃ§Ã£o jurÃ­dica" ou "pedido deferido"

DOCUMENTO ({nome_cat}) â€” primeiros 6000 chars:
{texto[:6000]}"""

    return _chamar_haiku(prompt, 1000)


# â”€â”€ Registro no grafo â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _obter_ou_criar_entidade(db: Session, tipo: str, valor: str, categoria: str) -> KgEntidade:
    slug = f"{tipo}:{_slugify(valor)}"
    ent = db.query(KgEntidade).filter_by(slug=slug).first()
    if not ent:
        ent = KgEntidade(
            tipo=tipo, valor=valor[:500], slug=slug,
            categoria=categoria, frequencia=0,
        )
        db.add(ent)
        db.flush()
    return ent


def _registrar_relacao(db: Session, origem_id: int, destino_id: int,
                       tipo_relacao: str, peso: float = 1.0):
    """Cria ou reforÃ§a uma aresta no grafo (mÃ©dia mÃ³vel do peso)."""
    rel = db.query(KgRelacao).filter_by(
        origem_id=origem_id, destino_id=destino_id, tipo_relacao=tipo_relacao
    ).first()
    if rel:
        rel.contagem += 1
        rel.peso = round(rel.peso * 0.9 + peso * 0.1, 4)  # mÃ©dia mÃ³vel exponencial
        rel.atualizado_em = datetime.utcnow()
    else:
        rel = KgRelacao(
            origem_id=origem_id, destino_id=destino_id,
            tipo_relacao=tipo_relacao, peso=peso, contagem=1,
        )
        db.add(rel)


def registrar_documento_no_grafo(
    db: Session,
    texto: str,
    categoria: str,
    resultado: str = "indefinido",
    documento_id: int = None,
    estrategico_id: int = None,
    data_doc: datetime = None,
) -> dict:
    """
    Extrai entidades do documento e registra no grafo com resultado.

    Args:
        db:             SessÃ£o SQLAlchemy
        texto:          Texto do documento
        categoria:      CÃ³digo da categoria (ex: "FAM")
        resultado:      "favoravel" | "parcial" | "desfavoravel" | "indefinido"
        documento_id:   ID em motor_documentos (opcional)
        estrategico_id: ID em motor_aprendizado_estrategico (opcional)
        data_doc:       Data do documento (para peso temporal)

    Returns:
        dict com resumo do registro
    """
    entidades_raw = extrair_entidades(texto, categoria)
    if not entidades_raw:
        return {"ok": False, "erro": "falha na extraÃ§Ã£o de entidades"}

    peso = _peso_temporal(data_doc or datetime.utcnow())
    entidade_ids = []
    args_ids, cits_ids = [], []

    # Mapeamento de chaves do JSON para tipos do grafo
    tipo_map = {
        "argumentos": "argumento",
        "citacoes":   "citacao",
        "expressoes": "expressao",
        "estrutura":  "estrutura",
    }

    for chave_json, tipo_kg in tipo_map.items():
        for valor in entidades_raw.get(chave_json, []):
            if not valor or len(valor.strip()) < 4:
                continue
            ent = _obter_ou_criar_entidade(db, tipo_kg, valor.strip(), categoria)
            ent.frequencia = (ent.frequencia or 0) + 1
            ent.ultimo_uso = datetime.utcnow()

            # Acumula resultado por entidade
            if resultado == "favoravel":
                ent.docs_favoravel = (ent.docs_favoravel or 0) + 1
            elif resultado == "parcial":
                ent.docs_parcial = (ent.docs_parcial or 0) + 1
            elif resultado == "desfavoravel":
                ent.docs_desfavoravel = (ent.docs_desfavoravel or 0) + 1

            # Recalcula score de eficÃ¡cia
            # favorÃ¡vel=100%, parcial=50%, desfavorÃ¡vel=0%
            total = (
                (ent.docs_favoravel or 0) +
                (ent.docs_parcial or 0) +
                (ent.docs_desfavoravel or 0)
            )
            if total > 0:
                ent.score_eficacia = round(
                    ((ent.docs_favoravel or 0) * 1.0 +
                     (ent.docs_parcial or 0) * 0.5) / total * 100, 1
                )

            entidade_ids.append(ent.id)
            if tipo_kg == "argumento":
                args_ids.append(ent.id)
            elif tipo_kg == "citacao":
                cits_ids.append(ent.id)
            db.flush()

    # Arestas: argumentos co-ocorrem com citaÃ§Ãµes
    for aid in args_ids:
        for cid in cits_ids:
            _registrar_relacao(db, aid, cid, "citado_com", peso)

    # Arestas: argumento â†’ resultado
    resultado_ent = _obter_ou_criar_entidade(db, "resultado", resultado, categoria)
    for aid in args_ids:
        _registrar_relacao(db, aid, resultado_ent.id, "resultou_em", peso)
    db.flush()

    # Registra o documento no grafo
    kg_doc = KgDocumento(
        documento_id=documento_id,
        estrategico_id=estrategico_id,
        categoria=categoria,
        tipo_pedido=entidades_raw.get("tipo_pedido", ""),
        resultado=resultado,
        entidades_ids=entidade_ids,
        peso_temporal=peso,
        data_doc=data_doc or datetime.utcnow(),
    )
    db.add(kg_doc)
    db.flush()

    return {
        "ok": True,
        "entidades":   len(entidade_ids),
        "argumentos":  len(args_ids),
        "citacoes":    len(cits_ids),
        "tipo_pedido": entidades_raw.get("tipo_pedido", ""),
    }


# â”€â”€ Consultas ao grafo â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def consultar_grafo_para_geracao(categoria: str, tipo_pedido: str = "") -> dict:
    """
    Retorna inteligÃªncia consolidada do grafo para guiar a geraÃ§Ã£o de documentos.
    Use no seu prompt de geraÃ§Ã£o para enriquecer com argumentos eficazes.

    Exemplo de uso:
        intel = kg_service.consultar_grafo_para_geracao("FAM", "guarda compartilhada")
        # intel["argumentos_eficazes"] â†’ lista de argumentos ranqueados
        # intel["citacoes_frequentes"] â†’ citaÃ§Ãµes mais usadas
        # intel["taxa_favoravel"]      â†’ % de sucesso histÃ³rico

    Returns:
        {
          "categoria": str,
          "total_docs_grafo": int,
          "taxa_favoravel": int | None,
          "argumentos_eficazes": [{"argumento": str, "score": float, "freq": int}],
          "citacoes_frequentes": [str],
          "expressoes_caracteristicas": [str],
        }
    """
    db = SessionLocal()
    try:
        args = (
            db.query(KgEntidade)
            .filter_by(tipo="argumento", categoria=categoria)
            .filter(KgEntidade.frequencia >= 2)
            .order_by(KgEntidade.score_eficacia.desc(), KgEntidade.frequencia.desc())
            .limit(10)
            .all()
        )
        cits = (
            db.query(KgEntidade)
            .filter_by(tipo="citacao", categoria=categoria)
            .order_by(KgEntidade.frequencia.desc())
            .limit(8)
            .all()
        )
        exprs = (
            db.query(KgEntidade)
            .filter_by(tipo="expressao", categoria=categoria)
            .order_by(KgEntidade.frequencia.desc())
            .limit(6)
            .all()
        )

        docs = db.query(KgDocumento).filter_by(categoria=categoria).all()
        total = len(docs)
        fav   = sum(1 for d in docs if d.resultado == "favoravel")
        taxa  = round(fav / total * 100) if total > 0 else None

        return {
            "categoria":         categoria,
            "total_docs_grafo":  total,
            "taxa_favoravel":    taxa,
            "argumentos_eficazes": [
                {"argumento": a.valor, "score": a.score_eficacia, "freq": a.frequencia}
                for a in args
            ],
            "citacoes_frequentes":         [c.valor for c in cits],
            "expressoes_caracteristicas":  [e.valor for e in exprs],
        }
    finally:
        db.close()


def encontrar_documentos_similares(texto: str, categoria: str, n: int = 5) -> list:
    """
    Encontra os N documentos histÃ³ricos mais similares ao texto fornecido.
    Algoritmo: sobreposiÃ§Ã£o de entidades (Jaccard) Ã— peso temporal.

    Ãštil para:
      - Sugerir precedentes similares
      - Avisar que um argumento costuma ser rejeitado
      - Mostrar taxa de sucesso em casos parecidos

    Returns:
        Lista de dicts ordenada por score_similaridade (maior = mais similar):
        [{"kg_doc_id": int, "tipo_pedido": str, "resultado": str,
          "score_similaridade": float, "data_doc": str}]
    """
    db = SessionLocal()
    try:
        entidades_novo = extrair_entidades(texto, categoria)
        slugs_novo = set()
        for tipo_kg, chave_json in [("argumento", "argumentos"), ("citacao", "citacoes")]:
            for v in entidades_novo.get(chave_json, []):
                slugs_novo.add(f"{tipo_kg}:{_slugify(v)}")

        if not slugs_novo:
            return []

        ents_match = db.query(KgEntidade).filter(KgEntidade.slug.in_(slugs_novo)).all()
        ids_match  = {e.id for e in ents_match}

        if not ids_match:
            return []

        docs_hist = db.query(KgDocumento).filter(
            KgDocumento.categoria == categoria,
            KgDocumento.entidades_ids.isnot(None),
        ).all()

        scored = []
        for doc in docs_hist:
            doc_ids = set(doc.entidades_ids or [])
            if not doc_ids:
                continue
            intersecao = len(doc_ids & ids_match)
            if intersecao == 0:
                continue
            jaccard = intersecao / len(doc_ids | ids_match)
            score = round(jaccard * (doc.peso_temporal or 0.5) * 100, 1)
            scored.append({
                "kg_doc_id":         doc.id,
                "documento_id":      doc.documento_id,
                "tipo_pedido":       doc.tipo_pedido,
                "resultado":         doc.resultado,
                "score_similaridade": score,
                "peso_temporal":     doc.peso_temporal,
                "data_doc":          doc.data_doc.isoformat() if doc.data_doc else None,
            })

        scored.sort(key=lambda x: -x["score_similaridade"])
        return scored[:n]
    finally:
        db.close()


# â”€â”€ Feedback e aprendizado â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def processar_feedback(
    minuta_id: int,
    feedback: str,          # "usou" | "descartou" | "editou"
    nota: int = None,       # 1-5
    resultado_real: str = None,  # "favoravel" | "parcial" | "desfavoravel"
    obs: str = None,
) -> dict:
    """
    Processa feedback do usuÃ¡rio sobre um documento gerado.
    Atualiza scores das entidades do KG que influenciaram a geraÃ§Ã£o.

    Este Ã© o coraÃ§Ã£o do ciclo de aprendizado contÃ­nuo:
    1. UsuÃ¡rio usa o documento gerado
    2. Informa o resultado real
    3. Motor atualiza scores das entidades â†’ prÃ³ximas geraÃ§Ãµes sÃ£o melhores
    """
    db = SessionLocal()
    try:
        minuta = db.query(MotorMinuta).get(minuta_id)
        if not minuta:
            return {"ok": False, "erro": "Documento nÃ£o encontrado"}

        minuta.feedback      = feedback
        minuta.feedback_nota = nota
        minuta.feedback_obs  = obs
        minuta.feedback_em   = datetime.utcnow()
        if resultado_real:
            minuta.resultado_real = resultado_real

        # Se o usuÃ¡rio usou E informou resultado, atualiza o KG
        if feedback == "usou" and resultado_real and minuta.entidades_usadas:
            for eid in minuta.entidades_usadas:
                ent = db.query(KgEntidade).get(eid)
                if not ent:
                    continue

                if resultado_real == "favoravel":
                    ent.docs_favoravel = (ent.docs_favoravel or 0) + 1
                elif resultado_real == "parcial":
                    ent.docs_parcial = (ent.docs_parcial or 0) + 1
                elif resultado_real == "desfavoravel":
                    ent.docs_desfavoravel = (ent.docs_desfavoravel or 0) + 1

                total = (
                    (ent.docs_favoravel or 0) +
                    (ent.docs_parcial or 0) +
                    (ent.docs_desfavoravel or 0)
                )
                if total > 0:
                    ent.score_eficacia = round(
                        ((ent.docs_favoravel or 0) * 1.0 +
                         (ent.docs_parcial or 0) * 0.5) / total * 100, 1
                    )
                ent.ultimo_uso = datetime.utcnow()

        db.commit()
        return {"ok": True, "minuta_id": minuta_id, "feedback": feedback}
    except Exception as e:
        db.rollback()
        return {"ok": False, "erro": str(e)}
    finally:
        db.close()


def registrar_documento_gerado(
    categoria: str,
    modelo_label: str,
    texto_gerado: str,
    source_id: int = None,
) -> int:
    """
    Registra um documento gerado para rastreabilidade e feedback posterior.
    Retorna o ID do registro (use para POST /feedback/{id}).
    """
    db = SessionLocal()
    try:
        # Entidades ativas desta categoria (influenciaram a geraÃ§Ã£o)
        ents = (
            db.query(KgEntidade)
            .filter_by(categoria=categoria)
            .filter(KgEntidade.score_eficacia >= 50)
            .order_by(KgEntidade.score_eficacia.desc())
            .limit(20)
            .all()
        )
        ent_ids = [e.id for e in ents]

        minuta = MotorMinuta(
            categoria=categoria,
            modelo_label=modelo_label,
            texto_gerado=texto_gerado[:50000],
            entidades_usadas=ent_ids,
            source_id=source_id,
        )
        db.add(minuta)
        db.commit()
        return minuta.id
    finally:
        db.close()


# â”€â”€ ManutenÃ§Ã£o â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def recalcular_pesos_temporais() -> dict:
    """
    Recalcula o peso temporal de todos os documentos no grafo.
    Execute semanalmente via scheduler para manter o decaimento atualizado.
    """
    db = SessionLocal()
    try:
        docs = db.query(KgDocumento).all()
        for doc in docs:
            doc.peso_temporal = _peso_temporal(doc.data_doc)
        db.commit()
        return {"ok": True, "recalculados": len(docs)}
    finally:
        db.close()


def status_grafo() -> dict:
    """EstatÃ­sticas gerais do grafo â€” use no dashboard."""
    db = SessionLocal()
    try:
        from sqlalchemy import func
        total_ents   = db.query(func.count(KgEntidade.id)).scalar()
        total_rels   = db.query(func.count(KgRelacao.id)).scalar()
        total_docs   = db.query(func.count(KgDocumento.id)).scalar()
        total_min    = db.query(func.count(MotorMinuta.id)).scalar()
        com_feedback = db.query(func.count(MotorMinuta.id)).filter(
            MotorMinuta.feedback.isnot(None)
        ).scalar()

        top_args = (
            db.query(KgEntidade)
            .filter_by(tipo="argumento")
            .order_by(KgEntidade.score_eficacia.desc())
            .limit(5)
            .all()
        )

        return {
            "total_entidades":      total_ents,
            "total_relacoes":       total_rels,
            "total_documentos_grafo": total_docs,
            "total_gerados":        total_min,
            "com_feedback":         com_feedback,
            "taxa_feedback":        round(com_feedback / total_min * 100) if total_min else 0,
            "top_argumentos": [
                {"argumento": a.valor[:60], "categoria": a.categoria,
                 "score": a.score_eficacia, "freq": a.frequencia}
                for a in top_args
            ],
        }
    finally:
        db.close()

