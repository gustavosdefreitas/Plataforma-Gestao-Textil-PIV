# Plataforma de Gestão Estratégica Têxtil — Estoque Fácil MEI

> Projeto Integrador IV — UNIVESP (PJI410)  
> Análise de dados em escala · Machine Learning · IoT · NF-e · Etiquetas · Visualização · Nuvem

**Versão atual: v1.2.2**

Sistema web full-stack para controle de estoque e vendas com camada de inteligência artificial, leitura de Nota Fiscal Eletrônica via QR Code e integração IoT com ESP32 + leitor de código de barras.

---

## Funcionalidades PI IV (Novas)

- **Entrada de NF-e via QR Code / Código de Barras / Chave de Acesso** — leitura da nota fiscal eletrônica pela câmera do celular, upload de imagem ou digitação da chave de 44 dígitos; consulta CNPJ do fornecedor via BrasilAPI
- **Etiquetas de produto** — geração de etiqueta A6 com QR Code + EAN-13 (Code-128) por produto, para impressão e colagem
- **Venda por scanner USB** — campo de scan na tela de vendas compatível com leitores USB HID (ex: Obitech MHT-U4)
- **Integração IoT (ESP32 + GM805)** — firmware MicroPython para ESP32 com leitor de barcode UART; registra venda e decrementa estoque em tempo real via Wi-Fi
- **Previsão de demanda (ML)** — regressão linear com scikit-learn, previsão para os próximos 3 meses com indicador R²
- **Dashboard Analytics ML** — 7 gráficos interativos: previsão de demanda, faturamento mensal, vendas diárias, top 10 produtos, padrão por dia da semana, giro de estoque e ticket médio
- **Importação em massa** — upload de planilha Excel/CSV para cadastrar produtos e fornecedores em lote, com detecção automática de duplicatas por nome e CNPJ; modelos CSV disponíveis para download
- **Perfil do usuário** — página `/meu-perfil` para edição de nome, CPF e senha; acessível pelo clique no nome no navbar

## Funcionalidades PI III (Base)

- Dashboard analítico com 5 gráficos (Chart.js)
- CRUD completo de produtos, fornecedores, empresas e usuários
- Controle de vendas com numeração sequencial e comprovante PDF
- Banco de horas por usuário
- Acessibilidade WCAG 2.1 AA
- API REST para integração externa

---

## Histórico de Versões

| Versão | Data | Descrição |
|---|---|---|
| v1.0.0 | Mai/2026 | Versão base — CRUD, vendas, IoT, NF-e, ML |
| v1.0.1 | Ago/2026 | Fix navbar layout — textos curtos, nowrap |
| v1.0.2 | Ago/2026 | Botão Sair em vermelho destacado |
| v1.0.3 | Ago/2026 | Data/hora nos logs formatada (dd/mm/aaaa hh:mm:ss) |
| v1.1.0 | Ago/2026 | Página /meu-perfil com edição de dados e senha |
| v1.2.0 | Ago/2026 | Importação de produtos e fornecedores via Excel/CSV |
| v1.2.1 | Ago/2026 | Importação ignora duplicatas por nome e CNPJ |
| v1.2.2 | Ago/2026 | Modelos CSV para download nas páginas de importação |

---

## Stack Tecnológica

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.13 |
| Framework web | FastAPI + Uvicorn |
| Templates | Jinja2 + Bootstrap 5 |
| Banco de dados | PostgreSQL (Render) |
| ORM | SQLAlchemy 2.x |
| Frontend | Bootstrap 5 + Chart.js |
| Machine Learning | scikit-learn + pandas + numpy |
| Geração de PDF | ReportLab |
| QR Code / Barcode | qrcode[pil] + python-barcode |
| Leitura NF-e | jsQR + ZXing (browser) + BrasilAPI |
| IoT | MicroPython + ESP32 + GM805 (UART) |
| Testes | Pytest |
| Lint | Ruff |
| CI | GitHub Actions |
| Deploy | Render.com |

---

## Como Executar Localmente (Windows)

### 1. Pré-requisitos

