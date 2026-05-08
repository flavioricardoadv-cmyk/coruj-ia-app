"""
models.py â€” Modelos SQLAlchemy do Motor de Aprendizado
======================================================
Inclui TODAS as tabelas necessÃ¡rias para o motor funcionar de forma standalone.

Tabelas criadas:
  motor_documentos           â€” documentos enviados para aprendizado
  motor_padroes              â€” padrÃµes extraÃ­dos por documento (multi-pass)
  motor_nivel_conhecimento   â€” bateria de conhecimento agregada por categoria
  motor_galhos               â€” Ã¡rvore de conhecimento (categoria Ã— tipo de pedido)
  motor_modelos              â€” templates gerados automaticamente pelo motor
  motor_aprendizado_estrategico â€” inteligÃªncia extraÃ­da de processos com resultado
  motor_fila                 â€” fila de processamento assÃ­ncrono
  kg_entidades               â€” nÃ³s do Grafo de Conhecimento
  kg_relacoes                â€” arestas do Grafo de Conhecimento
  kg_documentos              â€” vÃ­nculo documento â†” entidades do grafo
  motor_minutas              â€” minutas geradas (para feedback)

IntegraÃ§Ã£o com projeto existente:
  Se o seu projeto jÃ¡ tem tabelas prÃ³prias (ex: 'clientes', 'processos'),
  use source_id (Integer nullable) para fazer referÃªncia sem FK hard.
"""

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime,
    Float, JSON, ForeignKey, Boolean, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

from app.motor.config import DATABASE_URL

engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine       = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base         = declarative_base()


# â”€â”€ Documentos de treinamento â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class MotorDocumento(Base):
    """
    Documento real enviado para treinar o motor.
    Equivale a manifestacoes_originais no projeto original.
    """
    __tablename__ = "motor_documentos"

    id             = Column(Integer, primary_key=True, index=True)
    categoria      = Column(String(20), index=True)      # cÃ³digo de categoria (ex: FAM)
    nome_arquivo   = Column(String(255))
    tipo_arquivo   = Column(String(10))                  # pdf | docx | txt | image
    texto_extraido = Column(Text)                        # saÃ­da bruta do OCR/extraÃ§Ã£o
    texto_limpo    = Column(Text)                        # texto pÃ³s-processado
    hash_conteudo  = Column(String(64), unique=True)     # SHA-256 p/ evitar duplicatas
    status_analise = Column(String(20), default="pendente")  # pendente|processando|concluido|erro
    source_id      = Column(Integer, nullable=True)      # ID opcional no sistema externo
    criado_em      = Column(DateTime, default=datetime.utcnow)
    atualizado_em  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MotorPadrao(Base):
    """
    PadrÃµes extraÃ­dos e refinados de um documento pelo loop multi-pass.
    """
    __tablename__ = "motor_padroes"

    id            = Column(Integer, primary_key=True, index=True)
    documento_id  = Column(Integer, ForeignKey("motor_documentos.id"), index=True)
    categoria     = Column(String(20), index=True)
    iteracoes     = Column(Integer, default=1)
    score_final   = Column(Float, default=0.0)
    estrutura     = Column(JSON)   # seÃ§Ãµes, abertura, fechamento
    vocabulario   = Column(JSON)   # expressÃµes, conectivos, verbos
    argumentacao  = Column(JSON)   # lÃ³gica, estrutura de anÃ¡lise
    citacoes      = Column(JSON)   # leis, artigos, precedentes recorrentes
    estilo_formal = Column(JSON)   # pessoa, tempo verbal, formataÃ§Ã£o
    lacunas       = Column(JSON)   # o que o loop identificou como faltante
    rascunho      = Column(JSON)   # histÃ³rico das iteraÃ§Ãµes (auditoria)
    criado_em     = Column(DateTime, default=datetime.utcnow)


