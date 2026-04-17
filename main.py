from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from io import BytesIO, StringIO
import csv
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from datetime import datetime
from datetime import date
from decimal import Decimal
from math import ceil
import os
import hashlib
import uuid
import uvicorn
import numpy as np
import pandas as pd  # noqa: F401  (usado para análise/preparação de dados em ML)
from sklearn.linear_model import LinearRegression
from dateutil.relativedelta import relativedelta
from pydantic import BaseModel


# Usa DATABASE_URL do ambiente (produção) ou fallback local para desenvolvimento
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test_db")
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

def hash_password(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

def formatar_horas_minutos(valor):
    total_minutos = round(float(valor) * 60)
    horas = total_minutos // 60
    minutos = total_minutos % 60
    return f"{horas}h e {minutos} min"

def get_current_user(request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None

    with engine.connect() as conn:
        user = conn.execute(
            text("SELECT * FROM usuarios WHERE session_id = :sid"),
            {"sid": session_id}
        ).fetchone()

    return user

def registrar_log(usuario_id, username, acao, detalhes=None):
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO logs_sistema (usuario_id, username, acao, detalhes)
            VALUES (:usuario_id, :username, :acao, :detalhes)
        """), {
            "usuario_id": usuario_id,
            "username": username,
            "acao": acao,
            "detalhes": detalhes
        })
        conn.commit()

# --- STARTUP ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS logs_sistema (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER,
                username VARCHAR(50),
                acao VARCHAR(100) NOT NULL,
                detalhes TEXT,
                data_evento TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password VARCHAR(64) NOT NULL,
                perfil VARCHAR(20) DEFAULT 'user',
                session_id VARCHAR(36)
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS empresas (
                id SERIAL PRIMARY KEY,
                cnpj VARCHAR(20) NOT NULL,
                razao_social VARCHAR(100) NOT NULL,
                nome_fantasia VARCHAR(100),
                email VARCHAR(100),
                telefone VARCHAR(20),
                ativo INTEGER DEFAULT 1,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fornecedores (
                id SERIAL PRIMARY KEY,
                nome VARCHAR(100) NOT NULL,
                cnpj VARCHAR(20),
                telefone VARCHAR(20),
                email VARCHAR(100)
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS produtos (
                id SERIAL PRIMARY KEY,
                nome VARCHAR(100),
                quantidade NUMERIC(10,2) DEFAULT 0,
                preco NUMERIC(10,2),
                empresa_id INTEGER,
                fornecedor_id INTEGER,
                cor VARCHAR(50),
                tamanho VARCHAR(50)
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS vendas (
                id SERIAL PRIMARY KEY,
                produto_id INTEGER REFERENCES produtos(id) ON DELETE CASCADE,
                empresa_id INTEGER REFERENCES empresas(id),
                tipo_documento VARCHAR(20) NOT NULL DEFAULT 'comprovante',
                cliente_nome VARCHAR(100) NOT NULL,
                cliente_cpf_cnpj VARCHAR(20),
                cliente_email VARCHAR(100),
                cliente_telefone VARCHAR(20),
                status_documento VARCHAR(30) DEFAULT 'gerado',
                quantidade NUMERIC(10,2) NOT NULL,
                preco_unitario NUMERIC(10,2) NOT NULL,
                total NUMERIC(10,2) NOT NULL,
                data_venda TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        conn.execute(text("""
            ALTER TABLE vendas
            ADD COLUMN IF NOT EXISTS grupo_venda VARCHAR(36)
        """))

        conn.execute(text("""
            ALTER TABLE produtos
            ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """))

        conn.execute(text("""
            ALTER TABLE fornecedores
            ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """))

        conn.execute(text("CREATE SEQUENCE IF NOT EXISTS seq_numero_venda START 1"))

        conn.execute(text("""
            ALTER TABLE vendas
            ADD COLUMN IF NOT EXISTS numero_venda INTEGER
        """))

        conn.execute(text("""
            INSERT INTO usuarios (username, password, perfil)
            VALUES ('admin', :senha, 'admin')
            ON CONFLICT (username) DO NOTHING
        """), {"senha": hash_password("123456")})

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_logs_data_evento ON logs_sistema (data_evento DESC)"))

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_logs_acao ON logs_sistema (acao)"))

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_logs_username ON logs_sistema (username)"))

        conn.commit()
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --- PI IV: Modelos Pydantic para IoT ---
class SensorEstoquePayload(BaseModel):
    produto_id: int
    quantidade: float
    sensor_id: str
    timestamp: str | None = None


# --- PI IV: Machine Learning — previsão de demanda ---
def prever_demanda(conn, empresa_id: int | None = None) -> dict:
    """
    Treina modelo de regressão linear com histórico de vendas mensais
    e prevê a demanda para os próximos 3 meses.
    Retorna dict com histórico e previsões para uso no Chart.js.
    """
    try:
        filtro_empresa = "AND p.empresa_id = :empresa_id" if empresa_id else ""
        params = {"empresa_id": empresa_id} if empresa_id else {}

        historico = conn.execute(text(f"""
            SELECT
                DATE_TRUNC('month', v.data_venda) AS mes,
                TO_CHAR(DATE_TRUNC('month', v.data_venda), 'MM/YYYY') AS mes_label,
                COALESCE(SUM(v.quantidade), 0) AS total_vendido
            FROM vendas v
            JOIN produtos p ON v.produto_id = p.id
            WHERE v.data_venda >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '11 months'
            {filtro_empresa}
            GROUP BY DATE_TRUNC('month', v.data_venda)
            ORDER BY mes
        """), params).fetchall()

        if len(historico) < 2:
            return {
                "labels_hist": [],
                "labels_futuros": [],
                "historico": [],
                "previsao": [],
                "r2": None,
            }

        labels_hist = [r.mes_label for r in historico]
        valores_hist = [float(r.total_vendido) for r in historico]

        X = np.array(range(len(valores_hist))).reshape(-1, 1)
        y = np.array(valores_hist)
        modelo = LinearRegression()
        modelo.fit(X, y)
        r2 = round(float(modelo.score(X, y)), 3)

        n = len(valores_hist)
        X_futuro = np.array(range(n, n + 3)).reshape(-1, 1)
        previsoes = [max(0, round(float(v), 1)) for v in modelo.predict(X_futuro)]

        ultima_data = historico[-1].mes
        labels_futuros = [
            (ultima_data + relativedelta(months=i + 1)).strftime("%m/%Y")
            for i in range(3)
        ]

        coef = float(modelo.coef_[0])
        return {
            "labels_hist": labels_hist,
            "labels_futuros": labels_futuros,
            "historico": valores_hist,
            "previsao": previsoes,
            "r2": r2,
            "coeficiente": round(coef, 2),
            "tendencia": (
                "crescente" if coef > 0 else "decrescente" if coef < 0 else "estável"
            ),
        }
    except Exception:
        return {
            "labels_hist": [],
            "labels_futuros": [],
            "historico": [],
            "previsao": [],
            "r2": None,
        }


# --- PI IV: IoT — sensor de estoque ---
@app.post("/api/sensor/estoque")
async def sensor_estoque(payload: SensorEstoquePayload, request: Request):
    """
    Endpoint IoT: recebe leitura de sensor de estoque e atualiza quantidade do produto.
    Simula um sensor físico (balança/RFID) enviando dados em tempo real.
    """
    with engine.connect() as conn:
        produto = conn.execute(
            text("SELECT id, nome, quantidade FROM produtos WHERE id = :id"),
            {"id": payload.produto_id},
        ).fetchone()

        if not produto:
            return JSONResponse(
                status_code=404,
                content={"erro": "Produto não encontrado", "produto_id": payload.produto_id},
            )

        conn.execute(
            text("UPDATE produtos SET quantidade = :qtd WHERE id = :id"),
            {"qtd": payload.quantidade, "id": payload.produto_id},
        )

        conn.execute(
            text(
                """
                INSERT INTO logs_sistema (username, acao, descricao, data_evento)
                VALUES ('sistema', 'SENSOR_IOT', :descricao, CURRENT_TIMESTAMP)
                """
            ),
            {
                "descricao": (
                    f"Sensor {payload.sensor_id} | Produto ID {payload.produto_id} "
                    f"({produto.nome}) | Qtd anterior: {produto.quantidade} | "
                    f"Nova: {payload.quantidade}"
                )
            },
        )
        conn.commit()

    return {
        "status": "ok",
        "produto_id": payload.produto_id,
        "produto_nome": produto.nome,
        "quantidade_anterior": float(produto.quantidade),
        "quantidade_nova": payload.quantidade,
        "sensor_id": payload.sensor_id,
    }


@app.get("/api/sensor/estoque/{produto_id}")
async def sensor_estoque_status(produto_id: int, request: Request):
    """Retorna o status atual de estoque de um produto para o sensor consultar."""
    with engine.connect() as conn:
        produto = conn.execute(
            text("SELECT id, nome, quantidade FROM produtos WHERE id = :id"),
            {"id": produto_id},
        ).fetchone()

    if not produto:
        return JSONResponse(status_code=404, content={"erro": "Produto não encontrado"})

    return {
        "produto_id": produto.id,
        "nome": produto.nome,
        "quantidade_atual": float(produto.quantidade),
        "alerta_estoque_baixo": float(produto.quantidade) <= 5,
    }


# --- ROTAS DE AUTENTICAÇÃO ---
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with engine.connect() as conn:
        user = conn.execute(
            text("SELECT * FROM usuarios WHERE username = :username"),
            {"username": username}
        ).fetchone()

    if user and user.password == hash_password(password):
        session_id = str(uuid.uuid4())

        with engine.connect() as conn:
            conn.execute(
                text("UPDATE usuarios SET session_id = :sid WHERE id = :id"),
                {"sid": session_id, "id": user.id}
            )
            conn.commit()

        registrar_log(user.id, user.username, "LOGIN", "Usuário entrou no sistema")

        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="session_id", value=session_id, httponly=True)
        return response

    return templates.TemplateResponse(request, "login.html", {
        "error": "Credenciais Inválidas"
    })

@app.get("/logout")
async def logout(request: Request):
    user = get_current_user(request)

    if user:
        registrar_log(user.id, user.username, "LOGOUT", "Usuário saiu do sistema")

        with engine.connect() as conn:
            conn.execute(
                text("UPDATE usuarios SET session_id = NULL WHERE id = :id"),
                {"id": user.id}
            )
            conn.commit()

    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_id")
    return response


# --- DASHBOARD ---
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        # Contagem de itens distintos (SKUs) cadastrados
        total_produtos = conn.execute(
            text("SELECT COUNT(*) FROM produtos")
        ).fetchone()[0]

        # Soma total de unidades em estoque
        total_unidades = conn.execute(
            text("SELECT COALESCE(SUM(quantidade), 0) FROM produtos")
        ).fetchone()[0]

        total_vendas = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM vendas
                WHERE data_venda >= date_trunc('month', CURRENT_DATE)
                AND data_venda < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
            """)
        ).fetchone()[0]

        # Valor total vendido no mês atual
        total_vendas_valor = conn.execute(
            text("""
                SELECT COALESCE(SUM(total), 0)
                FROM vendas
                WHERE data_venda >= date_trunc('month', CURRENT_DATE)
                    AND data_venda < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
            """)
        ).fetchone()[0]

        total_empresas = conn.execute(
            text("SELECT COUNT(*) FROM empresas")
        ).fetchone()[0]

        total_fornecedores = conn.execute(
            text("SELECT COUNT(*) FROM fornecedores")
        ).fetchone()[0]

        dados_grafico = conn.execute(text("""
            SELECT e.nome_fantasia, COALESCE(SUM(p.quantidade), 0) AS total
            FROM empresas e
            LEFT JOIN produtos p ON p.empresa_id = e.id
            GROUP BY e.id, e.nome_fantasia
            ORDER BY e.nome_fantasia
        """)).fetchall()

        labels = [d.nome_fantasia for d in dados_grafico]
        valores = [float(d.total) for d in dados_grafico]

        vendas_recentes = conn.execute(text("""
            SELECT v.data_venda, v.quantidade, v.total, p.nome, p.cor, p.tamanho
            FROM vendas v
            JOIN produtos p ON v.produto_id = p.id
            ORDER BY v.data_venda DESC
            LIMIT 5
        """)).fetchall()

        # Vendas por empresa nos últimos 6 meses (para gráfico de linha)
        vendas_por_empresa = conn.execute(text("""
            SELECT
                e.nome_fantasia AS empresa,
                TO_CHAR(DATE_TRUNC('month', v.data_venda), 'MM/YYYY') AS mes,
                DATE_TRUNC('month', v.data_venda) AS mes_dt,
                COALESCE(SUM(v.total), 0) AS total_faturado
            FROM vendas v
            JOIN produtos p ON v.produto_id = p.id
            JOIN empresas e ON p.empresa_id = e.id
            WHERE v.data_venda >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '5 months'
            GROUP BY e.nome_fantasia, DATE_TRUNC('month', v.data_venda)
            ORDER BY mes_dt, e.nome_fantasia
        """)).fetchall()

        # Monta estrutura para Chart.js: {empresa: {mes: valor}}
        from collections import defaultdict
        empresas_vendas: dict = defaultdict(dict)
        meses_set: list = []
        for row in vendas_por_empresa:
            if row.mes not in meses_set:
                meses_set.append(row.mes)
            empresas_vendas[row.empresa][row.mes] = float(row.total_faturado)

        datasets_vendas = []
        cores = [
            "rgba(13,110,253,0.85)", "rgba(25,135,84,0.85)",
            "rgba(255,193,7,0.85)", "rgba(111,66,193,0.85)",
            "rgba(220,53,69,0.85)", "rgba(13,202,240,0.85)",
        ]
        for i, (empresa, dados_mes) in enumerate(empresas_vendas.items()):
            cor = cores[i % len(cores)]
            datasets_vendas.append({
                "label": empresa,
                "data": [dados_mes.get(m, 0) for m in meses_set],
                "borderColor": cor,
                "backgroundColor": cor,
                "tension": 0.4,
                "fill": False,
            })

        # Top 10 produtos mais vendidos (últimos 6 meses)
        try:
            top_produtos_rows = conn.execute(text("""
                SELECT p.nome, p.cor, p.tamanho, SUM(v.quantidade) AS total_vendido
                FROM vendas v
                JOIN produtos p ON v.produto_id = p.id
                WHERE v.data_venda >= date_trunc('month', CURRENT_DATE) - INTERVAL '5 months'
                GROUP BY p.nome, p.cor, p.tamanho
                ORDER BY total_vendido DESC
                LIMIT 10
            """)).fetchall()
            top_produtos_labels = [
                f"{r.nome} {r.cor or ''} {r.tamanho or ''}".strip()
                for r in top_produtos_rows
            ]
            top_produtos_valores = [float(r.total_vendido or 0) for r in top_produtos_rows]
        except Exception:
            top_produtos_labels = []
            top_produtos_valores = []

        # Produtos com estoque baixo (<= 5)
        try:
            estoque_baixo_rows = conn.execute(text("""
                SELECT p.nome, p.cor, p.tamanho, p.quantidade, e.nome_fantasia
                FROM produtos p
                JOIN empresas e ON p.empresa_id = e.id
                WHERE p.quantidade <= 5
                ORDER BY p.quantidade ASC
                LIMIT 20
            """)).fetchall()
            estoque_baixo = [
                {
                    "nome": r.nome,
                    "cor": r.cor,
                    "tamanho": r.tamanho,
                    "quantidade": float(r.quantidade or 0),
                    "nome_fantasia": r.nome_fantasia,
                }
                for r in estoque_baixo_rows
            ]
            total_estoque_baixo = len(estoque_baixo)
        except Exception:
            estoque_baixo = []
            total_estoque_baixo = 0

        # Ticket médio por empresa (mês atual)
        try:
            ticket_rows = conn.execute(text("""
                SELECT e.nome_fantasia,
                       COUNT(DISTINCT v.grupo_venda) AS total_vendas,
                       SUM(v.total) AS faturamento,
                       ROUND(SUM(v.total) / NULLIF(COUNT(DISTINCT v.grupo_venda), 0), 2) AS ticket_medio
                FROM vendas v
                JOIN produtos p ON v.produto_id = p.id
                JOIN empresas e ON p.empresa_id = e.id
                WHERE v.data_venda >= date_trunc('month', CURRENT_DATE)
                  AND v.data_venda < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
                GROUP BY e.nome_fantasia
                ORDER BY ticket_medio DESC NULLS LAST
            """)).fetchall()
            ticket_medio_empresas = [
                {
                    "nome_fantasia": r.nome_fantasia,
                    "total_vendas": int(r.total_vendas or 0),
                    "faturamento": float(r.faturamento or 0),
                    "ticket_medio": float(r.ticket_medio or 0),
                }
                for r in ticket_rows
            ]
        except Exception:
            ticket_medio_empresas = []

        # Evolução de cadastros (produtos e fornecedores) nos últimos 6 meses
        try:
            produtos_mes_rows = conn.execute(text("""
                SELECT TO_CHAR(DATE_TRUNC('month', criado_em), 'MM/YYYY') AS mes,
                       DATE_TRUNC('month', criado_em) AS mes_dt,
                       COUNT(*) AS total
                FROM produtos
                WHERE criado_em >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '5 months'
                GROUP BY mes, mes_dt
                ORDER BY mes_dt
            """)).fetchall()
            fornecedores_mes_rows = conn.execute(text("""
                SELECT TO_CHAR(DATE_TRUNC('month', criado_em), 'MM/YYYY') AS mes,
                       DATE_TRUNC('month', criado_em) AS mes_dt,
                       COUNT(*) AS total
                FROM fornecedores
                WHERE criado_em >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '5 months'
                GROUP BY mes, mes_dt
                ORDER BY mes_dt
            """)).fetchall()

            meses_cad: list = []
            for r in produtos_mes_rows:
                if r.mes not in meses_cad:
                    meses_cad.append(r.mes)
            for r in fornecedores_mes_rows:
                if r.mes not in meses_cad:
                    meses_cad.append(r.mes)

            produtos_map = {r.mes: int(r.total or 0) for r in produtos_mes_rows}
            fornecedores_map = {r.mes: int(r.total or 0) for r in fornecedores_mes_rows}

            evolucao_cadastros = {
                "meses": meses_cad,
                "produtos_por_mes": [produtos_map.get(m, 0) for m in meses_cad],
                "fornecedores_por_mes": [fornecedores_map.get(m, 0) for m in meses_cad],
            }
        except Exception:
            evolucao_cadastros = {
                "meses": [],
                "produtos_por_mes": [],
                "fornecedores_por_mes": [],
            }

        # PI IV — ML: previsão de demanda
        ml_previsao = prever_demanda(conn)

    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user,
        "total_produtos": int(total_produtos or 0),
        "total_unidades": float(total_unidades or 0),
        "total_vendas": total_vendas,
        "total_vendas_valor": float(total_vendas_valor or 0),
        "total_empresas": total_empresas,
        "total_fornecedores": total_fornecedores,
        "labels": labels,
        "valores": valores,
        "vendas_recentes": vendas_recentes,
        "meses_vendas": meses_set,
        "datasets_vendas": datasets_vendas,
        "top_produtos_labels": top_produtos_labels,
        "top_produtos_valores": top_produtos_valores,
        "estoque_baixo": estoque_baixo,
        "total_estoque_baixo": total_estoque_baixo,
        "ticket_medio_empresas": ticket_medio_empresas,
        "evolucao_cadastros": evolucao_cadastros,
        "ml_previsao": ml_previsao,
    })


