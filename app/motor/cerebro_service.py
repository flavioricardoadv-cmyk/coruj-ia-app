"""
cerebro_service.py â€” Motor de Aprendizado de PadrÃµes
=====================================================
Loop de anÃ¡lise multi-passagem que aprende padrÃµes de qualquer corpus de documentos.

Fluxo por documento:
  Pass 1 (Haiku)    â†’ extraÃ§Ã£o inicial de padrÃµes  [rÃ¡pido, barato]
  Pass 2 (Sonnet)   â†’ auto-crÃ­tica + score
  Pass 3+ (Sonnet)  â†’ refinamento atÃ© score >= SCORE_MINIMO ou MAX_ITERACOES

Fluxo estratÃ©gico (documentos com resultado):
  â†’ Extrai argumentos aceitos/rejeitados e liÃ§Ã£o para casos futuros

APIs pÃºblicas principais:
  processar_documento(doc_id)         â†’ analisa e salva padrÃµes
  analisar_com_resultado(texto, cat)  â†’ extrai inteligÃªncia de doc com resultado
  obter_perfil_categoria(cat)         â†’ retorna padrÃµes aprendidos
  obter_inteligencia(cat)             â†’ retorna argumentos eficazes consolidados
  detectar_categoria(texto)           â†’ classifica em categoria automÃ¡tica
"""

import anthropic, os, json, hashlib, re, copy, unicodedata, math
from datetime import datetime
from sqlalchemy.orm import Session

from app.motor.models import (
    SessionLocal, MotorDocumento, MotorPadrao, MotorNivelConhecimento,
    MotorFila, MotorGalho, MotorModelo, MotorAprendizadoEstrategico,
)
from app.motor.config import (
    ANTHROPIC_API_KEY, MODELO_RAPIDO, MODELO_QUALIDADE,
    SCORE_MINIMO, MAX_ITERACOES, LAMBDA_DECAIMENTO,
    CATEGORIAS, CATEGORIAS_VALIDAS, CONTEXTO_DOMINIO, NOME_AUTOR,
    DOCS_PARA_CONSOLIDAR, REGENERAR_MODELO_A_CADA,
)

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", ""))

# ChromaDB opcional â€” motor funciona sem ele
_chroma = None


def _get_chroma():
    """Inicializa ChromaDB sob demanda. Retorna None se indisponÃ­vel."""
    global _chroma
    if _chroma is None:
        try:
            from app.motor.config import CHROMA_ATIVO, CHROMA_HOST, CHROMA_PORT
            if not CHROMA_ATIVO:
                return None
            import chromadb
            c = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
            _chroma = c.get_or_create_collection("motor_padroes")
        except Exception:
            _chroma = None
    return _chroma


# â”€â”€ Processamento principal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def processar_documento(documento_id: int) -> dict:
    """
    Entry point para o scheduler/worker.
    Roda o loop completo de anÃ¡lise e salva no banco.

    Returns:
        {"ok": True, "score": float, "iteracoes": int}  em caso de sucesso
        {"erro": str}                                    em caso de falha
    """
    db = SessionLocal()
    try:
        doc = db.query(MotorDocumento).filter(MotorDocumento.id == documento_id).first()
        if not doc:
            return {"erro": "documento nÃ£o encontrado"}

        doc.status_analise = "processando"
        doc.atualizado_em  = datetime.utcnow()
        db.commit()

        texto = doc.texto_limpo or doc.texto_extraido or ""
        resultado = _loop_analise(texto, doc.categoria)

        # Identifica tipo de pedido/aÃ§Ã£o
        pedido_info = _detectar_tipo_pedido(texto, doc.categoria)

        # Salva padrÃ£o extraÃ­do
        padrao = MotorPadrao(
            documento_id  = documento_id,
            categoria     = doc.categoria,
            iteracoes     = resultado["iteracoes"],
            score_final   = resultado["score_final"],
            estrutura     = resultado.get("estrutura", {}),
            vocabulario   = resultado.get("vocabulario", {}),
            argumentacao  = resultado.get("argumentacao", {}),
            citacoes      = resultado.get("citacoes_tipicas", []),
            estilo_formal = resultado.get("estilo_formal", {}),
            lacunas       = resultado.get("lacunas", []),
            rascunho      = resultado.get("rascunho", []),
        )
        db.add(padrao)
        db.flush()

        # Atualiza galho da Ã¡rvore de conhecimento
        galho = _atualizar_galho(db, doc.categoria, pedido_info, resultado)

        # Gera/regenera template automÃ¡tico quando galho consolida
        deve_gerar = (
            galho.status == "consolidado" and not galho.modelo_gerado
        ) or (
            galho.modelo_gerado and galho.count_docs % REGENERAR_MODELO_A_CADA == 0
        )
        if deve_gerar:
            _gerar_template_galho(db, galho)

        # Atualiza bateria de conhecimento
        _atualizar_nivel(db, doc.categoria, resultado["score_final"], resultado,
                         resultado.get("lacunas", []))

        # Indexa no ChromaDB (opcional)
        _indexar_padrao(padrao.id, doc.categoria, resultado)

        # Registra no Grafo de Conhecimento
        try:
            from app.motor.kg_service import registrar_documento_no_grafo
            registrar_documento_no_grafo(
                db=db, texto=texto, categoria=doc.categoria,
                resultado="indefinido", documento_id=documento_id,
                data_doc=doc.criado_em,
            )
        except Exception:
            pass  # KG nunca bloqueia o processamento principal

        doc.status_analise = "concluido"
        doc.atualizado_em  = datetime.utcnow()
        db.commit()

        return {"ok": True, "score": resultado["score_final"], "iteracoes": resultado["iteracoes"]}

    except Exception as e:
        try:
            db.rollback()
            doc = db.query(MotorDocumento).filter(MotorDocumento.id == documento_id).first()
            if doc:
                doc.status_analise = "erro"
                db.commit()
        except Exception:
            pass
        return {"erro": str(e)}
    finally:
        db.close()


