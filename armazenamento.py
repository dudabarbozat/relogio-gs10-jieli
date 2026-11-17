"""
armazenamento.py - Consulta (so leitura) os armazenamentos que o relogio anuncia e seus
identificadores (devHandler), via 0x07 GetSysInfo [FF, mascara 00000004] -> atributo tipo 2.

Uso: python armazenamento.py
"""
import asyncio

from e87_badge.client import E87Client
from ota_recursos import ENDERECO, _cmd

NOMES = {0: "USB", 1: "SD0", 2: "SD1", 3: "FLASH", 4: "LINEIN", 5: "FLASH2"}


def ler_ltv(d):
    i = 0
    while i < len(d):
        n = d[i]
        if n == 0 or i + 1 + n > len(d):
            break
        yield d[i + 1], d[i + 2:i + 1 + n]
        i += 1 + n


def interpretar(dados):
    estados = []
    if dados and dados[0] == 0xFF:  # formato novo
        for tipo, v in ler_ltv(dados[2:]):
            if tipo == 1:
                j = 0
                while j + 2 <= len(v):
                    online, idx = v[j] == 1, v[j + 1]
                    if online and j + 6 <= len(v):
                        estados.append((idx, online, int.from_bytes(v[j + 2:j + 6], "big")))
                        j += 6
                    else:
                        estados.append((idx, online, None))
                        j += 2
    else:
        b, j = dados[0], 1
        for idx in range(6):
            if idx == 4:
                continue
            if j + 4 > len(dados):
                break
            estados.append((idx, bool(b >> idx & 1), int.from_bytes(dados[j:j + 4], "big")))
            j += 4
    return estados


async def consultar():
    async with E87Client(ENDERECO) as c:
        f = await _cmd(c, 0x07, bytes([0xFF, 0x00, 0x00, 0x00, 0x04]), "sysinfo", 0x61)
        print("   resposta:", f.body.hex(" "))
        for tipo, v in ler_ltv(f.body[3:]):
            if tipo == 2:
                for idx, online, h in interpretar(v):
                    print(f"   {NOMES.get(idx, idx)}: online={online} devHandler={h}")


if __name__ == "__main__":
    asyncio.run(consultar())
