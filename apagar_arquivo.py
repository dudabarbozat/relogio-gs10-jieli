"""
apagar_arquivo.py - Apaga UM arquivo do GS10 mini pelo processo oficial do app (RcspWatch):
  1. 0x1A op5 flag1 "/NOME"         (inicio da exclusao)
  2. 0x21 [01]                      (preparar ambiente de exclusao)
  3. 0x1F [ultimo=1, dev(4), tipo=1 (arquivo), cluster(4)]
  4. 0x1A op5 flag0                 (fim)
Protecoes: so apaga nomes da lista PERMITIDOS; confere a lista de arquivos antes e depois;
nunca apaga JL, FONT, SIDEBAR, LOGO.

Uso: python apagar_arquivo.py NOME   (ex.: WAT9001)
"""
import os
import asyncio
import struct
import sys

from e87_badge.client import E87Client
from e87_badge.const import FLAG_COMMAND
from e87_badge.notify import wait_for_frame

from leitor_arquivos import Fat16, Flash

ENDERECO = os.environ.get("RELOGIO_MAC", "00:00:00:00:00:00")  # endereco Bluetooth do seu relogio
DEV = 3
PROIBIDOS = {"JL", "FONT", "SIDEBAR"}
PERMITIDOS = {"WAT9001", "WATCH6", "WATCH7", "LOGO", "WAT3719", "WATCH36", "WAT2386", "WAT1733", "WAT3801", "WATCH473", "BGP_B473"} | {f"WAT90{n:02d}" for n in range(100)} | {"1FF0AED4.TMP", "0BADC0DE.TMP"}


async def main(nome):
    nome = nome.upper()
    # excecao unica e explicita: SIDEBAR so com --sistema-sidebar (usado no modo de recursos para troca-lo)
    liberado = nome in ("SIDEBAR", "JL") and "--sistema-sidebar" in sys.argv
    if not liberado and (nome in PROIBIDOS or nome not in PERMITIDOS):
        raise SystemExit(f"nao autorizado a apagar {nome}")
    async with E87Client(ENDERECO) as c:
        cli = c._client
        flash = Flash(cli)
        orig = c._bus.push
        def push(d):
            flash.receber(bytes(d))
            orig(d)
        c._bus.push = push

        fs = Fat16(flash)
        await fs.montar()
        itens = await fs.listar()
        alvo = [i for i in itens if i[0].upper() == "/" + nome]
        if not alvo:
            raise SystemExit(f"/{nome} nao existe no relogio")
        caminho, _, cluster, tam = alvo[0]
        print(f"vou apagar {caminho} ({tam} B, cluster {cluster})")

        seq = [0x60]
        async def cmd(op, params, rotulo, timeout=10.0):
            s = seq[0] & 0xFF
            seq[0] += 1
            from e87_badge.frame import build_fe_frame
            from e87_badge.const import AE_WRITE_UUID
            await cli.write_gatt_char(AE_WRITE_UUID, build_fe_frame(FLAG_COMMAND, op, bytes([s]) + params), response=False)
            f = await wait_for_frame(c._bus, lambda f: f.cmd == op and f.flag == 0x00 and len(f.body) >= 2 and f.body[1] == s, timeout=timeout, label=rotulo)
            print(f"   {rotulo}: resposta {f.body.hex(' ')}")
            if f.body[0] != 0:
                raise RuntimeError(f"{rotulo}: recusado (status {f.body[0]})")
            return f

        await cmd(0x1A, bytes([0x05, 0x01]) + caminho.encode(), "inicio da exclusao")
        try:
            await cmd(0x21, bytes([0x01]), "preparar exclusao")
            await cmd(0x1F, bytes([0x01]) + struct.pack(">I", DEV) + bytes([0x01]) + struct.pack(">I", cluster), "apagar", timeout=20.0)
        finally:
            await cmd(0x1A, bytes([0x05, 0x00]), "fim da exclusao", timeout=15.0)

        await asyncio.sleep(1.5)
        fs2 = Fat16(flash)
        await fs2.montar()
        nomes = [i[0].upper() for i in await fs2.listar()]
        print(f"   {caminho} ainda existe? {'/' + nome in nomes}")
        print("   arquivos agora:", ", ".join(n.strip('/') for n in nomes))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
