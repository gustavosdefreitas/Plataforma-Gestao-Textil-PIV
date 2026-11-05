# Teste reprodutivel da v1.4.0: historico sintetico (semente 2026), previsao por produto
# com linha de base e casos de teste do endpoint do scanner (idempotencia e fila offline).
# Uso: python scripts/teste_historico_v140.py
import os, sys, random, itertools, json
from datetime import datetime, date
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, RAIZ); os.chdir(RAIZ)
from sqlalchemy import create_engine, text, event
os.environ["DATABASE_URL"]="sqlite:///:memory:"
import main
from dateutil.relativedelta import relativedelta
import numpy as np

eng = create_engine("sqlite:////tmp/teste_v140.db")
cont = itertools.count(1000)
@event.listens_for(eng, "connect")
def _f(c, _):
    c.create_function("nextval", 1, lambda s: next(cont))
    c.create_function("NOW", 0, lambda: datetime.now().isoformat(sep=" "))
if os.path.exists("/tmp/teste_v140.db"): os.remove("/tmp/teste_v140.db")
with eng.begin() as c:
    c.execute(text("CREATE TABLE usuarios(id INTEGER PRIMARY KEY, username TEXT, nome_completo TEXT, perfil TEXT, session_id TEXT)"))
    c.execute(text("CREATE TABLE empresas(id INTEGER PRIMARY KEY, nome_fantasia TEXT)"))
    c.execute(text("CREATE TABLE produtos(id INTEGER PRIMARY KEY, nome TEXT, quantidade NUMERIC, preco NUMERIC, empresa_id INT, fornecedor_id INT, cor TEXT, tamanho TEXT)"))
    c.execute(text("CREATE TABLE vendas(id INTEGER PRIMARY KEY, produto_id INT, empresa_id INT, quantidade NUMERIC, preco_unitario NUMERIC, total NUMERIC, cliente_nome TEXT, tipo_documento TEXT, grupo_venda TEXT, numero_venda INT, data_venda TIMESTAMP)"))
    c.execute(text("CREATE TABLE leituras_iot(id INTEGER PRIMARY KEY, leitura_id TEXT UNIQUE NOT NULL, codigo TEXT, usuario_id INT, numero_venda INT, grupo_venda TEXT, resposta TEXT, data_leitura TIMESTAMP DEFAULT CURRENT_TIMESTAMP, tipo TEXT DEFAULT 'venda', reenvios INT DEFAULT 0, retida BOOLEAN DEFAULT 0)"))
    c.execute(text("INSERT INTO usuarios VALUES(1,'caixa','Caixa','user','SK-TESTE')"))
    c.execute(text("INSERT INTO empresas VALUES(1,'Loja Teste')"))

# ---- histórico sintético de 12 meses (semente fixa) ----
rng = np.random.default_rng(2026)
random.seed(2026)
catalogo = [  # nome, cor, tamanho, nível médio, tendência mensal, sazonalidade
 ("Camiseta básica","Branca","M",30,0.8,4),("Camiseta básica","Preta","G",26,0.5,3),
 ("Calça jeans","Azul","40",12,0.3,2),("Bermuda moletom","Cinza","M",10,-0.4,3),
 ("Vestido midi","Floral","P",8,0.2,2),("Blusa tricô","Bege","M",6,-0.3,4),
 ("Linha poliéster 120","Branca","Cone",40,0.0,5),("Linha poliéster 120","Preta","Cone",35,0.6,5),
 ("Zíper invisível 20cm","Preto","Un",25,-0.5,3),("Botão 4 furos 14mm","Branco","Pct",18,0.4,2),
 ("Elástico chato 2cm","Branco","m",50,1.0,6),("Fita cetim 10mm","Vermelha","m",22,0.0,8),
 ("Viés de algodão","Azul","m",15,0.2,2),("Agulha máquina 90","—","Cx",9,0.1,1),
 ("Moletom canguru","Preto","G",7,0.9,5),("Saia plissada","Rosa","M",5,0.0,2),
 ("Meia cano alto","Branca","Único",20,-0.2,3),("Cueca boxer","Preta","M",16,0.3,2),
 ("Botão pressão","Prata","Pct",3,0.0,1),("Renda guipir","Branca","m",2,0.0,1),
]
hoje = date.today().replace(day=1)
meses = [hoje - relativedelta(months=12 - i) for i in range(12)]
with eng.begin() as c:
    for pid, (nome, cor, tam, nivel, tend, saz) in enumerate(catalogo, 1):
        c.execute(text("INSERT INTO produtos VALUES(:i,:n,:q,:p,1,NULL,:c,:t)"),
                  {"i": pid, "n": nome, "q": int(rng.integers(0, 40)), "p": round(float(rng.uniform(3, 90)), 2), "c": cor, "t": tam})
        for k, m in enumerate(meses):
            esperado = max(0, nivel + tend * k + saz * np.sin(2 * np.pi * (m.month - 3) / 12))
            qtd = int(rng.poisson(esperado))
            # distribui em vendas individuais ao longo do mês
            while qtd > 0:
                q = min(qtd, int(rng.integers(1, 4))); qtd -= q
                d = datetime(m.year, m.month, int(rng.integers(1, 28)), 10, 0)
                c.execute(text("INSERT INTO vendas(produto_id,empresa_id,quantidade,preco_unitario,total,cliente_nome,tipo_documento,grupo_venda,numero_venda,data_venda) VALUES(:p,1,:q,1,:q,'x','CPF','g',1,:d)"),
                          {"p": pid, "q": q, "d": d.isoformat(sep=" ")})
    # produto novo sem histórico
    c.execute(text("INSERT INTO produtos VALUES(21,'Jaqueta corta-vento',10,150,1,NULL,'Verde','M')"))
    # produto descontinuado: sem vendas e estoque 0
    c.execute(text("INSERT INTO produtos VALUES(22,'Regata antiga',0,20,1,NULL,'Azul','P')"))

