"""
exemplo/uso_direto.py
======================
Uso do motor diretamente via Python, sem FastAPI.
Útil para scripts de importação em batch, testes ou pipelines.

Pré-requisito: banco PostgreSQL configurado em config.py (ou DATABASE_URL no .env)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import init_db, SessionLocal
import cerebro_service
import kg_service


def exemplo_importar_batch():
    """
    Importa um lote de arquivos de texto e processa com o motor.
    """
    print("=== Importação em Batch ===\n")

    # Inicializa o banco
    init_db()

    # Simula textos de documentos
    documentos = [
        {
            "texto": """MM. Juíza,

Trata-se de ação de regulamentação de visitas ajuizada por [REQUERENTE] em face de [REQUERIDO].
Às fls. 15-20, o requerente pugnou pela regulamentação de visitas ao filho menor.
Às fls. 35, foi proferida decisão deferindo as visitas quinzenais.
É o relato.

Conforme relatado, o Ministério Público manifesta-se favoravelmente ao pedido de regulamentação
de visitas, nos termos do art. 1.589 do Código Civil e da Súmula 309 do STJ.

Da Proteção Integral da Criança

O princípio do melhor interesse da criança, consagrado no art. 227 da CF/88 e no art. 4º do ECA,
impõe que a convivência com ambos os genitores seja preservada sempre que possível.

Diante do exposto, o Ministério Público manifesta-se favoravelmente ao deferimento das visitas.

Campo Grande/MS, 15 de março de 2024.
Promotor de Justiça — 6ª Promotoria""",
            "categoria": "FAM",
        },
        {
            "texto": """MM. Juíza,

Trata-se de ação de execução de alimentos ajuizada por [EXEQUENTE] em face de [EXECUTADO].
O débito alimentar totaliza R$ 8.500,00 (oito mil e quinhentos reais).
Às fls. 42, intimado pessoalmente, o executado quedou-se inerte.
É o relato.

Conforme relatado, caracterizada a mora alimentar, impõe-se a decretação da prisão civil,
nos termos do art. 528, §3º do CPC e da Súmula 309 do STJ.

Da Mora Alimentar Comprovada

O débito restou devidamente comprovado através dos documentos acostados às fls. 10-25.

Diante do exposto, o Ministério Público requer a decretação da prisão civil do executado.

