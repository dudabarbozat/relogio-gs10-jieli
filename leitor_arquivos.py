"""
leitor_arquivos.py - Le o sistema de arquivos (FAT16) do GS10 mini pelo Bluetooth.

SO LEITURA: usa apenas o comando 0x1A op 1 (ler bloco). Nada e gravado no relogio.

Uso:
    python leitor_arquivos.py listar            -> mostra todos os arquivos
    python leitor_arquivos.py copiar [FILTRO]   -> copia arquivos para arquivos_relogio/
                                                   (FILTRO opcional, ex: WATCH)
"""
import asyncio
import os
import struct
import sys
import time

from e87_badge.client import E87Client
from e87_badge.const import AE_WRITE_UUID
from e87_badge.frame import build_fe_frame

ENDERECO = os.environ.get("RELOGIO_MAC", "00:00:00:00:00:00")  # endereco Bluetooth do seu relogio
PASTA_SAIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arquivos_relogio")
BLOCO = 256  # bytes por leitura (o relogio informou blocos de 256)


class Flash:
    """Leitura de blocos da memoria externa via 0x1A op 1, com remontagem de pacotes."""

    def __init__(self, cli):
        self.cli = cli
        self.seq = 0x20
        self.buf = b""
        self.esperando = {}  # seq -> Future

    def receber(self, data: bytes):
        self.buf += data
        while True:
            i = self.buf.find(b"\xfe\xdc\xba")
            if i < 0:
                self.buf = b""
                return
            self.buf = self.buf[i:]
            if len(self.buf) < 7:
                return
            n = (self.buf[5] << 8) | self.buf[6]
            total = 7 + n + 1
            if len(self.buf) < total:
                return
            quadro, self.buf = self.buf[:total], self.buf[total:]
            if quadro[-1] != 0xEF:
                continue
            flag, cmd, corpo = quadro[3], quadro[4], quadro[7:-1]
            if cmd == 0x1A and flag == 0x00 and len(corpo) >= 2:
                status, seq = corpo[0], corpo[1]
                fut = self.esperando.pop(seq, None)
                if fut and not fut.done():
                    fut.set_result((status, corpo[2:]))

    async def ler(self, offset: int, tamanho: int, tentativas=4) -> bytes:
        for t in range(tentativas):
            seq = self.seq & 0xFF
            self.seq += 1
            params = bytes([0x01, 0x00]) + struct.pack(">IH", offset, tamanho)
            fut = asyncio.get_event_loop().create_future()
            self.esperando[seq] = fut
            await self.cli.write_gatt_char(
                AE_WRITE_UUID, build_fe_frame(0xC0, 0x1A, bytes([seq]) + params), response=False)
            try:
                status, dados = await asyncio.wait_for(fut, 3.0)
            except asyncio.TimeoutError:
                self.esperando.pop(seq, None)
                continue
            if status != 0:
                raise IOError(f"relogio recusou leitura em 0x{offset:x} (status {status})")
            # primeiro byte e um prefixo da resposta; o resto sao os dados
            dados = dados[1:]
            return dados[:tamanho]
        raise TimeoutError(f"sem resposta lendo 0x{offset:x}")

    async def ler_tudo(self, offset: int, tamanho: int) -> bytes:
        partes = []
        pos = 0
        while pos < tamanho:
            n = min(BLOCO, tamanho - pos)
            partes.append(await self.ler(offset + pos, n))
            pos += n
        return b"".join(partes)


