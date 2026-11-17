"""
abrir_mostrador.py - Abre o pacote de um mostrador Jieli (WATCHx / WATxxxx)
e extrai os arquivos internos (.sty, .res, .view, .str, .json, index.idx).

Uso: python abrir_mostrador.py arquivos_relogio/WATCH6
Saida: arquivos_relogio/WATCH6_aberto/
"""
import os
import struct
import sys


def abrir(caminho):
    d = open(caminho, "rb").read()
    saida = caminho + "_aberto"
    os.makedirs(saida, exist_ok=True)
    entradas = []
    # tabela: blocos de 32 bytes = [crc 4][offset 4][tamanho 4][tipo 1][ff][?][?][nome 16]
    for i in range(0, 32 * 64, 32):
        e = d[i:i + 32]
        if len(e) < 32:
            break
        nome = e[16:32].split(b"\x00")[0]
        if not nome or not all(32 <= c < 127 for c in nome):
            break
        off, tam = struct.unpack_from("<II", e, 4)
        entradas.append((nome.decode(), off, tam, e[12]))
    for nome, off, tam, tipo in entradas:
        if nome == "res" and tipo == 3:  # entrada raiz (o proprio pacote)
            print(f"  [raiz] {tam} B")
            continue
        dados = d[off:off + tam]
        open(os.path.join(saida, nome), "wb").write(dados)
        print(f"  {nome:<14} offset 0x{off:06x}  {tam:>8} B")
    return saida, entradas


if __name__ == "__main__":
    for c in sys.argv[1:]:
        print(c)
        abrir(c)
