# Motor de Aprendizado por Corpus

Motor de aprendizado contínuo que analisa documentos reais, identifica padrões, constrói um Grafo de Conhecimento e usa tudo isso para melhorar a geração de novos documentos.

Desenvolvido na Coruj IA (MP/MS) · Portável para qualquer domínio · 100% self-hosted · Sem mensalidade

---

## O que este motor faz

```
Documento enviado
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│  LOOP MULTI-PASS (cerebro_service.py)                   │
│                                                         │
│  Pass 1 — Claude Haiku ──► extração inicial de padrões  │
│  Pass 2 — Claude Sonnet ─► auto-crítica + score         │
│  Pass 3+ — Sonnet ───────► refinamento até score ≥ 8.5  │
└─────────────────────────────────────────────────────────┘
      │ padrões aceitos
      ▼
┌─────────────────────────────────────────────────────────┐
│  GRAFO DE CONHECIMENTO (kg_service.py)                  │
│                                                         │
│  • Extrai entidades: argumentos, citações, expressões   │
│  • Cria arestas de co-ocorrência entre entidades        │
│  • Calcula score de eficácia por resultado              │
│  • Peso temporal: docs recentes valem mais              │
│  • Meia-vida: 18 meses (ajustável em config.py)         │
└─────────────────────────────────────────────────────────┘
      │ inteligência acumulada
      ▼
┌─────────────────────────────────────────────────────────┐
│  GERAÇÃO ENRIQUECIDA                                    │
│                                                         │
│  • Argumentos ranqueados por taxa de sucesso histórico  │
│  • Citações mais frequentes e eficazes                  │
│  • Expressões características do estilo aprendido       │
│  • Templates gerados automaticamente (3+ docs/tema)     │
└─────────────────────────────────────────────────────────┘
      │ documento gerado
      ▼
┌─────────────────────────────────────────────────────────┐
│  FEEDBACK LOOP                                          │
│                                                         │
│  Usuário informa: usou / editou / descartou             │
│  + resultado real: favorável / parcial / desfavorável   │
│  → Motor atualiza scores do KG                          │
│  → Próximas gerações ficam melhores                     │
└─────────────────────────────────────────────────────────┘
```

---

## Estrutura do pacote

```
motor_aprendizado_export/
├── config.py              ← EDITE ESTE ARQUIVO para adaptar ao seu domínio
├── models.py              ← Tabelas SQLAlchemy (auto-criadas)
├── kg_service.py          ← Grafo de Conhecimento
├── cerebro_service.py     ← Motor de padrões multi-pass
├── router.py              ← FastAPI router (plug-and-play)
├── scheduler.py           ← Worker de fila (APScheduler)
├── requirements.txt       ← Dependências mínimas
├── docker-compose.exemplo.yml
└── exemplo/
    ├── integracao_fastapi.py  ← App FastAPI completo de exemplo
    └── uso_direto.py          ← Uso via Python puro (batch/scripts)
```

---

## Pré-requisitos

