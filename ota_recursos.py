"""
ota_recursos.py - Modo oficial de "atualizacao de recursos" do GS10 mini (usado pelo app para
trocar arquivos de sistema como SIDEBAR, JL, FONT sem que a interface os esteja usando).

  entrar : 0x1A op9 flag1  -> relogio reinicia no modo de recursos (tela "PID 739 v1000")
  sair   : 0x1A op9 flag0  -> relogio reinicia na interface normal
  modo   : 0x03 (info do dispositivo), campo LTV tipo 9, byte 2 (1 = modo de recursos)

Uso:
  python ota_recursos.py modo
  python ota_recursos.py teste                         (entra e sai sem mudar nada)
  python ota_recursos.py aplicar NOME NOVO ORIGINAL    (entra, grava NOVO por cima de /NOME, sai;
                                                        se falhar, grava ORIGINAL de volta)
"""
import os
import asyncio
import subprocess
import sys
import time

from e87_badge.client import E87Client
from e87_badge.const import AE_WRITE_UUID, FLAG_COMMAND
from e87_badge.frame import build_fe_frame
from e87_badge.notify import wait_for_frame

ENDERECO = os.environ.get("RELOGIO_MAC", "00:00:00:00:00:00")  # endereco Bluetooth do seu relogio


async def _cmd(c, op, params, rotulo, seq, timeout=10.0):
    await c._client.write_gatt_char(AE_WRITE_UUID, build_fe_frame(FLAG_COMMAND, op, bytes([seq]) + params), response=False)
    f = await wait_for_frame(c._bus, lambda f: f.cmd == op and f.flag == 0x00 and len(f.body) >= 2 and f.body[1] == seq,
                             timeout=timeout, label=rotulo)
    return f


def _modo_de(body):
    dados = body[2:]
    i = 0
    while i < len(dados):
        n = dados[i]
        if n == 0 or i + 1 + n > len(dados):
            break
        tipo, valor = dados[i + 1], dados[i + 2:i + 1 + n]
        if tipo == 9 and len(valor) >= 3:
            return valor[2]
        i += 1 + n
    return None


async def ler_modo():
    async with E87Client(ENDERECO) as c:
        f = await _cmd(c, 0x03, bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x01]), "info", 0x51)
        return _modo_de(f.body)


async def mandar_flag(flag):
    async with E87Client(ENDERECO) as c:
        try:
            f = await _cmd(c, 0x1A, bytes([0x09, flag]), "modo recursos", 0x52, timeout=8.0)
            print(f"   resposta: {f.body.hex(' ')}", flush=True)
        except Exception as e:  # o relogio pode reiniciar antes de responder
            print(f"   (sem resposta, provavelmente reiniciando: {type(e).__name__})", flush=True)


def esperar_modo(desejado, limite=240):
    t0 = time.time()
    while time.time() - t0 < limite:
        time.sleep(8)
        try:
            m = asyncio.run(ler_modo())
            print(f"   modo atual: {m} ({'RECURSOS' if m == 1 else 'NORMAL'})", flush=True)
            if m == desejado:
                return True
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt):
                raise
            print(f"   aguardando reconexao... ({type(e).__name__})", flush=True)
    return False


def entrar():
    print("== entrando no modo de recursos", flush=True)
    asyncio.run(mandar_flag(0x01))
    return esperar_modo(1)


def sair():
    print("== saindo do modo de recursos", flush=True)
    for tentativa in range(3):
        asyncio.run(mandar_flag(0x00))
        if esperar_modo(0):
            return True
    return False


def enviar(arq):
    for t in range(3):
        r = subprocess.run([sys.executable, "-u", "enviar_mostrador.py", arq, "--dev=3", "--substituir"],
                           capture_output=True, text=True)
        saida = r.stdout + r.stderr
        if "conteudo identico ao enviado: True" in saida:
            return True
        ult = [l for l in saida.splitlines() if any(k in l for k in ("fechou", "recusou", "Error", "OK:"))]
        print(f"   tentativa {t + 1} falhou: {ult[-1] if ult else saida[-200:]}", flush=True)
        time.sleep(15)
    return False


def main():
    acao = sys.argv[1] if len(sys.argv) > 1 else "modo"
    if acao == "modo":
        print(asyncio.run(ler_modo()))
    elif acao == "teste":
        ok_e = entrar()
        print(f"   entrou no modo de recursos: {ok_e}", flush=True)
        ok_s = sair()
        print(f"   voltou ao normal: {ok_s}", flush=True)
        print("TESTE OK" if (ok_e and ok_s) else "TESTE FALHOU", flush=True)
    elif acao == "aplicar":
        nome, novo, original = sys.argv[2], sys.argv[3], sys.argv[4]
        if not entrar():
            print("NAO ENTROU NO MODO; nada foi alterado", flush=True)
            sair()
            return
        print(f"== gravando {nome}", flush=True)
        if enviar(novo):
            print(f"   {nome} NOVO GRAVADO E CONFERIDO", flush=True)
        else:
            print(f"   {nome} falhou -> regravando original", flush=True)
            print("   ORIGINAL RESTAURADO" if enviar(original) else "   !!! ORIGINAL NAO RESTAURADO", flush=True)
        print("SAIU DO MODO" if sair() else "!!! NAO SAIU DO MODO", flush=True)


if __name__ == "__main__":
    main()
