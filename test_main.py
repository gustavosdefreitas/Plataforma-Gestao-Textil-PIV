"""
Testes automatizados — Plataforma de Gestão Estratégica Têxtil
Projeto Integrador III — UNIVESP (PJI310)

Cobertura:
  - Autenticação (login, logout, credenciais inválidas)
  - Proteção de rotas (redirecionamento sem sessão)
  - CRUD de produtos (criar, listar, editar, deletar)
  - Bloqueio de deleção de produto com vendas associadas
  - CRUD de fornecedores (criar, editar)
  - CRUD de empresas (criar, deletar)
  - Registro de venda e baixa de estoque
  - API /api/produtos/{empresa_id} (autenticada)
  - Acesso restrito a admin (logs, banco de horas, usuários)
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from main import app, engine, hash_password


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def client_app():
    """Inicia o TestClient em modo context manager, disparando o lifespan
    da app (cria tabelas via Base.metadata.create_all)."""
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture(autouse=True)
def limpar_banco(client_app):
    """Garante um estado limpo antes de cada teste.
    Depende de client_app para garantir que as tabelas já existem."""
    with engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE vendas, produtos, fornecedores, empresas "
            "RESTART IDENTITY CASCADE"
        ))
        conn.execute(text("DELETE FROM usuarios WHERE username != 'admin'"))
        conn.execute(text(
            "UPDATE usuarios SET session_id = NULL WHERE username = 'admin'"
        ))
        conn.commit()
    yield


@pytest.fixture()
def session_admin(client_app):
    """Faz login como admin e retorna o cookie de sessão."""
    resp = client_app.post(
        "/login",
        data={"username": "admin", "password": "123456"},
    )
    assert resp.status_code == 303
    cookie = resp.cookies.get("session_id")
    assert cookie, "Login não gerou session_id"
    return {"session_id": cookie}


@pytest.fixture()
def empresa_id(client_app, session_admin):
    """Cria uma empresa e retorna seu ID."""
    resp = client_app.post(
        "/empresas/nova",
        data={
            "cnpj": "00.000.000/0001-00",
            "razao_social": "Empresa Teste LTDA",
            "nome": "Empresa Teste",
            "email": "empresa@teste.com",
            "tel": "11999999999",
        },
        cookies=session_admin,
    )
    assert resp.status_code == 303
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM empresas WHERE nome_fantasia = 'Empresa Teste'")
        ).fetchone()
    assert row, "Empresa não foi criada no banco"
    return row.id


@pytest.fixture()
def fornecedor_id(client_app, session_admin):
    """Cria um fornecedor e retorna seu ID."""
    resp = client_app.post(
        "/fornecedores/novo",
        data={
            "nome": "Fornecedor Teste",
            "cnpj": "11.111.111/0001-11",
            "telefone": "11988888888",
            "email": "fornecedor@teste.com",
        },
        cookies=session_admin,
    )
    assert resp.status_code == 303
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM fornecedores WHERE nome = 'Fornecedor Teste'")
        ).fetchone()
    assert row, "Fornecedor não foi criado no banco"
    return row.id


@pytest.fixture()
def produto_id(client_app, session_admin, empresa_id, fornecedor_id):
    """Cria um produto e retorna seu ID."""
    resp = client_app.post(
        "/produtos/novo",
        data={
            "nome": "Camiseta Teste",
            "quantidade": "10",
            "preco": "49.90",
            "empresa_id": str(empresa_id),
            "fornecedor_id": str(fornecedor_id),
            "cor": "Azul",
            "tamanho": "M",
        },
        cookies=session_admin,
    )
    assert resp.status_code == 303
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM produtos WHERE nome = 'Camiseta Teste'")
        ).fetchone()
    assert row, "Produto não foi criado no banco"
    return row.id


# ---------------------------------------------------------------------------
# 1. Autenticação
# ---------------------------------------------------------------------------

class TestAutenticacao:

    def test_pagina_login_abre(self, client_app):
        """GET /login deve retornar 200 com formulário."""
        resp = client_app.get("/login")
        assert resp.status_code == 200
        assert "Login" in resp.text

    def test_login_credenciais_validas(self, client_app):
        """Login com admin/123456 deve redirecionar para / e setar cookie."""
        resp = client_app.post(
            "/login",
            data={"username": "admin", "password": "123456"},
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        assert "session_id" in resp.cookies

    def test_login_senha_errada(self, client_app):
        """Login com senha incorreta deve retornar 200 com mensagem de erro."""
        resp = client_app.post(
            "/login",
            data={"username": "admin", "password": "senha_errada"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Credenciais Inválidas" in resp.text

    def test_login_usuario_inexistente(self, client_app):
        """Login com usuário que não existe deve retornar erro."""
        resp = client_app.post(
            "/login",
            data={"username": "nao_existe", "password": "123456"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Credenciais Inválidas" in resp.text

    def test_logout_limpa_sessao(self, client_app, session_admin):
        """Logout deve apagar o cookie e redirecionar para /login."""
        resp = client_app.get("/logout", cookies=session_admin)
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

        # Após logout, acessar / deve redirecionar para /login
        resp2 = client_app.get("/", cookies=session_admin)
        assert resp2.status_code == 303


# ---------------------------------------------------------------------------
# 2. Proteção de rotas (sem autenticação)
# ---------------------------------------------------------------------------

class TestProtecaoRotas:

    def test_dashboard_sem_auth(self, client_app):
        """/ sem sessão deve redirecionar para /login."""
        resp = client_app.get("/")
        assert resp.status_code == 303
        assert "login" in resp.headers["location"]

    def test_produtos_sem_auth(self, client_app):
        """/produtos sem sessão deve redirecionar."""
        resp = client_app.get("/produtos")
        assert resp.status_code == 303

    def test_fornecedores_sem_auth(self, client_app):
        """/fornecedores sem sessão deve redirecionar."""
        resp = client_app.get("/fornecedores")
        assert resp.status_code == 303

    def test_empresas_sem_auth(self, client_app):
        """/empresas sem sessão deve redirecionar."""
        resp = client_app.get("/empresas")
        assert resp.status_code == 303

    def test_usuarios_sem_auth(self, client_app):
        """/usuarios sem sessão deve redirecionar."""
        resp = client_app.get("/usuarios")
        assert resp.status_code == 303

    def test_post_fornecedor_novo_sem_auth(self, client_app):
        """POST /fornecedores/novo sem sessão deve redirecionar (P1)."""
        resp = client_app.post(
            "/fornecedores/novo",
            data={"nome": "Tentativa", "cnpj": "", "telefone": "", "email": ""},
        )
        assert resp.status_code == 303
        assert "login" in resp.headers["location"]

    def test_post_fornecedor_editar_sem_auth(self, client_app):
        """POST /fornecedores/editar/{id} sem sessão deve redirecionar (P1)."""
        resp = client_app.post(
            "/fornecedores/editar/999",
            data={"nome": "Tentativa", "cnpj": "", "telefone": "", "email": ""},
        )
        assert resp.status_code == 303
        assert "login" in resp.headers["location"]

    def test_api_produtos_sem_auth(self, client_app):
        """GET /api/produtos/{id} sem sessão deve retornar 401 (P1)."""
        resp = client_app.get("/api/produtos/1")
        assert resp.status_code == 401
        assert "erro" in resp.json()

    def test_logs_sem_auth(self, client_app):
        """/logs sem sessão deve redirecionar."""
        resp = client_app.get("/logs")
        assert resp.status_code == 303

    def test_banco_horas_sem_auth(self, client_app):
        """/banco-horas sem sessão deve redirecionar."""
        resp = client_app.get("/banco-horas")
        assert resp.status_code == 303


# ---------------------------------------------------------------------------
# 3. CRUD de Empresas
# ---------------------------------------------------------------------------

class TestEmpresas:

    def test_criar_empresa(self, client_app, session_admin):
        """POST /empresas/nova deve criar e redirecionar."""
        resp = client_app.post(
            "/empresas/nova",
            data={
                "cnpj": "22.222.222/0001-22",
                "razao_social": "Razão Social Teste",
                "nome": "Fantasia Teste",
                "email": "teste@empresa.com",
                "tel": "11977777777",
            },
            cookies=session_admin,
        )
        assert resp.status_code == 303

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM empresas WHERE nome_fantasia = 'Fantasia Teste'")
            ).fetchone()
        assert row is not None
        assert row.razao_social == "Razão Social Teste"

    def test_listar_empresas(self, client_app, session_admin, empresa_id):
        """GET /empresas deve retornar 200 com a empresa criada."""
        resp = client_app.get("/empresas", cookies=session_admin)
        assert resp.status_code == 200
        assert "Empresa Teste" in resp.text

    def test_deletar_empresa(self, client_app, session_admin, empresa_id):
        """GET /empresas/deletar/{id} deve remover do banco."""
        resp = client_app.get(f"/empresas/deletar/{empresa_id}", cookies=session_admin)
        assert resp.status_code == 303

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM empresas WHERE id = :id"), {"id": empresa_id}
            ).fetchone()
        assert row is None


# ---------------------------------------------------------------------------
# 4. CRUD de Fornecedores
# ---------------------------------------------------------------------------

class TestFornecedores:

    def test_criar_fornecedor(self, client_app, session_admin):
        """POST /fornecedores/novo deve criar e redirecionar."""
        resp = client_app.post(
            "/fornecedores/novo",
            data={
                "nome": "Fornecedor Novo",
                "cnpj": "33.333.333/0001-33",
                "telefone": "11966666666",
                "email": "novo@fornecedor.com",
            },
            cookies=session_admin,
        )
        assert resp.status_code == 303

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM fornecedores WHERE nome = 'Fornecedor Novo'")
            ).fetchone()
        assert row is not None
        assert row.email == "novo@fornecedor.com"

    def test_editar_fornecedor(self, client_app, session_admin, fornecedor_id):
        """POST /fornecedores/editar/{id} deve atualizar os dados."""
        resp = client_app.post(
            f"/fornecedores/editar/{fornecedor_id}",
            data={
                "nome": "Fornecedor Atualizado",
                "cnpj": "44.444.444/0001-44",
                "telefone": "11955555555",
                "email": "atualizado@fornecedor.com",
            },
            cookies=session_admin,
        )
        assert resp.status_code == 303

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT nome FROM fornecedores WHERE id = :id"),
                {"id": fornecedor_id},
            ).fetchone()
        assert row.nome == "Fornecedor Atualizado"

    def test_listar_fornecedores(self, client_app, session_admin, fornecedor_id):
        """GET /fornecedores deve retornar 200 com o fornecedor criado."""
        resp = client_app.get("/fornecedores", cookies=session_admin)
        assert resp.status_code == 200
        assert "Fornecedor Teste" in resp.text

    def test_busca_fornecedor(self, client_app, session_admin, fornecedor_id):
        """GET /fornecedores?busca= deve filtrar por nome."""
        resp = client_app.get("/fornecedores?busca=Fornecedor Teste", cookies=session_admin)
        assert resp.status_code == 200
        assert "Fornecedor Teste" in resp.text

    def test_deletar_fornecedor(self, client_app, session_admin, fornecedor_id):
        """GET /fornecedores/deletar/{id} deve remover do banco."""
        resp = client_app.get(
            f"/fornecedores/deletar/{fornecedor_id}", cookies=session_admin
        )
        assert resp.status_code == 303

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM fornecedores WHERE id = :id"),
                {"id": fornecedor_id},
            ).fetchone()
        assert row is None


# ---------------------------------------------------------------------------
# 5. CRUD de Produtos
# ---------------------------------------------------------------------------

class TestProdutos:

    def test_criar_produto(self, client_app, session_admin, empresa_id, fornecedor_id):
        """POST /produtos/novo deve inserir produto no banco."""
        resp = client_app.post(
            "/produtos/novo",
            data={
                "nome": "Calça Jeans",
                "quantidade": "20",
                "preco": "89.90",
                "empresa_id": str(empresa_id),
                "fornecedor_id": str(fornecedor_id),
                "cor": "Preto",
                "tamanho": "42",
            },
            cookies=session_admin,
        )
        assert resp.status_code == 303

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM produtos WHERE nome = 'Calça Jeans'")
            ).fetchone()
        assert row is not None
        assert float(row.quantidade) == 20.0
        assert float(row.preco) == 89.90

    def test_listar_produtos(self, client_app, session_admin, produto_id):
        """GET /produtos deve retornar 200 com o produto criado."""
        resp = client_app.get("/produtos", cookies=session_admin)
        assert resp.status_code == 200
        assert "Camiseta Teste" in resp.text

    def test_busca_produto_por_nome(self, client_app, session_admin, produto_id):
        """GET /produtos?busca= deve filtrar por nome."""
        resp = client_app.get("/produtos?busca=Camiseta", cookies=session_admin)
        assert resp.status_code == 200
        assert "Camiseta Teste" in resp.text

    def test_busca_produto_por_cor(self, client_app, session_admin, produto_id):
        """GET /produtos?busca= deve filtrar por cor."""
        resp = client_app.get("/produtos?busca=Azul", cookies=session_admin)
        assert resp.status_code == 200
        assert "Camiseta Teste" in resp.text

    def test_editar_produto(self, client_app, session_admin, produto_id, empresa_id, fornecedor_id):
        """POST /produtos/editar/{id} deve atualizar os dados no banco."""
        resp = client_app.post(
            f"/produtos/editar/{produto_id}",
            data={
                "nome": "Camiseta Editada",
                "quantidade": "15",
                "preco": "59.90",
                "empresa_id": str(empresa_id),
                "fornecedor_id": str(fornecedor_id),
                "cor": "Vermelho",
                "tamanho": "G",
            },
            cookies=session_admin,
        )
        assert resp.status_code == 303

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM produtos WHERE id = :id"), {"id": produto_id}
            ).fetchone()
        assert row.nome == "Camiseta Editada"
        assert float(row.quantidade) == 15.0

    def test_deletar_produto_sem_vendas(self, client_app, session_admin, produto_id):
        """Produto sem vendas deve ser excluído normalmente."""
        resp = client_app.get(f"/produtos/deletar/{produto_id}", cookies=session_admin)
        assert resp.status_code == 303
        assert "msg=produto_excluido" in resp.headers["location"]

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM produtos WHERE id = :id"), {"id": produto_id}
            ).fetchone()
        assert row is None

    def test_deletar_produto_com_vendas_bloqueado(
        self, client_app, session_admin, produto_id, empresa_id
    ):
        """Produto com vendas associadas NÃO deve ser excluído (P2)."""
        # Registra uma venda para o produto
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO vendas (
                    grupo_venda, produto_id, empresa_id, tipo_documento,
                    cliente_nome, quantidade, preco_unitario, total
                ) VALUES (
                    'grupo-teste-uuid', :produto_id, :empresa_id, 'comprovante',
                    'Cliente Teste', 2, 49.90, 99.80
                )
            """), {"produto_id": produto_id, "empresa_id": empresa_id})
            conn.commit()

        resp = client_app.get(f"/produtos/deletar/{produto_id}", cookies=session_admin)
        assert resp.status_code == 303
        assert "produto_com_vendas" in resp.headers["location"]

        # Confirma que o produto ainda existe
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM produtos WHERE id = :id"), {"id": produto_id}
            ).fetchone()
        assert row is not None

    def test_deletar_produto_sem_auth(self, client_app, produto_id):
        """Deletar produto sem sessão deve redirecionar para login."""
        client_app.cookies.clear()
        resp = client_app.get(f"/produtos/deletar/{produto_id}")
        assert resp.status_code == 303
        assert "login" in resp.headers["location"]