# --- EXPORTAÇÃO CSV ---
@app.get("/relatorio/csv")
async def exportar_csv(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT TO_CHAR(DATE_TRUNC('month', v.data_venda), 'MM/YYYY') AS mes,
                   DATE_TRUNC('month', v.data_venda) AS mes_dt,
                   e.nome_fantasia AS empresa,
                   COUNT(DISTINCT v.grupo_venda) AS total_vendas,
                   ROUND(SUM(v.total), 2) AS faturamento,
                   ROUND(SUM(v.total) / NULLIF(COUNT(DISTINCT v.grupo_venda), 0), 2) AS ticket_medio,
                   COUNT(DISTINCT v.produto_id) AS produtos_unicos
            FROM vendas v
            JOIN produtos p ON v.produto_id = p.id
            JOIN empresas e ON p.empresa_id = e.id
            WHERE v.data_venda >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '5 months'
            GROUP BY mes, mes_dt, e.nome_fantasia
            ORDER BY mes_dt, e.nome_fantasia
        """)).fetchall()

    buf = StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([
        "Mês", "Empresa", "Total de Vendas",
        "Faturamento (R$)", "Ticket Médio (R$)", "Produtos Únicos Vendidos",
    ])
    for r in rows:
        writer.writerow([
            r.mes,
            r.empresa,
            int(r.total_vendas or 0),
            f"{float(r.faturamento or 0):.2f}".replace(".", ","),
            f"{float(r.ticket_medio or 0):.2f}".replace(".", ","),
            int(r.produtos_unicos or 0),
        ])

    registrar_log(
        user.id, user.username, "EXPORTACAO_CSV",
        f"Exportou relatório analítico ({len(rows)} linhas)",
    )

    csv_bytes = "\ufeff" + buf.getvalue()
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="relatorio_vendas.csv"'},
    )


# --- PRODUTOS ---
@app.get("/produtos", response_class=HTMLResponse)
async def listar_produtos(
    request: Request,
    busca: str = Query(None),
    empresa_id: str = Query(None),
    page: int = Query(1, ge=1),
    erro: str = Query(None)):

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    por_pagina = 20
    offset = (page - 1) * por_pagina
    empresa_id_int = int(empresa_id) if empresa_id else None

    filtros = ["1=1"]
    params: dict = {}

    if busca:
        filtros.append("(p.nome ILIKE :busca OR p.cor ILIKE :busca OR p.tamanho ILIKE :busca)")
        params["busca"] = f"%{busca}%"

    if empresa_id_int:
        filtros.append("p.empresa_id = :empresa_id")
        params["empresa_id"] = empresa_id_int

    where_clause = "WHERE " + " AND ".join(filtros)

    with engine.connect() as conn:
        total_produtos = conn.execute(text(f"""
            SELECT COUNT(*)
            FROM produtos p
            {where_clause}
        """), params).fetchone()[0]

        params["limit"] = por_pagina
        params["offset"] = offset

        produtos = conn.execute(text(f"""
            SELECT p.*, e.nome_fantasia AS empresa_nome, f.nome AS fornecedor_nome
            FROM produtos p
            LEFT JOIN empresas e ON p.empresa_id = e.id
            LEFT JOIN fornecedores f ON p.fornecedor_id = f.id
            {where_clause}
            ORDER BY p.id DESC
            LIMIT :limit OFFSET :offset
        """), params).fetchall()

        empresas = conn.execute(
            text("SELECT id, nome_fantasia FROM empresas ORDER BY nome_fantasia")
        ).fetchall()

        fornecedores = conn.execute(
            text("SELECT id, nome FROM fornecedores ORDER BY nome")
        ).fetchall()

    total_paginas = ceil(total_produtos / por_pagina) if total_produtos > 0 else 1

    return templates.TemplateResponse(request, "produtos.html", {
        "user": user,
        "produtos": produtos,
        "empresas": empresas,
        "fornecedores": fornecedores,
        "page": page,
        "total_paginas": total_paginas,
        "total_produtos": total_produtos,
        "busca": busca or "",
        "filtro_empresa": empresa_id_int,
        "erro": erro,
    })

@app.post("/produtos/novo")
async def novo_produto(
    request: Request,
    nome: str = Form(...),
    quantidade: float = Form(...),
    preco: float = Form(...),
    empresa_id: int = Form(...),
    fornecedor_id: int = Form(...),
    cor: str = Form(None),
    tamanho: str = Form(None),):

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO produtos (nome, quantidade, preco, empresa_id, fornecedor_id, cor, tamanho)
            VALUES (:nome, :quantidade, :preco, :empresa_id, :fornecedor_id, :cor, :tamanho)
        """), {
            "nome": nome,
            "quantidade": quantidade,
            "preco": preco,
            "empresa_id": empresa_id,
            "fornecedor_id": fornecedor_id,
            "cor": cor,
            "tamanho": tamanho,
        })
        conn.commit()

    registrar_log(user.id, user.username, "CADASTRO_PRODUTO", f"Produto: {nome}, cor: {cor}, tamanho: {tamanho}")

    return RedirectResponse(url="/produtos?msg=produto_criado", status_code=303)

