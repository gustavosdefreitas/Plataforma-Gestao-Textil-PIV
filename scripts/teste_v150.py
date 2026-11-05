# Teste reprodutível da v1.5.0: tipo de operação e segregação no endpoint do leitor,
# etiquetas de controle e cálculo dos critérios do módulo de validação.
# Os dados abaixo são de ensaio funcional (não substituem a validação na loja).
# Uso: python scripts/teste_v150.py
import os, sys, itertools, json
from datetime import datetime, timedelta, timezone
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, RAIZ); os.chdir(RAIZ)
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
from sqlalchemy import create_engine, text, event
import main

ARQ = "/tmp/teste_v150.db"
if os.path.exists(ARQ): os.remove(ARQ)
eng = create_engine("sqlite:///" + ARQ)
cont = itertools.count(5000)
@event.listens_for(eng, "connect")
def _f(c, _):
    c.create_function("nextval", 1, lambda s: next(cont))
    c.create_function("NOW", 0, lambda: datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds"))
with eng.begin() as c:
    for ddl in [
        "CREATE TABLE usuarios(id INTEGER PRIMARY KEY, username TEXT, nome_completo TEXT, perfil TEXT, session_id TEXT)",
        "CREATE TABLE empresas(id INTEGER PRIMARY KEY, nome_fantasia TEXT)",
        "CREATE TABLE produtos(id INTEGER PRIMARY KEY, nome TEXT, quantidade NUMERIC, preco NUMERIC, empresa_id INT, fornecedor_id INT, cor TEXT, tamanho TEXT)",
        "CREATE TABLE vendas(id INTEGER PRIMARY KEY, produto_id INT, empresa_id INT, quantidade NUMERIC, preco_unitario NUMERIC, total NUMERIC, cliente_nome TEXT, tipo_documento TEXT, grupo_venda TEXT, numero_venda INT, data_venda TIMESTAMP)",
        "CREATE TABLE leituras_iot(id INTEGER PRIMARY KEY, leitura_id TEXT UNIQUE NOT NULL, codigo TEXT, usuario_id INT, numero_venda INT, grupo_venda TEXT, resposta TEXT, data_leitura TIMESTAMP DEFAULT CURRENT_TIMESTAMP, tipo TEXT DEFAULT 'venda', reenvios INT DEFAULT 0, retida BOOLEAN DEFAULT 0)",
        "CREATE TABLE logs_sistema(id INTEGER PRIMARY KEY, usuario_id INT, username TEXT, acao TEXT, detalhes TEXT, data_evento TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE validacao_sessoes(id INTEGER PRIMARY KEY, descricao TEXT NOT NULL, inicio TIMESTAMP DEFAULT CURRENT_TIMESTAMP, fim TIMESTAMP, usuario_id INT, leituras_queda INT)",
        "CREATE TABLE validacao_marcacoes(id INTEGER PRIMARY KEY, sessao_id INT NOT NULL, leitura_id TEXT NOT NULL, correta BOOLEAN NOT NULL DEFAULT 1, UNIQUE(sessao_id, leitura_id))",
        "CREATE TABLE validacao_digitacao(id INTEGER PRIMARY KEY, sessao_id INT NOT NULL, codigo TEXT NOT NULL, duracao_ms INT NOT NULL, correto BOOLEAN NOT NULL DEFAULT 1, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE validacao_contagem(id INTEGER PRIMARY KEY, sessao_id INT NOT NULL, produto_id INT NOT NULL, qtd_sistema NUMERIC NOT NULL, qtd_fisica NUMERIC NOT NULL, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE validacao_questionario(id INTEGER PRIMARY KEY, sessao_id INT NOT NULL, respondente TEXT NOT NULL, respostas_sus TEXT NOT NULL, nota_sus NUMERIC NOT NULL, utilidade_previsao INT NOT NULL, sem_ajuda BOOLEAN NOT NULL DEFAULT 0, comentario TEXT, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    ]:
        c.execute(text(ddl))
    c.execute(text("INSERT INTO usuarios VALUES(1,'caixa','Caixa','user','SK-CAIXA'),(2,'admin','Admin','admin','SK-ADMIN')"))
    c.execute(text("INSERT INTO empresas VALUES(1,'Loja parceira'),(2,'Outra empresa')"))
    c.execute(text("INSERT INTO produtos VALUES(1,'Camiseta básica',20,39.9,1,NULL,'Branca','M'),(2,'Linha poliéster',50,6.5,1,NULL,'Preta','Cone'),(3,'Produto da outra empresa',10,10,2,NULL,NULL,NULL)"))

main.engine = eng
from fastapi.testclient import TestClient
cli = TestClient(main.app)
def est(pid):
    with eng.connect() as c: return float(c.execute(text("SELECT quantidade FROM produtos WHERE id=:i"), {"i": pid}).scalar())
def post(**kw):
    base = {"session_key": "SK-CAIXA", "quantidade": 1}; base.update(kw)
    return cli.post("/api/venda/scanner", json=base)

casos = []
def caso(nome, ok, detalhe):
    casos.append((nome, ok, detalhe)); print(("APROVADO " if ok else "REPROVADO"), "|", nome, "|", detalhe)

# 1. consulta não altera estoque
e0 = est(1); r = post(codigo="00000001", tipo="consulta", leitura_id="c-1")
caso("Modo consulta", r.status_code == 200 and r.json()["tipo"] == "consulta" and est(1) == e0,
     f"status {r.status_code}, estoque {e0} -> {est(1)}")
# 2. entrada soma ao estoque, e o reenvio não soma de novo
e0 = est(2); r1 = post(codigo="00000002", tipo="entrada", leitura_id="e-1"); r2 = post(codigo="00000002", tipo="entrada", leitura_id="e-1")
caso("Modo entrada sem duplicidade", r1.json().get("tipo") == "entrada" and r2.json().get("duplicada") and est(2) == e0 + 1,
     f"estoque {e0} -> {est(2)}; reenvio duplicada={r2.json().get('duplicada')}")
# 3. venda continua padrão
e0 = est(1); r = post(codigo="00000001", leitura_id="v-1")
caso("Modo venda (padrão)", r.json().get("tipo") == "venda" and est(1) == e0 - 1, f"estoque {e0} -> {est(1)}")
# 4. segregação por empresa no endpoint do dispositivo
e0 = est(3); r = post(codigo="00000003", empresa_id=1, leitura_id="s-1")
caso("Segregação por empresa", r.status_code == 403 and est(3) == e0, f"status {r.status_code}, estoque {e0} -> {est(3)}")
r = post(codigo="00000001", empresa_id=1, leitura_id="s-2")
caso("Produto da própria empresa", r.status_code == 200, f"status {r.status_code}")
# 5. entradas inválidas
r1 = post(codigo="00000001", tipo="troca", leitura_id="x-1"); r2 = post(codigo="00000001", quantidade=-2, leitura_id="x-2")
caso("Tipo ou quantidade inválidos", r1.status_code == 422 and r2.status_code == 422, f"status {r1.status_code} e {r2.status_code}")
r = post(codigo="12AB", leitura_id="x-3")
caso("Código parcial ou corrompido", r.status_code == 404, f"status {r.status_code}")
# 6. leitura retida é marcada e reenvio é contado
post(codigo="00000002", leitura_id="f-1", retida=True); post(codigo="00000002", leitura_id="f-1", retida=True)
with eng.connect() as c:
    row = c.execute(text("SELECT retida, reenvios FROM leituras_iot WHERE leitura_id='f-1'")).fetchone()
caso("Marcação de leitura retida e reenvios", bool(row.retida) and row.reenvios == 1, f"retida={bool(row.retida)}, reenvios={row.reenvios}")
# 7. etiquetas de controle
cli.cookies.set("session_id", "SK-ADMIN")
r = cli.get("/scanner/controle/OP-ENTRADA.png"); r2 = cli.get("/scanner/etiquetas-controle")
caso("Etiquetas de controle", r.status_code == 200 and r.headers["content-type"] == "image/png" and "Modo entrada" in r2.text,
     f"png {r.status_code}, página {r2.status_code}")

# ---- ensaio funcional do módulo de validação ----
assert main.nota_sus([5,1,5,1,5,1,5,1,5,1]) == 100 and main.nota_sus([3]*10) == 50
import time; time.sleep(1.2)  # a sessão começa depois dos casos acima
r = cli.post("/validacao/nova", data={"descricao": "Ensaio funcional"}, follow_redirects=False)
sid = int(r.headers["location"].rsplit("/", 1)[1])
with eng.begin() as c:
    ini = datetime.fromisoformat(c.execute(text("SELECT inicio FROM validacao_sessoes WHERE id=:s"), {"s": sid}).scalar())
    for i in range(10):  # 10 leituras a cada 3 s, 2 delas retidas na fila
        c.execute(text("INSERT INTO leituras_iot(leitura_id,codigo,usuario_id,numero_venda,tipo,retida,data_leitura) VALUES(:l,'00000001',1,:n,'venda',:r,:d)"),
                  {"l": f"val-{i}", "n": 9000 + i, "r": i >= 8, "d": (ini + timedelta(seconds=5 + 3 * i)).isoformat(sep=" ")})
        c.execute(text("INSERT INTO vendas(produto_id,empresa_id,quantidade,preco_unitario,total,cliente_nome,tipo_documento,grupo_venda,numero_venda,data_venda) VALUES(1,1,1,1,1,'x','CPF','g',:n,:d)"),
                  {"n": 9000 + i, "d": ini.isoformat(sep=" ")})
for i in range(10):
    cli.post(f"/validacao/{sid}/marcar", data={"leitura_id": f"val-{i}", "correta": "0" if i == 3 else "1"})
for ms in (7200, 8100, 6900):
    cli.post(f"/validacao/{sid}/digitacao", json={"codigo": "00000001", "duracao_ms": ms})
cli.post(f"/validacao/{sid}/contagem", data={"produto_id": 2, "qtd_fisica": est(2)})
cli.post(f"/validacao/{sid}/queda", data={"leituras_queda": 2})
form = {f"q{i}": (4 if i % 2 else 2) for i in range(1, 11)}; form.update(respondente="Ensaio", utilidade=4, sem_ajuda="1")
cli.post(f"/validacao/{sid}/questionario", data=form)
d = cli.get(f"/api/validacao/{sid}").json()
for c in d["criterios"]:
    print(f'  {c["criterio"]:28} | {c["resultado"]:70} | {c["status"]}')
st = {c["criterio"]: c["status"] for c in d["criterios"]}
esperado = {"Confiabilidade da leitura": "Não atendido",  # só 10 leituras e 90%: abaixo do mínimo
            "Tempo de registro": "Atendido", "Consistência do inventário": "Atendido",
            "Usabilidade": "Atendido", "Utilidade da previsão": "Atendido", "Robustez": "Atendido"}
caso("Cálculo dos critérios de validação", st == esperado, f"{d['atendidos']} de 6 atendidos, conforme esperado")
pag = cli.get(f"/validacao/{sid}"); csvr = cli.get(f"/validacao/{sid}/relatorio.csv")
caso("Páginas e exportação da validação", pag.status_code == 200 and "Questionário" in pag.text and "Critério;" in csvr.text,
     f"página {pag.status_code}, csv {csvr.status_code}")
json.dump({"casos": casos, "validacao": d}, open("/tmp/resultado_v150.json", "w"), ensure_ascii=False, indent=1, default=str)
print(f"\n{sum(1 for c in casos if c[1])} de {len(casos)} casos aprovados")
sys.exit(0 if all(c[1] for c in casos) else 1)