# â”€â”€ Loop de anÃ¡lise multi-pass â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _loop_analise(texto: str, categoria: str) -> dict:
    """
    Multi-pass: extrai â†’ critica â†’ refina atÃ© score >= SCORE_MINIMO.

    Pass 1: Haiku (rÃ¡pido, barato) â€” extraÃ§Ã£o inicial
    Pass 2+: Sonnet â€” auto-crÃ­tica + refinamento
    """
    nome_cat = CATEGORIAS.get(categoria, categoria)
    rascunho = []
    padrao   = {}
    score    = 0

    for iteracao in range(1, MAX_ITERACOES + 1):
        if iteracao == 1:
            padrao = _chamar_claude(
                _prompt_extracao(texto, nome_cat),
                modelo=MODELO_RAPIDO, max_tokens=2000,
            )
            rascunho.append({"pass": 1, "tipo": "extracao", "resultado": copy.deepcopy(padrao)})
        else:
            critica = _chamar_claude(
                _prompt_critica(texto, padrao),
                modelo=MODELO_QUALIDADE, max_tokens=1000,
            )
            rascunho.append({"pass": iteracao, "tipo": "critica", "resultado": copy.deepcopy(critica)})

            padrao = _chamar_claude(
                _prompt_refinamento(texto, padrao, critica, iteracao),
                modelo=MODELO_QUALIDADE, max_tokens=2500,
            )
            rascunho.append({"pass": iteracao, "tipo": "refinamento", "resultado": copy.deepcopy(padrao)})

        score = _pontuar(padrao)
        padrao["_score_iteracao"] = score

        if score >= SCORE_MINIMO:
            break

    padrao["score_final"] = score
    padrao["iteracoes"]   = iteracao
    padrao["rascunho"]    = rascunho
    padrao.pop("_score_iteracao", None)
    return padrao


# â”€â”€ Prompts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Adapte estes prompts para o vocabulÃ¡rio do seu domÃ­nio.

def _prompt_extracao(texto: str, nome_categoria: str) -> str:
    return f"""VocÃª Ã© um analista especializado em {CONTEXTO_DOMINIO}.

Analise este documento de {nome_categoria} e extraia os padrÃµes de escrita com mÃ¡xima precisÃ£o.

Retorne APENAS JSON vÃ¡lido com esta estrutura (sem markdown):
{{
  "estrutura": {{
    "abertura": "como exatamente comeÃ§a o documento",
    "ordem_secoes": ["lista de seÃ§Ãµes na ordem que aparecem"],
    "encerramento_relatorio": "frase exata que encerra a parte factual",
    "fechamento_dispositivo": "fÃ³rmula usada na conclusÃ£o/pedido",
    "linha_local_data": "formato da linha de local e data"
  }},
  "vocabulario": {{
    "verbos_preferidos": ["requerer", "pugnar", "postular"],
    "expressoes_caracteristicas": ["expressÃµes tÃ­picas do autor"],
    "conectivos": ["ademais", "contudo", "outrossim"],
    "referencias_documento": "padrÃ£o de referÃªncia a pÃ¡ginas/folhas"
  }},
  "argumentacao": {{
    "estrutura_analise": "como desenvolve a argumentaÃ§Ã£o",
    "uso_precedentes": "como cita e usa precedentes/jurisprudÃªncia",
    "relacao_fato_direito": "como conecta fatos ao fundamento"
  }},
  "citacoes_tipicas": ["referÃªncias explicitamente mencionadas no texto"],
  "estilo_formal": {{
    "pessoa_verbal": "terceira pessoa singular/plural",
    "tempo_verbal_relatorio": "pretÃ©rito perfeito/imperfeito",
    "tempo_verbal_analise": "presente/futuro",
    "subtitulos_negrito": true,
    "subtitulos_centralizados": false,
    "formato_subtitulo": "ex: 'Da QuestÃ£o Principal'"
  }},
  "lacunas": ["o que nÃ£o foi possÃ­vel identificar claramente neste documento"]
}}

DOCUMENTO PARA ANÃLISE ({nome_categoria}):
{texto[:8000]}"""


