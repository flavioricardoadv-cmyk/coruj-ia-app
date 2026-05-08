"""
scheduler.py â€” Worker de processamento assÃ­ncrono
==================================================
Usa APScheduler para processar a fila de documentos em background.

IntegraÃ§Ã£o com FastAPI:
    from app.motor.scheduler import iniciar_scheduler, parar_scheduler

    @asynccontextmanager
    async def lifespan(app):
        iniciar_scheduler()
        yield
        parar_scheduler()

    app = FastAPI(lifespan=lifespan)

Tarefas agendadas:
  A cada 60s  â†’ processa atÃ© 3 documentos da fila (sem bloquear requests)
  Toda domingo â†’ recalcula pesos temporais do Grafo de Conhecimento
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler

log = _scheduler = None


def _processar_fila():
    """Processa atÃ© 3 documentos pendentes da fila."""
    from app.motor.models import SessionLocal, MotorFila
    from app.motor import cerebro_service

    db = SessionLocal()
    try:
        pendentes = (
            db.query(MotorFila)
            .filter(MotorFila.status.in_(["aguardando", "falha"]))
            .filter(MotorFila.tentativas < 3)
            .limit(3).all()
        )
        for item in pendentes:
            item.status     = "processando"
            item.tentativas += 1
            db.commit()
            resultado = cerebro_service.processar_documento(item.documento_id)
            item.status   = "concluido" if resultado.get("ok") else "falha"
            item.erro_msg = resultado.get("erro", "")
            db.commit()
            if resultado.get("ok"):
                logging.getLogger("motor").info(
                    f"[scheduler] doc {item.documento_id} processado â€” "
                    f"score={resultado.get('score'):.1f}"
                )
            else:
                logging.getLogger("motor").warning(
                    f"[scheduler] doc {item.documento_id} falhou â€” {resultado.get('erro')}"
                )
    finally:
        db.close()


def _recalcular_pesos():
    """Recalcula pesos temporais do grafo (decay exponencial)."""
    from app.motor import kg_service
    r = kg_service.recalcular_pesos_temporais()
    logging.getLogger("motor").info(f"[scheduler] pesos recalculados â€” {r.get('recalculados')} docs")


def iniciar_scheduler():
    """Inicia o scheduler em background. Chame no lifespan do FastAPI."""
    global _scheduler
    logging.basicConfig(level=logging.INFO)
    _scheduler = BackgroundScheduler(timezone="America/Campo_Grande")
    _scheduler.add_job(_processar_fila,    "interval", seconds=60,  id="fila")
    _scheduler.add_job(_recalcular_pesos,  "cron",     day_of_week="sun",
                       hour=3, minute=0, id="pesos")
    _scheduler.start()
    logging.getLogger("motor").info("[scheduler] iniciado")


def parar_scheduler():
    """Para o scheduler. Chame no shutdown do FastAPI."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logging.getLogger("motor").info("[scheduler] parado")