@app.get("/produtos/novo")
async def exibir_formulario_cadastro(request: Request):
    with engine.connect() as conn:
        empresas = conn.execute(
            text("SELECT id, nome_fantasia FROM empresas ORDER BY nome_fantasia")
        ).fetchall()

        fornecedores = conn.execute(
            text("SELECT id, nome FROM fornecedores ORDER BY nome")
        ).fetchall()

    return templates.TemplateResponse(request, "cadastrar_produto.html", {
        "empresas": empresas,
        "fornecedores": fornecedores
    })

@app.get("/produtos/editar/{id}", response_class=HTMLResponse)
async def editar_produto_page(request: Request, id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        produto = conn.execute(
            text("SELECT * FROM produtos WHERE id = :id"),
            {"id": id}
        ).fetchone()

        empresas = conn.execute(
            text("SELECT id, nome_fantasia FROM empresas ORDER BY nome_fantasia")
        ).fetchall()

        fornecedores = conn.execute(
            text("SELECT id, nome FROM fornecedores ORDER BY nome")
        ).fetchall()

    return templates.TemplateResponse(request, "editar_produto.html", {
        "user": user,
        "produto": produto,
        "empresas": empresas,
        "fornecedores": fornecedores
    })

@app.post("/produtos/editar/{id}")
async def editar_produto(
    id: int,
    nome: str = Form(...),
    quantidade: float = Form(...),
    preco: float = Form(...),
    empresa_id: int = Form(...),
    fornecedor_id: int = Form(...),
    cor: str = Form(None),
    tamanho: str = Form(None),):

    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE produtos
            SET nome = :nome,
                quantidade = :quantidade,
                preco = :preco,
                empresa_id = :empresa_id,
                fornecedor_id = :fornecedor_id,
                cor = :cor,
                tamanho = :tamanho
            WHERE id = :id
        """), {
            "id": id,
            "nome": nome,
            "quantidade": quantidade,
            "preco": preco,
            "empresa_id": empresa_id,
            "fornecedor_id": fornecedor_id,
            "cor": cor,
            "tamanho": tamanho,
        })
        conn.commit()

    return RedirectResponse(url="/produtos?msg=produto_atualizado", status_code=303)

@app.get("/produtos/deletar/{id}")
async def deletar_produto(request: Request, id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        # Bloqueia exclusão se houver vendas vinculadas ao produto
        vendas_count = conn.execute(
            text("SELECT COUNT(*) FROM vendas WHERE produto_id = :id"),
            {"id": id}
        ).fetchone()[0]

        if vendas_count > 0:
            # Redireciona com flag de erro para o template exibir o alerta
            return RedirectResponse(url="/produtos?erro=produto_com_vendas", status_code=303)

        conn.execute(
            text("DELETE FROM produtos WHERE id = :id"),
            {"id": id}
        )
        conn.commit()

    registrar_log(user.id, user.username, "EXCLUSAO_PRODUTO", f"Produto ID: {id} excluído")
    return RedirectResponse(url="/produtos?msg=produto_excluido", status_code=303)


# --- GESTÃO DE USUÁRIOS ---
@app.get("/usuarios", response_class=HTMLResponse)
async def listar_usuarios(request: Request):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        usuarios = conn.execute(
            text("SELECT id, username, perfil FROM usuarios WHERE username != 'admin' ORDER BY id")
        ).fetchall()

    return templates.TemplateResponse(request, "usuarios.html", {
        "user": user,
        "usuarios": usuarios
    })

@app.post("/usuarios/novo")
async def novo_usuario(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    perfil: str = Form(...)):

    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/", status_code=303)

    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO usuarios (username, password, perfil)
                VALUES (:username, :password, :perfil)
            """), {
                "username": username,
                "password": hash_password(password),
                "perfil": perfil
            })
            conn.commit()
    except IntegrityError:
        pass

    return RedirectResponse(url="/usuarios", status_code=303)

