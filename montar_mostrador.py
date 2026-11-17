"""
montar_mostrador.py - Codificador do formato de mostrador Jieli (W002).

Funcoes:
  codificar_imagem(im, fmt)        PNG/PIL -> bytes no formato do relogio (tabela de linhas + RLE)
  montar_res(modelo_res, imagens)  troca as imagens de um .res existente, mantendo ids/ordem
  montar_pacote(modelo, arquivos)  remonta o pacote WATxxxx com CRCs corretos

Teste (sem relogio): python montar_mostrador.py teste
  -> decodifica o WAT3719, recodifica tudo e compara byte a byte com o original.
"""
import struct
import sys

from e87_badge.crc import crc16xmodem as c16
from PIL import Image

TAM_PIXEL = {0x20: 4, 0x24: 2, 0x30: 3}


def px_bytes(fmt, rgba):
    r, g, b, a = rgba
    v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    if fmt == 0x20:
        return bytes((a, r, g, b))
    if fmt == 0x24:
        return bytes((v >> 8, v & 0xFF))
    return bytes((a, v >> 8, v & 0xFF))


def codificar_linha(pix, fmt):
    """RLE: ctrl>=0x80 repete (ctrl&0x7F) vezes; ctrl<0x80 = N literais. Max 127."""
    out = bytearray()
    i, n = 0, len(pix)
    literais = []

    def despeja():
        while literais:
            bloco = literais[:127]
            del literais[:127]
            out.append(len(bloco))
            for p in bloco:
                out.extend(p)

    while i < n:
        j = i
        while j < n and pix[j] == pix[i] and j - i < 127:
            j += 1
        run = j - i
        # regra da Jieli: pares iguais ficam como literais, e um bloco de
        # literais quase cheio (>=125) e completado ate 127 antes de abrir repeticao
        if run >= 3 and not (125 <= len(literais) % 127 < 127 and literais):
            despeja()
            out.append(0x80 | run)
            out += pix[i]
            i = j
        else:
            literais.append(pix[i])
            i += 1
    despeja()
    return bytes(out)


def codificar_imagem(im, fmt):
    im = im.convert("RGBA")
    w, h = im.size
    p = im.load()
    linhas = []
    for y in range(h):
        pix = [px_bytes(fmt, p[x, y]) for x in range(w)]
        linhas.append(codificar_linha(pix, fmt))
    tabela = bytearray()
    off = 4 * h
    for ln in linhas:
        tabela += struct.pack("<I", (len(ln) << 21) | off)
        off += len(ln)
    return bytes(tabela) + b"".join(linhas)


# ---------- .res ----------

def ler_tabela_res(d):
    inicio = struct.unpack_from("<I", d, 0x18)[0] + 0x0C
    ents = []
    pos = inicio
    while pos + 20 <= len(d):
        crc, x, idn, fmt, w, h, tam, off = struct.unpack_from("<IHBBHHII", d, pos)
        if w == 0 or h == 0 or w > 2000 or h > 2000:
            break
        ents.append(dict(pos=pos, x=x, id=idn, fmt=fmt, w=w, h=h, tam=tam, off=off))
        pos += 20
    return inicio, ents


def montar_res(modelo: bytes, novas: dict, zerar_cabecalho=False):
    """novas = {id: bytes_codificados}. Mantem ordem e cabecalho do modelo; recalcula offsets e CRCs."""
    inicio, ents = ler_tabela_res(modelo)
    dados_ini = min(e["off"] for e in ents)
    cab = bytearray(modelo[:dados_ini])
    # dados na mesma ordem fisica do modelo
    ordem = sorted(ents, key=lambda e: e["off"])
    corpo = bytearray()
    novos_off = {}
    for e in ordem:
        blob = novas.get(e["id"], modelo[e["off"]:e["off"] + e["tam"]])
        # preserva o alinhamento/folga original entre imagens
        alvo = dados_ini + len(corpo)
        novos_off[e["id"]] = (alvo, len(blob))
        corpo += blob  # imagens coladas, sem folga (igual ao original)
    for e in ents:
        off, tam = novos_off[e["id"]]
        blob = bytes(corpo[off - dados_ini: off - dados_ini + tam])
        ent = bytearray(cab[e["pos"]:e["pos"] + 20])
        struct.pack_into("<II", ent, 12, tam, off)
        struct.pack_into("<H", ent, 2, c16(blob))
        struct.pack_into("<H", ent, 0, c16(bytes(ent[2:])))
        cab[e["pos"]:e["pos"] + 20] = ent
    if zerar_cabecalho:
        struct.pack_into("<I", cab, 0x0C, 0)
    return bytes(cab) + bytes(corpo)