Campo Grande/MS, 20 de março de 2024.
Promotor de Justiça — 6ª Promotoria""",
            "categoria": "EXE",
        },
    ]

    db = SessionLocal()
    try:
        from models import MotorDocumento, MotorFila
        import hashlib

        for i, doc in enumerate(documentos, 1):
            print(f"[{i}/{len(documentos)}] Importando documento de {doc['categoria']}...")

            hash_doc = hashlib.sha256(doc["texto"].encode()).hexdigest()

            # Verifica duplicata
            existente = db.query(MotorDocumento).filter_by(hash_conteudo=hash_doc).first()
            if existente:
                print(f"  → Duplicata, ignorado (id={existente.id})")
                continue

            motor_doc = MotorDocumento(
                categoria      = doc["categoria"],
                nome_arquivo   = f"doc_exemplo_{i}.txt",
                tipo_arquivo   = "txt",
                texto_extraido = doc["texto"],
                texto_limpo    = doc["texto"],
                hash_conteudo  = hash_doc,
                status_analise = "pendente",
            )
            db.add(motor_doc)
            db.flush()
            db.add(MotorFila(documento_id=motor_doc.id, status="aguardando"))
            print(f"  → Enfileirado (id={motor_doc.id})")

        db.commit()
        print("\n✓ Importação concluída")

        # Processa imediatamente
        print("\n=== Processando... ===")
        from models import MotorFila as Fila
        pendentes = db.query(Fila).filter_by(status="aguardando").all()

        for item in pendentes:
            print(f"\nProcessando documento {item.documento_id}...")
            resultado = cerebro_service.processar_documento(item.documento_id)
            if resultado.get("ok"):
                print(f"  ✓ Score: {resultado['score']:.1f} | Iterações: {resultado['iteracoes']}")
                item.status = "concluido"
            else:
                print(f"  ✗ Erro: {resultado.get('erro')}")
                item.status = "falha"
                item.erro_msg = resultado.get("erro", "")
            db.commit()

    finally:
        db.close()


def exemplo_consultar_grafo():
    """
    Consulta o grafo de conhecimento acumulado.
    """
    print("\n=== Consulta ao Grafo de Conhecimento ===\n")

    # Status geral
    status = kg_service.status_grafo()
    print(f"Total de entidades: {status['total_entidades']}")
    print(f"Total de relações: {status['total_relacoes']}")
    print(f"Documentos no grafo: {status['total_documentos_grafo']}")

    if status["top_argumentos"]:
        print("\nTop argumentos por eficácia:")
        for a in status["top_argumentos"]:
            print(f"  [{a['score']:.0f}%] {a['argumento'][:60]} (freq={a['freq']})")

    # Consulta específica por categoria
    print("\n--- Inteligência para FAM ---")
    intel = kg_service.consultar_grafo_para_geracao("FAM")
    if intel["argumentos_eficazes"]:
        print("Argumentos eficazes:")
        for a in intel["argumentos_eficazes"][:3]:
            print(f"  [{a['score']:.0f}%] {a['argumento']}")
    if intel["citacoes_frequentes"]:
        print(f"Citações frequentes: {', '.join(intel['citacoes_frequentes'][:3])}")


def exemplo_feedback():
    """
    Simula o ciclo de feedback: gera documento → recebe resultado → atualiza KG.
    """
    print("\n=== Ciclo de Feedback ===\n")

    # Registra um documento gerado (normalmente feito pela API)
    minuta_id = kg_service.registrar_documento_gerado(
        categoria="FAM",
        modelo_label="Regulamentação de Visitas",
        texto_gerado="[texto da minuta gerada...]",
        source_id=None,
    )
    print(f"Minuta registrada com id={minuta_id}")

    # Simula o usuário dizendo que usou e o resultado foi favorável
    resultado = kg_service.processar_feedback(
        minuta_id=minuta_id,
        feedback="usou",
        nota=5,
        resultado_real="favoravel",
        obs="Juíza deferiu integralmente",
    )
    print(f"Feedback processado: {resultado}")


def exemplo_analise_estrategica():
    """
    Analisa um documento com resultado e aprende com ele.
    """
    print("\n=== Análise Estratégica ===\n")

    # Documento que inclui tanto a peça do proponente quanto a decisão
    texto_com_resultado = """
    [Manifestação do MP pedindo fornecimento de medicamento...]
    O Ministério Público argumentou: necessidade comprovada por laudo médico, art. 196 CF/88.

    [Decisão do Juiz]
    DECIDO: Defiro o pedido liminar. O art. 196 da Constituição Federal assegura o direito à saúde.
    O laudo médico é suficiente para demonstrar a necessidade do medicamento.
    RESULTADO: FAVORÁVEL ao MP.
    """

    db = SessionLocal()
    try:
        estrategia = cerebro_service.analisar_com_resultado(texto_com_resultado, "SAU")
        print(f"Pedido identificado: {estrategia.get('pedido_principal')}")
        print(f"Resultado: {estrategia.get('resultado')}")
        print(f"Argumentos acolhidos: {estrategia.get('argumentos_acolhidos', [])}")
        print(f"Lição: {estrategia.get('licao_estrategica', '')[:100]}")

        import hashlib
        hash_doc = hashlib.sha256(texto_com_resultado.encode()).hexdigest()
        ae = cerebro_service.salvar_aprendizado_estrategico(
            db, "SAU", "exemplo_estrategico.txt", hash_doc, estrategia
        )
        db.commit()
        print(f"\n✓ Aprendizado estratégico salvo (id={ae.id})")

        # Agora consulta a inteligência acumulada
        intel = cerebro_service.obter_inteligencia("SAU")
        if intel:
            print(f"\nInteligência SAU: taxa favorável = {intel.get('taxa_favoravel')}%")
    finally:
        db.close()


if __name__ == "__main__":
    print("Motor de Aprendizado — Demonstração\n" + "=" * 40)

    # Configura banco
    from dotenv import load_dotenv
    load_dotenv()

    init_db()

    exemplo_importar_batch()
    exemplo_consultar_grafo()
    exemplo_feedback()
    # exemplo_analise_estrategica()  # descomente para testar (consome API)
