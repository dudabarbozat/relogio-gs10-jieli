"""
sonda_tamanho.py - Descobre ate que tamanho o relogio aceita iniciar um envio (passo 0x1B),
SEM transferir dados: para cada tamanho, abre o envio, anota a resposta e cancela.

Uso: python sonda_tamanho.py [recursos] /NOME=arquivo_ou_tamanho ...
  'recursos' = faz a sonda dentro do modo de recursos (entra e sai sozinho)
"""
import asyncio
import struct
import sys

from e87_badge.client import E87Client
from e87_badge.const import AE_WRITE_UUID, FLAG_COMMAND
from e87_badge.frame import build_fe_frame
from ota_recursos import ENDERECO, _cmd

NOME = "/WAT9099"


async def sondar(tamanhos):
    res = {}
    async with E87Client(ENDERECO) as c:
        seq = 0x70
        for item in tamanhos:
            nome, t, crc = item
            def prox():
                nonlocal seq
                seq = (seq + 1) & 0xFF
                return seq
            try:
                await _cmd(c, 0x1A, bytes([0x02, 0x01]) + struct.pack(">I", t) + nome.encode(), "inserir", prox())
                await _cmd(c, 0x21, bytes([0x00]), "preparar", prox())
                await _cmd(c, 0x27, bytes([0x00, 0, 0, 0, 3, 0x01]), "parametros", prox())
                f = await _cmd(c, 0x1B, struct.pack(">IH", t, crc) + b"0badc0de.tmp\x00", "metadados", prox())
                res[item] = f.body.hex(" ")
                if f.body[0] == 0:  # aceitou: cancela antes de qualquer dado
                    await c._client.write_gatt_char(AE_WRITE_UUID, build_fe_frame(FLAG_COMMAND, 0x1C, bytes([prox(), 0x00])), response=False)
                    await asyncio.sleep(1.5)
            except Exception as e:
                res[item] = f"erro {type(e).__name__}"
            try:
                await _cmd(c, 0x1A, bytes([0x02, 0x00]), "inserir fim", prox(), timeout=8)
            except Exception:
                pass
            print(f"   {nome} {t:>9} B crc {crc:04x} -> {res[item]}", flush=True)
    return res


if __name__ == "__main__":
    args = sys.argv[1:]
    modo = args and args[0] == "recursos"
    import os
    from e87_badge.crc import crc16xmodem
    tams = []
    for a in args[1 if modo else 0:]:
        nome, arq = a.split("=")  # /NOME=arquivo_local  ou  /NOME=tamanho
        if os.path.exists(arq):
            d = open(arq, "rb").read(); tams.append((nome, len(d), crc16xmodem(d)))
        else:
            tams.append((nome, int(arq), 0x1234))
    if modo:
        import ota_recursos as o
        print("entrou:", o.entrar())
    try:
        asyncio.run(sondar(tams))
    finally:
        if modo:
            print("saiu:", o.sair())
