"""
config.py — Configurações do Motor de Aprendizado
=================================================
ÚNICO ARQUIVO QUE VOCÊ PRECISA EDITAR para adaptar a outro domínio.

Exemplos de domínios:
  - Ministério Público (original)
  - Advogados trabalhistas
  - Escritório previdenciário
  - Consultoria tributária
"""

import os
import math

# ── Banco de Dados ────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./modelo_db_export/motor_aprendizado.db"
)

# ── API de IA ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Modelos Claude usados
MODELO_RAPIDO   = "claude-haiku-4-5-20251001"   # extração inicial (barato ~$0.0003/doc)
MODELO_QUALIDADE = "claude-sonnet-4-6"           # refinamento e geração

# ── Motor de Aprendizado ──────────────────────────────────────────────────────

# Loop de análise: score mínimo para aceitar padrão extraído (0-10)
SCORE_MINIMO  = 8.5
# Máximo de iterações de refinamento por documento
MAX_ITERACOES = 5

# Peso temporal: meia-vida em dias (quantos dias até um doc valer 50%)
# 540 dias ≈ 18 meses (padrão para documentos jurídicos)
MEIA_VIDA_DIAS = 540
LAMBDA_DECAIMENTO = math.log(2) / MEIA_VIDA_DIAS

# Quantos documentos de um galho para gerar modelo automaticamente
DOCS_PARA_CONSOLIDAR = 3
# A cada quantos docs novos regenerar o modelo (mantém preciso)
REGENERAR_MODELO_A_CADA = 10

# ── Categorias do Domínio ─────────────────────────────────────────────────────
# Adapte para o seu domínio.
# Formato: { "CODIGO": "Nome legível" }
#
# Exemplo jurídico (MP/MS):
CATEGORIAS = {
    "FAM": "Família",
    "EXE": "Execução de Alimentos",
    "CUR": "Curatela/Interdição",
    "MS":  "Mandado de Segurança",
    "PC":  "Procedimento Comum",
    "SAU": "Saúde",
    "PAT": "Patrimônio/Inventário",
    "REC": "Recurso",
    "GEN": "Geral",
}

# Exemplo para escritório trabalhista (descomente e substitua):
# CATEGORIAS = {
#     "REC": "Reclamação Trabalhista",
#     "ACO": "Acordo / Homologação",
#     "INS": "Insalubridade / Periculosidade",
#     "HOR": "Horas Extras",
#     "DEM": "Demissão sem Justa Causa",
#     "FGT": "FGTS",
#     "MOB": "Assédio / Dano Moral",
#     "GEN": "Geral",
# }

# Conjunto de códigos válidos (derivado de CATEGORIAS)
CATEGORIAS_VALIDAS = set(CATEGORIAS.keys())

# ── ChromaDB (opcional) ────────────────────────────────────────────────────────
# Se não tiver ChromaDB, deixe CHROMA_ATIVO = False.
# O motor funciona 100% sem ChromaDB — ele só é usado para busca semântica.
CHROMA_ATIVO = os.getenv("CHROMA_ATIVO", "false").lower() == "true"
CHROMA_HOST  = os.getenv("CHROMA_HOST", "chroma")
CHROMA_PORT  = int(os.getenv("CHROMA_PORT", "8000"))

# ── Contexto do Domínio (para os prompts de IA) ───────────────────────────────
# Descreve em 1-2 linhas quem você é e o tipo de documento que será aprendido.
# Aparece nos prompts enviados ao Claude.
CONTEXTO_DOMINIO = os.getenv(
    "CONTEXTO_DOMINIO",
    "manifestações jurídicas do Ministério Público"
)

# Autor/emissor dos documentos (usado nos prompts)
NOME_AUTOR = os.getenv("NOME_AUTOR", "Promotor(a) de Justiça")

# ── Tipos de entidade extraídas pelo KG ───────────────────────────────────────
# Adapte os nomes se seu domínio usar termos diferentes
TIPOS_ENTIDADE = ("argumento", "citacao", "expressao", "estrutura")