- Python 3.12 ou superior instalado
- Git instalado ([git-scm.com](https://git-scm.com/download/win))
- PostgreSQL local **ou** URL do banco no Render

### 2. Clonar o repositório

```bash
git clone https://github.com/gustavosdefreitas/Plataforma-Gestao-Textil-PIV.git
cd Plataforma-Gestao-Textil-PIV
```

### 3. Criar e ativar o ambiente virtual

```bash
python -m venv venv
venv\Scripts\activate
```

### 4. Instalar dependências

```bash
pip install -r requirements.txt
```

### 5. Configurar o banco de dados

Crie um arquivo `.env` na raiz do projeto com a URL do banco:

```
DATABASE_URL=postgresql://usuario:senha@host/banco
```

> **Render (produção):** copie a *External Database URL* no painel do Render → seu banco PostgreSQL → aba *Connect*.  
> **Local:** use `postgresql://postgres:suasenha@localhost:5432/nome_do_banco`

> ⚠️ Se a senha tiver caracteres especiais como `@`, substitua por `%40`. O arquivo `.env` deve ser salvo com encoding **UTF-8**.

### 6. Iniciar o servidor

No PowerShell, na pasta do projeto:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Acesse no navegador: [http://localhost:8000](http://localhost:8000)  
Login padrão: **admin / 123456**

> O `--host 0.0.0.0` permite que dispositivos na mesma rede (como o ESP32) acessem o servidor pelo IP local do PC.

### 7. Atualizar o projeto

Para baixar novas alterações do GitHub:

```bash
git pull origin main
```

Reinicie o uvicorn após atualizar.

---

## Integração IoT — ESP32 + GM805

### Hardware necessário

| Componente | Descrição |
|---|---|
| ESP32 DevKit | Microcontrolador Wi-Fi |
| GM805 | Leitor de código de barras 2D UART TTL 5V |
| Protoboard + jumpers | Conexão dos módulos |

### Ligações (GM805 → ESP32)

| GM805 | ESP32 |
|---|---|
| TX | GPIO 16 (RX2) |
| RX | GPIO 17 (TX2) |
| GND | GND |
| VCC | VIN (5V) |

### Instalação do MicroPython no ESP32

1. Baixe o firmware em [micropython.org/download/ESP32_GENERIC](https://micropython.org/download/ESP32_GENERIC/)
2. Instale o esptool: `pip install esptool`
3. Apague o flash: `py -m esptool --port COM3 erase-flash`
4. Grave o firmware: `py -m esptool --port COM3 --baud 460800 write_flash -z 0x1000 ESP32_GENERIC-vX.X.X.bin`

### Configuração do firmware

Edite o arquivo `esp32_scanner.py` com suas configurações:

```python
WIFI_SSID   = "Nome_da_sua_rede"
WIFI_PASS   = "Senha_da_rede"
SERVER_URL  = "https://plataforma-de-gestao-estrategica-textil.onrender.com"
SESSION_KEY = "valor_do_cookie_session_id"  # Pegue no navegador após login
```

Para obter o `SESSION_KEY`:
1. Faça login no sistema
2. Abra DevTools (F12) → Application → Cookies → URL do sistema
3. Copie o valor do cookie `session_id`

Carregue o arquivo no ESP32 pelo Thonny e salve como `main.py` para executar automaticamente na inicialização.

### Teste sem o leitor GM805

Use o arquivo `esp32_teste_simulado.py` para testar a conexão Wi-Fi e o registro de vendas sem o leitor físico:

```python
WIFI_SSID    = "Nome_da_rede"
WIFI_PASS    = "Senha_da_rede"
SESSION_KEY  = "valor_do_cookie_session_id"
CODIGO_TESTE = "00000001"  # ID do produto com zeros à esquerda (8 dígitos)
SERVER_URL   = "http://192.168.X.X:8000"  # IP do PC na rede local
```

---

## Endpoints Principais

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/nfe/scanner` | Tela de leitura de NF-e |
| `POST` | `/nfe/consultar` | Consulta dados da nota via chave ou URL |
| `GET` | `/nfe/confirmar` | Confirmação e cadastro de produtos/fornecedor |
| `POST` | `/nfe/salvar` | Salva entrada de estoque da nota |
| `GET` | `/produtos/{id}/etiqueta` | Preview da etiqueta do produto |
| `GET` | `/produtos/{id}/etiqueta/pdf` | Download da etiqueta em PDF (A6) |
| `GET` | `/api/produto/scan/{codigo}` | Busca produto pelo código (scanner USB) |
| `POST` | `/api/venda/scanner` | Registra venda via ESP32 (IoT) |
| `GET` | `/analytics` | Dashboard Analytics ML (7 gráficos) |
| `GET` | `/meu-perfil` | Página de perfil do usuário logado |
| `POST` | `/meu-perfil` | Salva alterações de perfil e senha |
| `GET` | `/produtos/importar` | Tela de importação de produtos via CSV/Excel |
| `POST` | `/produtos/importar` | Processa upload e importa produtos |
| `GET` | `/fornecedores/importar` | Tela de importação de fornecedores via CSV/Excel |
| `POST` | `/fornecedores/importar` | Processa upload e importa fornecedores |

---

## Sistema em Produção

- **URL:** [plataforma-de-gestao-estrategica-textil.onrender.com](https://plataforma-de-gestao-estrategica-textil.onrender.com)
- **Login demo:** admin / 123456

---

## Projetos Relacionados

- [PI III — PJI310](https://github.com/gustavosdefreitas/Plataforma-de-Gestao-Estrategica-Textil) — Base do sistema

---

Desenvolvido por estudantes da UNIVESP como requisito do Projeto Integrador IV (PJI410).