def _prompt_critica(texto: str, padrao: dict) -> str:
    return f"""VocÃª extraiu estes padrÃµes de um documento. Critique: o que estÃ¡ impreciso, genÃ©rico ou faltando?

PADRÃƒO EXTRAÃDO:
{json.dumps(padrao, ensure_ascii=False, indent=2)[:3000]}

TRECHO DO DOCUMENTO ORIGINAL:
{texto[:3000]}

Retorne APENAS JSON:
{{
  "pontos_fracos": ["o que estÃ¡ vago ou incorreto"],
  "lacunas_criticas": ["o que nÃ£o foi capturado mas Ã© importante"],
  "sugestoes": ["como melhorar cada item"],
  "campos_confiantes": ["campos que estÃ£o corretos"],
  "score_parcial": 6.5
}}"""


def _prompt_refinamento(texto: str, padrao: dict, critica: dict, iteracao: int) -> str:
    return f"""Refine os padrÃµes extraÃ­dos com base na crÃ­tica. IteraÃ§Ã£o {iteracao} â€” seja mais preciso.

VERSÃƒO ANTERIOR:
{json.dumps(padrao, ensure_ascii=False, indent=2)[:2000]}

CRÃTICA:
{json.dumps(critica, ensure_ascii=False, indent=2)[:1000]}

TRECHO DO DOCUMENTO:
{texto[:5000]}

Retorne APENAS JSON refinado com a mesma estrutura (estrutura, vocabulario, argumentacao,
citacoes_tipicas, estilo_formal, lacunas) â€” mais preciso e especÃ­fico que antes."""


def _pontuar(padrao: dict) -> float:
    """
    Pontua o padrÃ£o extraÃ­do (0-10) baseado em cobertura e especificidade.
    Ajuste os pesos conforme a importÃ¢ncia de cada campo no seu domÃ­nio.
    """
    score = 0.0

    estrutura = padrao.get("estrutura", {})
    if estrutura.get("abertura"):              score += 0.8
    if estrutura.get("ordem_secoes"):          score += 0.8
    if estrutura.get("fechamento_dispositivo"): score += 0.7
    if estrutura.get("linha_local_data"):      score += 0.7

    vocab = padrao.get("vocabulario", {})
    exprs = vocab.get("expressoes_caracteristicas", [])
    if len(exprs) >= 3:   score += 1.0
    elif len(exprs) >= 1: score += 0.5
    if vocab.get("verbos_preferidos"):         score += 0.8
    if vocab.get("referencias_documento"):     score += 0.7

    arg = padrao.get("argumentacao", {})
    if arg.get("estrutura_analise"):           score += 0.8
    if arg.get("uso_precedentes"):             score += 0.7
    if arg.get("relacao_fato_direito"):        score += 0.5

    cits = padrao.get("citacoes_tipicas", [])
    if len(cits) >= 2:   score += 1.0
    elif len(cits) >= 1: score += 0.5

    estilo = padrao.get("estilo_formal", {})
    if estilo.get("pessoa_verbal"):            score += 0.5
    if "subtitulos_negrito" in estilo:         score += 0.5
    if estilo.get("formato_subtitulo"):        score += 0.5

    return min(10.0, round(score, 2))


# â”€â”€ Ãrvore de conhecimento â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _slugify(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower())
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", sem_acento).strip("_")[:80]


