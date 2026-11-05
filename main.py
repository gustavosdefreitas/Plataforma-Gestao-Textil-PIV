from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, Request, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from io import BytesIO, StringIO
import csv
import json
import re
import tempfile
import qrcode
import barcode
from barcode.writer import ImageWriter
from bs4 import BeautifulSoup
import httpx
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
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

def validar_cpf(cpf: str) -> bool:
    """Valida CPF verificando os dois dígitos verificadores."""
    cpf = ''.join(c for c in cpf if c.isdigit())
    if len(cpf) != 11 or len(set(cpf)) == 1:
        return False
    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    d1 = (soma * 10 % 11) % 10
    if d1 != int(cpf[9]):
        return False
    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    d2 = (soma * 10 % 11) % 10
    return d2 == int(cpf[10])

def formatar_cpf(cpf: str) -> str:
    """Formata CPF como 000.000.000-00."""
    cpf = ''.join(c for c in cpf if c.isdigit())
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if len(cpf) == 11 else cpf

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
                nome_completo VARCHAR(150),
                cpf VARCHAR(14),
                password VARCHAR(64) NOT NULL,
                perfil VARCHAR(20) DEFAULT 'user',
                session_id VARCHAR(36)
            );
        """))

        conn.execute(text("""
            ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS nome_completo VARCHAR(150);
        """))

        conn.execute(text("""
            ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS cpf VARCHAR(14);
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
            ALTER TABLE empresas ADD COLUMN IF NOT EXISTS situacao_cadastral VARCHAR(30);
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
            ALTER TABLE fornecedores ADD COLUMN IF NOT EXISTS situacao_cadastral VARCHAR(30);
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
        # PI IV v1.4.0: registro de leituras do dispositivo IoT (evita duplicidade)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS leituras_iot (
                id SERIAL PRIMARY KEY,
                leitura_id VARCHAR(64) UNIQUE NOT NULL,
                codigo VARCHAR(64) NOT NULL,
                usuario_id INTEGER,
                numero_venda INTEGER,
                grupo_venda VARCHAR(36),
                resposta TEXT,
                data_leitura TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        conn.execute(text("""
            ALTER TABLE vendas
            ADD COLUMN IF NOT EXISTS numero_venda INTEGER
        """))

        # PI IV v1.5.0: tipo de operação no dispositivo, contagem de reenvios e módulo de validação
        conn.execute(text("ALTER TABLE leituras_iot ADD COLUMN IF NOT EXISTS tipo VARCHAR(20) DEFAULT 'venda'"))
        conn.execute(text("ALTER TABLE leituras_iot ADD COLUMN IF NOT EXISTS reenvios INTEGER DEFAULT 0"))
        conn.execute(text("ALTER TABLE leituras_iot ADD COLUMN IF NOT EXISTS retida BOOLEAN DEFAULT FALSE"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS validacao_sessoes (
                id SERIAL PRIMARY KEY,
                descricao VARCHAR(150) NOT NULL,
                inicio TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fim TIMESTAMP,
                usuario_id INTEGER,
                leituras_queda INTEGER
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS validacao_marcacoes (
                id SERIAL PRIMARY KEY,
                sessao_id INTEGER NOT NULL,
                leitura_id VARCHAR(64) NOT NULL,
                correta BOOLEAN NOT NULL DEFAULT TRUE,
                UNIQUE (sessao_id, leitura_id)
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS validacao_digitacao (
                id SERIAL PRIMARY KEY,
                sessao_id INTEGER NOT NULL,
                codigo VARCHAR(64) NOT NULL,
                duracao_ms INTEGER NOT NULL,
                correto BOOLEAN NOT NULL DEFAULT TRUE,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS validacao_contagem (
                id SERIAL PRIMARY KEY,
                sessao_id INTEGER NOT NULL,
                produto_id INTEGER NOT NULL,
                qtd_sistema NUMERIC(10,2) NOT NULL,
                qtd_fisica NUMERIC(10,2) NOT NULL,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS validacao_questionario (
                id SERIAL PRIMARY KEY,
                sessao_id INTEGER NOT NULL,
                respondente VARCHAR(100) NOT NULL,
                respostas_sus TEXT NOT NULL,
                nota_sus NUMERIC(5,1) NOT NULL,
                utilidade_previsao INTEGER NOT NULL,
                sem_ajuda BOOLEAN NOT NULL DEFAULT FALSE,
                comentario TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
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


# --- PI IV v1.4.0: previsão por produto com linha de base e validação fora da amostra ---
HORIZONTE_PREVISAO = 3      # meses previstos
JANELA_MEDIA_MOVEL = 3      # meses usados pela linha de base
MESES_HISTORICO = 12        # meses considerados no treinamento
MIN_MESES_COM_VENDA = 4     # abaixo disso o produto é tratado como "histórico insuficiente"


def _mae(reais, previstos) -> float:
    r = np.asarray(reais, dtype=float); p = np.asarray(previstos, dtype=float)
    return float(np.mean(np.abs(r - p))) if len(r) else 0.0


def _rmse(reais, previstos) -> float:
    r = np.asarray(reais, dtype=float); p = np.asarray(previstos, dtype=float)
    return float(np.sqrt(np.mean((r - p) ** 2))) if len(r) else 0.0


def _prever_regressao(serie, passos):
    """Ajusta regressão linear (índice do mês -> quantidade) e projeta `passos` meses."""
    n = len(serie)
    X = np.arange(n).reshape(-1, 1)
    modelo = LinearRegression().fit(X, np.asarray(serie, dtype=float))
    futuro = modelo.predict(np.arange(n, n + passos).reshape(-1, 1))
    return [max(0.0, float(v)) for v in futuro], modelo


def _prever_media_movel(serie, passos, janela=JANELA_MEDIA_MOVEL):
    """Linha de base: média dos últimos `janela` meses, repetida em todo o horizonte."""
    base = float(np.mean(serie[-janela:])) if len(serie) else 0.0
    return [base] * passos


def avaliar_serie(serie, horizonte=HORIZONTE_PREVISAO, janela=JANELA_MEDIA_MOVEL) -> dict:
    """
    Avalia uma série mensal (meses sem venda = 0) reservando os `horizonte` meses
    finais como teste. Compara regressão linear e média móvel por MAE/RMSE e,
    em seguida, gera a previsão futura com o método de menor MAE treinado na série completa.
    """
    serie = [float(v) for v in serie]
    if len(serie) < horizonte + janela:
        return {"avaliavel": False}
    treino, teste = serie[:-horizonte], serie[-horizonte:]
    prev_rl, modelo = _prever_regressao(treino, horizonte)
    prev_mm = _prever_media_movel(treino, horizonte, janela)
    mae_rl, mae_mm = _mae(teste, prev_rl), _mae(teste, prev_mm)
    rmse_rl, rmse_mm = _rmse(teste, prev_rl), _rmse(teste, prev_mm)
    r2_treino = float(modelo.score(np.arange(len(treino)).reshape(-1, 1), treino)) if np.var(treino) > 0 else 0.0
    vencedor = "Regressão linear" if mae_rl < mae_mm else "Média móvel"
    if vencedor == "Regressão linear":
        futuro, _ = _prever_regressao(serie, horizonte)
    else:
        futuro = _prever_media_movel(serie, horizonte, janela)
    return {
        "avaliavel": True,
        "teste_real": teste,
        "teste_rl": [round(v, 2) for v in prev_rl],
        "teste_mm": [round(v, 2) for v in prev_mm],
        "mae_rl": round(mae_rl, 2), "mae_mm": round(mae_mm, 2),
        "rmse_rl": round(rmse_rl, 2), "rmse_mm": round(rmse_mm, 2),
        "r2_treino": round(r2_treino, 3),
        "vencedor": vencedor,
        "previsao": [round(v, 1) for v in futuro],
    }


def montar_series_mensais(linhas, meses):
    """Converte linhas (chave, mes, qtd) em séries alinhadas aos `meses`, com zero nos meses sem venda."""
    idx = {m: i for i, m in enumerate(meses)}
    series = {}
    for chave, mes, qtd in linhas:
        if mes not in idx:
            continue
        series.setdefault(chave, [0.0] * len(meses))[idx[mes]] += float(qtd or 0)
    return series


def prever_demanda_por_produto(conn, empresa_id: int | None = None) -> dict:
    """
    Previsão por produto (cor e tamanho como itens distintos). Produtos com histórico
    insuficiente são agregados pelo produto-base (nome); produtos sem nenhum histórico
    recebem a média dos produtos avaliáveis, com aviso. Produtos sem venda há 90 dias
    e com saldo zero são considerados descontinuados e excluídos.
    """
    vazio = {"itens": [], "resumo": None, "labels_futuros": []}
    try:
        hoje = date.today().replace(day=1)
        meses = [(hoje - relativedelta(months=MESES_HISTORICO - i)) for i in range(MESES_HISTORICO)]
        meses_str = [m.strftime("%Y-%m") for m in meses]
        filtro = "AND p.empresa_id = :empresa_id" if empresa_id else ""
        params = {"inicio": meses[0], "fim": hoje}
        if empresa_id:
            params["empresa_id"] = empresa_id
        produtos = conn.execute(text(f"""
            SELECT p.id, p.nome, p.cor, p.tamanho, p.quantidade,
                   MAX(v.data_venda) AS ultima_venda
            FROM produtos p LEFT JOIN vendas v ON v.produto_id = p.id
            WHERE 1=1 {filtro}
            GROUP BY p.id, p.nome, p.cor, p.tamanho, p.quantidade
        """), params).fetchall()
        linhas = conn.execute(text(f"""
            SELECT v.produto_id, v.data_venda, v.quantidade
            FROM vendas v JOIN produtos p ON p.id = v.produto_id
            WHERE v.data_venda >= :inicio AND v.data_venda < :fim {filtro}
        """), params).fetchall()
    except Exception:
        return vazio

    def _mes(d):
        d = d if isinstance(d, (datetime, date)) else datetime.fromisoformat(str(d))
        return d.strftime("%Y-%m")

    series = montar_series_mensais([(l.produto_id, _mes(l.data_venda), l.quantidade) for l in linhas], meses_str)
    limite_desc = datetime.now() - relativedelta(days=90)
    itens, base_nome = [], {}
    for p in produtos:
        ult = p.ultima_venda
        if ult is not None and not isinstance(ult, datetime):
            ult = datetime.fromisoformat(str(ult))
        if float(p.quantidade or 0) <= 0 and (ult is None or ult < limite_desc):
            continue  # descontinuado
        serie = series.get(p.id, [0.0] * MESES_HISTORICO)
        rotulo = " ".join(x for x in [p.nome, p.cor or "", p.tamanho or ""] if x).strip()
        meses_com_venda = sum(1 for v in serie if v > 0)
        base = base_nome.setdefault(p.nome, [0.0] * MESES_HISTORICO)
        for i, v in enumerate(serie):
            base[i] += v
        itens.append({"id": p.id, "produto": rotulo, "nome_base": p.nome, "serie": serie,
                      "meses_com_venda": meses_com_venda, "estoque": float(p.quantidade or 0)})

    avaliados = []
    for it in itens:
        if it["meses_com_venda"] >= MIN_MESES_COM_VENDA:
            res = avaliar_serie(it["serie"]); it["origem"] = "Produto"
        elif sum(1 for v in base_nome[it["nome_base"]] if v > 0) >= MIN_MESES_COM_VENDA:
            res = avaliar_serie(base_nome[it["nome_base"]]); it["origem"] = "Produto-base (agregado)"
        else:
            res = {"avaliavel": False}; it["origem"] = "Sem histórico"
        it.update(res)
        if res.get("avaliavel") and it["origem"] == "Produto":
            avaliados.append(it)

    media_geral = [round(float(np.mean([a["previsao"][h] for a in avaliados])), 1)
                   for h in range(HORIZONTE_PREVISAO)] if avaliados else [0.0] * HORIZONTE_PREVISAO
    for it in itens:
        if not it.get("avaliavel"):
            it["previsao"] = media_geral
            it["vencedor"] = "Média geral (aviso: produto sem histórico suficiente)"
        it["total_previsto"] = round(sum(it["previsao"]), 1)
        it["sugestao_compra"] = max(0.0, round(it["total_previsto"] - it["estoque"], 1))
        it.pop("serie", None)

    resumo = None
    if avaliados:
        mae_rl = float(np.mean([a["mae_rl"] for a in avaliados]))
        mae_mm = float(np.mean([a["mae_mm"] for a in avaliados]))
        resumo = {
            "produtos_avaliados": len(avaliados),
            "mae_rl": round(mae_rl, 2), "mae_mm": round(mae_mm, 2),
            "rmse_rl": round(float(np.mean([a["rmse_rl"] for a in avaliados])), 2),
            "rmse_mm": round(float(np.mean([a["rmse_mm"] for a in avaliados])), 2),
            "rl_vence": sum(1 for a in avaliados if a["vencedor"] == "Regressão linear"),
            "aceito": mae_rl < mae_mm,
        }
    itens.sort(key=lambda x: x["total_previsto"], reverse=True)
    labels = [(hoje + relativedelta(months=i)).strftime("%m/%Y") for i in range(HORIZONTE_PREVISAO)]
    return {"itens": itens, "resumo": resumo, "labels_futuros": labels}


@app.get("/api/previsao/produtos")
async def api_previsao_produtos(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"erro": "Não autenticado"})
    with engine.connect() as conn:
        return JSONResponse(prever_demanda_por_produto(conn))


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

@app.get("/meu-perfil")
async def meu_perfil(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, username, nome_completo, cpf, perfil, session_id FROM usuarios WHERE id = :id"),
            {"id": user.id}
        ).fetchone()
    return templates.TemplateResponse(request, "meu_perfil.html", {"user": user, "dados": row})


@app.post("/meu-perfil")
async def meu_perfil_salvar(
    request: Request,
    nome_completo: str = Form(""),
    cpf: str = Form(""),
    senha_atual: str = Form(""),
    nova_senha: str = Form(""),
    confirmar_senha: str = Form("")):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    erros = []
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, username, nome_completo, cpf, perfil, password FROM usuarios WHERE id = :id"),
            {"id": user.id}
        ).fetchone()
        # Atualizar dados básicos
        novo_nome = nome_completo.strip() or row.nome_completo
        novo_cpf  = cpf.strip() or row.cpf
        # Troca de senha (opcional)
        novo_hash = row.password
        if nova_senha:
            import hashlib
            if not senha_atual:
                erros.append("Informe a senha atual para alterar a senha.")
            elif hashlib.sha256(senha_atual.encode()).hexdigest() != row.password:
                erros.append("Senha atual incorreta.")
            elif nova_senha != confirmar_senha:
                erros.append("Nova senha e confirmação não coincidem.")
            else:
                novo_hash = hashlib.sha256(nova_senha.encode()).hexdigest()
        if erros:
            return templates.TemplateResponse(request, "meu_perfil.html", {"user": user, "dados": row, "erros": erros})
        conn.execute(
            text("UPDATE usuarios SET nome_completo=:nome, cpf=:cpf, password=:pwd WHERE id=:id"),
            {"nome": novo_nome, "cpf": novo_cpf, "pwd": novo_hash, "id": user.id}
        )
        conn.commit()
        registrar_log(user.id, user.username, "EDITAR_PERFIL", "Usuário atualizou seu perfil")
    return RedirectResponse(url="/meu-perfil?ok=1", status_code=303)


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


# --- ANALYTICS / ML AVANÇADO ---
@app.get("/analytics", response_class=HTMLResponse)
async def analytics(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:

        # 1. Previsão de demanda (ML já existente)
        ml_previsao = prever_demanda(conn)
        ml_produto = prever_demanda_por_produto(conn)

        # 2. Vendas diárias dos últimos 30 dias
        try:
            vendas_diarias = conn.execute(text("""
                SELECT
                    DATE(data_venda) AS dia,
                    TO_CHAR(DATE(data_venda), 'DD/MM') AS dia_label,
                    COUNT(DISTINCT grupo_venda) AS num_vendas,
                    COALESCE(SUM(total), 0) AS faturamento
                FROM vendas
                WHERE data_venda >= CURRENT_DATE - INTERVAL '29 days'
                GROUP BY DATE(data_venda)
                ORDER BY dia
            """)).fetchall()
            vd_labels   = [r.dia_label   for r in vendas_diarias]
            vd_vendas   = [int(r.num_vendas)     for r in vendas_diarias]
            vd_fatura   = [float(r.faturamento)  for r in vendas_diarias]
        except Exception:
            vd_labels = vd_vendas = vd_fatura = []

        # 3. Distribuição de vendas por dia da semana
        try:
            vendas_semana = conn.execute(text("""
                SELECT
                    EXTRACT(DOW FROM data_venda)::int AS dow,
                    COALESCE(SUM(total), 0) AS faturamento,
                    COUNT(*) AS num_vendas
                FROM vendas
                WHERE data_venda >= CURRENT_DATE - INTERVAL '90 days'
                GROUP BY dow
                ORDER BY dow
            """)).fetchall()
            dias_nomes = ["Dom","Seg","Ter","Qua","Qui","Sex","Sáb"]
            semana_labels = dias_nomes
            semana_fat_map = {r.dow: float(r.faturamento) for r in vendas_semana}
            semana_fat = [semana_fat_map.get(i, 0) for i in range(7)]
            semana_qtd_map = {r.dow: int(r.num_vendas) for r in vendas_semana}
            semana_qtd = [semana_qtd_map.get(i, 0) for i in range(7)]
        except Exception:
            semana_labels = ["Dom","Seg","Ter","Qua","Qui","Sex","Sáb"]
            semana_fat = semana_qtd = [0]*7

        # 4. Top 10 produtos mais vendidos (últimos 6 meses) com faturamento
        try:
            top10 = conn.execute(text("""
                SELECT p.nome, p.cor, p.tamanho,
                       SUM(v.quantidade) AS qtd_vendida,
                       COALESCE(SUM(v.total), 0) AS faturamento
                FROM vendas v
                JOIN produtos p ON v.produto_id = p.id
                WHERE v.data_venda >= CURRENT_DATE - INTERVAL '180 days'
                GROUP BY p.nome, p.cor, p.tamanho
                ORDER BY faturamento DESC
                LIMIT 10
            """)).fetchall()
            top10_labels = [f"{r.nome} {r.cor or ''} {r.tamanho or ''}".strip() for r in top10]
            top10_qtd    = [float(r.qtd_vendida or 0) for r in top10]
            top10_fat    = [float(r.faturamento or 0) for r in top10]
        except Exception:
            top10_labels = top10_qtd = top10_fat = []

        # 5. Evolução mensal de faturamento (12 meses)
        try:
            fat_mensal = conn.execute(text("""
                SELECT
                    TO_CHAR(DATE_TRUNC('month', data_venda), 'MM/YYYY') AS mes,
                    DATE_TRUNC('month', data_venda) AS mes_dt,
                    COALESCE(SUM(total), 0) AS faturamento,
                    COUNT(DISTINCT grupo_venda) AS num_vendas
                FROM vendas
                WHERE data_venda >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '11 months'
                GROUP BY mes, mes_dt
                ORDER BY mes_dt
            """)).fetchall()
            fat_labels  = [r.mes          for r in fat_mensal]
            fat_valores = [float(r.faturamento)  for r in fat_mensal]
            fat_qtd     = [int(r.num_vendas)      for r in fat_mensal]
        except Exception:
            fat_labels = fat_valores = fat_qtd = []

        # 6. Ticket médio mensal
        try:
            ticket_mensal = [
                round(fat_valores[i] / fat_qtd[i], 2) if fat_qtd[i] > 0 else 0
                for i in range(len(fat_labels))
            ]
        except Exception:
            ticket_mensal = []

        # 7. Estoque vs. giro (produtos com maior e menor giro)
        try:
            giro = conn.execute(text("""
                SELECT p.nome,
                       p.quantidade AS estoque_atual,
                       COALESCE(SUM(v.quantidade), 0) AS vendido_6m,
                       CASE WHEN p.quantidade > 0
                            THEN ROUND(COALESCE(SUM(v.quantidade),0) / p.quantidade, 2)
                            ELSE 0 END AS giro
                FROM produtos p
                LEFT JOIN vendas v ON v.produto_id = p.id
                    AND v.data_venda >= CURRENT_DATE - INTERVAL '180 days'
                GROUP BY p.id, p.nome, p.quantidade
                ORDER BY giro DESC
                LIMIT 15
            """)).fetchall()
            giro_labels   = [r.nome                    for r in giro]
            giro_estoque  = [float(r.estoque_atual or 0) for r in giro]
            giro_vendido  = [float(r.vendido_6m or 0)   for r in giro]
            giro_indices  = [float(r.giro or 0)          for r in giro]
        except Exception:
            giro_labels = giro_estoque = giro_vendido = giro_indices = []

        # 8. KPIs analíticos
        try:
            kpi = conn.execute(text("""
                SELECT
                    COUNT(DISTINCT grupo_venda)                          AS total_transacoes,
                    COALESCE(SUM(total), 0)                              AS faturamento_total,
                    COALESCE(AVG(total), 0)                              AS ticket_medio_geral,
                    COALESCE(SUM(quantidade), 0)                         AS unidades_vendidas,
                    COUNT(DISTINCT produto_id)                           AS produtos_distintos
                FROM vendas
                WHERE data_venda >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '11 months'
            """)).fetchone()
            kpis = {
                "transacoes":        int(kpi.total_transacoes or 0),
                "faturamento":       float(kpi.faturamento_total or 0),
                "ticket_medio":      round(float(kpi.ticket_medio_geral or 0), 2),
                "unidades_vendidas": float(kpi.unidades_vendidas or 0),
                "produtos_distintos": int(kpi.produtos_distintos or 0),
            }
        except Exception:
            kpis = {"transacoes": 0, "faturamento": 0, "ticket_medio": 0, "unidades_vendidas": 0, "produtos_distintos": 0}

    return templates.TemplateResponse(request, "analytics.html", {
        "user": user,
        "ml_previsao": ml_previsao,
        "ml_produto": ml_produto,
        "vd_labels": vd_labels,
        "vd_vendas": vd_vendas,
        "vd_fatura": vd_fatura,
        "semana_labels": semana_labels,
        "semana_fat": semana_fat,
        "semana_qtd": semana_qtd,
        "top10_labels": top10_labels,
        "top10_qtd": top10_qtd,
        "top10_fat": top10_fat,
        "fat_labels": fat_labels,
        "fat_valores": fat_valores,
        "fat_qtd": fat_qtd,
        "ticket_mensal": ticket_mensal,
        "giro_labels": giro_labels,
        "giro_estoque": giro_estoque,
        "giro_vendido": giro_vendido,
        "giro_indices": giro_indices,
        "kpis": kpis,
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

# --- IMPORTAÇÃO DE PRODUTOS ---
@app.get("/produtos/importar", response_class=HTMLResponse)
async def importar_produtos_get(request: Request):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "importar_produtos.html", {"user": user})


@app.post("/produtos/importar")
async def importar_produtos_post(request: Request, arquivo: UploadFile = File(...)):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/login", status_code=303)

    conteudo = await arquivo.read()
    erros = []
    inseridos = 0
    ignorados = 0

    try:
        if arquivo.filename.lower().endswith(".csv"):
            df = pd.read_csv(BytesIO(conteudo), dtype=str)
        else:
            df = pd.read_excel(BytesIO(conteudo), dtype=str)

        # Normalizar colunas
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
        colunas_req = {"nome"}
        if not colunas_req.issubset(set(df.columns)):
            return templates.TemplateResponse(request, "importar_produtos.html", {
                "user": user,
                "erro_global": f"Coluna obrigatória 'nome' não encontrada. Colunas encontradas: {list(df.columns)}"
            })

        def _val(row, col):
            v = str(row.get(col, "")).strip()
            return "" if v.lower() == "nan" else v

        with engine.connect() as conn:
            # Mapas para vincular fornecedor (por CNPJ ou nome) e empresa (por nome fantasia)
            forn_rows = conn.execute(text("SELECT id, nome, cnpj FROM fornecedores")).fetchall()
            forn_por_cnpj = {re.sub(r"\D", "", f.cnpj or ""): f.id for f in forn_rows if f.cnpj}
            forn_por_nome = {(f.nome or "").strip().lower(): f.id for f in forn_rows}
            emp_rows = conn.execute(text("SELECT id, nome_fantasia FROM empresas ORDER BY id")).fetchall()
            emp_por_nome = {(e.nome_fantasia or "").strip().lower(): e.id for e in emp_rows}
            # Sem coluna 'empresa', o produto vai para a primeira empresa cadastrada,
            # garantindo que apareça no dashboard, nas etiquetas e na exportação
            empresa_padrao = emp_rows[0].id if emp_rows else None

            for i, row in df.iterrows():
                nome = str(row.get("nome", "")).strip()
                if not nome or nome == "nan":
                    ignorados += 1
                    continue
                try:
                    # Vincular fornecedor: CNPJ tem prioridade sobre o nome
                    fornecedor_id = None
                    f_cnpj = re.sub(r"\D", "", _val(row, "fornecedor_cnpj"))
                    f_nome = _val(row, "fornecedor")
                    if f_cnpj or f_nome:
                        fornecedor_id = forn_por_cnpj.get(f_cnpj) if f_cnpj else None
                        if fornecedor_id is None and f_nome:
                            fornecedor_id = forn_por_nome.get(f_nome.lower())
                        if fornecedor_id is None:
                            ignorados += 1
                            erros.append(f"Linha {i+2}: fornecedor '{f_nome or f_cnpj}' não cadastrado — importe os fornecedores antes (ignorado)")
                            continue
                    # Vincular empresa
                    e_nome = _val(row, "empresa")
                    if e_nome:
                        empresa_id = emp_por_nome.get(e_nome.lower())
                        if empresa_id is None:
                            ignorados += 1
                            erros.append(f"Linha {i+2}: empresa '{e_nome}' não cadastrada (ignorado)")
                            continue
                    else:
                        empresa_id = empresa_padrao
                    # Verificar duplicata por nome
                    existe = conn.execute(
                        text("SELECT id FROM produtos WHERE LOWER(nome) = LOWER(:nome)"),
                        {"nome": nome}
                    ).fetchone()
                    if existe:
                        ignorados += 1
                        erros.append(f"Linha {i+2}: produto '{nome}' já existe (ignorado)")
                        continue
                    quantidade = float(str(row.get("quantidade", "0")).replace(",", ".")) if str(row.get("quantidade", "")) not in ["", "nan"] else 0
                    preco = float(str(row.get("preco", "0")).replace(",", ".").replace("R$", "").strip()) if str(row.get("preco", "")) not in ["", "nan"] else 0
                    cor = str(row.get("cor", "")).strip() if str(row.get("cor", "")) != "nan" else ""
                    tamanho = str(row.get("tamanho", "")).strip() if str(row.get("tamanho", "")) != "nan" else ""
                    conn.execute(text("""
                        INSERT INTO produtos (nome, quantidade, preco, cor, tamanho, empresa_id, fornecedor_id)
                        VALUES (:nome, :quantidade, :preco, :cor, :tamanho, :empresa_id, :fornecedor_id)
                    """), {"nome": nome, "quantidade": quantidade, "preco": preco, "cor": cor, "tamanho": tamanho,
                           "empresa_id": empresa_id, "fornecedor_id": fornecedor_id})
                    inseridos += 1
                except Exception as e:
                    erros.append(f"Linha {i+2}: {str(e)[:80]}")
            conn.commit()

        registrar_log(user.id, user.username, "IMPORTAR_PRODUTOS", f"{inseridos} produtos importados, {ignorados} ignorados, {len(erros)} erros")

    except Exception as e:
        return templates.TemplateResponse(request, "importar_produtos.html", {
            "user": user, "erro_global": f"Erro ao ler arquivo: {str(e)}"
        })

    return templates.TemplateResponse(request, "importar_produtos.html", {
        "user": user,
        "inseridos": inseridos,
        "ignorados": ignorados,
        "erros": erros
    })


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
            text("SELECT id, username, nome_completo, cpf, perfil FROM usuarios WHERE username != 'admin' ORDER BY id")
        ).fetchall()

    return templates.TemplateResponse(request, "usuarios.html", {
        "user": user,
        "usuarios": usuarios
    })

@app.post("/usuarios/novo")
async def novo_usuario(
    request: Request,
    username: str = Form(...),
    nome_completo: str = Form(...),
    cpf: str = Form(""),
    password: str = Form(...),
    perfil: str = Form(...)):

    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/", status_code=303)

    # CPF é opcional: valida somente quando informado
    cpf = (cpf or "").strip()
    if cpf:
        if not validar_cpf(cpf):
            return RedirectResponse(url="/usuarios?erro=CPF+inv%C3%A1lido", status_code=303)
        cpf_formatado = formatar_cpf(cpf)
    else:
        cpf_formatado = None

    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO usuarios (username, nome_completo, cpf, password, perfil)
                VALUES (:username, :nome_completo, :cpf, :password, :perfil)
            """), {
                "username": username,
                "nome_completo": nome_completo,
                "cpf": cpf_formatado,
                "password": hash_password(password),
                "perfil": perfil
            })
            conn.commit()
    except IntegrityError:
        return RedirectResponse(url="/usuarios?erro=Login+j%C3%A1+em+uso", status_code=303)

    registrar_log(user.id, user.username, "CADASTRO_USUARIO", f"Novo usuário: {username} ({perfil})")
    return RedirectResponse(url="/usuarios?msg=Usu%C3%A1rio+cadastrado+com+sucesso", status_code=303)

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
    nome_completo: str = Form(...),
    cpf: str = Form(""),
    perfil: str = Form(...),
    password: str = Form(None)):

    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/", status_code=303)

    # CPF é opcional: valida somente quando informado
    cpf = (cpf or "").strip()
    if cpf:
        if not validar_cpf(cpf):
            return RedirectResponse(url="/usuarios?erro=CPF+inv%C3%A1lido", status_code=303)
        cpf_formatado = formatar_cpf(cpf)
    else:
        cpf_formatado = None

    with engine.connect() as conn:
        if password:
            conn.execute(text("""
                UPDATE usuarios
                SET username = :username,
                    nome_completo = :nome_completo,
                    cpf = :cpf,
                    perfil = :perfil,
                    password = :password
                WHERE id = :id
            """), {
                "username": username,
                "nome_completo": nome_completo,
                "cpf": cpf_formatado,
                "perfil": perfil,
                "password": hash_password(password),
                "id": user_id
            })
        else:
            conn.execute(text("""
                UPDATE usuarios
                SET username = :username,
                    nome_completo = :nome_completo,
                    cpf = :cpf,
                    perfil = :perfil
                WHERE id = :id
            """), {
                "username": username,
                "nome_completo": nome_completo,
                "cpf": cpf_formatado,
                "perfil": perfil,
                "id": user_id
            })
        conn.commit()

    registrar_log(user.id, user.username, "EDICAO_USUARIO", f"Usuário editado: {username}")
    return RedirectResponse(url="/usuarios?msg=Usu%C3%A1rio+atualizado+com+sucesso", status_code=303)


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
    email: str = Form(None),
    situacao_cadastral: str = Form(None)):

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE fornecedores
            SET nome = :nome,
                cnpj = :cnpj,
                telefone = :telefone,
                email = :email,
                situacao_cadastral = :situacao_cadastral
            WHERE id = :id
        """), {
            "nome": nome,
            "cnpj": cnpj,
            "telefone": telefone,
            "email": email,
            "situacao_cadastral": (situacao_cadastral or "").upper() or None,
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
    email: str = Form(None),
    situacao_cadastral: str = Form(None)):

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO fornecedores (nome, cnpj, telefone, email, situacao_cadastral)
            VALUES (:nome, :cnpj, :telefone, :email, :situacao_cadastral)
        """), {
            "nome": nome,
            "cnpj": cnpj,
            "telefone": telefone,
            "email": email,
            "situacao_cadastral": (situacao_cadastral or "").upper() or None,
        })
        conn.commit()

    return RedirectResponse(url="/fornecedores?msg=fornecedor_criado", status_code=303)

# --- IMPORTAÇÃO DE FORNECEDORES ---
@app.get("/fornecedores/importar", response_class=HTMLResponse)
async def importar_fornecedores_get(request: Request):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "importar_fornecedores.html", {"user": user})


@app.post("/fornecedores/importar")
async def importar_fornecedores_post(request: Request, arquivo: UploadFile = File(...)):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse(url="/login", status_code=303)

    conteudo = await arquivo.read()
    erros = []
    inseridos = 0
    ignorados = 0

    try:
        if arquivo.filename.lower().endswith(".csv"):
            df = pd.read_csv(BytesIO(conteudo), dtype=str)
        else:
            df = pd.read_excel(BytesIO(conteudo), dtype=str)

        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
        if "nome" not in df.columns:
            return templates.TemplateResponse(request, "importar_fornecedores.html", {
                "user": user,
                "erro_global": f"Coluna obrigatória 'nome' não encontrada. Colunas: {list(df.columns)}"
            })

        with engine.connect() as conn:
            for i, row in df.iterrows():
                nome = str(row.get("nome", "")).strip()
                if not nome or nome == "nan":
                    ignorados += 1
                    continue
                try:
                    cnpj = str(row.get("cnpj", "")).strip() if str(row.get("cnpj", "")) != "nan" else ""
                    # Verificar duplicata por nome ou CNPJ
                    if cnpj:
                        existe = conn.execute(
                            text("SELECT id FROM fornecedores WHERE LOWER(nome) = LOWER(:nome) OR cnpj = :cnpj"),
                            {"nome": nome, "cnpj": cnpj}
                        ).fetchone()
                    else:
                        existe = conn.execute(
                            text("SELECT id FROM fornecedores WHERE LOWER(nome) = LOWER(:nome)"),
                            {"nome": nome}
                        ).fetchone()
                    if existe:
                        ignorados += 1
                        erros.append(f"Linha {i+2}: fornecedor '{nome}' já existe (ignorado)")
                        continue
                    telefone = str(row.get("telefone", "")).strip() if str(row.get("telefone", "")) != "nan" else ""
                    email = str(row.get("email", "")).strip() if str(row.get("email", "")) != "nan" else ""
                    situacao = str(row.get("situacao_cadastral", "Ativo")).strip() if str(row.get("situacao_cadastral", "")) != "nan" else "Ativo"
                    conn.execute(text("""
                        INSERT INTO fornecedores (nome, cnpj, telefone, email, situacao_cadastral)
                        VALUES (:nome, :cnpj, :telefone, :email, :situacao)
                    """), {"nome": nome, "cnpj": cnpj, "telefone": telefone, "email": email, "situacao": situacao})
                    inseridos += 1
                except Exception as e:
                    erros.append(f"Linha {i+2}: {str(e)[:80]}")
            conn.commit()

        registrar_log(user.id, user.username, "IMPORTAR_FORNECEDORES", f"{inseridos} fornecedores importados, {ignorados} ignorados, {len(erros)} erros")

    except Exception as e:
        return templates.TemplateResponse(request, "importar_fornecedores.html", {
            "user": user, "erro_global": f"Erro ao ler arquivo: {str(e)}"
        })

    return templates.TemplateResponse(request, "importar_fornecedores.html", {
        "user": user,
        "inseridos": inseridos,
        "ignorados": ignorados,
        "erros": erros
    })


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
    email: str = Form(""),
    situacao_cadastral: str = Form("")):

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO empresas (nome_fantasia, razao_social, cnpj, telefone, email, situacao_cadastral)
            VALUES (:nome, :razao_social, :cnpj, :telefone, :email, :situacao_cadastral)
        """), {
            "nome": nome,
            "razao_social": razao_social,
            "cnpj": cnpj,
            "telefone": tel,
            "email": email,
            "situacao_cadastral": (situacao_cadastral or "").upper() or None
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
    email: str = Form(...),
    situacao_cadastral: str = Form("")):

    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE empresas
            SET nome_fantasia = :nome,
                cnpj = :cnpj,
                telefone = :telefone,
                email = :email,
                situacao_cadastral = :situacao_cadastral
            WHERE id = :id
        """), {
            "nome": nome,
            "cnpj": cnpj,
            "telefone": tel,
            "email": email,
            "situacao_cadastral": (situacao_cadastral or "").upper() or None,
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

    filtros = ["l.acao IN ('LOGIN', 'LOGOUT')"]
    params = {}

    if usuario:
        filtros.append("l.username = :usuario")
        params["usuario"] = usuario

    if data_inicio:
        filtros.append("DATE(l.data_evento) >= :data_inicio")
        params["data_inicio"] = data_inicio

    if data_fim:
        filtros.append("DATE(l.data_evento) <= :data_fim")
        params["data_fim"] = data_fim

    where_clause = "WHERE " + " AND ".join(filtros)

    LIMITE_HORAS = 8

    query = f"""
        WITH eventos AS (
            SELECT
                l.username,
                COALESCE(u.nome_completo, l.username) AS nome_completo,
                COALESCE(u.cpf, '') AS cpf,
                l.acao,
                l.data_evento,
                DATE(l.data_evento) AS dia,
                LEAD(l.data_evento) OVER (
                    PARTITION BY l.username, DATE(l.data_evento)
                    ORDER BY l.data_evento
                ) AS proximo_evento,
                LEAD(l.acao) OVER (
                    PARTITION BY l.username, DATE(l.data_evento)
                    ORDER BY l.data_evento
                ) AS proxima_acao
            FROM logs_sistema l
            LEFT JOIN usuarios u ON u.username = l.username
            {where_clause}
        ),
        pares AS (
            SELECT
                username,
                nome_completo,
                cpf,
                dia,
                CASE
                    WHEN acao = 'LOGIN' AND proxima_acao = 'LOGOUT'
                        THEN LEAST(
                            EXTRACT(EPOCH FROM (proximo_evento - data_evento)) / 3600,
                            {LIMITE_HORAS}
                        )
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
            nome_completo,
            cpf,
            dia,
            ROUND(SUM(horas_sessao)::numeric, 2) AS horas_trabalhadas
        FROM pares
        GROUP BY username, nome_completo, cpf, dia
        ORDER BY username, dia
    """

    with engine.connect() as conn:
        resultados = conn.execute(text(query), params).fetchall()

    total_geral = sum(float(r.horas_trabalhadas or 0) for r in resultados)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    largura, altura = A4
    MARGEM = 50
    COL_HORAS = 400

    def nova_pagina(pdf, altura):
        pdf.showPage()
        return altura - MARGEM

    def cabecalho_tabela(pdf, y):
        pdf.setFillColorRGB(0.2, 0.2, 0.2)
        pdf.rect(MARGEM, y - 4, largura - 2 * MARGEM, 18, fill=1, stroke=0)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(MARGEM + 4, y + 1, "Data de Acesso")
        pdf.drawString(COL_HORAS, y + 1, "Tempo no Sistema")
        pdf.setFillColorRGB(0, 0, 0)
        return y - 22

    # Cabeçalho do documento
    y = altura - MARGEM

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(MARGEM, y, "Relatório de Banco de Horas")
    y -= 20

    pdf.setFont("Helvetica", 9)
    pdf.setFillColorRGB(0.4, 0.4, 0.4)
    periodo_txt = f"Período: {data_inicio or 'início'} até {data_fim or 'hoje'}"
    pdf.drawString(MARGEM, y, periodo_txt)
    pdf.drawRightString(largura - MARGEM, y, f"Gerado em: {date.today().strftime('%d/%m/%Y')}")
    pdf.setFillColorRGB(0, 0, 0)
    y -= 6

    pdf.setStrokeColorRGB(0.7, 0.7, 0.7)
    pdf.line(MARGEM, y, largura - MARGEM, y)
    y -= 20

    # Seção por usuário
    usuarios_agrupados = {}
    for r in resultados:
        key = r.username
        if key not in usuarios_agrupados:
            usuarios_agrupados[key] = {
                "nome_completo": r.nome_completo,
                "cpf": r.cpf,
                "registros": []
            }
        usuarios_agrupados[key]["registros"].append(r)

    for username_key, dados in usuarios_agrupados.items():
        nome = dados["nome_completo"] or username_key
        cpf_fmt = dados["cpf"] or "Não informado"
        registros = dados["registros"]
        total_usuario = sum(float(r.horas_trabalhadas or 0) for r in registros)

        if y < 120:
            y = nova_pagina(pdf, altura)

        # Bloco de identificação do usuário
        pdf.setFillColorRGB(0.93, 0.96, 1.0)
        pdf.rect(MARGEM, y - 6, largura - 2 * MARGEM, 40, fill=1, stroke=0)
        pdf.setFillColorRGB(0, 0, 0)

        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(MARGEM + 8, y + 26, nome)

        pdf.setFont("Helvetica", 9)
        pdf.drawString(MARGEM + 8, y + 10, f"CPF: {cpf_fmt}")
        pdf.drawRightString(largura - MARGEM - 8, y + 10,
                            f"Total: {formatar_horas_minutos(total_usuario)}")

        y -= 50

        y = cabecalho_tabela(pdf, y)

        pdf.setFont("Helvetica", 9)
        for i, reg in enumerate(registros):
            if y < 50:
                y = nova_pagina(pdf, altura)
                y = cabecalho_tabela(pdf, y)
                pdf.setFont("Helvetica", 9)

            if i % 2 == 0:
                pdf.setFillColorRGB(0.97, 0.97, 0.97)
                pdf.rect(MARGEM, y - 4, largura - 2 * MARGEM, 16, fill=1, stroke=0)
                pdf.setFillColorRGB(0, 0, 0)

            data_fmt = reg.dia.strftime("%d/%m/%Y") if hasattr(reg.dia, "strftime") else str(reg.dia)
            pdf.drawString(MARGEM + 4, y, data_fmt)
            pdf.drawString(COL_HORAS, y, formatar_horas_minutos(reg.horas_trabalhadas))
            y -= 18

        y -= 16

    # Rodapé com total geral
    if y < 60:
        y = nova_pagina(pdf, altura)

    pdf.setStrokeColorRGB(0.3, 0.3, 0.3)
    pdf.line(MARGEM, y, largura - MARGEM, y)
    y -= 16

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(MARGEM, y, "TOTAL GERAL DE HORAS:")
    pdf.drawString(COL_HORAS, y, formatar_horas_minutos(total_geral))

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


# ─────────────────────────────────────────────────────────────
#  NF-e QR Code Reader
# ─────────────────────────────────────────────────────────────

# Token store em memória (UUID → dados parseados da NF-e)
nfe_cache: dict = {}


def gerar_qrcode_bytes(conteudo: str, size: int = 200) -> bytes:
    """Retorna PNG do QR Code em bytes."""
    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(conteudo)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


def gerar_barcode_bytes(codigo: str) -> bytes:
    """Retorna PNG do Code-128 em bytes."""
    CODE128 = barcode.get_barcode_class("code128")
    buf = BytesIO()
    CODE128(codigo, writer=ImageWriter()).write(buf)
    buf.seek(0)
    return buf.getvalue()


def _texto_limpo(tag) -> str:
    return tag.get_text(separator=" ", strip=True) if tag else ""



def chave_acesso_para_url(chave: str) -> str:
    """
    Converte chave de acesso NF-e (44 dígitos) em URL de consulta Sefaz.
    A chave contém: cUF(2) + AAMM(4) + CNPJ(14) + mod(2) + série(3) + nNF(9) + ...
    Monta a URL do portal nacional ou do estado emissor.
    """
    chave = re.sub(r"\D", "", chave)
    if len(chave) != 44:
        raise ValueError(f"Chave de acesso inválida: deve ter 44 dígitos, recebeu {len(chave)}.")

    cUF = chave[:2]

    # Mapa de cUF → domínio do portal Sefaz estadual
    SEFAZ_URLS = {
        "12": "https://www.sefaz.ac.gov.br/nfce/consulta",        # AC
        "27": "https://www.sefaz.al.gov.br/nfce/consulta",        # AL
        "16": "https://www.sefaz.ap.gov.br/nfce/consulta",        # AP
        "13": "https://www.sefaz.am.gov.br/nfce/consulta",        # AM
        "29": "https://www.sefaz.ba.gov.br/nfce/consulta",        # BA
        "23": "https://www.sefaz.ce.gov.br/nfce/consulta",        # CE
        "53": "https://www.sefaz.df.gov.br/nfce/consulta",        # DF
        "32": "https://www.sefaz.es.gov.br/nfce/consulta",        # ES
        "52": "https://www.sefaz.go.gov.br/nfce/consulta",        # GO
        "21": "https://www.sefaz.ma.gov.br/nfce/consulta",        # MA
        "51": "https://www.sefaz.mt.gov.br/nfce/consulta",        # MT
        "50": "https://www.sefaz.ms.gov.br/nfce/consulta",        # MS
        "31": "https://www.nfce.fazenda.mg.gov.br/portalnfce",    # MG
        "15": "https://www.sefa.pa.gov.br/nfce/consulta",         # PA
        "25": "https://www.sefaz.pb.gov.br/nfce/consulta",        # PB
        "41": "https://www.nfce.fazenda.pr.gov.br/portalnfce",    # PR
        "26": "https://nfce.sefaz.pe.gov.br/nfce-web/consultarNFCe", # PE
        "22": "https://www.sefaz.pi.gov.br/nfce/consulta",        # PI
        "33": "https://www.nfce.fazenda.rj.gov.br/consulta",      # RJ
        "24": "https://www.set.rn.gov.br/nfce/consulta",          # RN
        "43": "https://www.nfe.se.rs.gov.br/nfce/consulta",       # RS
        "11": "https://www.nfe.ro.gov.br/nfce/consulta",          # RO
        "14": "https://www.sefaz.rr.gov.br/nfce/consulta",        # RR
        "42": "https://www.nfe.sef.sc.gov.br/nfce/consulta",      # SC
        "35": "https://www.nfce.fazenda.sp.gov.br/consulta",      # SP
        "28": "https://www.nfce.se.gov.br/consulta",              # SE
        "17": "https://www.sefaz.to.gov.br/nfce/consulta",        # TO
    }

    base = SEFAZ_URLS.get(cUF)

    if base:
        return f"{base}?p={chave}|2|1|1|"

    # Fallback: portal nacional NF-e (modelo 55)
    return f"https://www.nfe.fazenda.gov.br/portal/consultaRecaptcha.aspx?tipoConsulta=completa&tipoConteudo=7PhJ+gAVw2g=&nfe={chave}"


def extrair_dados_chave(chave: str) -> dict:
    """
    Extrai CNPJ do emitente diretamente da chave de acesso (posições 6-19)
    e consulta a BrasilAPI para obter razão social.
    Retorna dict com fornecedor e itens=[].
    """
    chave = re.sub(r"\D", "", chave)
    if len(chave) != 44:
        raise ValueError(f"Chave de acesso inválida: {len(chave)} dígitos (esperado 44).")

    cnpj = chave[6:20]

    # Consultar BrasilAPI (gratuita, sem autenticação)
    razao_social = ""
    try:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            r = client.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}")
            if r.status_code == 200:
                data = r.json()
                razao_social = data.get("razao_social") or data.get("nome_fantasia") or ""
    except Exception:
        pass

    return {
        "fornecedor": {"cnpj": cnpj, "razao_social": razao_social},
        "itens": [],
        "chave": chave,
        "aviso": "" if razao_social else "Razão social não encontrada — confirme manualmente.",
    }


def url_para_chave(url: str) -> str:
    """
    Tenta extrair a chave de 44 dígitos de uma URL Sefaz.
    Muitos QR Codes contêm a chave como parâmetro ?p=CHAVE|... ou na query string.
    """
    # Padrão comum: ?p=44digitos| ou &chNFe=44digitos
    m = re.search(r'[?&][pP]=(\d{44})', url)
    if m:
        return m.group(1)
    m = re.search(r'chNFe=(\d{44})', url)
    if m:
        return m.group(1)
    m = re.search(r'nfe=(\d{44})', url, re.I)
    if m:
        return m.group(1)
    # Busca genérica: sequência de 44 dígitos na URL
    m = re.search(r'(\d{44})', url)
    if m:
        return m.group(1)
    return ""

def consultar_sefaz_url(url: str) -> dict:
    """
    Faz GET na URL da NF-e e extrai emitente + itens via BeautifulSoup.
    Usado como complemento quando temos a URL completa (QR Code).
    Se o scraping falhar para o emitente, extrai CNPJ da própria URL/chave.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }
    try:
        with httpx.Client(follow_redirects=True, timeout=15) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        raise ValueError(f"Erro ao acessar URL Sefaz: {e}")

    soup = BeautifulSoup(html, "lxml")

    # ── Emitente ──────────────────────────────────────────────
    cnpj = ""
    razao_social = ""

    for td in soup.find_all("td"):
        txt = td.get_text(strip=True)
        if "CNPJ" in txt and not cnpj:
            nxt = td.find_next_sibling("td")
            if nxt:
                cnpj = re.sub(r"\D", "", nxt.get_text(strip=True))
        if any(k in txt for k in ["Razão Social", "Razao Social", "RAZÃO SOCIAL", "EMITENTE"]):
            nxt = td.find_next_sibling("td")
            if nxt and not razao_social:
                razao_social = nxt.get_text(strip=True)

    if not cnpj:
        for tag in soup.find_all(["span", "div", "p"]):
            txt = tag.get_text(strip=True)
            m = re.search(r"\d{2}[\.\s]?\d{3}[\.\s]?\d{3}[\/\s]?\d{4}[-\s]?\d{2}", txt)
            if m:
                cnpj = re.sub(r"\D", "", m.group())
                break

    if not cnpj:
        m = re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", html)
        if m:
            cnpj = re.sub(r"\D", "", m.group())

    # Se ainda sem CNPJ, tentar extrair da própria URL (chave embutida)
    if not cnpj:
        chave_url = url_para_chave(url)
        if len(chave_url) == 44:
            cnpj = chave_url[6:20]

    # Razão social via BrasilAPI se não veio no scraping
    if cnpj and not razao_social:
        try:
            with httpx.Client(follow_redirects=True, timeout=8) as client:
                r = client.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}")
                if r.status_code == 200:
                    data = r.json()
                    razao_social = data.get("razao_social") or data.get("nome_fantasia") or ""
        except Exception:
            pass

    if not razao_social:
        for tag in soup.find_all(["h4", "h3", "h2", "strong", "b"]):
            t = tag.get_text(strip=True)
            if len(t) > 5 and not any(c.isdigit() for c in t[:3]):
                razao_social = t
                break

    # ── Itens ─────────────────────────────────────────────────
    itens = []

    tabelas = soup.find_all("table")
    for tabela in tabelas:
        headers_row = tabela.find("tr")
        if not headers_row:
            continue
        ths = [th.get_text(strip=True).lower() for th in headers_row.find_all(["th", "td"])]
        if not any("desc" in h for h in ths):
            continue
        idx_desc = next((i for i, h in enumerate(ths) if "desc" in h), None)
        idx_cod  = next((i for i, h in enumerate(ths) if "cod" in h), None)
        idx_qtd  = next((i for i, h in enumerate(ths) if h in ("qtd", "qtde", "quant", "quantidade")), None)
        idx_vun  = next((i for i, h in enumerate(ths) if "unit" in h or "v.unit" in h or "vl.unit" in h), None)
        idx_vtot = next((i for i, h in enumerate(ths) if "total" in h or "v.total" in h), None)

        for tr in tabela.find_all("tr")[1:]:
            cols = tr.find_all(["td", "th"])
            if not cols:
                continue
            def col(i):
                if i is None or i >= len(cols):
                    return ""
                return cols[i].get_text(strip=True)

            desc = col(idx_desc)
            if not desc:
                continue
            cod      = col(idx_cod)
            qtd_txt  = col(idx_qtd).replace(",", ".")
            vun_txt  = col(idx_vun).replace("R$", "").replace(".", "").replace(",", ".").strip()
            vtot_txt = col(idx_vtot).replace("R$", "").replace(".", "").replace(",", ".").strip()

            try:
                qtd = float(qtd_txt) if qtd_txt else 1.0
            except ValueError:
                qtd = 1.0
            try:
                vun = float(vun_txt)
            except ValueError:
                try:
                    vun = float(vtot_txt) / qtd if qtd else 0.0
                except Exception:
                    vun = 0.0

            itens.append({
                "descricao": desc,
                "codigo": cod,
                "quantidade": qtd,
                "preco_unitario": round(vun, 2),
            })
        if itens:
            break

    if not itens:
        for row in soup.find_all(class_=re.compile(r"item", re.I)):
            tds = row.find_all(["td", "span", "div"])
            if len(tds) >= 3:
                desc    = _texto_limpo(tds[0])
                qtd_txt = _texto_limpo(tds[1]).replace(",", ".")
                val_txt = _texto_limpo(tds[-1]).replace("R$", "").replace(".", "").replace(",", ".").strip()
                try:
                    qtd = float(qtd_txt)
                except Exception:
                    qtd = 1.0
                try:
                    vun = float(val_txt)
                except Exception:
                    vun = 0.0
                itens.append({"descricao": desc, "codigo": "", "quantidade": qtd, "preco_unitario": round(vun, 2)})

    return {
        "fornecedor": {"cnpj": cnpj, "razao_social": razao_social},
        "itens": itens,
    }


# ── Rotas NF-e ────────────────────────────────────────────────

@app.get("/nfe/scanner", response_class=HTMLResponse)
async def nfe_scanner_page(request: Request):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("nfe_scanner.html", {"request": request, "user": user})


@app.post("/nfe/consultar")
async def nfe_consultar(request: Request):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return JSONResponse(status_code=403, content={"erro": "Acesso negado"})

    try:
        body = await request.json()
        url_nfe   = body.get("url", "").strip()
        chave_raw = re.sub(r"\D", "", body.get("chave", ""))
    except Exception:
        return JSONResponse(status_code=400, content={"erro": "Corpo da requisição inválido."})

    if not url_nfe and not chave_raw:
        return JSONResponse(status_code=400, content={"erro": "Informe a URL da NF-e ou a chave de acesso (44 dígitos)."})

    dados = None

    # ── Estratégia 1: chave pura (barcode/digitação manual) ──────────────────
    if chave_raw and not url_nfe:
        try:
            dados = extrair_dados_chave(chave_raw)
        except ValueError as e:
            return JSONResponse(status_code=400, content={"erro": str(e)})

    # ── Estratégia 2: URL do QR Code ─────────────────────────────────────────
    elif url_nfe:
        # Primeiro tenta extrair chave da URL para usar BrasilAPI
        chave_da_url = url_para_chave(url_nfe)
        if len(chave_da_url) == 44:
            try:
                dados = extrair_dados_chave(chave_da_url)
            except Exception:
                pass

        # Tenta scraping da página Sefaz para pegar os ITENS
        try:
            dados_sefaz = consultar_sefaz_url(url_nfe)
            if dados_sefaz["itens"]:
                if dados:
                    dados["itens"] = dados_sefaz["itens"]
                else:
                    dados = dados_sefaz
            elif dados is None:
                dados = dados_sefaz
        except Exception:
            pass  # scraping falhou — continua com dados da BrasilAPI

    if not dados or not dados["fornecedor"]["cnpj"]:
        return JSONResponse(status_code=422, content={
            "erro": "Não foi possível identificar o CNPJ do emitente. "
                    "Verifique se a chave de acesso está completa (44 dígitos) e tente novamente."
        })

    token = str(uuid.uuid4())
    nfe_cache[token] = dados

    return JSONResponse({"ok": True, "token": token})


@app.get("/nfe/confirmar", response_class=HTMLResponse)
async def nfe_confirmar_page(request: Request, token: str):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse("/", status_code=302)

    dados = nfe_cache.get(token)
    if not dados:
        return RedirectResponse("/nfe/scanner?msg=token_invalido", status_code=302)

    cnpj = re.sub(r"\D", "", dados["fornecedor"]["cnpj"])

    with engine.connect() as conn:
        # Verificar se fornecedor já existe
        forn = conn.execute(
            text("SELECT id, nome FROM fornecedores WHERE cnpj = :cnpj"),
            {"cnpj": cnpj}
        ).fetchone()
        fornecedor_existente = forn is not None  # forn.nome available if needed

        # Listar empresas para o usuário selecionar a empresa destino
        empresas = conn.execute(
            text("SELECT id, nome_fantasia FROM empresas ORDER BY nome_fantasia")
        ).fetchall()

        # Para cada item, verificar se produto já existe (busca por nome aproximado)
        empresa_padrao_id = empresas[0].id if empresas else None
        itens_com_status = []
        for item in dados["itens"]:
            prod = None
            if empresa_padrao_id:
                prod = conn.execute(
                    text("""
                        SELECT id, nome, quantidade, preco
                        FROM produtos
                        WHERE empresa_id = :eid
                          AND LOWER(nome) LIKE LOWER(:busca)
                        LIMIT 1
                    """),
                    {"eid": empresa_padrao_id, "busca": f"%{item['descricao'][:20]}%"}
                ).fetchone()

            itens_com_status.append({
                **item,
                "nome_sugerido": item["descricao"],
                "produto_existente": prod is not None,
                "produto_id": prod.id if prod else None,
            })

    dados_view = {**dados, "itens": itens_com_status}

    return templates.TemplateResponse("nfe_confirmar.html", {
        "request": request,
        "user": user,
        "token": token,
        "dados": dados_view,
        "fornecedor_existente": fornecedor_existente,
        "empresas": [{"id": e.id, "nome": e.nome_fantasia} for e in empresas],
    })


@app.post("/nfe/salvar")
async def nfe_salvar(request: Request):
    """Recebe form-encoded do nfe_confirmar.html e salva fornecedor + itens."""
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse("/", status_code=302)

    form = await request.form()

    token       = form.get("token", "")
    empresa_id  = int(form.get("empresa_id", 0))
    total_itens = int(form.get("total_itens", 0))

    dados_cache = nfe_cache.get(token)
    if not dados_cache:
        return RedirectResponse("/nfe/scanner?msg=token_invalido", status_code=302)

    # Fornecedor — pode já existir (sem campos no form) ou ser novo
    forn_cnpj        = re.sub(r"\D", "", form.get("forn_cnpj", dados_cache["fornecedor"]["cnpj"]))
    forn_razao_social = form.get("forn_razao_social", dados_cache["fornecedor"]["razao_social"])
    forn_telefone    = form.get("forn_telefone", "")
    forn_email       = form.get("forn_email", "")

    resultados = []

    with engine.connect() as conn:
        # ── Fornecedor ──────────────────────────────────────────
        forn = conn.execute(
            text("SELECT id FROM fornecedores WHERE cnpj = :cnpj"),
            {"cnpj": forn_cnpj}
        ).fetchone()

        if forn:
            fornecedor_id = forn.id
            conn.execute(
                text("UPDATE fornecedores SET nome = :nome WHERE id = :id"),
                {"nome": forn_razao_social, "id": fornecedor_id}
            )
        else:
            r = conn.execute(
                text("""
                    INSERT INTO fornecedores
                      (nome, cnpj, telefone, email, situacao_cadastral, criado_em)
                    VALUES (:nome, :cnpj, :tel, :email, 'Ativo', NOW())
                    RETURNING id
                """),
                {"nome": forn_razao_social, "cnpj": forn_cnpj,
                 "tel": forn_telefone or None, "email": forn_email or None}
            )
            fornecedor_id = r.fetchone().id

        # ── Itens ───────────────────────────────────────────────
        # Reconstituir produto_id de cada item a partir do cache
        cache_itens = dados_cache.get("itens", [])

        for i in range(total_itens):
            nome  = (form.get(f"item_nome_{i}") or "").strip()
            qtd   = float(form.get(f"item_qtd_{i}") or 1)
            preco = float(form.get(f"item_preco_{i}") or 0)

            if not nome:
                continue

            # Verificar se produto existe (re-consulta pelo nome dentro da empresa)
            prod = conn.execute(
                text("""
                    SELECT id FROM produtos
                    WHERE empresa_id = :eid
                      AND LOWER(nome) = LOWER(:nome)
                    LIMIT 1
                """),
                {"eid": empresa_id, "nome": nome}
            ).fetchone()

            if not prod and i < len(cache_itens):
                # Tenta pelo nome sugerido do cache (descrição parcial)
                desc = cache_itens[i].get("descricao", "")[:20]
                prod = conn.execute(
                    text("""
                        SELECT id FROM produtos
                        WHERE empresa_id = :eid
                          AND LOWER(nome) LIKE LOWER(:busca)
                        LIMIT 1
                    """),
                    {"eid": empresa_id, "busca": f"%{desc}%"}
                ).fetchone()

            if prod:
                conn.execute(
                    text("""
                        UPDATE produtos
                        SET quantidade = quantidade + :qtd,
                            fornecedor_id = :fid
                        WHERE id = :pid
                    """),
                    {"qtd": qtd, "fid": fornecedor_id, "pid": prod.id}
                )
                resultados.append({"acao": "estoque_atualizado", "nome": nome, "produto_id": prod.id})
            else:
                r2 = conn.execute(
                    text("""
                        INSERT INTO produtos
                          (nome, quantidade, preco, empresa_id, fornecedor_id, criado_em)
                        VALUES (:nome, :qtd, :preco, :eid, :fid, NOW())
                        RETURNING id
                    """),
                    {"nome": nome, "qtd": qtd, "preco": preco,
                     "eid": empresa_id, "fid": fornecedor_id}
                )
                novo_id = r2.fetchone().id
                resultados.append({"acao": "produto_criado", "nome": nome, "produto_id": novo_id})

        conn.commit()

    nfe_cache.pop(token, None)

    # Log
    try:
        with engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO logs_sistema (usuario_id, acao, detalhes, criado_em)
                    VALUES (:uid, 'nfe_entrada', :det, NOW())
                """),
                {"uid": user.id,
                 "det": json.dumps({"fornecedor_id": fornecedor_id, "itens": len(resultados)}, ensure_ascii=False)}
            )
            conn.commit()
    except Exception:
        pass

    acoes = len([r for r in resultados if r["acao"] == "produto_criado"])
    atualizacoes = len([r for r in resultados if r["acao"] == "estoque_atualizado"])
    return RedirectResponse(
        f"/produtos?msg=nfe_ok&criados={acoes}&atualizados={atualizacoes}",
        status_code=303
    )


# ─────────────────────────────────────────────────────────────
#  Etiquetas: QR Code + Code-128 por produto
# ─────────────────────────────────────────────────────────────

@app.get("/produtos/{produto_id}/qrcode.png")
async def produto_qrcode(request: Request, produto_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"erro": "Não autenticado"})

    with engine.connect() as conn:
        prod = conn.execute(
            text("SELECT id, nome, preco FROM produtos WHERE id = :id"),
            {"id": produto_id}
        ).fetchone()

    if not prod:
        return JSONResponse(status_code=404, content={"erro": "Produto não encontrado"})

    conteudo = json.dumps({"id": prod.id, "nome": prod.nome, "preco": float(prod.preco)}, ensure_ascii=False)
    png_bytes = gerar_qrcode_bytes(conteudo)
    return Response(content=png_bytes, media_type="image/png")


@app.get("/produtos/{produto_id}/barcode.png")
async def produto_barcode(request: Request, produto_id: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"erro": "Não autenticado"})

    with engine.connect() as conn:
        prod = conn.execute(
            text("SELECT id FROM produtos WHERE id = :id"),
            {"id": produto_id}
        ).fetchone()

    if not prod:
        return JSONResponse(status_code=404, content={"erro": "Produto não encontrado"})

    codigo = str(prod.id).zfill(8)
    png_bytes = gerar_barcode_bytes(codigo)
    return Response(content=png_bytes, media_type="image/png")


@app.get("/produtos/{produto_id}/etiqueta", response_class=HTMLResponse)
async def produto_etiqueta_page(request: Request, produto_id: int):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse("/", status_code=302)

    with engine.connect() as conn:
        prod = conn.execute(
            text("""
                SELECT p.id, p.nome, p.quantidade, p.preco, p.cor, p.tamanho,
                       e.nome_fantasia AS empresa_nome
                FROM produtos p
                JOIN empresas e ON e.id = p.empresa_id
                WHERE p.id = :id
            """),
            {"id": produto_id}
        ).fetchone()

    if not prod:
        return RedirectResponse("/produtos?erro=nao_encontrado", status_code=302)

    return templates.TemplateResponse("produto_etiqueta.html", {
        "request": request,
        "user": user,
        "produto": prod,
        "empresa_nome": prod.empresa_nome,
    })


@app.get("/produtos/{produto_id}/etiqueta/pdf")
async def produto_etiqueta_pdf(request: Request, produto_id: int):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return JSONResponse(status_code=403, content={"erro": "Acesso negado"})

    with engine.connect() as conn:
        prod = conn.execute(
            text("""
                SELECT p.id, p.nome, p.preco, p.cor, p.tamanho,
                       e.nome_fantasia AS empresa_nome
                FROM produtos p
                JOIN empresas e ON e.id = p.empresa_id
                WHERE p.id = :id
            """),
            {"id": produto_id}
        ).fetchone()

    if not prod:
        return JSONResponse(status_code=404, content={"erro": "Produto não encontrado"})

    codigo = str(prod.id).zfill(8)
    qr_bytes  = gerar_qrcode_bytes(json.dumps({"id": prod.id, "nome": prod.nome, "preco": float(prod.preco)}, ensure_ascii=False))
    bar_bytes = gerar_barcode_bytes(codigo)

    # Salvar imagens temporárias para o ReportLab
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f_qr:
        f_qr.write(qr_bytes)
        qr_path = f_qr.name
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f_bar:
        f_bar.write(bar_bytes)
        bar_path = f_bar.name

    try:
        # A6 landscape: 148mm x 105mm
        W, H = 148 * mm, 105 * mm
        buf = BytesIO()
        pdf = canvas.Canvas(buf, pagesize=(W, H))

        # Cabeçalho: nome da empresa
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawCentredString(W / 2, H - 8 * mm, prod.empresa_nome[:40])

        # Nome do produto
        pdf.setFont("Helvetica-Bold", 11)
        nome_display = prod.nome[:35]
        pdf.drawCentredString(W / 2, H - 16 * mm, nome_display)

        # Cor / Tamanho
        if prod.cor or prod.tamanho:
            extras = []
            if prod.cor:
                extras.append(f"Cor: {prod.cor}")
            if prod.tamanho:
                extras.append(f"Tam: {prod.tamanho}")
            pdf.setFont("Helvetica", 8)
            pdf.drawCentredString(W / 2, H - 22 * mm, "  |  ".join(extras))

        # QR Code (lado esquerdo)
        qr_size = 45 * mm
        pdf.drawImage(qr_path, 6 * mm, H - 22 * mm - qr_size, width=qr_size, height=qr_size)

        # Preço
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawCentredString(W * 0.65, H - 38 * mm, f"R$ {prod.preco:.2f}")

        # Código
        pdf.setFont("Helvetica", 8)
        pdf.drawCentredString(W * 0.65, H - 46 * mm, f"Cód: {codigo}")

        # Barcode (parte inferior)
        bar_w, bar_h = 90 * mm, 22 * mm
        pdf.drawImage(bar_path, (W - bar_w) / 2, 6 * mm, width=bar_w, height=bar_h)

        pdf.save()
        buf.seek(0)
    finally:
        os.unlink(qr_path)
        os.unlink(bar_path)

    nome_arquivo = f"etiqueta_produto_{codigo}.pdf"
    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={nome_arquivo}"}
    )


# ─────────────────────────────────────────────────────────────
#  API scan de produto (leitor USB via JS no vendas.html)
# ─────────────────────────────────────────────────────────────

@app.get("/api/produto/scan/{codigo}")
async def api_scan_produto(request: Request, codigo: str):
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"erro": "Não autenticado"})

    # Código pode ser o ID zero-padded (ex: "00000042") ou o ID direto
    codigo_limpo = codigo.lstrip("0") or "0"
    try:
        produto_id = int(codigo_limpo)
    except ValueError:
        return JSONResponse(status_code=404, content={"erro": "Código inválido"})

    with engine.connect() as conn:
        prod = conn.execute(
            text("""
                SELECT p.id, p.nome, p.preco, p.quantidade, p.cor, p.tamanho,
                       e.nome_fantasia AS empresa_nome, e.id AS empresa_id
                FROM produtos p
                JOIN empresas e ON e.id = p.empresa_id
                WHERE p.id = :id
            """),
            {"id": produto_id}
        ).fetchone()

    if not prod:
        return JSONResponse(status_code=404, content={"erro": "Produto não encontrado"})

    return JSONResponse({
        "id": prod.id,
        "nome": prod.nome,
        "preco": float(prod.preco),
        "quantidade": float(prod.quantidade),
        "cor": prod.cor,
        "tamanho": prod.tamanho,
        "empresa_id": prod.empresa_id,
        "empresa_nome": prod.empresa_nome,
    })


# ─────────────────────────────────────────────────────────────
#  API ESP32 — venda via scanner IoT
# ─────────────────────────────────────────────────────────────

class VendaScannerPayload(BaseModel):
    codigo: str
    session_key: str
    quantidade: float = 1.0
    cliente_nome: str = "Balcão"
    leitura_id: str | None = None  # identificador único gerado no dispositivo (v1.4.0)
    tipo: str = "venda"            # v1.5.0: "venda", "entrada" ou "consulta", escolhido por etiqueta de controle
    empresa_id: int | None = None  # v1.5.0: empresa à qual o dispositivo pertence (segregação)
    retida: bool = False           # v1.5.0: leitura enviada a partir da fila (houve falha de rede)


TIPOS_OPERACAO_SCANNER = {"venda", "entrada", "consulta"}


@app.post("/api/venda/scanner")
async def api_venda_scanner(payload: VendaScannerPayload):
    """
    Endpoint para ESP32 + leitor de barcode.
    Autentica via session_key, localiza produto pelo código,
    registra venda e retorna JSON de confirmação.
    """
    # Autenticar via session_key (SHA-256 armazenado em usuarios.session_id)
    with engine.connect() as conn:
        usuario = conn.execute(
            text("SELECT id, nome_completo AS nome, perfil FROM usuarios WHERE session_id = :sk"),
            {"sk": payload.session_key}
        ).fetchone()

    if not usuario:
        return JSONResponse(status_code=401, content={"erro": "session_key inválida"})

    # Leitura já processada (reenvio após falha de rede): devolve o mesmo resultado sem nova baixa
    if payload.leitura_id:
        with engine.connect() as conn:
            anterior = conn.execute(
                text("SELECT resposta FROM leituras_iot WHERE leitura_id = :lid"),
                {"lid": payload.leitura_id}
            ).fetchone()
        if anterior:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE leituras_iot SET reenvios = COALESCE(reenvios, 0) + 1 WHERE leitura_id = :lid"),
                    {"lid": payload.leitura_id}
                )
            dados = json.loads(anterior.resposta or "{}")
            dados["duplicada"] = True
            return JSONResponse(dados)

    tipo = (payload.tipo or "venda").strip().lower()
    if tipo not in TIPOS_OPERACAO_SCANNER:
        return JSONResponse(status_code=422, content={"erro": "Tipo de operação inválido"})
    if payload.quantidade <= 0:
        return JSONResponse(status_code=422, content={"erro": "Quantidade deve ser positiva"})

    codigo_limpo = payload.codigo.lstrip("0") or "0"
    try:
        produto_id = int(codigo_limpo)
    except ValueError:
        return JSONResponse(status_code=404, content={"erro": "Código inválido"})

    with engine.connect() as conn:
        prod = conn.execute(
            text("""
                SELECT id, nome, preco, quantidade, empresa_id
                FROM produtos
                WHERE id = :id
            """),
            {"id": produto_id}
        ).fetchone()

        if not prod:
            return JSONResponse(status_code=404, content={"erro": "Produto não encontrado"})

        # Segregação: o dispositivo só movimenta produtos da empresa em que está instalado
        if payload.empresa_id is not None and prod.empresa_id != payload.empresa_id:
            return JSONResponse(status_code=403, content={"erro": "Produto não pertence a esta empresa"})

        if tipo == "consulta":
            return JSONResponse({
                "ok": True, "tipo": "consulta", "produto": prod.nome,
                "preco_unitario": float(prod.preco), "estoque": float(prod.quantidade),
                "duplicada": False,
            })

        if tipo == "entrada":
            conn.execute(
                text("UPDATE produtos SET quantidade = quantidade + :qtd WHERE id = :id"),
                {"qtd": payload.quantidade, "id": prod.id}
            )
            resposta = {
                "ok": True, "tipo": "entrada", "produto": prod.nome,
                "quantidade": payload.quantidade,
                "estoque": float(prod.quantidade) + payload.quantidade,
                "duplicada": False,
            }
            try:
                if payload.leitura_id:
                    conn.execute(
                        text("""
                            INSERT INTO leituras_iot (leitura_id, codigo, usuario_id, resposta, tipo, retida)
                            VALUES (:lid, :cod, :uid, :resp, 'entrada', :ret)
                        """),
                        {"lid": payload.leitura_id, "cod": payload.codigo, "uid": usuario.id,
                         "resp": json.dumps(resposta), "ret": bool(payload.retida)}
                    )
                conn.commit()
            except IntegrityError:
                conn.rollback()
                return JSONResponse({"ok": True, "tipo": "entrada", "duplicada": True, "produto": prod.nome})
            registrar_log(usuario.id, usuario.nome or "scanner", "ENTRADA_SCANNER",
                          f"{prod.nome}: +{payload.quantidade}")
            return JSONResponse(resposta)

        if prod.quantidade < payload.quantidade:
            return JSONResponse(status_code=409, content={"erro": "Estoque insuficiente"})

        # Registrar venda
        grupo = str(uuid.uuid4())
        num_venda = conn.execute(text("SELECT nextval('seq_numero_venda')")).scalar()

        total_venda = float(prod.preco) * payload.quantidade
        conn.execute(
            text("""
                INSERT INTO vendas
                  (produto_id, empresa_id, quantidade, preco_unitario, total,
                   cliente_nome, tipo_documento, grupo_venda, numero_venda, data_venda)
                VALUES
                  (:pid, :eid, :qtd, :preco, :total,
                   :cliente, 'CPF', :grupo, :num, NOW())
            """),
            {
                "pid": prod.id,
                "eid": prod.empresa_id,
                "qtd": payload.quantidade,
                "preco": float(prod.preco),
                "total": total_venda,
                "cliente": payload.cliente_nome,
                "grupo": grupo,
                "num": num_venda,
            }
        )

        # Atualizar estoque
        conn.execute(
            text("UPDATE produtos SET quantidade = quantidade - :qtd WHERE id = :id"),
            {"qtd": payload.quantidade, "id": prod.id}
        )
        resposta = {
            "ok": True,
            "numero_venda": num_venda,
            "produto": prod.nome,
            "preco_unitario": float(prod.preco),
            "quantidade": payload.quantidade,
            "total": round(float(prod.preco) * payload.quantidade, 2),
            "comprovante_url": f"/vendas/comprovante/grupo/{grupo}",
            "tipo": "venda",
            "duplicada": False,
        }
        try:
            if payload.leitura_id:
                # mesma transação da venda: se o identificador já existir, nada é gravado
                conn.execute(
                    text("""
                        INSERT INTO leituras_iot (leitura_id, codigo, usuario_id, numero_venda, grupo_venda, resposta, tipo, retida)
                        VALUES (:lid, :cod, :uid, :num, :grupo, :resp, 'venda', :ret)
                    """),
                    {"lid": payload.leitura_id, "cod": payload.codigo, "uid": usuario.id,
                     "num": num_venda, "grupo": grupo, "resp": json.dumps(resposta),
                     "ret": bool(payload.retida)}
                )
            conn.commit()
        except IntegrityError:
            conn.rollback()
            return JSONResponse({"ok": True, "duplicada": True, "produto": prod.nome})

    return JSONResponse(resposta)


# ═════════════════════════════════════════════════════════════
#  PI IV v1.5.0 — Etiquetas de controle do leitor
# ═════════════════════════════════════════════════════════════

ETIQUETAS_CONTROLE = {
    "OP-VENDA":    ("Modo venda", "Cada leitura registra uma venda e baixa o estoque (padrão).", "1 piscada longa"),
    "OP-ENTRADA":  ("Modo entrada", "Cada leitura soma uma unidade ao estoque (recebimento de mercadoria).", "2 piscadas longas"),
    "OP-CONSULTA": ("Modo consulta", "A leitura apenas informa preço e saldo, sem alterar o estoque.", "3 piscadas longas"),
}


@app.get("/scanner/controle/{codigo}.png")
async def scanner_controle_png(request: Request, codigo: str):
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"erro": "Não autenticado"})
    if codigo not in ETIQUETAS_CONTROLE:
        return JSONResponse(status_code=404, content={"erro": "Etiqueta inexistente"})
    return Response(content=gerar_barcode_bytes(codigo), media_type="image/png")


@app.get("/scanner/etiquetas-controle", response_class=HTMLResponse)
async def scanner_etiquetas_controle(request: Request):
    user = get_current_user(request)
    if not user or user.perfil != "admin":
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "scanner_controle.html", {
        "user": user, "etiquetas": ETIQUETAS_CONTROLE,
    })


# ═════════════════════════════════════════════════════════════
#  PI IV v1.5.0 — Módulo de validação com a comunidade externa
#  Calcula automaticamente os critérios do Quadro 6 do relatório
# ═════════════════════════════════════════════════════════════

SUS_PERGUNTAS = [
    "Eu usaria este sistema com frequência.",
    "Achei o sistema desnecessariamente complexo.",
    "Achei o sistema fácil de usar.",
    "Eu precisaria de ajuda técnica para conseguir usar o sistema.",
    "As funções do sistema estão bem integradas.",
    "Achei o sistema muito inconsistente.",
    "A maioria das pessoas aprenderia a usar o sistema rapidamente.",
    "Achei o sistema muito complicado de usar.",
    "Senti-me confiante ao usar o sistema.",
    "Precisei aprender muitas coisas antes de usar o sistema.",
]
VAL_MIN_LEITURAS = 100
VAL_MIN_ACERTO = 0.95
VAL_SUS_MINIMO = 68.0
VAL_UTILIDADE_MINIMA = 4.0
VAL_INTERVALO_MAX_S = 60.0   # intervalos maiores indicam pausa, não tempo de registro


def nota_sus(respostas: list[int]) -> float:
    """System Usability Scale (Brooke, 1996): itens ímpares r-1, pares 5-r, soma x 2,5."""
    if len(respostas) != 10 or any(r < 1 or r > 5 for r in respostas):
        raise ValueError("SUS exige 10 respostas entre 1 e 5")
    soma = sum((r - 1) if i % 2 == 0 else (5 - r) for i, r in enumerate(respostas))
    return soma * 2.5


def _mediana(valores: list[float]) -> float | None:
    if not valores:
        return None
    v = sorted(valores)
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def _ts(valor):
    if isinstance(valor, str):
        return datetime.fromisoformat(valor)
    return valor


def calcular_validacao(conn, sessao_id: int) -> dict | None:
    sessao = conn.execute(text("SELECT * FROM validacao_sessoes WHERE id = :id"), {"id": sessao_id}).fetchone()
    if not sessao:
        return None
    filtro_fim = "AND l.data_leitura <= :fim" if sessao.fim else ""
    params = {"ini": sessao.inicio, "sid": sessao_id}
    if sessao.fim:
        params["fim"] = sessao.fim
    leituras = conn.execute(text(f"""
        SELECT l.leitura_id, l.codigo, l.tipo, l.numero_venda, l.data_leitura,
               COALESCE(l.reenvios, 0) AS reenvios, COALESCE(l.retida, FALSE) AS retida,
               m.correta, p.nome AS produto_nome
        FROM leituras_iot l
        LEFT JOIN validacao_marcacoes m ON m.leitura_id = l.leitura_id AND m.sessao_id = :sid
        LEFT JOIN produtos p ON p.id = CAST(l.codigo AS INTEGER)
        WHERE l.data_leitura >= :ini {filtro_fim}
        ORDER BY l.data_leitura
    """), params).fetchall()

    # 1. Confiabilidade da leitura (somente leituras conferidas pelo observador)
    conferidas = [l for l in leituras if l.correta is not None]
    corretas = sum(1 for l in conferidas if l.correta)
    taxa = corretas / len(conferidas) if conferidas else None
    c1_ok = len(conferidas) >= VAL_MIN_LEITURAS and taxa is not None and taxa >= VAL_MIN_ACERTO

    # 2. Tempo de registro: intervalo entre leituras consecutivas x digitação do código
    instantes = [_ts(l.data_leitura) for l in leituras]
    intervalos = [(b - a).total_seconds() for a, b in zip(instantes, instantes[1:])]
    intervalos = [i for i in intervalos if 0 < i <= VAL_INTERVALO_MAX_S]
    t_leitura = _mediana(intervalos)
    dig = conn.execute(text("SELECT duracao_ms, correto FROM validacao_digitacao WHERE sessao_id = :sid"),
                       {"sid": sessao_id}).fetchall()
    t_digitacao = _mediana([d.duracao_ms / 1000 for d in dig])
    erros_dig = sum(1 for d in dig if not d.correto)
    c2_ok = t_leitura is not None and t_digitacao is not None and t_leitura < t_digitacao

    # 3. Consistência do inventário
    cont = conn.execute(text("""
        SELECT c.produto_id, p.nome, c.qtd_sistema, c.qtd_fisica
        FROM validacao_contagem c LEFT JOIN produtos p ON p.id = c.produto_id
        WHERE c.sessao_id = :sid ORDER BY c.id
    """), {"sid": sessao_id}).fetchall()
    divergencia = sum(abs(float(c.qtd_fisica) - float(c.qtd_sistema)) for c in cont)
    itens_div = sum(1 for c in cont if float(c.qtd_fisica) != float(c.qtd_sistema))
    c3_ok = len(cont) > 0 and divergencia == 0

    # 4 e 5. Questionário (SUS, operação sem ajuda e utilidade da previsão)
    quest = conn.execute(text("SELECT * FROM validacao_questionario WHERE sessao_id = :sid ORDER BY id"),
                         {"sid": sessao_id}).fetchall()
    sus_medio = sum(float(q.nota_sus) for q in quest) / len(quest) if quest else None
    sem_ajuda = all(bool(q.sem_ajuda) for q in quest) if quest else None
    utilidade = sum(q.utilidade_previsao for q in quest) / len(quest) if quest else None
    c4_ok = sus_medio is not None and sus_medio >= VAL_SUS_MINIMO and bool(sem_ajuda)
    c5_ok = utilidade is not None and utilidade >= VAL_UTILIDADE_MINIMA

    # 6. Robustez: leituras retidas durante a queda de rede x vendas efetivamente registradas
    vendas_l = [l for l in leituras if l.tipo == "venda" and l.numero_venda is not None]
    retidas = [l for l in leituras if l.retida]
    nums = [l.numero_venda for l in vendas_l]
    baixas = 0
    if nums:
        baixas = conn.execute(text(
            "SELECT COUNT(*) FROM vendas WHERE numero_venda IN (" + ",".join(str(int(n)) for n in nums) + ")"
        )).scalar() or 0
    duplicadas = max(0, baixas - len(set(nums)))
    reenvios = sum(l.reenvios for l in leituras)
    declaradas = sessao.leituras_queda
    c6_ok = declaradas is not None and declaradas > 0 and len(retidas) == declaradas and duplicadas == 0

    def st(ok, tem_dados):
        return "Atendido" if ok else ("Não atendido" if tem_dados else "Sem dados")

    fmt = lambda v, n=1: "—" if v is None else f"{v:.{n}f}".replace(".", ",")
    criterios = [
        {"criterio": "Confiabilidade da leitura",
         "verificacao": f"{len(conferidas)} leituras conferidas no balcão",
         "aceitacao": f"Ao menos {VAL_MIN_LEITURAS} leituras e {int(VAL_MIN_ACERTO*100)}% corretas",
         "resultado": f"{corretas} de {len(conferidas)} corretas ({fmt(taxa*100 if taxa is not None else None)}%)",
         "status": st(c1_ok, bool(conferidas))},
        {"criterio": "Tempo de registro",
         "verificacao": f"Mediana de {len(intervalos)} intervalos entre leituras x {len(dig)} digitações",
         "aceitacao": "Leitura mais rápida que a digitação",
         "resultado": f"Leitura {fmt(t_leitura)} s x digitação {fmt(t_digitacao)} s ({erros_dig} código(s) digitado(s) errado)",
         "status": st(c2_ok, t_leitura is not None and t_digitacao is not None)},
        {"criterio": "Consistência do inventário",
         "verificacao": f"Contagem física de {len(cont)} produto(s)",
         "aceitacao": "Divergência nula",
         "resultado": f"{itens_div} item(ns) divergente(s); diferença total {fmt(divergencia, 0)} un.",
         "status": st(c3_ok, bool(cont))},
        {"criterio": "Usabilidade",
         "verificacao": f"SUS respondido por {len(quest)} pessoa(s) e observação da operação",
         "aceitacao": f"SUS ≥ {int(VAL_SUS_MINIMO)} e operação sem ajuda",
         "resultado": f"SUS médio {fmt(sus_medio)}; sem ajuda: {'sim' if sem_ajuda else ('não' if quest else '—')}",
         "status": st(c4_ok, bool(quest))},
        {"criterio": "Utilidade da previsão",
         "verificacao": "Avaliação da gestora (escala de 1 a 5)",
         "aceitacao": f"Nota média ≥ {int(VAL_UTILIDADE_MINIMA)}",
         "resultado": f"Nota média {fmt(utilidade)}",
         "status": st(c5_ok, bool(quest))},
        {"criterio": "Robustez",
         "verificacao": f"Queda proposital da rede; {declaradas if declaradas is not None else '—'} leitura(s) feitas sem conexão",
         "aceitacao": "Nenhuma leitura perdida ou duplicada",
         "resultado": f"{len(retidas)} recebida(s) da fila; {duplicadas} baixa(s) duplicada(s); {reenvios} reenvio(s) ignorado(s)",
         "status": st(c6_ok, declaradas is not None)},
    ]
    return {
        "sessao": {"id": sessao.id, "descricao": sessao.descricao,
                   "inicio": str(sessao.inicio), "fim": str(sessao.fim) if sessao.fim else None,
                   "leituras_queda": declaradas},
        "criterios": criterios,
        "atendidos": sum(1 for c in criterios if c["status"] == "Atendido"),
        "leituras": [{"leitura_id": l.leitura_id, "codigo": l.codigo, "produto": l.produto_nome,
                      "tipo": l.tipo, "data": str(l.data_leitura), "correta": l.correta,
                      "retida": bool(l.retida)} for l in leituras],
        "contagens": [{"produto": c.nome, "sistema": float(c.qtd_sistema), "fisica": float(c.qtd_fisica)} for c in cont],
        "questionarios": [{"respondente": q.respondente, "sus": float(q.nota_sus), "utilidade": q.utilidade_previsao,
                           "sem_ajuda": bool(q.sem_ajuda), "comentario": q.comentario} for q in quest],
    }


def _admin(request: Request):
    user = get_current_user(request)
    return user if user and user.perfil == "admin" else None


@app.get("/validacao", response_class=HTMLResponse)
async def validacao_lista(request: Request):
    user = _admin(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    with engine.connect() as conn:
        sessoes = conn.execute(text("SELECT * FROM validacao_sessoes ORDER BY id DESC")).fetchall()
    return templates.TemplateResponse(request, "validacao.html", {
        "user": user, "sessoes": sessoes, "dados": None,
    })


@app.post("/validacao/nova")
async def validacao_nova(request: Request, descricao: str = Form(...)):
    user = _admin(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    with engine.begin() as conn:
        sid = conn.execute(text("""
            INSERT INTO validacao_sessoes (descricao, usuario_id) VALUES (:d, :u) RETURNING id
        """), {"d": descricao.strip()[:150], "u": user.id}).scalar()
    registrar_log(user.id, user.username, "VALIDACAO_INICIADA", descricao[:150])
    return RedirectResponse(f"/validacao/{sid}", status_code=303)


@app.get("/validacao/{sessao_id}", response_class=HTMLResponse)
async def validacao_detalhe(request: Request, sessao_id: int):
    user = _admin(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    with engine.connect() as conn:
        dados = calcular_validacao(conn, sessao_id)
        produtos = conn.execute(text("SELECT id, nome, cor, tamanho, quantidade FROM produtos ORDER BY nome")).fetchall()
    if not dados:
        return RedirectResponse("/validacao", status_code=302)
    return templates.TemplateResponse(request, "validacao.html", {
        "user": user, "sessoes": None, "dados": dados,
        "produtos": produtos, "perguntas": SUS_PERGUNTAS,
    })


@app.get("/api/validacao/{sessao_id}")
async def api_validacao(request: Request, sessao_id: int):
    if not _admin(request):
        return JSONResponse(status_code=403, content={"erro": "Acesso negado"})
    with engine.connect() as conn:
        dados = calcular_validacao(conn, sessao_id)
    if not dados:
        return JSONResponse(status_code=404, content={"erro": "Sessão não encontrada"})
    return JSONResponse(dados)


@app.post("/validacao/{sessao_id}/marcar")
async def validacao_marcar(request: Request, sessao_id: int,
                           leitura_id: str = Form(...), correta: str = Form(...)):
    if not _admin(request):
        return JSONResponse(status_code=403, content={"erro": "Acesso negado"})
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM validacao_marcacoes WHERE sessao_id = :s AND leitura_id = :l"),
                     {"s": sessao_id, "l": leitura_id})
        conn.execute(text("INSERT INTO validacao_marcacoes (sessao_id, leitura_id, correta) VALUES (:s, :l, :c)"),
                     {"s": sessao_id, "l": leitura_id, "c": correta == "1"})
    return JSONResponse({"ok": True})


class DigitacaoPayload(BaseModel):
    codigo: str
    duracao_ms: int


@app.post("/validacao/{sessao_id}/digitacao")
async def validacao_digitacao(request: Request, sessao_id: int, payload: DigitacaoPayload):
    if not _admin(request):
        return JSONResponse(status_code=403, content={"erro": "Acesso negado"})
    codigo = payload.codigo.strip()
    with engine.begin() as conn:
        try:
            existe = conn.execute(text("SELECT 1 FROM produtos WHERE id = :id"),
                                  {"id": int(codigo.lstrip("0") or "0")}).fetchone() is not None
        except ValueError:
            existe = False
        conn.execute(text("""
            INSERT INTO validacao_digitacao (sessao_id, codigo, duracao_ms, correto)
            VALUES (:s, :c, :d, :ok)
        """), {"s": sessao_id, "c": codigo[:64], "d": max(0, payload.duracao_ms), "ok": existe})
    return JSONResponse({"ok": True, "codigo_valido": existe})


@app.post("/validacao/{sessao_id}/contagem")
async def validacao_contagem(request: Request, sessao_id: int,
                             produto_id: int = Form(...), qtd_fisica: float = Form(...)):
    if not _admin(request):
        return RedirectResponse("/", status_code=302)
    with engine.begin() as conn:
        qtd = conn.execute(text("SELECT quantidade FROM produtos WHERE id = :id"), {"id": produto_id}).scalar()
        if qtd is not None:
            conn.execute(text("""
                INSERT INTO validacao_contagem (sessao_id, produto_id, qtd_sistema, qtd_fisica)
                VALUES (:s, :p, :qs, :qf)
            """), {"s": sessao_id, "p": produto_id, "qs": float(qtd), "qf": qtd_fisica})
    return RedirectResponse(f"/validacao/{sessao_id}#contagem", status_code=303)


@app.post("/validacao/{sessao_id}/questionario")
async def validacao_questionario(request: Request, sessao_id: int):
    if not _admin(request):
        return RedirectResponse("/", status_code=302)
    form = await request.form()
    try:
        respostas = [int(form.get(f"q{i}", 0)) for i in range(1, 11)]
        nota = nota_sus(respostas)
        utilidade = int(form.get("utilidade", 0))
        if not 1 <= utilidade <= 5:
            raise ValueError
    except ValueError:
        return RedirectResponse(f"/validacao/{sessao_id}?erro=questionario#questionario", status_code=303)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO validacao_questionario
              (sessao_id, respondente, respostas_sus, nota_sus, utilidade_previsao, sem_ajuda, comentario)
            VALUES (:s, :r, :rs, :n, :u, :a, :c)
        """), {"s": sessao_id, "r": (form.get("respondente") or "Gestora")[:100], "rs": json.dumps(respostas),
               "n": nota, "u": utilidade, "a": form.get("sem_ajuda") == "1",
               "c": (form.get("comentario") or "").strip() or None})
    return RedirectResponse(f"/validacao/{sessao_id}#resultado", status_code=303)


@app.post("/validacao/{sessao_id}/queda")
async def validacao_queda(request: Request, sessao_id: int, leituras_queda: int = Form(...)):
    if not _admin(request):
        return RedirectResponse("/", status_code=302)
    with engine.begin() as conn:
        conn.execute(text("UPDATE validacao_sessoes SET leituras_queda = :q WHERE id = :id"),
                     {"q": max(0, leituras_queda), "id": sessao_id})
    return RedirectResponse(f"/validacao/{sessao_id}#robustez", status_code=303)


@app.post("/validacao/{sessao_id}/encerrar")
async def validacao_encerrar(request: Request, sessao_id: int):
    user = _admin(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    with engine.begin() as conn:
        conn.execute(text("UPDATE validacao_sessoes SET fim = CURRENT_TIMESTAMP WHERE id = :id AND fim IS NULL"),
                     {"id": sessao_id})
    registrar_log(user.id, user.username, "VALIDACAO_ENCERRADA", f"Sessão {sessao_id}")
    return RedirectResponse(f"/validacao/{sessao_id}#resultado", status_code=303)


@app.get("/validacao/{sessao_id}/relatorio.csv")
async def validacao_csv(request: Request, sessao_id: int):
    if not _admin(request):
        return RedirectResponse("/", status_code=302)
    with engine.connect() as conn:
        dados = calcular_validacao(conn, sessao_id)
    if not dados:
        return RedirectResponse("/validacao", status_code=302)
    buf = StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Critério", "Verificação", "Aceitação", "Resultado", "Situação"])
    for c in dados["criterios"]:
        w.writerow([c["criterio"], c["verificacao"], c["aceitacao"], c["resultado"], c["status"]])
    w.writerow([])
    w.writerow(["Leitura", "Código", "Produto", "Tipo", "Data", "Conferência", "Retida na fila"])
    for l in dados["leituras"]:
        conf = "" if l["correta"] is None else ("correta" if l["correta"] else "incorreta")
        w.writerow([l["leitura_id"], l["codigo"], l["produto"] or "", l["tipo"], l["data"], conf,
                    "sim" if l["retida"] else "não"])
    return Response(content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=validacao_{sessao_id}.csv"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
