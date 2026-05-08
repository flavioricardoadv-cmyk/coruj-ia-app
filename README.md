# MP Assistente - MVP

MVP local para listar modelos de manifestações e recomendar o modelo mais adequado a partir do texto de um caso.

## Rodar localmente

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Abra:

```txt
http://127.0.0.1:8000
```

## Usar PostgreSQL

Se quiser usar o banco `mp_assistente` em vez do JSON local:

```powershell
$env:DATABASE_URL = "postgresql://postgres:SUA_SENHA@localhost:5432/mp_assistente"
uvicorn app.main:app --reload --port 8000
```

Sem `DATABASE_URL`, o app usa automaticamente:

```txt
modelo_db_export/modelos_manifestacoes.json
```

## Rotas

```txt
GET  /health
GET  /modelos
GET  /modelos/{codigo}
POST /recomendar
```

Exemplo de `POST /recomendar`:

```json
{
  "case_text": "Foi juntado laudo social em ação de guarda e alimentos. As partes ainda não foram intimadas para alegações finais.",
  "limit": 3
}
```