# ---------- pacote ----------

def ler_pacote(d):
    ents = []
    for i in range(0, 32 * 64, 32):
        e = d[i:i + 32]
        nome = e[16:32].split(b"\x00")[0]
        if not nome or not all(32 <= c < 127 for c in nome):
            break
        off, tam = struct.unpack_from("<II", e, 4)
        ents.append(dict(i=i, nome=nome.decode(), off=off, tam=tam, raw=bytes(e)))
    return ents


def montar_pacote(modelo: bytes, arquivos: dict):
    """arquivos = {nome_interno: bytes}. Mesma ordem e cabecalho do modelo, offsets/CRCs recalculados."""
    ents = ler_pacote(modelo)
    raiz = ents[0]
    filhos = ents[1:]
    dados_ini = min(e["off"] for e in filhos)
    cab = bytearray(modelo[:dados_ini])
    corpo = bytearray()
    for e in sorted(filhos, key=lambda e: e["off"]):
        blob = arquivos.get(e["nome"], modelo[e["off"]:e["off"] + e["tam"]])
        e["novo_off"] = dados_ini + len(corpo)
        e["novo_tam"] = len(blob)
        corpo += blob
        # cada arquivo interno comeca em multiplo de 16; folga preenchida com FF
        corpo += b"\xff" * ((-(dados_ini + len(corpo))) % 16)
    total = bytes(cab) + bytes(corpo)
    total = bytearray(total)
    for e in filhos:
        ent = bytearray(e["raw"])
        struct.pack_into("<II", ent, 4, e["novo_off"], e["novo_tam"])
        blob = bytes(total[e["novo_off"]:e["novo_off"] + e["novo_tam"]])
        struct.pack_into("<H", ent, 2, c16(blob))
        struct.pack_into("<H", ent, 0, c16(bytes(ent[2:])))
        total[e["i"]:e["i"] + 32] = ent
    # raiz: cobre do offset dela ate o fim
    ent = bytearray(raiz["raw"])
    roff = raiz["off"]
    struct.pack_into("<I", ent, 8, len(total))
    blob = bytes(total[roff:len(total)])
    struct.pack_into("<H", ent, 2, c16(blob))
    struct.pack_into("<H", ent, 0, c16(bytes(ent[2:])))
    total[raiz["i"]:raiz["i"] + 32] = ent
    return bytes(total)


def montar_res_grupos(modelo: bytes, novas: dict, entradas: list, grupos_off: list) -> bytes:
    """Remonta um .res de VARIOS grupos trocando imagens de tamanho diferente.
    novas = {(grupo, id): blob}; entradas = lista de dict(grupo,id,off,tam,pos);
    grupos_off = offsets (antigos) dos cabecalhos de grupo.
    Copia tudo que nao e imagem byte a byte; atualiza offsets/tamanhos/CRCs das entradas,
    a tabela de grupos (0x14 e pares em 0x18) e os ponteiros absolutos de cada grupo (G+0x00, G+0x10, G+0x24)."""
    # imagens fisicas unicas (varias entradas podem apontar para o mesmo dado)
    fisicas = {}
    for e in entradas:
        fisicas.setdefault((e["off"], e["tam"]), []).append((e["grupo"], e["id"]))
    saida = bytearray()
    cursor = 0
    mapa = []  # (fim_antigo, delta acumulado)
    delta = 0
    novos_pos = {}
    for (off, tam) in sorted(fisicas):
        saida += modelo[cursor:off]
        chaves = fisicas[(off, tam)]
        # entradas que compartilhavam a mesma imagem mas agora recebem imagens DIFERENTES sao separadas:
        # cada blob distinto e gravado uma vez, em sequencia, no lugar da imagem antiga
        distintos = {}
        for k in chaves:
            blob = novas.get(k, modelo[off:off + tam])
            distintos.setdefault(blob, []).append(k)
        total = 0
        for blob, ks in distintos.items():
            for k in ks:
                novos_pos[k] = (len(saida), len(blob))
            saida += blob
            total += len(blob)
        cursor = off + tam
        delta += total - tam
        mapa.append((cursor, delta))
    saida += modelo[cursor:]

    def novo_off(antigo):
        d = 0
        for limite, dl in mapa:
            if antigo >= limite:
                d = dl
            else:
                break
        return antigo + d

    def u32(p_antigo, valor_antigo):
        struct.pack_into("<I", saida, novo_off(p_antigo), novo_off(valor_antigo))

    u32(0x14, grupos_off[0])
    for k in range(1, len(grupos_off)):
        p = 0x18 + 8 * (k - 1) + 4
        u32(p, grupos_off[k])
    for g in grupos_off:
        for campo in (0x00, 0x10, 0x24):  # 3 ponteiros absolutos por grupo
            antigo = struct.unpack_from("<I", modelo, g + campo)[0]
            u32(g + campo, antigo)
    for e in entradas:
        p = novo_off(e["pos"])
        off, tam = novos_pos[(e["grupo"], e["id"])]
        ent = bytearray(saida[p:p + 20])
        struct.pack_into("<II", ent, 12, tam, off)
        struct.pack_into("<H", ent, 2, c16(bytes(saida[off:off + tam])))
        struct.pack_into("<H", ent, 0, c16(bytes(ent[2:])))
        saida[p:p + 20] = ent
    return bytes(saida)