@app.get("/usuarios/deletar/{id}")
async def deletar_usuario(request: Request, id: int):
    user = get_current_user(request)
    if user and user.perfil == "admin" and user.id != id:
        with engine.connect() as conn:
            conn.execute(
                text("DELETE FROM usuarios WHERE id = :id"),
                {"id": id}
            )
            conn.commit()

    return RedirectResponse(url="/usuarios", status_code=303)

@app.post("/usuarios/editar/{user_id}")
async def editar_usuario(
    user_id: int,
    request: Request,
    username: str = Form(...),
    perfil: str = Form(...),
    password: str = Form(None)):

    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/", status_code=303)

    with engine.connect() as conn:
        if password:
            conn.execute(text("""
                UPDATE usuarios
                SET username = :username,
                    perfil = :perfil,
                    password = :password
                WHERE id = :id
            """), {
                "username": username,
                "perfil": perfil,
                "password": hash_password(password),
                "id": user_id
            })
        else:
            conn.execute(text("""
                UPDATE usuarios
                SET username = :username,
                    perfil = :perfil
                WHERE id = :id
            """), {
                "username": username,
                "perfil": perfil,
                "id": user_id
            })
        conn.commit()

    return RedirectResponse(url="/usuarios", status_code=303)


# --- FORNECEDORES ---
@app.get("/fornecedores", response_class=HTMLResponse)
async def listar_fornecedores(
    request: Request,
    busca: str = Query(None),
    page: int = Query(1, ge=1)):

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    por_pagina = 20
    offset = (page - 1) * por_pagina

    filtros = ["1=1"]
    params: dict = {}

    if busca:
        filtros.append("(nome ILIKE :busca OR cnpj ILIKE :busca OR email ILIKE :busca)")
        params["busca"] = f"%{busca}%"

    where_clause = "WHERE " + " AND ".join(filtros)

    with engine.connect() as conn:
        total_fornecedores = conn.execute(text(f"""
            SELECT COUNT(*) FROM fornecedores {where_clause}
        """), params).fetchone()[0]

        params["limit"] = por_pagina
        params["offset"] = offset

        fornecedores = conn.execute(text(f"""
            SELECT * FROM fornecedores
            {where_clause}
            ORDER BY nome
            LIMIT :limit OFFSET :offset
        """), params).fetchall()

    total_paginas = ceil(total_fornecedores / por_pagina) if total_fornecedores > 0 else 1

    return templates.TemplateResponse(request, "fornecedores.html", {
        "user": user,
        "fornecedores": fornecedores,
        "page": page,
        "total_paginas": total_paginas,
        "total_fornecedores": total_fornecedores,
        "busca": busca or "",
    })

