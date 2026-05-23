# ============================================================
#  Firmware MicroPython — ESP32 + Leitor de Barcode (UART)
#  Plataforma de Gestão Têxtil PI4
#
#  Hardware necessário:
#    - ESP32 (qualquer variante com Wi-Fi)
#    - Módulo leitor de barcode/QR com saída UART
#      (ex: GM65, Waveshare barcode scanner, E3000)
#
#  Conexão física (padrão):
#    Leitor TX  →  ESP32 GPIO 16 (RX2)
#    Leitor RX  →  ESP32 GPIO 17 (TX2)
#    Leitor GND →  ESP32 GND
#    Leitor VCC →  ESP32 3.3V ou 5V (verificar datasheet do módulo)
#
#  Dependências (instalar via upip ou Thonny):
#    - urequests  (incluso no MicroPython ESP32 por padrão)
#    - network    (incluso no MicroPython ESP32 por padrão)
#
#  Como usar:
#    1. Edite as constantes WIFI_SSID, WIFI_PASS, SERVER_URL e SESSION_KEY abaixo
#    2. Grave este arquivo como main.py no ESP32 via Thonny ou ampy
#    3. Ao ligar, o ESP32 conecta ao Wi-Fi e fica aguardando leituras
#    4. Cada leitura envia POST /api/venda/scanner e exibe resultado no Serial Monitor
# ============================================================

import machine
import network
import urequests
import ujson
import time
import sys

# ─────────────────────────────────────────────────────────────
#  CONFIGURAÇÕES — edite aqui antes de gravar no ESP32
# ─────────────────────────────────────────────────────────────

WIFI_SSID    = "NOME_DA_SUA_REDE"        # SSID da rede Wi-Fi
WIFI_PASS    = "SENHA_DA_REDE"           # Senha da rede Wi-Fi

# URL do servidor (produção ou local)
SERVER_URL   = "https://plataforma-de-gestao-estrategica-textil.onrender.com"

# session_key: copie o valor de usuarios.session_id do banco de dados
# para o usuário operador do caixa (coluna session_id em SHA-256)
SESSION_KEY  = "COLE_AQUI_O_SESSION_ID_DO_USUARIO"

# Quantidade padrão por leitura (pode ser alterada via botão físico futuramente)
QUANTIDADE_PADRAO = 1.0

# Nome do cliente padrão (venda de balcão)
CLIENTE_NOME = "Balcão"

# UART do leitor de barcode
UART_ID   = 2     # UART2 do ESP32 → pinos GPIO16 (RX) e GPIO17 (TX)
UART_BAUD = 9600  # Maioria dos módulos de barcode usa 9600

# LED embutido (GPIO 2 na maioria dos ESP32) — pisca ao ler
LED_PIN = 2

# ─────────────────────────────────────────────────────────────
#  Setup de hardware
# ─────────────────────────────────────────────────────────────

led = machine.Pin(LED_PIN, machine.Pin.OUT)
uart = machine.UART(UART_ID, baudrate=UART_BAUD, rx=16, tx=17, timeout=10)


def piscar_led(vezes=1, intervalo_ms=150):
    for _ in range(vezes):
        led.value(1)
        time.sleep_ms(intervalo_ms)
        led.value(0)
        time.sleep_ms(intervalo_ms)


# ─────────────────────────────────────────────────────────────
#  Conexão Wi-Fi
# ─────────────────────────────────────────────────────────────

def conectar_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        print("[WiFi] Já conectado:", wlan.ifconfig()[0])
        return True

    print(f"[WiFi] Conectando a '{WIFI_SSID}'...")
    wlan.connect(WIFI_SSID, WIFI_PASS)

    tentativas = 0
    while not wlan.isconnected():
        tentativas += 1
        if tentativas > 20:
            print("[WiFi] ERRO: Não foi possível conectar.")
            return False
        time.sleep(0.5)
        sys.stdout.write(".")

    ip = wlan.ifconfig()[0]
    print(f"\n[WiFi] Conectado! IP: {ip}")
    piscar_led(3)
    return True


# ─────────────────────────────────────────────────────────────
#  Envio da venda ao servidor
# ─────────────────────────────────────────────────────────────

def enviar_venda(codigo: str) -> bool:
    """
    Envia POST /api/venda/scanner com o código lido.
    Retorna True se a venda foi registrada com sucesso.
    """
    url = f"{SERVER_URL}/api/venda/scanner"
    payload = ujson.dumps({
        "codigo":       codigo.strip(),
        "session_key":  SESSION_KEY,
        "quantidade":   QUANTIDADE_PADRAO,
        "cliente_nome": CLIENTE_NOME,
    })
    headers = {"Content-Type": "application/json"}

    try:
        resp = urequests.post(url, data=payload, headers=headers, timeout=10)
        data = resp.json()
        resp.close()

        if resp.status_code == 200 and data.get("ok"):
            print(f"[VENDA OK] #{data['numero_venda']} | {data['produto']} | R$ {data['total']:.2f}")
            piscar_led(2)
            return True
        else:
            erro = data.get("erro", "Erro desconhecido")
            print(f"[VENDA ERRO {resp.status_code}] {erro}")
            piscar_led(5, intervalo_ms=80)  # pisca rápido = erro
            return False

    except OSError as e:
        print(f"[REDE ERRO] {e}")
        piscar_led(5, intervalo_ms=80)
        return False


# ─────────────────────────────────────────────────────────────
#  Loop principal
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  Plataforma Gestao Textil PI4 — Scanner IoT")
    print("=" * 50)

    if not conectar_wifi():
        print("[FATAL] Sem Wi-Fi — reiniciando em 10s...")
        time.sleep(10)
        machine.reset()

    print("[Scanner] Pronto. Aguardando leituras de barcode/QR...\n")
    led.value(0)

    buffer = b""

    while True:
        # Lê bytes disponíveis da UART
        if uart.any():
            chunk = uart.read()
            if chunk:
                buffer += chunk

            # Maioria dos leitores envia CR (\r) ou LF (\n) como terminador
            while b"\r" in buffer or b"\n" in buffer:
                # Separar na primeira quebra de linha
                for sep in (b"\r\n", b"\n", b"\r"):
                    if sep in buffer:
                        linha, buffer = buffer.split(sep, 1)
                        break

                codigo = linha.decode("utf-8", "ignore").strip()
                if codigo:
                    print(f"[LIDO] Código: {codigo}")
                    enviar_venda(codigo)

        time.sleep_ms(50)


# ─────────────────────────────────────────────────────────────
#  Ponto de entrada
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
