"""
imagens_jieli.py - Decodifica as imagens dos arquivos .res/.view/BGP dos mostradores Jieli.

Formato descoberto (W002):
  Pacote "RU41"/"RU30": tabela de imagens com entradas de 20 bytes a partir de 0x44:
      [crc 4][? 2][id 1][formato 1][largura 2][altura 2][tamanho 4][offset 4]
  Cada imagem: tabela de linhas (4 bytes por linha, little-endian):
      bits 0-20 = offset da linha (a partir do inicio da imagem)
      bits 21-31 = tamanho da linha compactada
  Linha compactada (RLE):
      controle >= 0x80 -> repete o proximo pixel (controle & 0x7F) vezes
      controle <  0x80 -> os proximos 'controle' pixels vem literais
  Pixel (formato 0x30): [alfa][RGB565 big-endian] = 3 bytes

Uso: python imagens_jieli.py ARQUIVO.res [pasta_saida]
"""
import os
import struct
import sys

from PIL import Image


def tabela(d):
    imgs = []
    pos = struct.unpack_from("<I", d, 0x18)[0] + 0x0C  # 0x44 em RU41, 0x3C em RU30
    while pos + 20 <= len(d):
        crc, _, idn, fmt, w, h, tam, off = struct.unpack_from("<IHBBHHII", d, pos)
        if w == 0 or h == 0 or off == 0 or off + tam > len(d) or w > 2000 or h > 2000:
            break
        imgs.append(dict(id=idn, fmt=fmt, w=w, h=h, tam=tam, off=off))
        pos += 20
    return imgs


def rgb565(hi, lo, be=True):
    v = (hi << 8 | lo) if be else (lo << 8 | hi)
    r = (v >> 11) & 0x1F
    g = (v >> 5) & 0x3F
    b = v & 0x1F
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)


def ler_pixel(fmt, b, be=True):
    if fmt == 0x20:  # ARGB8888
        return (b[1], b[2], b[3], b[0])
    if fmt == 0x24:  # RGB565 sem alfa
        return rgb565(b[0], b[1], be) + (255,)
    return rgb565(b[1], b[2], be) + (b[0],)  # 0x30: alfa + RGB565


TAM_PIXEL = {0x20: 4, 0x24: 2, 0x30: 3}


def decodificar(d, img, be=True):
    fmt = img["fmt"]
    tp = TAM_PIXEL.get(fmt, 3)
    base, w, h = img["off"], img["w"], img["h"]
    px = Image.new("RGBA", (w, h))
    p = px.load()
    for y in range(h):
        ent = struct.unpack_from("<I", d, base + 4 * y)[0]
        off, ln = ent & 0x1FFFFF, ent >> 21
        lin = d[base + off: base + off + ln]
        i = x = 0
        while i < len(lin) and x < w:
            c = lin[i]; i += 1
            if c & 0x80:
                n = c & 0x7F
                cor = ler_pixel(fmt, lin[i:i + tp], be); i += tp
                for _ in range(n):
                    if x < w:
                        p[x, y] = cor
                    x += 1
            else:
                for _ in range(c):
                    cor = ler_pixel(fmt, lin[i:i + tp], be); i += tp
                    if x < w:
                        p[x, y] = cor
                    x += 1
    return px


def extrair(caminho, saida=None, be=True):
    d = open(caminho, "rb").read()
    saida = saida or caminho + "_png"
    os.makedirs(saida, exist_ok=True)
    out = []
    for img in tabela(d):
        try:
            im = decodificar(d, img, be)
        except Exception as e:
            print(f"  id {img['id']}: erro {e}")
            continue
        nome = os.path.join(saida, f"img{img['id']:03d}_f{img['fmt']:02x}_{img['w']}x{img['h']}.png")
        im.save(nome)
        out.append(nome)
        print(f"  id {img['id']:3d} formato 0x{img['fmt']:02x} {img['w']}x{img['h']} -> {os.path.basename(nome)}")
    return out


if __name__ == "__main__":
    extrair(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