@app.post("/fornecedores/editar/{id}")
async def editar_fornecedor(
    request: Request,
    id: int,
    nome: str = Form(...),
    cnpj: str = Form(None),
    telefone: str = Form(None),
    email: str = Form(None)):

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE fornecedores
            SET nome = :nome,
                cnpj = :cnpj,
                telefone = :telefone,
                email = :email
            WHERE id = :id
        """), {
            "nome": nome,
            "cnpj": cnpj,
            "telefone": telefone,
            "email": email,
            "id": id
        })
        conn.commit()

    return RedirectResponse(url="/fornecedores?msg=fornecedor_atualizado", status_code=303)

@app.post("/fornecedores/novo")
async def novo_fornecedor(
    request: Request,
    nome: str = Form(...),
    cnpj: str = Form(None),
    telefone: str = Form(None),
    email: str = Form(None)):

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO fornecedores (nome, cnpj, telefone, email)
            VALUES (:nome, :cnpj, :telefone, :email)
        """), {
            "nome": nome,
            "cnpj": cnpj,
            "telefone": telefone,
            "email": email,
        })
        conn.commit()

    return RedirectResponse(url="/fornecedores?msg=fornecedor_criado", status_code=303)

@app.get("/fornecedores/deletar/{id}")
async def deletar_fornecedor(request: Request, id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM fornecedores WHERE id = :id"),
            {"id": id}
        )
        conn.commit()

    return RedirectResponse(url="/fornecedores?msg=fornecedor_excluido", status_code=303)


# --- VENDAS ---
@app.get("/vendas", response_class=HTMLResponse)
async def pagina_vendas(
    request: Request,
    fornecedor_id: str | None = Query(None),
    empresa_id: str | None = Query(None),
    data_inicio: str | None = Query(None),
    data_fim: str | None = Query(None),):

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    # converte strings da query em int, se tiver valor
    fornecedor_id_int = int(fornecedor_id) if fornecedor_id else None
    empresa_id_int = int(empresa_id) if empresa_id else None

    filtros = ["1=1"]
    params: dict[str, object] = {}

    if fornecedor_id_int:
        filtros.append("p.fornecedor_id = :fornecedor_id")
        params["fornecedor_id"] = fornecedor_id_int

    if empresa_id_int:
        filtros.append("p.empresa_id = :empresa_id")
        params["empresa_id"] = empresa_id_int

    if data_inicio:
        filtros.append("DATE(v.data_venda) >= :data_inicio")
        params["data_inicio"] = data_inicio

    if data_fim:
        filtros.append("DATE(v.data_venda) <= :data_fim")
        params["data_fim"] = data_fim

    where_clause = "WHERE " + " AND ".join(filtros)

    query = f"""
        SELECT
            v.id,
            v.grupo_venda,
            v.numero_venda,
            v.data_venda,
            v.tipo_documento,
            v.cliente_nome,
            v.cliente_cpf_cnpj,
            v.quantidade,
            v.preco_unitario,
            v.total,
            p.nome AS produto_nome,
            p.cor,
            p.tamanho,
            f.nome AS fornecedor_nome,
            e.nome_fantasia AS empresa_nome
        FROM vendas v
        JOIN produtos p ON p.id = v.produto_id
        LEFT JOIN fornecedores f ON f.id = p.fornecedor_id
        LEFT JOIN empresas e ON e.id = p.empresa_id
        {where_clause}
        ORDER BY v.data_venda DESC, v.id DESC
    """

    with engine.connect() as conn:
        produtos = conn.execute(text("""
            SELECT *
            FROM produtos
            WHERE quantidade > 0
            ORDER BY nome
        """)).fetchall()

        vendas = conn.execute(text(query), params).fetchall()

        fornecedores = conn.execute(text("""
            SELECT id, nome
            FROM fornecedores
            ORDER BY nome
        """)).fetchall()

        empresas = conn.execute(text("""
            SELECT id, nome_fantasia
            FROM empresas
            ORDER BY nome_fantasia
        """)).fetchall()

    total_geral = sum(float(v.total or 0) for v in vendas)

    return templates.TemplateResponse("vendas.html", {
        "request": request,
        "user": user,
        "produtos": produtos,
        "vendas": vendas,
        "fornecedores": fornecedores,
        "empresas": empresas,
        "filtro_fornecedor": fornecedor_id_int,
        "filtro_empresa": empresa_id_int,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "total_geral": total_geral,
    })

@app.post("/vendas/nova")
async def registrar_venda(
    request: Request,
    tipo_documento: str = Form(...),
    empresa_id: int = Form(...),
    cliente_nome: str = Form(...),
    cliente_cpf_cnpj: str = Form(""),
    cliente_email: str = Form(""),
    cliente_telefone: str = Form(""),
    produto_id: list[int] = Form(...),
    qtd_venda: list[float] = Form(...)):

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    if len(produto_id) != len(qtd_venda):
        return RedirectResponse(url="/vendas", status_code=303)

    grupo_venda = str(uuid.uuid4())
    data_venda = datetime.now()
    status_documento = "pendente_nfe" if tipo_documento == "nfe" else "gerado"

    with engine.connect() as conn:
        numero_venda = conn.execute(text("SELECT nextval('seq_numero_venda')")).scalar()
        ids_vendas = []
        total_geral = 0.0
        itens_log = []

        for i in range(len(produto_id)):
            prod_id = int(produto_id[i])
            qtd = float(qtd_venda[i])

            if qtd <= 0:
                continue

            prod = conn.execute(text("""
                SELECT id, nome, cor, tamanho, quantidade, preco, empresa_id
                FROM produtos
                WHERE id = :id
            """), {"id": prod_id}).fetchone()

            if not prod:
                return RedirectResponse(url="/vendas", status_code=303)

            if float(prod.quantidade) < qtd:
                return RedirectResponse(url="/vendas", status_code=303)

            preco_unitario = float(prod.preco)
            total = qtd * preco_unitario
            total_geral += total

            resultado = conn.execute(text("""
                INSERT INTO vendas (
                    grupo_venda,
                    numero_venda,
                    produto_id,
                    empresa_id,
                    tipo_documento,
                    cliente_nome,
                    cliente_cpf_cnpj,
                    cliente_email,
                    cliente_telefone,
                    status_documento,
                    quantidade,
                    preco_unitario,
                    total,
                    data_venda
                )
                VALUES (
                    :grupo_venda,
                    :numero_venda,
                    :produto_id,
                    :empresa_id,
                    :tipo_documento,
                    :cliente_nome,
                    :cliente_cpf_cnpj,
                    :cliente_email,
                    :cliente_telefone,
                    :status_documento,
                    :quantidade,
                    :preco_unitario,
                    :total,
                    :data_venda
                )
                RETURNING id
            """), {
                "grupo_venda": grupo_venda,
                "numero_venda": numero_venda,
                "produto_id": prod_id,
                "empresa_id": empresa_id,
                "tipo_documento": tipo_documento,
                "cliente_nome": cliente_nome,
                "cliente_cpf_cnpj": cliente_cpf_cnpj,
                "cliente_email": cliente_email,
                "cliente_telefone": cliente_telefone,
                "status_documento": status_documento,
                "quantidade": qtd,
                "preco_unitario": preco_unitario,
                "total": total,
                "data_venda": data_venda
            })

            venda_id = resultado.fetchone().id
            ids_vendas.append(venda_id)

            conn.execute(text("""
                UPDATE produtos
                SET quantidade = quantidade - :qtd
                WHERE id = :id
            """), {
                "qtd": qtd,
                "id": prod_id
            })

            itens_log.append(f"{prod.nome} (qtd: {qtd}, total: R$ {total:.2f})")

        if not ids_vendas:
            return RedirectResponse(url="/vendas", status_code=303)

        conn.commit()

    registrar_log(
        user.id,
        user.username,
        "VENDA",
        f"Venda: #{numero_venda:06d} | Grupo: {grupo_venda} | Cliente: {cliente_nome} | Tipo: {tipo_documento} | Itens: {'; '.join(itens_log)} | Total geral: R$ {total_geral:.2f}"
    )

    if tipo_documento == "comprovante":
        return RedirectResponse(url=f"/vendas/comprovante/grupo/{grupo_venda}", status_code=303)

    return RedirectResponse(url="/vendas", status_code=303)