def _detectar_tipo_pedido(texto: str, categoria: str) -> dict:
    """Usa Haiku para identificar o tipo especÃ­fico de pedido/aÃ§Ã£o."""
    nome_cat = CATEGORIAS.get(categoria, categoria)
    prompt = f"""Analise este documento de {nome_cat} e responda APENAS com JSON:
{{
  "pedido": "nome curto e preciso do tipo de pedido/aÃ§Ã£o (ex: 'RegulamentaÃ§Ã£o de visitas')",
  "dispositivo": "fÃ³rmula exata usada na conclusÃ£o (copie do texto)",
  "argumentos_principais": ["arg1", "arg2", "arg3"]
}}

DOCUMENTO ({nome_cat}):
{texto[:4000]}"""

    try:
        resp = _client.messages.create(
            model=MODELO_RAPIDO, max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        txt = re.sub(r"^```\w*\n?", "", resp.content[0].text.strip())
        txt = re.sub(r"\n?```$", "", txt)
        return json.loads(txt)
    except Exception:
        return {"pedido": f"Documento de {nome_cat}", "dispositivo": "", "argumentos_principais": []}


def _atualizar_galho(db: Session, categoria: str, pedido_info: dict, padrao: dict) -> MotorGalho:
    """Cria ou atualiza um galho da Ã¡rvore de conhecimento."""
    pedido = pedido_info.get("pedido", f"Documento de {CATEGORIAS.get(categoria, categoria)}")
    slug   = _slugify(pedido)

    galho = db.query(MotorGalho).filter_by(categoria=categoria, tipo_pedido_slug=slug).first()
    if not galho:
        galho = MotorGalho(
            categoria=categoria, tipo_pedido=pedido,
            tipo_pedido_slug=slug, count_docs=0, status="novo",
        )
        db.add(galho)
        db.flush()

    galho.count_docs = (galho.count_docs or 0) + 1
    galho.status = (
        "consolidado" if galho.count_docs >= DOCS_PARA_CONSOLIDAR
        else "aprendendo" if galho.count_docs >= 2
        else "novo"
    )
    galho.padroes = _merge_padroes(galho.padroes or {}, padrao)

    disp = pedido_info.get("dispositivo", "")
    if disp:
        displist = galho.dispositivos or []
        if disp not in displist:
            displist.append(disp)
        galho.dispositivos = displist[:5]

    args = pedido_info.get("argumentos_principais", [])
    if args:
        arglist = galho.argumentos_tipo or []
        for a in args:
            if a not in arglist:
                arglist.append(a)
        galho.argumentos_tipo = arglist[:20]

    galho.atualizado_em = datetime.utcnow()
    db.flush()
    return galho


def _gerar_template_galho(db: Session, galho: MotorGalho) -> bool:
    """
    Gera automaticamente um template quando o galho consolida.
    Usa Claude Sonnet com os padrÃµes aprendidos como contexto.
    """
    nome_cat = CATEGORIAS.get(galho.categoria, galho.categoria)
    padrao   = galho.padroes or {}
    args     = galho.argumentos_tipo or []
    disps    = galho.dispositivos or []

    # Adapte este prompt para o formato de documento do seu domÃ­nio
    prompt = f"""VocÃª Ã© um assistente especializado em {CONTEXTO_DOMINIO}.

Com base nos padrÃµes reais aprendidos, crie um template de documento para:

CATEGORIA: {nome_cat}
TIPO DE PEDIDO/AÃ‡ÃƒO: {galho.tipo_pedido}
DOCUMENTOS ANALISADOS: {galho.count_docs}

PADRÃ•ES APRENDIDOS:
- Abertura: {(padrao.get('estrutura') or {}).get('abertura', 'â€”')}
- VocabulÃ¡rio tÃ­pico: {', '.join((padrao.get('vocabulario') or {}).get('expressoes_caracteristicas', [])[:5])}
- Argumentos recorrentes: {'; '.join(args[:5])}
- FÃ³rmulas de conclusÃ£o: {' | '.join(disps[:3])}
- CitaÃ§Ãµes tÃ­picas: {', '.join((padrao.get('citacoes_tipicas') or [])[:5])}
- Encerramento factual: {(padrao.get('estrutura') or {}).get('encerramento_relatorio', 'â€”')}

Use [PLACEHOLDER] para dados variÃ¡veis.
Retorne APENAS o texto do template, sem explicaÃ§Ãµes."""

    try:
        resp = _client.messages.create(
            model=MODELO_QUALIDADE, max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        texto_template = resp.content[0].text.strip()

        slug_curto = galho.tipo_pedido_slug[:8].upper().replace("_", "")
        base_codigo = f"{galho.categoria}-{slug_curto}"
        n, codigo = 1, f"{base_codigo}-{1:03d}"
        while db.query(MotorModelo).filter_by(codigo=codigo).first():
            n += 1
            codigo = f"{base_codigo}-{n:03d}"

        modelo = MotorModelo(
            codigo=codigo,
            titulo=f"{galho.tipo_pedido} ({nome_cat})",
            categoria=nome_cat,
            texto=texto_template,
            gatilhos=galho.tipo_pedido,
            origem="auto-gerado",
        )
        db.add(modelo)
        db.flush()

        galho.modelo_id     = modelo.id
        galho.modelo_gerado = True
        galho.atualizado_em = datetime.utcnow()
        db.flush()
        return True
    except Exception:
        return False


# â”€â”€ AnÃ¡lise estratÃ©gica â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def analisar_com_resultado(texto: str, categoria: str) -> dict:
    """
    Analisa um documento que inclui o resultado final (sentenÃ§a, decisÃ£o, etc.).
    Extrai inteligÃªncia estratÃ©gica: o que foi acolhido/rejeitado e por quÃª.

    Args:
        texto:     Texto do documento (pode ser o processo completo)
        categoria: CÃ³digo da categoria

    Returns:
        dict com argumentos_proponente, resultado, argumentos_acolhidos,
        argumentos_rejeitados, citacoes_decisao, licao_estrategica
    """
    nome_cat = CATEGORIAS.get(categoria, categoria)

    # Pass 1 (Haiku): segmentaÃ§Ã£o rÃ¡pida
    prompt_seg = f"""Analise este documento de {nome_cat} e identifique o que existe.
Retorne APENAS JSON:
{{
  "tem_argumentacao":  true,
  "tem_contra_argumentacao": false,
  "tem_decisao_resultado": true,
  "pedido_principal": "descriÃ§Ã£o curta do pedido",
  "resultado": "favoravel|parcial|desfavoravel|indefinido",
  "resultado_resumo": "o que foi decidido em 1-2 frases"
}}

DOCUMENTO: {texto[:3000]}...{texto[-2000:]}"""

    try:
        segm = _chamar_claude(prompt_seg, MODELO_RAPIDO, 400)
    except Exception:
        segm = {}

    # Pass 2 (Sonnet): extraÃ§Ã£o estratÃ©gica profunda
    prompt_estrategia = f"""Analise este documento de {nome_cat} e extraia inteligÃªncia estratÃ©gica.

Foque em:
1. Os argumentos usados pelo proponente
2. Os contra-argumentos (se houver)
3. O que foi acolhido e rejeitado pela autoridade decisora
4. Quais fundamentos convenceram a decisÃ£o
5. LiÃ§Ã£o estratÃ©gica para casos futuros similares

Retorne APENAS JSON:
{{
  "pedido_principal": "tipo especÃ­fico de pedido/aÃ§Ã£o",
  "argumentos_proponente": ["argumento 1", "argumento 2"],
  "argumentos_contrarios": ["contra-argumento 1"],
  "resultado": "favoravel|parcial|desfavoravel|indefinido",
  "resultado_descricao": "o que exatamente foi decidido",
  "argumentos_acolhidos": ["argumento do proponente que foi aceito"],
  "argumentos_rejeitados": ["argumento do proponente que foi rejeitado"],
  "citacoes_decisao": ["lei/precedente que a autoridade usou na decisÃ£o"],
  "licao_estrategica": "em 2-3 frases: o que aprender deste caso para situaÃ§Ãµes futuras similares",
  "vocabulario_proponente": ["termos e expressÃµes caracterÃ­sticos usados"],
  "estrutura_argumentacao": "como a argumentaÃ§Ã£o foi estruturada"
}}

DOCUMENTO COMPLETO ({nome_cat}):
{texto[:12000]}"""

    try:
        estrategia = _chamar_claude(prompt_estrategia, MODELO_QUALIDADE, 2000)
    except Exception as e:
        estrategia = {"_erro": str(e)}

    # Merge com segmentaÃ§Ã£o
    if segm.get("resultado") and not estrategia.get("resultado"):
        estrategia["resultado"] = segm["resultado"]
    if segm.get("resultado_resumo") and not estrategia.get("resultado_descricao"):
        estrategia["resultado_descricao"] = segm["resultado_resumo"]
    if segm.get("pedido_principal") and not estrategia.get("pedido_principal"):
        estrategia["pedido_principal"] = segm["pedido_principal"]

    return estrategia


def salvar_aprendizado_estrategico(
    db: Session, categoria: str, nome_arquivo: str,
    hash_doc: str, estrategia: dict,
) -> MotorAprendizadoEstrategico:
    """Persiste a inteligÃªncia extraÃ­da de um documento com resultado."""
    pedido = estrategia.get("pedido_principal",
                            f"Documento de {CATEGORIAS.get(categoria, categoria)}")
    slug = _slugify(pedido)

    ae = MotorAprendizadoEstrategico(
        categoria             = categoria,
        tipo_pedido           = pedido,
        tipo_pedido_slug      = slug,
        nome_arquivo          = nome_arquivo,
        hash_conteudo         = hash_doc,
        argumentos_proponente = estrategia.get("argumentos_proponente", []),
        argumentos_contrarios = estrategia.get("argumentos_contrarios", []),
        resultado             = estrategia.get("resultado", "indefinido"),
        resultado_descricao   = estrategia.get("resultado_descricao", ""),
        argumentos_acolhidos  = estrategia.get("argumentos_acolhidos", []),
        argumentos_rejeitados = estrategia.get("argumentos_rejeitados", []),
        citacoes_decisao      = estrategia.get("citacoes_decisao", []),
        licao_estrategica     = estrategia.get("licao_estrategica", ""),
        padroes_extras        = {
            "vocabulario": estrategia.get("vocabulario_proponente", []),
            "estrutura":   estrategia.get("estrutura_argumentacao", ""),
        },
        status_analise = "concluido",
    )
    db.add(ae)
    db.flush()

    # Registra no KG com resultado real
    try:
        from app.motor.kg_service import registrar_documento_no_grafo
        registrar_documento_no_grafo(
            db=db,
            texto=str(estrategia.get("argumentos_proponente", [])),
            categoria=categoria,
            resultado=estrategia.get("resultado", "indefinido"),
            estrategico_id=ae.id,
            data_doc=datetime.utcnow(),
        )
    except Exception:
        pass

    # Atualiza galho
    padrao_para_galho = {
        "vocabulario":    {"expressoes_caracteristicas": estrategia.get("vocabulario_proponente", [])},
        "argumentacao":   {"estrutura_analise": estrategia.get("estrutura_argumentacao", "")},
        "citacoes_tipicas": estrategia.get("citacoes_decisao", []),
    }
    pedido_info = {
        "pedido": pedido, "dispositivo": "",
        "argumentos_principais": estrategia.get("argumentos_proponente", [])[:3],
    }
    _atualizar_galho(db, categoria, pedido_info, padrao_para_galho)
    return ae


# â”€â”€ Consultas pÃºblicas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def obter_perfil_categoria(categoria: str) -> dict | None:
    """
    Retorna o perfil de padrÃµes aprendidos para a categoria.
    Use no prompt de geraÃ§Ã£o para incorporar o estilo aprendido.
    """
    db = SessionLocal()
    try:
        nivel = db.query(MotorNivelConhecimento).filter_by(categoria=categoria).first()
        if not nivel or nivel.total_docs == 0:
            return None
        return {
            "categoria":          nivel.categoria,
            "percentual":         nivel.percentual,
            "total_docs":         nivel.total_docs,
            "docs_aprovados":     nivel.docs_aprovados,
            "padrao_medio":       nivel.padrao_medio or {},
            "cobertura_temas":    nivel.cobertura_temas or {},
            "lacunas_atuais":     nivel.lacunas_atuais or [],
            "proxima_necessidade": nivel.proxima_necessidade or "",
            "atualizado_em":      nivel.atualizado_em.isoformat() if nivel.atualizado_em else None,
        }
    finally:
        db.close()


def obter_inteligencia(categoria: str, tipo_pedido_slug: str = None) -> dict:
    """
    Retorna inteligÃªncia estratÃ©gica consolidada para uma categoria.
    Usa peso temporal â€” documentos recentes tÃªm mais influÃªncia.
    """
    db = SessionLocal()
    try:
        q = db.query(MotorAprendizadoEstrategico).filter_by(categoria=categoria)
        if tipo_pedido_slug:
            q = q.filter_by(tipo_pedido_slug=tipo_pedido_slug)
        registros = q.order_by(MotorAprendizadoEstrategico.criado_em.desc()).all()

        if not registros:
            return {}

        from collections import Counter

        def peso(r) -> float:
            if r.criado_em is None:
                return 0.5
            dias = max(0, (datetime.utcnow() - r.criado_em).days)
            return math.exp(-LAMBDA_DECAIMENTO * dias)

        acolhidos_c: Counter = Counter()
        rejeitados_c: Counter = Counter()
        citacoes_c:   Counter = Counter()
        licoes: list = []
        favoraveis_peso = 0.0
        total_peso = 0.0

        for r in registros:
            w = peso(r)
            total_peso += w
            for a in (r.argumentos_acolhidos or []):
                acolhidos_c[a] += w
            for a in (r.argumentos_rejeitados or []):
                rejeitados_c[a] += w
            for c in (r.citacoes_decisao or []):
                citacoes_c[c] += w
            if r.licao_estrategica:
                licoes.append((r.criado_em, r.licao_estrategica))
            if r.resultado == "favoravel":
                favoraveis_peso += w

        licoes.sort(key=lambda x: x[0] or datetime.min)
        licoes_texto = [l for _, l in licoes[-5:]]

        return {
            "total_documentos":       len(registros),
            "taxa_favoravel":         round(favoraveis_peso / total_peso * 100) if total_peso > 0 else 0,
            "argumentos_eficazes":    [a for a, _ in acolhidos_c.most_common(8)],
            "argumentos_evitar":      [a for a, _ in rejeitados_c.most_common(5)],
            "citacoes_que_convencem": [c for c, _ in citacoes_c.most_common(8)],
            "licoes":                 licoes_texto,
        }
    finally:
        db.close()


def obter_status_todos() -> list:
    """Retorna bateria de conhecimento de todas as categorias â€” para dashboard."""
    db = SessionLocal()
    try:
        niveis   = db.query(MotorNivelConhecimento).all()
        resultado = {c: {
            "categoria":          c,
            "nome":               CATEGORIAS[c],
            "percentual":         0,
            "total_docs":         0,
            "proxima_necessidade": _sugerir_necessidade(c, MotorNivelConhecimento()),
        } for c in CATEGORIAS}

        for n in niveis:
            resultado[n.categoria] = {
                "categoria":          n.categoria,
                "nome":               CATEGORIAS.get(n.categoria, n.categoria),
                "percentual":         n.percentual or 0,
                "total_docs":         n.total_docs or 0,
                "docs_aprovados":     n.docs_aprovados or 0,
                "lacunas_count":      len(n.lacunas_atuais or []),
                "proxima_necessidade": n.proxima_necessidade or "",
                "atualizado_em":      n.atualizado_em.isoformat() if n.atualizado_em else None,
            }
        return sorted(resultado.values(), key=lambda x: -x["percentual"])
    finally:
        db.close()


def obter_arvore_conhecimento() -> list:
    """Ãrvore completa de conhecimento por categoria â†’ galhos."""
    db = SessionLocal()
    try:
        galhos = db.query(MotorGalho).order_by(
            MotorGalho.categoria, MotorGalho.count_docs.desc()
        ).all()

        from sqlalchemy import func
        estrategicos = dict(
            db.query(MotorAprendizadoEstrategico.categoria,
                     func.count(MotorAprendizadoEstrategico.id))
            .group_by(MotorAprendizadoEstrategico.categoria).all()
        )
        favoraveis = dict(
            db.query(MotorAprendizadoEstrategico.categoria,
                     func.count(MotorAprendizadoEstrategico.id))
            .filter(MotorAprendizadoEstrategico.resultado == "favoravel")
            .group_by(MotorAprendizadoEstrategico.categoria).all()
        )

        arvore: dict = {c: {
            "categoria": c, "nome": CATEGORIAS[c],
            "total_docs": 0,
            "documentos_com_resultado": estrategicos.get(c, 0),
            "taxa_favoravel": (
                round(favoraveis.get(c, 0) / estrategicos[c] * 100)
                if estrategicos.get(c) else None
            ),
            "galhos": [],
        } for c in CATEGORIAS}

        for g in galhos:
            if g.categoria not in arvore:
                continue
            arvore[g.categoria]["total_docs"] += g.count_docs
            ae_galho = db.query(MotorAprendizadoEstrategico).filter_by(
                categoria=g.categoria, tipo_pedido_slug=g.tipo_pedido_slug
            ).all()
            fav_galho = sum(1 for ae in ae_galho if ae.resultado == "favoravel")

            arvore[g.categoria]["galhos"].append({
                "id":            g.id,
                "tipo_pedido":   g.tipo_pedido,
                "count":         g.count_docs,
                "status":        g.status,
                "modelo_gerado": g.modelo_gerado,
                "argumentos":    (g.argumentos_tipo or [])[:5],
                "com_resultado": len(ae_galho),
                "taxa_favoravel": round(fav_galho / len(ae_galho) * 100) if ae_galho else None,
                "atualizado_em": g.atualizado_em.isoformat() if g.atualizado_em else None,
            })

        return sorted(arvore.values(),
                      key=lambda x: -(x["total_docs"] + x["documentos_com_resultado"] * 2))
    finally:
        db.close()


def detectar_categoria(texto: str) -> str:
    """
    Classifica o texto em uma das categorias configuradas.
    Adapte o prompt para o vocabulÃ¡rio do seu domÃ­nio.
    """
    fallback = _detectar_categoria_local(texto)
    if not (ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", "")):
        return fallback

    opcoes = "\n".join(f"{k} â€” {v}" for k, v in CATEGORIAS.items())
    prompt = f"""Classifique este documento em UMA das categorias abaixo.
Responda APENAS com o cÃ³digo (2-4 letras), sem explicaÃ§Ã£o.

{opcoes}

DOCUMENTO:
{texto[:3000]}

Responda apenas com o cÃ³digo (ex: FAM):"""

    try:
        resp = _client.messages.create(
            model=MODELO_RAPIDO, max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        codigo = resp.content[0].text.strip().upper()[:4]
        return codigo if codigo in CATEGORIAS_VALIDAS else fallback
    except Exception:
        return fallback


def _detectar_categoria_local(texto: str) -> str:
    """Classificador local simples para importacao em lote sem depender de IA."""
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    hints = {
        "FAM": ["guarda", "alimentos", "convivencia", "visita", "menor", "familia", "divorcio", "genitor"],
        "EXE": ["prisao civil", "execucao de alimentos", "exequente", "executado", "debito alimentar", "rito da prisao"],
        "CUR": ["curatela", "interdicao", "curador", "interditando", "incapaz"],
        "MS": ["mandado de seguranca", "impetrante", "autoridade coatora", "direito liquido e certo"],
        "PC": ["indenizacao", "dano moral", "procedimento comum", "responsabilidade civil"],
        "SAU": ["saude", "medicamento", "tratamento", "sus", "vaga", "natjus"],
        "PAT": ["inventario", "alvara", "espolio", "prestacao de contas", "patrimonio"],
        "REC": ["apelacao", "recurso", "contrarrazoes", "agravo"],
    }
    scores = {code: sum(1 for hint in words if hint in t) for code, words in hints.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "GEN"


# â”€â”€ Helpers internos â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _atualizar_nivel(db: Session, categoria: str, score: float,
                     padrao: dict, lacunas: list):
    nivel = db.query(MotorNivelConhecimento).filter_by(categoria=categoria).first()
    if not nivel:
        nivel = MotorNivelConhecimento(categoria=categoria)
        db.add(nivel)

    nivel.total_docs = (nivel.total_docs or 0) + 1
    if score >= SCORE_MINIMO:
        nivel.docs_aprovados = (nivel.docs_aprovados or 0) + 1

    nivel.percentual = round((nivel.docs_aprovados or 0) / nivel.total_docs * 100, 1)

    base = nivel.padrao_medio or {}
    nivel.padrao_medio = _merge_padroes(base, padrao)

    cobertura = nivel.cobertura_temas or {}
    for campo in ["estrutura", "vocabulario", "argumentacao", "citacoes_tipicas", "estilo_formal"]:
        if padrao.get(campo):
            cobertura[campo] = True
    nivel.cobertura_temas = cobertura

    lacunas_atuais = set(nivel.lacunas_atuais or [])
    nivel.lacunas_atuais = list(lacunas_atuais | set(lacunas or []))

    nivel.proxima_necessidade = _sugerir_necessidade(categoria, nivel)
    nivel.atualizado_em = datetime.utcnow()
    db.flush()


def _sugerir_necessidade(categoria: str, nivel: MotorNivelConhecimento) -> str:
    nome = CATEGORIAS.get(categoria, categoria)
    pct  = nivel.percentual or 0
    docs = nivel.total_docs or 0
    if docs == 0:
        return f"Envie seus primeiros documentos de {nome} para comeÃ§ar o aprendizado"
    if pct < 30:
        return f"Preciso de mais documentos de {nome} â€” qualquer tipo"
    if pct < 60:
        return f"Ã“timo progresso em {nome}! Envie documentos com fundamentaÃ§Ã£o mais elaborada"
    if pct < 85:
        lacunas = (nivel.lacunas_atuais or [])[:2]
        if lacunas:
            return f"Para completar {nome}: {'; '.join(lacunas)}"
        return f"Envie casos mais complexos de {nome} para refinar o estilo"
    return f"Categoria {nome} consolidada â€” continue alimentando para manter atualizado"


def _merge_padroes(base: dict, novo: dict) -> dict:
    """Deep merge: listas sÃ£o unidas (sem duplicatas), strings sobrescrevem, dicts recursam."""
    resultado = dict(base)
    for k, v in novo.items():
        if k.startswith("_") or k in ("score_final", "iteracoes", "rascunho", "lacunas"):
            continue
        if k not in resultado:
            resultado[k] = v
        elif isinstance(v, list) and isinstance(resultado[k], list):
            seen, merged = set(), []
            for item in resultado[k] + v:
                key = str(item)
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
            resultado[k] = merged[:100]  # cap generoso
        elif isinstance(v, dict) and isinstance(resultado[k], dict):
            resultado[k] = _merge_padroes(resultado[k], v)
        else:
            resultado[k] = v
    return resultado


def _indexar_padrao(padrao_id: int, categoria: str, padrao: dict):
    """Indexa no ChromaDB para busca semÃ¢ntica (opcional)."""
    col = _get_chroma()
    if col is None:
        return
    try:
        doc_text = json.dumps({
            "estrutura":   padrao.get("estrutura", {}),
            "vocabulario": padrao.get("vocabulario", {}),
            "argumentacao": padrao.get("argumentacao", {}),
        }, ensure_ascii=False)
        col.upsert(
            ids=[f"padrao_{padrao_id}"],
            documents=[doc_text],
            metadatas=[{"categoria": categoria, "score": padrao.get("score_final", 0),
                        "padrao_id": padrao_id}],
        )
    except Exception:
        pass


def _chamar_claude(prompt: str, modelo: str, max_tokens: int = 2000) -> dict:
    """Chama o Claude e parseia o JSON retornado."""
    resp = _client.messages.create(
        model=modelo, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = resp.content[0].text.strip()
    texto = re.sub(r"^```\w*\n?", "", texto)
    texto = re.sub(r"\n?```$", "", texto)
    try:
        return json.loads(texto)
    except Exception:
        return {"_raw": texto}