# ---------------------------------------------------------------------------
# 6. Vendas
# ---------------------------------------------------------------------------

class TestVendas:

    def test_registrar_venda_e_baixar_estoque(
        self, client_app, session_admin, produto_id, empresa_id
    ):
        """POST /vendas/nova deve registrar venda e descontar estoque."""
        resp = client_app.post(
            "/vendas/nova",
            data={
                "tipo_documento": "comprovante",
                "empresa_id": str(empresa_id),
                "cliente_nome": "João da Silva",
                "cliente_cpf_cnpj": "123.456.789-00",
                "cliente_email": "joao@teste.com",
                "cliente_telefone": "11944444444",
                "produto_id": [str(produto_id)],
                "qtd_venda": ["3"],
            },
            cookies=session_admin,
        )
        # Redireciona para o comprovante
        assert resp.status_code == 303

        # Verifica que o estoque foi descontado (era 10, vendeu 3 → 7)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT quantidade FROM produtos WHERE id = :id"),
                {"id": produto_id},
            ).fetchone()
        assert float(row.quantidade) == 7.0

    def test_venda_sem_auth(self, client_app, produto_id, empresa_id):
        """POST /vendas/nova sem sessão deve redirecionar para login."""
        client_app.cookies.clear()
        resp = client_app.post(
            "/vendas/nova",
            data={
                "tipo_documento": "comprovante",
                "empresa_id": str(empresa_id),
                "cliente_nome": "Tentativa",
                "produto_id": [str(produto_id)],
                "qtd_venda": ["1"],
            },
        )
        assert resp.status_code == 303
        assert "login" in resp.headers["location"]

    def test_listar_vendas(self, client_app, session_admin):
        """GET /vendas deve retornar 200."""
        resp = client_app.get("/vendas", cookies=session_admin)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 7. API