class Fat16:
    def __init__(self, flash: Flash):
        self.f = flash

    async def montar(self):
        bs = await self.f.ler_tudo(0, 512)
        if bs[0x36:0x3B] != b"FAT16":
            raise RuntimeError("setor de inicializacao nao e FAT16: " + bs[:64].hex(" "))
        self.bps = struct.unpack_from("<H", bs, 0x0B)[0]
        self.spc = bs[0x0D]
        self.reservados = struct.unpack_from("<H", bs, 0x0E)[0]
        self.nfats = bs[0x10]
        self.entradas_raiz = struct.unpack_from("<H", bs, 0x11)[0]
        total16 = struct.unpack_from("<H", bs, 0x13)[0]
        self.spf = struct.unpack_from("<H", bs, 0x16)[0]
        total32 = struct.unpack_from("<I", bs, 0x20)[0]
        self.total_setores = total16 or total32
        self.tam_cluster = self.bps * self.spc
        self.ini_fat = self.reservados * self.bps
        self.ini_raiz = self.ini_fat + self.nfats * self.spf * self.bps
        self.ini_dados = self.ini_raiz + self.entradas_raiz * 32
        print(f"FAT16: {self.bps} bytes/setor, {self.spc} setores/cluster, "
              f"{self.total_setores * self.bps // 1024} KB no total")
        self.fat = await self.f.ler_tudo(self.ini_fat, self.spf * self.bps)

    def cadeia(self, cluster):
        c = []
        while 2 <= cluster < 0xFFF8 and len(c) < 100000:
            c.append(cluster)
            cluster = struct.unpack_from("<H", self.fat, cluster * 2)[0]
        return c

    def off_cluster(self, c):
        return self.ini_dados + (c - 2) * self.tam_cluster

    async def ler_cadeia(self, cluster, tamanho=None):
        partes = []
        for c in self.cadeia(cluster):
            partes.append(await self.f.ler_tudo(self.off_cluster(c), self.tam_cluster))
            if tamanho is not None and sum(map(len, partes)) >= tamanho:
                break
        dados = b"".join(partes)
        return dados[:tamanho] if tamanho is not None else dados

    async def copiar_retomando(self, cluster, tamanho, destino):
        """Copia cluster a cluster para destino.parcial; se ja existir, continua de onde parou."""
        parcial = destino + ".parcial"
        feito = os.path.getsize(parcial) if os.path.exists(parcial) else 0
        feito -= feito % self.tam_cluster
        cadeia = self.cadeia(cluster)
        with open(parcial, "r+b" if os.path.exists(parcial) else "wb") as fh:
            fh.seek(feito)
            fh.truncate()
            t0 = time.time()
            for idx in range(feito // self.tam_cluster, len(cadeia)):
                bloco = await self.f.ler_tudo(self.off_cluster(cadeia[idx]), self.tam_cluster)
                fh.write(bloco)
                fh.flush()
                pos = (idx + 1) * self.tam_cluster
                if idx % 16 == 0:
                    vel = (pos - feito) / max(time.time() - t0, 0.1) / 1024
                    print(f"     {min(pos, tamanho) * 100 // tamanho}%  ({vel:.1f} KB/s)", flush=True)
        with open(parcial, "r+b") as fh:
            fh.truncate(tamanho)
        os.replace(parcial, destino)

    @staticmethod
    def entradas(bruto):
        lfn = []
        for i in range(0, len(bruto), 32):
            e = bruto[i:i + 32]
            if len(e) < 32 or e[0] == 0x00:
                break
            if e[0] == 0xE5:
                lfn = []
                continue
            attr = e[11]
            if attr == 0x0F:  # parte de nome longo
                parte = e[1:11] + e[14:26] + e[28:32]
                lfn.insert(0, parte.decode("utf-16le", "ignore").split("\x00")[0].replace("￿", ""))
                continue
            if attr & 0x08:  # rotulo de volume
                lfn = []
                continue
            curto = e[0:8].decode("latin1").rstrip()
            ext = e[8:11].decode("latin1").rstrip()
            nome = "".join(lfn) or (curto + ("." + ext if ext else ""))
            lfn = []
            if nome in (".", ".."):
                continue
            cluster = struct.unpack_from("<H", e, 26)[0]
            tamanho = struct.unpack_from("<I", e, 28)[0]
            yield nome, bool(attr & 0x10), cluster, tamanho

    async def listar(self, caminho="", cluster=None):
        if cluster is None:
            bruto = await self.f.ler_tudo(self.ini_raiz, self.entradas_raiz * 32)
        else:
            bruto = await self.ler_cadeia(cluster)
        itens = []
        for nome, eh_pasta, cl, tam in self.entradas(bruto):
            completo = f"{caminho}/{nome}"
            itens.append((completo, eh_pasta, cl, tam))
            if eh_pasta and cl:
                itens += await self.listar(completo, cl)
        return itens


async def main(acao, filtro):
    async with E87Client(ENDERECO) as c:
        flash = Flash(c._client)
        orig = c._bus.push
        def push(d):
            flash.receber(bytes(d))
            orig(d)
        c._bus.push = push

        fs = Fat16(flash)
        await fs.montar()
        itens = await fs.listar()
        total = 0
        print()
        for caminho, eh_pasta, cl, tam in itens:
            if eh_pasta:
                print(f"  [pasta] {caminho}")
            else:
                total += tam
                print(f"  {tam:>10,} B  {caminho}".replace(",", "."))
        print(f"\n{sum(1 for i in itens if not i[1])} arquivos, {total // 1024} KB ocupados")

        if acao == "copiar":
            os.makedirs(PASTA_SAIDA, exist_ok=True)
            filtros = [x.upper() for x in filtro.split(",") if x]
            alvo = [i for i in itens if not i[1] and (not filtros or any(f in i[0].upper() for f in filtros))]
            alvo.sort(key=lambda i: i[3])  # menores primeiro
            for caminho, eh_pasta, cl, tam in alvo:
                destino = os.path.join(PASTA_SAIDA, caminho.strip("/").replace("/", os.sep))
                if os.path.exists(destino) and os.path.getsize(destino) == tam:
                    print(f"  ja copiado {caminho}")
                    continue
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                print(f"  copiando {caminho} ({tam} B)...", flush=True)
                t0 = time.time()
                await fs.copiar_retomando(cl, tam, destino)
                print(f"  copiado {caminho} ({tam} B, {time.time() - t0:.1f}s)", flush=True)
            print(f"\nArquivos em: {PASTA_SAIDA}")
            return True


def rodar(acao, filtro):
    if acao != "copiar":
        asyncio.run(main(acao, filtro))
        return
    # reconecta e continua de onde parou, ate 20 tentativas
    for tentativa in range(1, 21):
        try:
            if asyncio.run(main(acao, filtro)):
                return
        except KeyboardInterrupt:
            raise
        except BaseException as e:  # inclui CancelledError do bleak quando a conexao cai
            print(f"\n!! conexao caiu ({type(e).__name__}: {e}). Tentativa {tentativa}/20 em 10s...", flush=True)
            time.sleep(10)
    print("Desisti apos 20 tentativas. Rode de novo: o progresso esta salvo.")


if __name__ == "__main__":
    acao = sys.argv[1] if len(sys.argv) > 1 else "listar"
    filtro = sys.argv[2] if len(sys.argv) > 2 else ""
    rodar(acao, filtro)
