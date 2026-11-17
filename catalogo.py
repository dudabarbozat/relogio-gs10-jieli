"""
catalogo.py - Catalogo visual dos arquivos de interface do sistema (JL, SIDEBAR, FONT, LOGO).

Para cada pacote: extrai os arquivos internos, decodifica todas as imagens dos .res/.view
em PNG e monta folhas de contato (imagens agrupadas por tamanho, com o id embaixo).
Formatos de pixel ainda desconhecidos sao contados e listados, sem travar.

Uso: python catalogo.py LOGO [FONT SIDEBAR JL]
Saida: arquivos_relogio/catalogo/<PACOTE>/...
"""
import collections
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

from abrir_mostrador import abrir
from imagens_jieli import TAM_PIXEL, decodificar

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(AQUI, "arquivos_relogio")


def grupos(d):
    """Cabecalhos de grupo: 0x08 = quantidade; 0x14 = offset do grupo 0; depois pares (indice, offset)."""
    n = struct.unpack_from("<I", d, 0x08)[0]
    gs = [struct.unpack_from("<I", d, 0x14)[0]]
    for k in range(1, n):
        idx, off = struct.unpack_from("<II", d, 0x18 + 8 * (k - 1))
        gs.append(off)
    return gs


def tabela_generica(d):
    if len(d) < 0x20 or d[:2] != b"RU":
        return []
    imgs = []
    try:
        gs = grupos(d)
    except struct.error:
        return []
    for g, base in enumerate(gs):
        pos = base + 0x2C
        while pos + 20 <= len(d):
            crc, x, idn, fmt, w, h, tam, off = struct.unpack_from("<IHBBHHII", d, pos)
            if w == 0 or h == 0 or w > 2000 or h > 2000 or off + tam > len(d) or fmt not in (0x10, 0x20, 0x24, 0x30, 0x31, 0x34):
                break
            imgs.append(dict(id=idn, x=x, fmt=fmt, w=w, h=h, tam=tam, off=off, grupo=g))
            pos += 20
    return imgs


def folha(imgs_png, titulo, saida, largura=1200):
    f = ImageFont.truetype("C:/Windows/Fonts/bahnschrift.ttf", 12)
    ft = ImageFont.truetype("C:/Windows/Fonts/bahnschrift.ttf", 18)
    x, y, alt_linha = 10, 34, 0
    pos = []
    for rot, im in imgs_png:
        w, h = im.size
        esc = min(1.0, 220 / max(w, h))
        w2, h2 = max(1, int(w * esc)), max(1, int(h * esc))
        if x + w2 + 10 > largura:
            x, y = 10, y + alt_linha + 22
            alt_linha = 0
        pos.append((rot, im, x, y, w2, h2))
        x += max(w2, 60) + 12
        alt_linha = max(alt_linha, h2)
    c = Image.new("RGB", (largura, y + alt_linha + 30), (22, 22, 28))
    d = ImageDraw.Draw(c)
    d.text((10, 6), titulo, font=ft, fill=(230, 230, 240))
    xadrez = Image.new("RGB", (8, 8), (45, 45, 55))
    for rot, im, x, y, w2, h2 in pos:
        fundo = Image.new("RGB", (w2, h2), (35, 35, 45))
        im2 = im.convert("RGBA").resize((w2, h2), Image.NEAREST)
        fundo.paste(im2, (0, 0), im2)
        c.paste(fundo, (x, y))
        d.text((x, y + h2 + 2), rot, font=f, fill=(150, 160, 180))
    c.save(saida)


def catalogar(nome):
    arq = os.path.join(BASE, nome)
    destino = os.path.join(BASE, "catalogo", nome)
    os.makedirs(destino, exist_ok=True)
    pasta_int, _ = abrir(arq)
    resumo = []
    for interno in sorted(os.listdir(pasta_int)):
        caminho = os.path.join(pasta_int, interno)
        if os.path.isdir(caminho):
            continue
        d = open(caminho, "rb").read()
        imgs = tabela_generica(d)
        if not imgs:
            resumo.append(f"  {interno}: {len(d)} B (sem tabela de imagens)")
            continue
        formatos = collections.Counter(i["fmt"] for i in imgs)
        pngs, desconhecidos = [], collections.Counter()
        pasta_png = os.path.join(destino, interno.replace(".", "_"))
        os.makedirs(pasta_png, exist_ok=True)
        for i in imgs:
            if i["fmt"] not in TAM_PIXEL:
                desconhecidos[i["fmt"]] += 1
                continue
            try:
                im = decodificar(d, i)
            except Exception:
                desconhecidos[i["fmt"]] += 1
                continue
            rot = f"g{i.get('grupo',0)} {i['id']} {i['w']}x{i['h']}"
            im.save(os.path.join(pasta_png, f"g{i.get('grupo',0)}_id{i['id']:03d}_{i['w']}x{i['h']}_f{i['fmt']:02x}.png"))
            pngs.append((rot, im))
        resumo.append(f"  {interno}: {len(imgs)} imagens, formatos {dict((hex(k), v) for k, v in formatos.items())}"
                      + (f", NAO decodificados {dict((hex(k), v) for k, v in desconhecidos.items())}" if desconhecidos else ""))
        # folhas de contato (no maximo 120 imagens por folha)
        for k in range(0, len(pngs), 120):
            folha(pngs[k:k + 120], f"{nome} / {interno}  ({k + 1}-{min(k + 120, len(pngs))} de {len(pngs)})",
                  os.path.join(destino, f"folha_{interno.replace('.', '_')}_{k // 120 + 1:02d}.png"))
    print(nome)
    print("\n".join(resumo))


if __name__ == "__main__":
    for n in sys.argv[1:]:
        catalogar(n)
