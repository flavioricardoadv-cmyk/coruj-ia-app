"""
exemplo/integracao_fastapi.py
==============================
Exemplo completo de como integrar o Motor de Aprendizado em um projeto FastAPI existente.

Execute com:
    cd motor_aprendizado_export
    uvicorn exemplo.integracao_fastapi:app --reload --port 8000
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 1. Importe os módulos do motor
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import init_db
from router import router as motor_router
from scheduler import iniciar_scheduler, parar_scheduler


# 2. Lifespan: inicializa banco e scheduler
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()            # cria as tabelas do motor (idempotente)
    iniciar_scheduler()  # processa fila em background
    yield
    parar_scheduler()


# 3. App FastAPI
app = FastAPI(
    title="Meu Sistema com Motor de Aprendizado",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 4. Inclui o router do motor (prefixo /motor)
app.include_router(motor_router)

# 5. Seus próprios routes continuam funcionando normalmente
@app.get("/")
def root():
    return {"mensagem": "Motor de Aprendizado integrado com sucesso!"}


# ── Exemplo de uso do motor no seu próprio endpoint de geração ────────────────

@app.post("/meu-endpoint/gerar")
def gerar_documento(body: dict):
    """
    Exemplo: como usar o grafo + perfil aprendido para enriquecer sua geração.
    """
    categoria    = body.get("categoria", "GEN")
    tipo_pedido  = body.get("tipo_pedido", "")
    texto_base   = body.get("texto", "")

    # Consulta o grafo para obter argumentos eficazes
    import kg_service
    intel_grafo = kg_service.consultar_grafo_para_geracao(categoria, tipo_pedido)

    # Consulta padrões de estilo aprendidos
    import cerebro_service
    perfil = cerebro_service.obter_perfil_categoria(categoria) or {}
    intel_estrategica = cerebro_service.obter_inteligencia(categoria)

    # Monta contexto adicional para o seu prompt
    contexto_motor = ""

    if intel_grafo.get("argumentos_eficazes"):
        args = [a["argumento"] for a in intel_grafo["argumentos_eficazes"][:5]]
        contexto_motor += f"\n\nARGUMENTOS MAIS EFICAZES (score histórico):\n"
        contexto_motor += "\n".join(f"- {a}" for a in args)

    if intel_grafo.get("citacoes_frequentes"):
        contexto_motor += f"\n\nCITAÇÕES FREQUENTES:\n"
        contexto_motor += "\n".join(f"- {c}" for c in intel_grafo["citacoes_frequentes"][:5])

    if intel_estrategica.get("argumentos_evitar"):
        contexto_motor += f"\n\nARGUMENTOS A EVITAR (historicamente rejeitados):\n"
        contexto_motor += "\n".join(f"- {a}" for a in intel_estrategica["argumentos_evitar"][:3])

    if intel_estrategica.get("licoes"):
        contexto_motor += f"\n\nLIÇÃO ESTRATÉGICA:\n{intel_estrategica['licoes'][-1]}"

    taxa = intel_grafo.get("taxa_favoravel")
    if taxa is not None:
        contexto_motor += f"\n\n[Base histórica: {intel_grafo['total_docs_grafo']} docs, {taxa}% taxa favorável]"

    # Use contexto_motor no seu próprio prompt de geração com Claude
    # (este exemplo não chama Claude diretamente — integre com seu código)

    return {
        "categoria":      categoria,
        "contexto_motor": contexto_motor,
        "intel_grafo":    intel_grafo,
        "perfil_estilo":  perfil.get("padrao_medio", {}),
        "intel_estrategica": intel_estrategica,
        "mensagem":       "Use 'contexto_motor' como parte do seu prompt de geração.",
    }


# ── Exemplo de ciclo de feedback ─────────────────────────────────────────────

@app.post("/meu-endpoint/registrar-resultado/{minuta_id}")
def registrar_resultado(minuta_id: int, body: dict):
    """
    Registra o resultado real de um documento gerado.
    Fecha o ciclo de aprendizado contínuo.
    """
    import kg_service
    return kg_service.processar_feedback(
        minuta_id=minuta_id,
        feedback=body.get("feedback", "usou"),
        nota=body.get("nota"),
        resultado_real=body.get("resultado"),  # "favoravel" | "parcial" | "desfavoravel"
        obs=body.get("obs"),
    )