| Requisito | Versão mínima | Notas |
|---|---|---|
| Python | 3.11+ | |
| PostgreSQL | 14+ | Ou SQLite para testes locais |
| Anthropic API Key | — | [console.anthropic.com](https://console.anthropic.com) |
| pdfplumber | opcional | Apenas se for processar PDFs |
| python-docx | opcional | Apenas se for processar DOCX |

---

## Instalação em 5 passos

### 1. Copie o pacote para o seu projeto

```bash
# Copie a pasta inteira para o seu projeto
cp -r motor_aprendizado_export/ meu_projeto/motor/
cd meu_projeto/motor/
```

### 2. Instale as dependências

```bash
pip install -r requirements.txt
```

### 3. Configure as variáveis de ambiente

Crie um arquivo `.env` na raiz:

```env
# Obrigatório
DATABASE_URL=postgresql://usuario:senha@localhost:5432/meu_banco
ANTHROPIC_API_KEY=sk-ant-...

# Opcional — descrição do seu domínio (aparece nos prompts de IA)
CONTEXTO_DOMINIO=documentos jurídicos do Ministério Público
NOME_AUTOR=Promotor(a) de Justiça

# ChromaDB (deixe false se não tiver)
CHROMA_ATIVO=false
```

### 4. Adapte as categorias do domínio

Edite `config.py` — **este é o único arquivo que você precisa modificar**:

```python
# Exemplo para escritório trabalhista:
CATEGORIAS = {
    "REC": "Reclamação Trabalhista",
    "ACO": "Acordo / Homologação",
    "INS": "Insalubridade",
    "HOR": "Horas Extras",
    "DEM": "Demissão sem Justa Causa",
    "GEN": "Geral",
}
```

### 5. Integre ao seu FastAPI

```python
# main.py do seu projeto
from contextlib import asynccontextmanager
from fastapi import FastAPI

# Importe o motor
from motor.models import init_db
from motor.router import router as motor_router
from motor.scheduler import iniciar_scheduler, parar_scheduler

@asynccontextmanager
async def lifespan(app):
    init_db()            # cria as tabelas (idempotente)
    iniciar_scheduler()  # inicia o worker de fila
    yield
    parar_scheduler()

app = FastAPI(lifespan=lifespan)
app.include_router(motor_router)  # prefixo: /motor
```

**Pronto.** Todos os endpoints ficam disponíveis em `/motor/...`

---

## Endpoints da API

### Upload e aprendizado

```http
POST /motor/upload
Content-Type: multipart/form-data
  arquivo: (arquivo PDF, DOCX ou TXT)
  categoria: "FAM"   (ou "AUTO" para detecção automática)

→ Retorna: { "status": "enfileirado", "documento_id": 42, "categoria": "FAM" }
```

```http
POST /motor/upload-com-resultado
Content-Type: multipart/form-data
  arquivo: (documento que inclui resultado/sentença)
  categoria: "SAU"

→ Extrai inteligência estratégica imediatamente
→ Retorna: { "resultado": "favoravel", "licao": "..." }
```

### Consulta de inteligência

```http
GET /motor/status
→ Bateria de conhecimento de todas as categorias (0-100%)

GET /motor/arvore
→ Árvore de conhecimento: categorias → tipos de pedido → templates gerados

GET /motor/perfil/{categoria}
→ Padrões de estilo aprendidos: vocabulário, estrutura, citações típicas

GET /motor/estrategia/{categoria}
→ Inteligência estratégica: argumentos eficazes, argumentos a evitar, lições

GET /motor/grafo/status
→ Estatísticas do Grafo: entidades, relações, taxa de feedback

GET /motor/grafo/{categoria}
→ Top argumentos e citações ranqueados por taxa de sucesso
```

### Feedback e rastreabilidade

```http
POST /motor/feedback/{doc_id}
Content-Type: application/json
{
  "feedback":      "usou",        // "usou" | "editou" | "descartou"
  "nota":          5,             // 1-5 (opcional)
  "resultado_real": "favoravel",  // "favoravel" | "parcial" | "desfavoravel"
  "obs":           "texto livre"  // opcional
}
```

```http
GET /motor/minutas
→ Lista documentos gerados com métricas de uso e feedback

GET /motor/fila
→ Status da fila de processamento

POST /motor/processar-agora
→ Força processamento imediato (sem esperar o scheduler de 60s)

POST /motor/grafo/recalcular-pesos
→ Recalcula decaimento temporal (execute semanalmente via cron)
```

---

## Como usar a inteligência do motor na geração

Enriqueça o prompt enviado ao Claude com a inteligência acumulada:

```python
import kg_service
import cerebro_service

def gerar_documento(categoria: str, tipo_pedido: str, contexto: str) -> str:
    # 1. Busca inteligência do grafo
    intel_grafo = kg_service.consultar_grafo_para_geracao(categoria, tipo_pedido)
    intel_estrategica = cerebro_service.obter_inteligencia(categoria)
    perfil_estilo = cerebro_service.obter_perfil_categoria(categoria) or {}

    # 2. Monta contexto adicional
    adicional = []

    if intel_grafo.get("argumentos_eficazes"):
        args = [f"- {a['argumento']} (eficácia {a['score']:.0f}%)"
                for a in intel_grafo["argumentos_eficazes"][:5]]
        adicional.append("ARGUMENTOS HISTORICAMENTE EFICAZES:\n" + "\n".join(args))

    if intel_grafo.get("citacoes_frequentes"):
        cits = intel_grafo["citacoes_frequentes"][:5]
        adicional.append(f"CITAÇÕES MAIS USADAS: {', '.join(cits)}")

    if intel_estrategica.get("argumentos_evitar"):
        evitar = intel_estrategica["argumentos_evitar"][:3]
        adicional.append("EVITE ESTES ARGUMENTOS (historicamente rejeitados):\n" +
                         "\n".join(f"- {a}" for a in evitar))

    if intel_estrategica.get("licoes"):
        adicional.append(f"LIÇÃO ESTRATÉGICA: {intel_estrategica['licoes'][-1]}")

    taxa = intel_grafo.get("taxa_favoravel")
    if taxa is not None:
        adicional.append(f"[Base: {intel_grafo['total_docs_grafo']} docs, {taxa}% taxa favorável]")

    # Padrões de estilo
    padrao = perfil_estilo.get("padrao_medio", {})
    vocab = padrao.get("vocabulario", {})
    if vocab.get("expressoes_caracteristicas"):
        adicional.append(f"ESTILO APRENDIDO — expressões típicas: "
                         f"{', '.join(vocab['expressoes_caracteristicas'][:4])}")

    # 3. Chama seu gerador com o contexto enriquecido
    prompt = f"""[Seu prompt de geração aqui]

CONTEXTO DO PROCESSO:
{contexto}

INTELIGÊNCIA DO MOTOR DE APRENDIZADO:
{chr(10).join(adicional) if adicional else 'Ainda sem histórico para esta categoria.'}
"""
    # client.messages.create(...)
    return prompt
```

---

## Escala e capacidade

| Cenário | Comportamento |
|---|---|
| **0 docs** | Motor funciona sem inteligência; gera sem contexto histórico |
| **1-2 docs/categoria** | Galho em status "aprendendo"; sem template automático ainda |
| **3+ docs/categoria** | Galho consolidado; template gerado automaticamente |
| **2 anos de corpus** | Totalmente suportado; peso temporal garante que docs recentes dominam |
| **10.000+ docs** | Suportado; PostgreSQL + índices aguentam |
| **Múltiplos usuários** | Suportado; banco compartilhado, sem estado em memória |

### Custo de IA por documento

| Operação | Modelo | Custo estimado |
|---|---|---|
| Extração de padrões (Pass 1) | Claude Haiku | ~$0.0003 |
| Refinamento (Pass 2+) | Claude Sonnet | ~$0.003 |
| Extração KG | Claude Haiku | ~$0.0003 |
| **Total por documento** | | **~$0.004** |

Com 500 documentos/mês: ~$2/mês em IA.

---

## Banco de dados — tabelas criadas

```
motor_documentos              — documentos enviados
motor_padroes                 — padrões extraídos (histórico de iterações)
motor_nivel_conhecimento      — bateria por categoria (0-100%)
motor_galhos                  — árvore de conhecimento (categoria × pedido)
motor_modelos                 — templates gerados automaticamente
motor_aprendizado_estrategico — inteligência de docs com resultado
motor_fila                    — fila de processamento assíncrono
motor_minutas                 — documentos gerados + feedback
kg_entidades                  — nós do grafo (argumentos, citações, etc.)
kg_relacoes                   — arestas do grafo (co-ocorrência, resultado)
kg_documentos                 — vínculo doc ↔ entidades do grafo
```

Todas criadas automaticamente pelo `init_db()` — sem migrations necessárias.

---

## Parâmetros ajustáveis (config.py)

| Parâmetro | Padrão | O que faz |
|---|---|---|
| `SCORE_MINIMO` | 8.5 | Score para aceitar padrão extraído (0-10) |
| `MAX_ITERACOES` | 5 | Máximo de passes de refinamento |
| `MEIA_VIDA_DIAS` | 540 | Dias até um doc valer 50% (18 meses) |
| `DOCS_PARA_CONSOLIDAR` | 3 | Docs necessários para gerar template |
| `REGENERAR_MODELO_A_CADA` | 10 | Re-gera template a cada N docs novos |
| `MODELO_RAPIDO` | claude-haiku-4-5 | Modelo para extração inicial (barato) |
| `MODELO_QUALIDADE` | claude-sonnet-4-6 | Modelo para refinamento (melhor) |

---

## Deploy com Docker

```bash
# Copie o arquivo de exemplo
cp docker-compose.exemplo.yml docker-compose.yml

# Configure sua API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Suba
docker compose up -d
```

---

## SQLite para desenvolvimento local

Para testes sem PostgreSQL, mude em `config.py`:

```python
DATABASE_URL = "sqlite:///./motor_dev.db"
```

E em `models.py`:
```python
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
```

---

## Diferenças em relação ao projeto original (Coruj IA)

| Original | Este pacote |
|---|---|
| `ManifestacaoOriginal` | `MotorDocumento` |
| `PadraoManifestacao` | `MotorPadrao` |
| `NivelConhecimento` | `MotorNivelConhecimento` |
| `GalhoConhecimento` | `MotorGalho` |
| `AprendizadoEstrategico` | `MotorAprendizadoEstrategico` |
| `MinutaGerada` | `MotorMinuta` |
| `materia` | `categoria` |
| FK para `processos` | `source_id` (Integer livre) |
| Acoplado ao OCR/formatação do MP | Extrator plugável (substitua `_extrair_texto_arquivo`) |
| Categorias fixas (FAM, EXE...) | Categorias configuráveis em `config.py` |

---

## Suporte

Este motor é **código-fonte aberto**, self-hosted.
Dados ficam no **seu banco**, na **sua infraestrutura**.
Nenhum dado é enviado a terceiros além das chamadas à API do Anthropic.

Projeto original: Coruj IA — MPMS (Ministério Público de Mato Grosso do Sul)