def renomear_pacote(pacote: bytes, antigo: str, novo: str) -> bytes:
    """Renomeia os arquivos internos (antigo.* -> novo.*, mesmo tamanho de nome),
    recalcula os CRCs do index.idx e troca o prj_uuid do json. Depois recalcula o pacote."""
    import uuid
    assert len(antigo) == len(novo)
    d = bytearray(pacote)
    a, n = antigo.lower().encode(), novo.lower().encode()
    ents = ler_pacote(bytes(d))
    for e in ents[1:]:  # nomes na tabela do pacote
        nome = d[e["i"] + 16:e["i"] + 32]
        d[e["i"] + 16:e["i"] + 32] = nome.replace(a, n)
    ents = ler_pacote(bytes(d))
    idx = next(e for e in ents if e["nome"] == "index.idx")
    p, fim = idx["off"] + 16, idx["off"] + idx["tam"]
    while p < fim:
        tam = d[p + 2]
        ent = bytearray(d[p:p + tam]).replace(a, n)
        struct.pack_into("<H", ent, 0, c16(bytes(ent[2:])))  # CRC da propria entrada
        d[p:p + tam] = ent
        p += tam
    js = next(e for e in ents if e["nome"].endswith(".json"))
    texto = bytes(d[js["off"]:js["off"] + js["tam"]])
    import re
    novo_uuid = str(uuid.uuid4()).upper().encode()
    texto2 = re.sub(rb'"prj_uuid": "[0-9A-F-]{36}"', b'"prj_uuid": "' + novo_uuid + b'"', texto)
    assert len(texto2) == len(texto)
    d[js["off"]:js["off"] + js["tam"]] = texto2
    return montar_pacote(bytes(d), {})  # recalcula CRCs de todas as entradas


# ---------- teste de ida e volta ----------

def teste():
    sys.path.insert(0, ".")
    from imagens_jieli import decodificar
    orig = open("arquivos_relogio/WAT3719", "rb").read()
    ents = ler_pacote(orig)
    res_e = next(e for e in ents if e["nome"].endswith(".res"))
    res = orig[res_e["off"]:res_e["off"] + res_e["tam"]]
    _, imgs = ler_tabela_res(res)
    novas = {}
    iguais = 0
    for e in imgs:
        im = decodificar(res, e)
        enc = codificar_imagem(im, e["fmt"])
        if enc == res[e["off"]:e["off"] + e["tam"]]:
            iguais += 1
        novas[e["id"]] = enc
    print(f"imagens identicas ao original: {iguais}/{len(imgs)}")
    res2 = montar_res(res, novas)
    print(f".res recriado identico: {res2 == res}  ({len(res2)} vs {len(res)} B)")
    pac = montar_pacote(orig, {res_e["nome"]: res2})
    print(f"pacote recriado identico: {pac == orig}  ({len(pac)} vs {len(orig)} B)")
    if pac != orig:
        dif = next(i for i in range(min(len(pac), len(orig))) if pac[i] != orig[i])
        print(f"  primeira diferenca em 0x{dif:x}")


if __name__ == "__main__":
    if sys.argv[1:] == ["teste"]:
        teste()