# ---------------------------------------------------------------------------

class TestAPI:

    def test_api_produtos_sem_auth_retorna_401(self, client_app):
        """GET /api/produtos/{id} sem sessão deve retornar 401 (P1)."""
        resp = client_app.get("/api/produtos/1")
        assert resp.status_code == 401
        data = resp.json()
        assert "erro" in data

    def test_api_produtos_com_auth(self, client_app, session_admin, produto_id, empresa_id):
        """GET /api/produtos/{empresa_id} autenticado deve retornar JSON com estoque."""
        resp = client_app.get(
            f"/api/produtos/{empresa_id}", cookies=session_admin
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "estoque" in data
        assert data["empresa_id"] == empresa_id
        # Deve conter o produto criado
        nomes = [p["nome"] for p in data["estoque"]]
        assert "Camiseta Teste" in nomes

    def test_api_empresa_inexistente(self, client_app, session_admin):
        """API com empresa_id que não existe deve retornar lista vazia."""
        resp = client_app.get("/api/produtos/99999", cookies=session_admin)
        assert resp.status_code == 200
        data = resp.json()
        assert data["estoque"] == []


# ---------------------------------------------------------------------------
# 8. Restrições de perfil (admin-only)
# ---------------------------------------------------------------------------

class TestRestricaoPerfil:

    def test_logs_acessivel_apenas_para_admin(self, client_app, session_admin):
        """Admin deve conseguir acessar /logs."""
        resp = client_app.get("/logs", cookies=session_admin)
        assert resp.status_code == 200

    def test_banco_horas_acessivel_apenas_para_admin(self, client_app, session_admin):
        """Admin deve conseguir acessar /banco-horas."""
        resp = client_app.get("/banco-horas", cookies=session_admin)
        assert resp.status_code == 200

    def test_usuario_comum_nao_acessa_logs(self, client_app, session_admin):
        """Usuário com perfil 'user' não deve acessar /logs."""
        # Cria usuário comum
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO usuarios (username, password, perfil)
                VALUES ('user_comum', :senha, 'user')
            """), {"senha": hash_password("senha123")})
            conn.commit()

        # Faz login como usuário comum
        resp_login = client_app.post(
            "/login",
            data={"username": "user_comum", "password": "senha123"},
        )
        assert resp_login.status_code == 303
        cookie_comum = {"session_id": resp_login.cookies.get("session_id")}

        # Tenta acessar /logs
        resp = client_app.get("/logs", cookies=cookie_comum)
        assert resp.status_code == 303  # Redireciona para /

    def test_usuario_comum_nao_acessa_banco_horas(self, client_app, session_admin):
        """Usuário com perfil 'user' não deve acessar /banco-horas."""
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO usuarios (username, password, perfil)
                VALUES ('user_comum2', :senha, 'user')
            """), {"senha": hash_password("senha123")})
            conn.commit()

        resp_login = client_app.post(
            "/login",
            data={"username": "user_comum2", "password": "senha123"},
        )
        cookie_comum = {"session_id": resp_login.cookies.get("session_id")}

        resp = client_app.get("/banco-horas", cookies=cookie_comum)
        assert resp.status_code == 303


# ---------------------------------------------------------------------------
# 9. Dashboard
# ---------------------------------------------------------------------------

class TestDashboard:

    def test_dashboard_admin(self, client_app, session_admin):
        """Dashboard deve retornar 200 para admin."""
        resp = client_app.get("/", cookies=session_admin)
        assert resp.status_code == 200
        assert "Resumo do Sistema" in resp.text

    def test_dashboard_contem_cards(self, client_app, session_admin):
        """Dashboard deve exibir os cards de resumo."""
        resp = client_app.get("/", cookies=session_admin)
        assert "Produtos Cadastrados" in resp.text
        assert "Vendas do Mês" in resp.text
        assert "Faturamento do Mês" in resp.text