class MotorNivelConhecimento(Base):
    """
    Bateria de conhecimento agregada por categoria.
    Alimenta dashboard e geraÃ§Ã£o de minutas.
    """
    __tablename__ = "motor_nivel_conhecimento"

    id                  = Column(Integer, primary_key=True, index=True)
    categoria           = Column(String(20), unique=True, index=True)
    total_docs          = Column(Integer, default=0)
    docs_aprovados      = Column(Integer, default=0)    # score >= SCORE_MINIMO
    percentual          = Column(Float, default=0.0)    # 0-100
    padrao_medio        = Column(JSON)                  # merge de todos os padrÃµes aprovados
    cobertura_temas     = Column(JSON)                  # {tema: bool}
    lacunas_atuais      = Column(JSON)                  # o que ainda falta aprender
    proxima_necessidade = Column(Text)                  # sugestÃ£o para o usuÃ¡rio
    atualizado_em       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MotorGalho(Base):
    """
    Galho da Ã¡rvore de conhecimento: categoria Ã— tipo de pedido.
    Ex: FAM + "RegulamentaÃ§Ã£o de Visitas"
    Quando count_docs >= DOCS_PARA_CONSOLIDAR, gera template automaticamente.
    """
    __tablename__ = "motor_galhos"

    id              = Column(Integer, primary_key=True, index=True)
    categoria       = Column(String(20), nullable=False, index=True)
    tipo_pedido     = Column(String(200), nullable=False)
    tipo_pedido_slug = Column(String(100), nullable=False)
    count_docs      = Column(Integer, default=1)
    status          = Column(String(20), default="novo")  # novo | aprendendo | consolidado
    padroes         = Column(JSON)                         # padrÃµes mesclados
    argumentos_tipo = Column(JSON)                         # argumentos tÃ­picos
    dispositivos    = Column(JSON)                         # fÃ³rmulas/conclusÃµes aprendidas
    modelo_id       = Column(Integer, ForeignKey("motor_modelos.id"), nullable=True)
    modelo_gerado   = Column(Boolean, default=False)
    criado_em       = Column(DateTime, default=datetime.utcnow)
    atualizado_em   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("categoria", "tipo_pedido_slug", name="uq_galho_categoria_pedido"),
    )


class MotorModelo(Base):
    """
    Template gerado automaticamente pelo motor quando um galho consolida.
    Pode tambÃ©m ser importado manualmente (DOCX, TXT).
    """
    __tablename__ = "motor_modelos"

    id        = Column(Integer, primary_key=True, index=True)
    codigo    = Column(String(30), unique=True, index=True)  # ex: FAM-REGVIS-001
    titulo    = Column(String(255))
    categoria = Column(String(50), index=True)
    texto     = Column(Text)         # template com placeholders [DADO]
    gatilhos  = Column(Text)         # palavras-chave para sugestÃ£o automÃ¡tica
    origem    = Column(String(20), default="manual")  # manual | auto-gerado
    criado_em = Column(DateTime, default=datetime.utcnow)


class MotorAprendizadoEstrategico(Base):
    """
    InteligÃªncia extraÃ­da de documentos com resultado conhecido.
    Aprende quais argumentos foram aceitos/rejeitados e por quÃª.
    """
    __tablename__ = "motor_aprendizado_estrategico"

    id                    = Column(Integer, primary_key=True, index=True)
    categoria             = Column(String(20), nullable=False, index=True)
    tipo_pedido           = Column(String(200))
    tipo_pedido_slug      = Column(String(100))
    nome_arquivo          = Column(String(255))
    hash_conteudo         = Column(String(64), unique=True)

    argumentos_proponente = Column(JSON)   # argumentos de quem enviou
    argumentos_contrarios = Column(JSON)   # contra-argumentos

    resultado             = Column(String(20))   # favoravel | parcial | desfavoravel | indefinido
    resultado_descricao   = Column(Text)

    argumentos_acolhidos  = Column(JSON)   # o que foi aceito pela autoridade decisora
    argumentos_rejeitados = Column(JSON)   # o que foi rejeitado

    citacoes_decisao      = Column(JSON)   # fundamentos usados na decisÃ£o
    licao_estrategica     = Column(Text)   # liÃ§Ã£o sintetizada para casos futuros

    padroes_extras        = Column(JSON)   # padrÃµes de linguagem adicionais
    status_analise        = Column(String(20), default="concluido")
    source_id             = Column(Integer, nullable=True)  # referÃªncia ao sistema externo
    criado_em             = Column(DateTime, default=datetime.utcnow)
    atualizado_em         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MotorFila(Base):
    """Fila de trabalho para o worker de anÃ¡lise assÃ­ncrono."""
    __tablename__ = "motor_fila"

    id            = Column(Integer, primary_key=True, index=True)
    documento_id  = Column(Integer, ForeignKey("motor_documentos.id"), unique=True)
    tentativas    = Column(Integer, default=0)
    proximo_retry = Column(DateTime, nullable=True)
    status        = Column(String(20), default="aguardando")  # aguardando|processando|concluido|falha
    erro_msg      = Column(Text, nullable=True)
    criado_em     = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# â”€â”€ Grafo de Conhecimento â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class KgEntidade(Base):
    """
    NÃ³ do Grafo de Conhecimento.
    Tipos: argumento | citacao | expressao | estrutura | resultado
    Cada entidade acumula frequÃªncia e score de eficÃ¡cia ao longo do tempo.
    """
    __tablename__ = "kg_entidades"

    id            = Column(Integer, primary_key=True, index=True)
    tipo          = Column(String(30), nullable=False, index=True)
    valor         = Column(Text, nullable=False)
    slug          = Column(String(300), unique=True, nullable=False)  # tipo:normalized_value
    categoria     = Column(String(20), index=True)

    frequencia    = Column(Integer, default=1)
    docs_favoravel    = Column(Integer, default=0)
    docs_parcial      = Column(Integer, default=0)
    docs_desfavoravel = Column(Integer, default=0)
    score_eficacia    = Column(Float, default=0.0)   # 0-100

    ultimo_uso    = Column(DateTime, default=datetime.utcnow)
    criado_em     = Column(DateTime, default=datetime.utcnow)


