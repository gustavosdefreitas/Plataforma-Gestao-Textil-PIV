# Plataforma de Gestão Estratégica Têxtil — Estoque Fácil MEI

> Projeto Integrador IV — UNIVESP (PJI410)
> Análise de dados em escala · Machine Learning · IoT · Visualização · Nuvem

Sistema web full-stack para controle de estoque e vendas com camada de inteligência artificial e integração IoT. Evolução do PI III (PJI310), acrescentando previsão de demanda por regressão linear e integração com sensores físicos de estoque.

---

## Funcionalidades PI IV (Novas)

- **Previsão de demanda (ML)** — modelo de regressão linear treinado sobre histórico de vendas mensais, com previsão para os próximos 3 meses e indicador R²
- **Integração IoT** — endpoints REST para receber leituras de sensores físicos (balanças, RFID) e atualizar estoque em tempo real
- **Dashboard ML** — gráfico de linha com histórico real vs. previsão do modelo
- **Pipeline de dados** — exportação CSV do histórico de vendas para uso em análises externas

## Funcionalidades PI III (Base)

- Dashboard analítico com 5 gráficos (Chart.js)
- CRUD completo de produtos, fornecedores, empresas e usuários
- Controle de vendas com numeração sequencial e comprovante PDF
- Banco de horas por usuário
- Acessibilidade WCAG 2.1 AA
- API REST para integração externa

---

## Requisitos Atendidos — Checklist PJI410

| Requisito | Tecnologia / Implementação |
|---|---|
| Resolução de problemas | Previsão de demanda para evitar ruptura de estoque |
| Visualização de dados | Chart.js — 6 gráficos incluindo previsão ML |
| Nuvem | Render.com (deploy contínuo via GitHub) |
| Projeto IoT | Endpoints `POST/GET /api/sensor/estoque` |
| Aprendizagem de máquina | Regressão linear com scikit-learn, indicador R² |

---

## Stack Tecnológica

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.13 |
| Framework web | FastAPI + Uvicorn |
| Templates | Jinja2 |
| Banco de dados | PostgreSQL |
| ORM | SQLAlchemy 2.x |
| Frontend | Bootstrap 5 + Chart.js |
| Machine Learning | scikit-learn + pandas + numpy |
| Geração de PDF | ReportLab |
| Testes | Pytest + pytest-cov |
| Lint | Ruff |
| CI | GitHub Actions |
| Deploy | Render.com |

---

## Endpoints IoT

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/sensor/estoque` | Recebe leitura de sensor e atualiza estoque |
| `GET` | `/api/sensor/estoque/{produto_id}` | Consulta estoque atual para o sensor |

### Exemplo de uso (sensor enviando leitura):
```bash
curl -X POST https://seu-app.onrender.com/api/sensor/estoque \
  -H "Content-Type: application/json" \
  -d '{"produto_id": 1, "quantidade": 42.5, "sensor_id": "BALANCA-01"}'
```

---

## Como Executar Localmente

```bash
git clone https://github.com/gustavosdefreitas/Plataforma-Gestao-Textil-PIV.git
cd Plataforma-Gestao-Textil-PIV
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="postgresql://usuario:senha@localhost:5432/nome_do_banco"
uvicorn main:app --reload
```

## Como Executar os Testes

```bash
pip install -r requirements-dev.txt
export DATABASE_URL="postgresql://usuario:senha@localhost:5432/test_db"
pytest test_main.py -v --cov=main --cov-report=term-missing
```

---

## Integração Contínua

[![CI](https://github.com/gustavosdefreitas/Plataforma-Gestao-Textil-PIV/actions/workflows/python-app.yaml/badge.svg)](https://github.com/gustavosdefreitas/Plataforma-Gestao-Textil-PIV/actions/workflows/python-app.yaml)

---

## Projetos Relacionados

- [PI III — PJI310](https://github.com/gustavosdefreitas/Plataforma-de-Gestao-Estrategica-Textil) — Base do sistema (framework web, banco de dados, CI, testes, acessibilidade)

---

Desenvolvido por estudantes da UNIVESP como requisito do Projeto Integrador IV (PJI410).