@app.get("/vendas/pdf")
async def relatorio_vendas_pdf(
    request: Request,
    fornecedor_id: str | None = Query(None),
    empresa_id: str | None = Query(None),
    data_inicio: str | None = Query(None),
    data_fim: str | None = Query(None),):

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    fornecedor_id_int = int(fornecedor_id) if fornecedor_id else None
    empresa_id_int = int(empresa_id) if empresa_id else None

    filtros = ["1=1"]
    params: dict[str, object] = {}

    if fornecedor_id_int:
        filtros.append("p.fornecedor_id = :fornecedor_id")
        params["fornecedor_id"] = fornecedor_id_int

    if empresa_id_int:
        filtros.append("p.empresa_id = :empresa_id")
        params["empresa_id"] = empresa_id_int

    if data_inicio:
        filtros.append("DATE(v.data_venda) >= :data_inicio")
        params["data_inicio"] = data_inicio

    if data_fim:
        filtros.append("DATE(v.data_venda) <= :data_fim")
        params["data_fim"] = data_fim

    where_clause = "WHERE " + " AND ".join(filtros)

    query = f"""
        SELECT
            v.id,
            v.data_venda,
            v.quantidade,
            v.preco_unitario,
            v.total,
            p.nome AS produto_nome,
            f.nome AS fornecedor_nome,
            e.nome_fantasia AS empresa_nome
        FROM vendas v
        JOIN produtos p ON p.id = v.produto_id
        LEFT JOIN fornecedores f ON f.id = p.fornecedor_id
        LEFT JOIN empresas e ON e.id = p.empresa_id
        {where_clause}
        ORDER BY v.data_venda DESC, v.id DESC
    """

    with engine.connect() as conn:
        vendas = conn.execute(text(query), params).fetchall()

    total_geral = sum(float(v.total or 0) for v in vendas)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    largura, altura = A4

    y = altura - 50
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, y, "Relatório de Vendas")

    y -= 25
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, f"Fornecedor: {fornecedor_id or 'Todos'}")
    y -= 15
    pdf.drawString(50, y, f"Empresa: {empresa_id or 'Todas'}")
    y -= 15
    pdf.drawString(50, y, f"Período: {data_inicio or '---'} até {data_fim or '---'}")
    y -= 15
    pdf.drawString(50, y, f"Total geral: R$ {total_geral:.2f}")

    y -= 30
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(30, y, "Data")
    pdf.drawString(100, y, "Empresa")
    pdf.drawString(190, y, "Fornecedor")
    pdf.drawString(290, y, "Produto")
    pdf.drawString(380, y, "Qtd")
    pdf.drawString(420, y, "Preço")
    pdf.drawString(470, y, "Total")

    y -= 15
    pdf.setFont("Helvetica", 8)

    for v in vendas:
        if y < 40:
            pdf.showPage()
            y = altura - 40
            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawString(30, y, "Data")
            pdf.drawString(100, y, "Empresa")
            pdf.drawString(190, y, "Fornecedor")
            pdf.drawString(290, y, "Produto")
            pdf.drawString(380, y, "Qtd")
            pdf.drawString(420, y, "Preço")
            pdf.drawString(470, y, "Total")
            y -= 15
            pdf.setFont("Helvetica", 8)

        pdf.drawString(30, y, str(v.data_venda)[:16])
        pdf.drawString(100, y, str(v.empresa_nome or ""))
        pdf.drawString(190, y, str(v.fornecedor_nome or ""))
        pdf.drawString(290, y, str(v.produto_nome or ""))
        pdf.drawString(380, y, str(v.quantidade))
        pdf.drawString(420, y, f"{float(v.preco_unitario):.2f}")
        pdf.drawString(470, y, f"{float(v.total):.2f}")
        y -= 12

    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=relatorio_vendas.pdf"}
    )

@app.get("/vendas/comprovante/{venda_id}")
async def gerar_comprovante_venda(venda_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        venda = conn.execute(text("""
            SELECT
                v.id,
                v.data_venda,
                v.tipo_documento,
                v.cliente_nome,
                v.cliente_cpf_cnpj,
                v.cliente_email,
                v.cliente_telefone,
                v.quantidade,
                v.preco_unitario,
                v.total,
                p.nome AS produto_nome,
                p.cor,
                p.tamanho,
                e.nome_fantasia AS empresa_nome,
                e.cnpj AS empresa_cnpj
            FROM vendas v
            JOIN produtos p ON p.id = v.produto_id
            LEFT JOIN empresas e ON e.id = v.empresa_id
            WHERE v.id = :venda_id
        """), {"venda_id": venda_id}).fetchone()

    if not venda:
        return RedirectResponse(url="/vendas", status_code=303)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    largura, altura = A4

    y = altura - 50
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, y, "Comprovante de Venda")

    y -= 30
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, f"Venda nº: {venda.id}")
    y -= 18
    pdf.drawString(50, y, f"Data: {str(venda.data_venda)[:16]}")
    y -= 18
    pdf.drawString(50, y, f"Empresa: {venda.empresa_nome or '-'}")
    y -= 18
    pdf.drawString(50, y, f"CNPJ da empresa: {venda.empresa_cnpj or '-'}")

    y -= 28
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, y, "Dados do cliente")
    y -= 20
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, f"Nome: {venda.cliente_nome or '-'}")
    y -= 18
    pdf.drawString(50, y, f"CPF/CNPJ: {venda.cliente_cpf_cnpj or '-'}")
    y -= 18
    pdf.drawString(50, y, f"E-mail: {venda.cliente_email or '-'}")
    y -= 18
    pdf.drawString(50, y, f"Telefone: {venda.cliente_telefone or '-'}")

    y -= 28
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, y, "Produto")
    y -= 20
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, f"Nome: {venda.produto_nome}")
    y -= 18
    pdf.drawString(50, y, f"Cor: {venda.cor or '-'}")
    y -= 18
    pdf.drawString(50, y, f"Tamanho: {venda.tamanho or '-'}")
    y -= 18
    pdf.drawString(50, y, f"Quantidade: {venda.quantidade}")
    y -= 18
    pdf.drawString(50, y, f"Preço unitário: R$ {float(venda.preco_unitario):.2f}")
    y -= 18
    pdf.drawString(50, y, f"Total: R$ {float(venda.total):.2f}")

    y -= 35
    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawString(50, y, "Este documento é um comprovante interno de venda e não substitui a nota fiscal eletrônica.")

    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=comprovante_venda_{venda.id}.pdf"}
    )