with eng.connect() as c:
    r = main.prever_demanda_por_produto(c)
res = r["resumo"]
print("RESUMO", json.dumps(res, ensure_ascii=False))
print("LABELS", r["labels_futuros"])
for it in r["itens"]:
    print(f'{it["produto"][:32]:32} | {it["origem"][:12]:12} | {it["vencedor"][:16]:16} | RL {it.get("mae_rl","-")!s:>6} | MM {it.get("mae_mm","-")!s:>6} | prev {it["previsao"]} | est {it["estoque"]} | compra {it["sugestao_compra"]}')
assert not any(i["produto"].startswith("Regata antiga") for i in r["itens"]), "descontinuado deveria ser excluído"
json.dump(r, open("/tmp/resultado_previsao_v140.json","w"), ensure_ascii=False, indent=1, default=str)

# ---- teste de idempotência do endpoint do scanner ----
main.engine = eng
from fastapi.testclient import TestClient
cli = TestClient(main.app)
def estoque(pid):
    with eng.connect() as c: return float(c.execute(text("SELECT quantidade FROM produtos WHERE id=:i"), {"i": pid}).scalar())
casos = []
e0 = estoque(1)
r1 = cli.post("/api/venda/scanner", json={"codigo": "00000001", "session_key": "SK-TESTE", "leitura_id": "AA-1-1"}).json()
r2 = cli.post("/api/venda/scanner", json={"codigo": "00000001", "session_key": "SK-TESTE", "leitura_id": "AA-1-1"}).json()
casos.append(("Leitura válida", r1.get("ok") and not r1.get("duplicada")))
casos.append(("Reenvio da mesma leitura (duplicidade)", r2.get("duplicada") is True and estoque(1) == e0 - 1))
casos.append(("Atualização de estoque", estoque(1) == e0 - 1))
r3 = cli.post("/api/venda/scanner", json={"codigo": "00000999", "session_key": "SK-TESTE", "leitura_id": "AA-1-2"})
casos.append(("Código inexistente", r3.status_code == 404))
r4 = cli.post("/api/venda/scanner", json={"codigo": "00000001", "session_key": "INVALIDA", "leitura_id": "AA-1-3"})
casos.append(("Autorização inválida", r4.status_code == 401))
# fila offline: 3 leituras retidas reenviadas duas vezes
e1 = estoque(2)
fila = [{"codigo": "00000002", "leitura_id": f"AA-2-{i}"} for i in range(3)]
for _ in range(2):
    for l in fila: cli.post("/api/venda/scanner", json={**l, "session_key": "SK-TESTE"})
casos.append(("Reenvio da fila offline (3 leituras, 2 tentativas)", estoque(2) == e1 - 3))
with eng.connect() as c:
    nleit = c.execute(text("SELECT COUNT(*) FROM leituras_iot")).scalar()
casos.append(("Registros únicos em leituras_iot", nleit == 4))
for n, ok in casos: print("CASO", n, "->", "APROVADO" if ok else "REPROVADO")