class KgRelacao(Base):
    """
    Aresta do Grafo de Conhecimento.
    Tipos: usado_com | resultou_em | similar_a | citado_com
    """
    __tablename__ = "kg_relacoes"

    id           = Column(Integer, primary_key=True, index=True)
    origem_id    = Column(Integer, ForeignKey("kg_entidades.id"), nullable=False, index=True)
    destino_id   = Column(Integer, ForeignKey("kg_entidades.id"), nullable=False, index=True)
    tipo_relacao = Column(String(50), nullable=False)
    peso         = Column(Float, default=1.0)    # forÃ§a da relaÃ§Ã£o (mÃ©dia mÃ³vel)
    contagem     = Column(Integer, default=1)
    criado_em    = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("origem_id", "destino_id", "tipo_relacao", name="uq_kg_relacao"),
    )


class KgDocumento(Base):
    """VÃ­nculo entre documento e entidades do grafo."""
    __tablename__ = "kg_documentos"

    id             = Column(Integer, primary_key=True, index=True)
    documento_id   = Column(Integer, ForeignKey("motor_documentos.id"), nullable=True, index=True)
    estrategico_id = Column(Integer, ForeignKey("motor_aprendizado_estrategico.id"), nullable=True, index=True)
    categoria      = Column(String(20), index=True)
    tipo_pedido    = Column(String(200))
    resultado      = Column(String(20))    # favoravel|parcial|desfavoravel|indefinido
    entidades_ids  = Column(JSON)          # lista de KgEntidade.id presentes neste doc
    peso_temporal  = Column(Float, default=1.0)  # decai com o tempo
    data_doc       = Column(DateTime, default=datetime.utcnow)
    criado_em      = Column(DateTime, default=datetime.utcnow)


class MotorMinuta(Base):
    """
    Registro de cada documento/minuta gerado â€” para rastreabilidade e feedback.
    """
    __tablename__ = "motor_minutas"

    id               = Column(Integer, primary_key=True, index=True)
    categoria        = Column(String(50), index=True)
    modelo_label     = Column(String(255))
    texto_gerado     = Column(Text)
    entidades_usadas = Column(JSON)    # IDs do KG que influenciaram a geraÃ§Ã£o
    source_id        = Column(Integer, nullable=True)  # referÃªncia ao sistema externo

    # Feedback
    feedback         = Column(String(20), nullable=True)   # usou|descartou|editou
    feedback_nota    = Column(Integer, nullable=True)       # 1-5 estrelas
    feedback_obs     = Column(Text, nullable=True)
    feedback_em      = Column(DateTime, nullable=True)
    resultado_real   = Column(String(20), nullable=True)   # favoravel|parcial|desfavoravel

    criado_em        = Column(DateTime, default=datetime.utcnow)


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def get_db():
    """FastAPI dependency â€” use com Depends(get_db)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Cria todas as tabelas. Chame uma vez na inicializaÃ§Ã£o da aplicaÃ§Ã£o."""
    Base.metadata.create_all(bind=engine)
    print("[motor] Tabelas criadas/verificadas com sucesso.")