@app.get("/vendas/comprovante/grupo/{grupo_venda}")
async def gerar_comprovante_grupo(grupo_venda: str, request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        itens = conn.execute(text("""
            SELECT
                v.id,
                v.grupo_venda,
                v.numero_venda,
                v.data_venda,
                v.tipo_documento,
                v.cliente_nome,
                v.cliente_cpf_cnpj,
                v.cliente_email,
                v.cliente_telefone,
                v.quantidade,
                v.preco_unitario,
                v.total,
                p.nome AS produto_nome,
                p.cor,
                p.tamanho,
                e.nome_fantasia AS empresa_nome,
                e.cnpj AS empresa_cnpj
            FROM vendas v
            JOIN produtos p ON p.id = v.produto_id
            LEFT JOIN empresas e ON e.id = v.empresa_id
            WHERE v.grupo_venda = :grupo_venda
            ORDER BY v.id
        """), {"grupo_venda": grupo_venda}).fetchall()

    if not itens:
        return RedirectResponse(url="/vendas", status_code=303)

    venda_base = itens[0]
    total_geral = sum(float(item.total or 0) for item in itens)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    largura, altura = A4

    y = altura - 50
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, y, "Comprovante de Venda")

    y -= 30
    pdf.setFont("Helvetica", 10)
    numero_fmt = f"#{venda_base.numero_venda:06d}" if venda_base.numero_venda else "—"
    pdf.drawString(50, y, f"Nº da Venda: {numero_fmt}")
    y -= 18
    pdf.drawString(50, y, f"Data: {str(venda_base.data_venda)[:16]}")
    y -= 18
    pdf.drawString(50, y, f"Empresa: {venda_base.empresa_nome or '-'}")
    y -= 18
    pdf.drawString(50, y, f"CNPJ da empresa: {venda_base.empresa_cnpj or '-'}")

    y -= 28
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, y, "Dados do cliente")

    y -= 20
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, f"Nome: {venda_base.cliente_nome or '-'}")
    y -= 18
    pdf.drawString(50, y, f"CPF/CNPJ: {venda_base.cliente_cpf_cnpj or '-'}")
    y -= 18
    pdf.drawString(50, y, f"E-mail: {venda_base.cliente_email or '-'}")
    y -= 18
    pdf.drawString(50, y, f"Telefone: {venda_base.cliente_telefone or '-'}")

    y -= 28
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, y, "Itens da venda")

    y -= 20
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(50, y, "Produto")
    pdf.drawString(260, y, "Qtd")
    pdf.drawString(320, y, "Preço Unit.")
    pdf.drawString(430, y, "Total")

    y -= 15
    pdf.setFont("Helvetica", 9)

    for item in itens:
        if y < 60:
            pdf.showPage()
            y = altura - 50
            pdf.setFont("Helvetica-Bold", 12)
            pdf.drawString(50, y, "Itens da venda")
            y -= 20
            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawString(50, y, "Produto")
            pdf.drawString(260, y, "Qtd")
            pdf.drawString(320, y, "Preço Unit.")
            pdf.drawString(430, y, "Total")
            y -= 15
            pdf.setFont("Helvetica", 9)

        descricao = item.produto_nome or "-"
        if item.cor:
            descricao += f" | Cor: {item.cor}"
        if item.tamanho:
            descricao += f" | Tam: {item.tamanho}"

        pdf.drawString(50, y, descricao[:38])
        pdf.drawString(260, y, str(item.quantidade))
        pdf.drawString(320, y, f"R$ {float(item.preco_unitario):.2f}")
        pdf.drawString(430, y, f"R$ {float(item.total):.2f}")
        y -= 16

    y -= 10
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, f"Total geral: R$ {total_geral:.2f}")

    y -= 30
    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawString(50, y, "Este documento é um comprovante interno de venda e não substitui a nota fiscal eletrônica.")

    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=comprovante_grupo_{grupo_venda}.pdf"}
    )


# --- EMPRESAS ---
@app.get("/empresas", response_class=HTMLResponse)
async def listar_empresas(request: Request):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/", status_code=303)

    with engine.connect() as conn:
        empresas = conn.execute(
            text("SELECT * FROM empresas ORDER BY nome_fantasia")
        ).fetchall()

    return templates.TemplateResponse(request, "empresas.html", {
        "user": user,
        "empresas": empresas
    })

@app.post("/empresas/nova")
async def nova_empresa(
    nome: str = Form(...),
    razao_social: str = Form(...),
    cnpj: str = Form(""),
    tel: str = Form(""),
    email: str = Form("")):

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO empresas (nome_fantasia, razao_social, cnpj, telefone, email)
            VALUES (:nome, :razao_social, :cnpj, :telefone, :email)
        """), {
            "nome": nome,
            "razao_social": razao_social,
            "cnpj": cnpj,
            "telefone": tel,
            "email": email
        })
        conn.commit()

    return RedirectResponse(url="/empresas", status_code=303)

@app.get("/empresas/deletar/{id}")
async def deletar_empresa(id: int):
    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM empresas WHERE id = :id"),
            {"id": id}
        )
        conn.commit()

    return RedirectResponse(url="/empresas", status_code=303)

@app.get("/empresas/editar/{id}", response_class=HTMLResponse)
async def editar_empresa_page(request: Request, id: int):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/", status_code=303)

    with engine.connect() as conn:
        empresa = conn.execute(
            text("SELECT * FROM empresas WHERE id = :id"),
            {"id": id}
        ).fetchone()

    if not empresa:
        return RedirectResponse(url="/empresas", status_code=303)

    return templates.TemplateResponse(request, "editar_empresa.html", {
        "user": user,
        "empresa": empresa
    })

@app.post("/empresas/editar/{id}")
async def atualizar_empresa(
    id: int,
    nome: str = Form(...),
    cnpj: str = Form(...),
    tel: str = Form(...),
    email: str = Form(...)):

    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE empresas
            SET nome_fantasia = :nome,
                cnpj = :cnpj,
                telefone = :telefone,
                email = :email
            WHERE id = :id
        """), {
            "nome": nome,
            "cnpj": cnpj,
            "telefone": tel,
            "email": email,
            "id": id
        })
        conn.commit()

    return RedirectResponse(url="/empresas", status_code=303)


# --- LOGS SOMENTE ADMIN ---
@app.get("/logs", response_class=HTMLResponse)
async def listar_logs(
    request: Request,
    acao: str = Query(None),
    usuario: str = Query(None),
    page: int = Query(1, ge=1)):

    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/", status_code=303)

    por_pagina = 20
    offset = (page - 1) * por_pagina

    filtros = []
    params = {}

    if acao:
        filtros.append("acao = :acao")
        params["acao"] = acao

    if usuario:
        filtros.append("username ILIKE :usuario")
        params["usuario"] = f"%{usuario}%"

    where_clause = ""
    if filtros:
        where_clause = "WHERE " + " AND ".join(filtros)

    with engine.connect() as conn:
        total_registros = conn.execute(text(f"""
            SELECT COUNT(*)
            FROM logs_sistema
            {where_clause}
        """), params).fetchone()[0]

        params["limit"] = por_pagina
        params["offset"] = offset

        logs = conn.execute(text(f"""
            SELECT *
            FROM logs_sistema
            {where_clause}
            ORDER BY data_evento DESC
            LIMIT :limit OFFSET :offset
        """), params).fetchall()

        acoes_disponiveis = conn.execute(text("""
            SELECT DISTINCT acao
            FROM logs_sistema
            WHERE acao IS NOT NULL
            ORDER BY acao
        """)).fetchall()

    total_paginas = ceil(total_registros / por_pagina) if total_registros > 0 else 1

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "user": user,
        "logs": logs,
        "acoes_disponiveis": acoes_disponiveis,
        "filtro_acao": acao,
        "filtro_usuario": usuario,
        "page": page,
        "total_paginas": total_paginas
    })


