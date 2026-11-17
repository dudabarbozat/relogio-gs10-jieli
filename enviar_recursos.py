"""
enviar_recursos.py - Grava um arquivo de sistema (SIDEBAR, LOGO...) no modo de recursos.

Usa EXATAMENTE o inicio de envio da sonda_tamanho.py (que o relogio aceita nesse modo)
e depois a mesma transferencia de dados do enviar_mostrador.py.

Uso: python enviar_recursos.py ARQUIVO_LOCAL /NOME
  ex: python enviar_recursos.py arquivos_relogio/SIDEBAR /SIDEBAR
Entra no modo de recursos, envia e sempre sai do modo no fim (mesmo se der erro).
"""
import asyncio
import os
import secrets
import struct
import sys

os.environ.setdefault("MSYS_NO_PATHCONV", "1")
sys.argv += ["--dev=3"]  # o enviar_mostrador le o DEV da linha de comando

from e87_badge.client import E87Client
from e87_badge.const import AE_WRITE_UUID
from e87_badge.crc import crc16xmodem

import ota_recursos as o
from enviar_mostrador import EnvioMostrador


async def gravar(data: bytes, caminho: str):
    async with E87Client(o.ENDERECO) as c:
        seq = 0x70

        def prox():
            nonlocal seq
            seq = (seq + 1) & 0xFF
            return seq

        async def cmd(op, params, rotulo, timeout=10.0):
            f = await o._cmd(c, op, params, rotulo, prox(), timeout=timeout)
            print(f"   {rotulo}: resposta {f.body.hex(' ')}", flush=True)
            if f.body[0] != 0:
                raise RuntimeError(f"{rotulo}: relogio recusou (status {f.body[0]})")
            return f

        crc = crc16xmodem(data)
        await cmd(0x1A, bytes([0x07, 0x01]), "comecar atualizacao (op7)")
        await cmd(0x1A, bytes([0x02, 0x01]) + struct.pack(">I", len(data)) + caminho.encode(), "inserir inicio")
        try:
            await cmd(0x21, bytes([0x00]), "preparar")
            await cmd(0x27, bytes([0x00, 0, 0, 0, 3, 0x01]), "parametros")
            ack = await cmd(0x1B, struct.pack(">IH", len(data), crc) + f"{crc:04x}{len(data) & 0xFFFF:04x}.tmp".encode() + b"\x00", "metadados")  # mesmo conteudo -> mesmo nome -> retoma
            chunk = 512
            if len(ack.body) >= 4:
                dica = (ack.body[2] << 8) | ack.body[3]
                if 0 < dica <= 4096:
                    chunk = dica

            async def w(frame):
                await c._client.write_gatt_char(AE_WRITE_UUID, frame, response=False)

            sess = EnvioMostrador(w, w, c._bus)
            sess._seq = prox()
            print(f"   enviando {len(data)} bytes em pacotes de {chunk}", flush=True)
            await sess.transferir(data, chunk, caminho.strip("/"))
            seq = sess._seq
            print("ENVIO CONCLUIDO", flush=True)
        finally:
            try:
                await cmd(0x1A, bytes([0x02, 0x00]), "inserir fim", timeout=15.0)
            except Exception as e:
                print(f"   aviso: fim nao confirmado ({e})", flush=True)
            try:
                await cmd(0x1A, bytes([0x07, 0x00]), "terminar atualizacao (op7)", timeout=15.0)
            except Exception as e:
                print(f"   aviso: op7 fim nao confirmado ({e})", flush=True)


def main():
    arq, caminho = sys.argv[1], sys.argv[2]
    data = open(arq, "rb").read()
    print(f"== {arq} -> {caminho} ({len(data)} B, crc {crc16xmodem(data):04x})", flush=True)
    if not o.entrar():
        print("NAO ENTROU NO MODO; nada foi alterado", flush=True)
        o.sair()
        return 1
    ok = False
    try:
        asyncio.run(gravar(data, caminho))
        ok = True
    except Exception as e:
        print(f"FALHOU: {type(e).__name__}: {e}", flush=True)
    finally:
        if "--sem-sair" not in sys.argv:
            print("SAIU DO MODO" if o.sair() else "!!! NAO SAIU DO MODO", flush=True)
    print("RESULTADO: OK" if ok else "RESULTADO: FALHOU", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