# --- BANCO DE HORAS ---
@app.get("/banco-horas", response_class=HTMLResponse)
async def banco_horas(
    request: Request,
    usuario: str = Query(None),
    data_inicio_str: str | None = Query(None, alias="data_inicio"),
    data_fim_str: str | None = Query(None, alias="data_fim")):

    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/", status_code=303)

    data_inicio: date | None = None
    data_fim: date | None = None
    try:
        if data_inicio_str and data_inicio_str.strip():
            data_inicio = date.fromisoformat(data_inicio_str.strip())
    except ValueError:
        data_inicio = None
    try:
        if data_fim_str and data_fim_str.strip():
            data_fim = date.fromisoformat(data_fim_str.strip())
    except ValueError:
        data_fim = None

    filtros = ["acao IN ('LOGIN', 'LOGOUT')"]
    params = {}

    if usuario:
        filtros.append("username = :usuario")
        params["usuario"] = usuario

    if data_inicio:
        filtros.append("DATE(data_evento) >= :data_inicio")
        params["data_inicio"] = data_inicio

    if data_fim:
        filtros.append("DATE(data_evento) <= :data_fim")
        params["data_fim"] = data_fim

    where_clause = "WHERE " + " AND ".join(filtros)

    # Limite máximo de 8h para sessões sem LOGOUT (evita inflação)
    LIMITE_HORAS = 8

    query = f"""
        WITH eventos AS (
            SELECT
                username,
                acao,
                data_evento,
                DATE(data_evento) AS dia,
                LEAD(data_evento) OVER (
                    PARTITION BY username, DATE(data_evento)
                    ORDER BY data_evento
                ) AS proximo_evento,
                LEAD(acao) OVER (
                    PARTITION BY username, DATE(data_evento)
                    ORDER BY data_evento
                ) AS proxima_acao
            FROM logs_sistema
            {where_clause}
        ),
        pares AS (
            SELECT
                username,
                dia,
                acao,
                data_evento,
                proxima_acao,
                proximo_evento,
                CASE
                    -- Par limpo: LOGIN seguido de LOGOUT
                    WHEN acao = 'LOGIN' AND proxima_acao = 'LOGOUT'
                        THEN LEAST(
                            EXTRACT(EPOCH FROM (proximo_evento - data_evento)) / 3600,
                            {LIMITE_HORAS}
                        )
                    -- LOGIN sem LOGOUT: usa horário atual se for hoje,
                    -- ou fim do dia (23:59) caso contrário — máx. 8h
                    WHEN acao = 'LOGIN' AND proxima_acao IS NULL
                        THEN LEAST(
                            EXTRACT(EPOCH FROM (
                                CASE
                                    WHEN dia = CURRENT_DATE
                                        THEN CURRENT_TIMESTAMP
                                    ELSE (dia::timestamp + INTERVAL '23 hours 59 minutes')
                                END
                                - data_evento
                            )) / 3600,
                            {LIMITE_HORAS}
                        )
                    ELSE 0
                END AS horas_sessao
            FROM eventos
            WHERE acao = 'LOGIN'
        )
        SELECT
            username,
            dia,
            ROUND(SUM(horas_sessao)::numeric, 2) AS horas_trabalhadas
        FROM pares
        GROUP BY username, dia
        ORDER BY dia DESC, username
    """

    with engine.connect() as conn:
        resultados = conn.execute(text(query), params).fetchall()

        usuarios = conn.execute(text("""
            SELECT DISTINCT username
            FROM logs_sistema
            WHERE username IS NOT NULL
            ORDER BY username
        """)).fetchall()

    total_horas = sum(float(item.horas_trabalhadas or 0) for item in resultados)

    resultados_formatados = []
    for item in resultados:
        resultados_formatados.append({
            "username": item.username,
            "dia": item.dia,
            "horas_trabalhadas": item.horas_trabalhadas,
            "horas_formatadas": formatar_horas_minutos(item.horas_trabalhadas)
        })

    total_horas_formatado = formatar_horas_minutos(total_horas)

    return templates.TemplateResponse("banco_horas.html", {
        "request": request,
        "user": user,
        "resultados": resultados_formatados,
        "usuarios": usuarios,
        "filtro_usuario": usuario,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "total_horas": round(total_horas, 2),
        "total_horas_formatado": total_horas_formatado
    })

@app.get("/banco-horas/pdf")
async def banco_horas_pdf(
    request: Request,
    usuario: str = Query(None),
    data_inicio_str: str | None = Query(None, alias="data_inicio"),
    data_fim_str: str | None = Query(None, alias="data_fim")):

    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/", status_code=303)

    data_inicio: date | None = None
    data_fim: date | None = None
    try:
        if data_inicio_str and data_inicio_str.strip():
            data_inicio = date.fromisoformat(data_inicio_str.strip())
    except ValueError:
        data_inicio = None
    try:
        if data_fim_str and data_fim_str.strip():
            data_fim = date.fromisoformat(data_fim_str.strip())
    except ValueError:
        data_fim = None

    filtros = ["acao IN ('LOGIN', 'LOGOUT')"]
    params = {}

    if usuario:
        filtros.append("username = :usuario")
        params["usuario"] = usuario

    if data_inicio:
        filtros.append("DATE(data_evento) >= :data_inicio")
        params["data_inicio"] = data_inicio

    if data_fim:
        filtros.append("DATE(data_evento) <= :data_fim")
        params["data_fim"] = data_fim

    where_clause = "WHERE " + " AND ".join(filtros)

    # Limite máximo de 8h para sessões sem LOGOUT (evita inflação)
    LIMITE_HORAS = 8

    query = f"""
        WITH eventos AS (
            SELECT
                username,
                acao,
                data_evento,
                DATE(data_evento) AS dia,
                LEAD(data_evento) OVER (
                    PARTITION BY username, DATE(data_evento)
                    ORDER BY data_evento
                ) AS proximo_evento,
                LEAD(acao) OVER (
                    PARTITION BY username, DATE(data_evento)
                    ORDER BY data_evento
                ) AS proxima_acao
            FROM logs_sistema
            {where_clause}
        ),
        pares AS (
            SELECT
                username,
                dia,
                acao,
                data_evento,
                proxima_acao,
                proximo_evento,
                CASE
                    -- Par limpo: LOGIN seguido de LOGOUT
                    WHEN acao = 'LOGIN' AND proxima_acao = 'LOGOUT'
                        THEN LEAST(
                            EXTRACT(EPOCH FROM (proximo_evento - data_evento)) / 3600,
                            {LIMITE_HORAS}
                        )
                    -- LOGIN sem LOGOUT: usa horário atual se for hoje,
                    -- ou fim do dia (23:59) caso contrário — máx. 8h
                    WHEN acao = 'LOGIN' AND proxima_acao IS NULL
                        THEN LEAST(
                            EXTRACT(EPOCH FROM (
                                CASE
                                    WHEN dia = CURRENT_DATE
                                        THEN CURRENT_TIMESTAMP
                                    ELSE (dia::timestamp + INTERVAL '23 hours 59 minutes')
                                END
                                - data_evento
                            )) / 3600,
                            {LIMITE_HORAS}
                        )
                    ELSE 0
                END AS horas_sessao
            FROM eventos
            WHERE acao = 'LOGIN'
        )
        SELECT
            username,
            dia,
            ROUND(SUM(horas_sessao)::numeric, 2) AS horas_trabalhadas
        FROM pares
        GROUP BY username, dia
        ORDER BY dia DESC, username
    """

    with engine.connect() as conn:
        resultados = conn.execute(text(query), params).fetchall()

    total_horas = sum(float(item.horas_trabalhadas or 0) for item in resultados)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    largura, altura = A4

    y = altura - 50
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, y, "Relatório de Banco de Horas")

    y -= 30
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, f"Usuário: {usuario or 'Todos'}")

    y -= 20
    pdf.drawString(50, y, f"Período: {data_inicio or '---'} até {data_fim or '---'}")

    y -= 20
    pdf.drawString(50, y, f"Total de horas: {formatar_horas_minutos(total_horas)} h")

    y -= 30
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, y, "Usuário")
    pdf.drawString(220, y, "Data")
    pdf.drawString(380, y, "Horas")

    y -= 20
    pdf.setFont("Helvetica", 10)

    for item in resultados:
        if y < 50:
            pdf.showPage()
            y = altura - 50
            pdf.setFont("Helvetica-Bold", 10)
            pdf.drawString(50, y, "Usuário")
            pdf.drawString(220, y, "Data")
            pdf.drawString(380, y, "Horas")
            y -= 20
            pdf.setFont("Helvetica", 10)

        pdf.drawString(50, y, str(item.username))
        pdf.drawString(220, y, str(item.dia))
        pdf.drawString(380, y, formatar_horas_minutos(item.horas_trabalhadas))
        y -= 18

    pdf.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=relatorio_banco_horas.pdf"}
    )


# --- API ---
@app.get("/api/produtos/{empresa_id}")
async def api_listar_produtos(request: Request, empresa_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"erro": "Não autenticado"})
    with engine.connect() as conn:
        produtos = conn.execute(text("""
            SELECT nome, quantidade, preco
            FROM produtos
            WHERE empresa_id = :empresa_id
            ORDER BY nome
        """), {"empresa_id": empresa_id}).fetchall()

    estoque = []
    for p in produtos:
        estoque.append({
            "nome": p.nome,
            "quantidade": float(p.quantidade) if isinstance(p.quantidade, Decimal) else p.quantidade,
            "preco": float(p.preco) if isinstance(p.preco, Decimal) else p.preco
        })

    return {"empresa_id": empresa_id, "estoque": estoque}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
