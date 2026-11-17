"""
enviar_mostrador.py - Envia um mostrador novo para o GS10 mini pelo protocolo
oficial de transferencia da Jieli (o relogio grava o arquivo no proprio FAT).

Sequencia (igual ao app GS Fit, RcspWatch + TransferTask com useFlash):
  1. 0x1A op2 flag1  -> "vou criar /NOME com N bytes"
  2. 0x21            -> preparar ambiente
  3. 0x27            -> parametros (memoria 0, CRC por pacote)
  4. 0x1B            -> tamanho, CRC do arquivo, nome temporario
  5. janelas de dados (0x01) com confirmacao (0x1D)
  6. relogio pede o nome (0x20) -> respondemos NOME
  7. 0x1A op2 flag0  -> fim
Protecoes: confere espaco livre, nome inexistente, faz backup do FAT antes,
e confere a lista de arquivos depois.

Uso: python enviar_mostrador.py meus_arquivos/WAT9001 [--ativar]
"""
import asyncio
import datetime
import logging
import os
import secrets
import struct
import sys

from e87_badge import protocol as P
from e87_badge.client import E87Client
from e87_badge.const import AE_WRITE_UUID, FLAG_COMMAND
from e87_badge.crc import crc16xmodem
from e87_badge.notify import wait_for_frame

from leitor_arquivos import Fat16, Flash

ENDERECO = os.environ.get("RELOGIO_MAC", "00:00:00:00:00:00")  # endereco Bluetooth do seu relogio
FORMATO_META = "e87" if "--meta-e87" in sys.argv else "app"
DEV = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--dev=")), "0"))
INSERIR = "--sem-insercao" not in sys.argv
PAUSA = "--pausa" in sys.argv
AQUI = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
logging.getLogger("e87_badge.protocol").setLevel(logging.INFO)


BLE_MAX = 240  # maior escrita BLE aceita pelo Windows neste PC (~244)


class EnvioMostrador(P.UploadSession):
    async def _escrever_partido(self, quadro: bytes):
        """Pacotes maiores que o limite BLE vao em pedacos; o relogio remonta pelo tamanho do quadro."""
        for i in range(0, len(quadro), BLE_MAX):
            await self._write_ae01(quadro[i:i + BLE_MAX])

    async def transferir(self, data: bytes, mtu: int, nome: str):
        """Igual ao TransferTask do app: atende todo pedido 0x1D [op, tamanho(2), offset(4)]
        com pacotes numerados 0,1,2... [slot, crc16(2), dados<=mtu]; responde 0x20 com o nome;
        termina no 0x1C."""
        from e87_badge.frame import build_fe_frame
        dseq = self._seq
        enviados = set()
        primeiro = True
        while True:
            # o primeiro pedido demora: o relogio prepara (apaga) o espaco do arquivo antes
            f = await wait_for_frame(
                self._bus, lambda f: (f.cmd == 0x1D and len(f.body) >= 8) or f.cmd in (0x20, 0x1C),
                timeout=120.0 if primeiro else 30.0, label="pedido do relogio")
            primeiro = False
            if f.cmd == 0x1D:
                op = f.body[1]
                tam = (f.body[2] << 8) | f.body[3]
                off = int.from_bytes(f.body[4:8], "big")
                trecho = data[off:off + tam]
                if off in enviados:
                    print(f"   relogio pediu de novo o trecho {off} ({tam} B, op {op})", flush=True)
                enviados.add(off)
                for slot, i in enumerate(range(0, len(trecho), mtu)):
                    pac = trecho[i:i + mtu]
                    crc = crc16xmodem(pac)
                    corpo = bytes([dseq & 0xFF, 0x1D, slot & 0xFF, crc >> 8, crc & 0xFF]) + pac
                    dseq += 1
                    await self._escrever_partido(build_fe_frame(0x80, 0x01, corpo))
                fim = off + len(trecho)
                if fim * 20 // len(data) != off * 20 // len(data) or fim >= len(data):
                    print(f"   {min(fim, len(data)) * 100 // len(data)}%  ({fim}/{len(data)} B)", flush=True)
            elif f.cmd == 0x20:
                self._pedidos_nome = getattr(self, "_pedidos_nome", 0) + 1
                if self._pedidos_nome > 3:  # nome recusado: cancela (status 1) em vez de repetir sem fim
                    print("   relogio recusou o nome 3 vezes -> cancelando", flush=True)
                    await self._send_fe(0x00, 0x20, bytes((0x01, f.body[0])) + nome.encode() + b"\x00\x00")
                    raise RuntimeError("nome recusado pelo relogio")
                print(f"   relogio pediu o nome -> {nome}", flush=True)
                await self._send_fe(0x00, 0x20, bytes((0x00, f.body[0])) + nome.encode() + b"\x00\x00")
            else:  # 0x1C fim da sessao
                status = f.body[1] if len(f.body) > 1 else 0xFF
                await self._send_fe(0x00, 0x1C, bytes((0x00, f.body[0])))
                print(f"   relogio fechou a transferencia com status {status} ({'OK' if status == 0 else 'ERRO'})", flush=True)
                if status != 0:
                    raise RuntimeError(f"transferencia terminou com status {status}")
                return
    async def _cmd(self, opcode, params, nome, timeout=10.0):
        seq = self._seq & 0xFF
        self._seq = (self._seq + 1) & 0xFF
        await self._send_fe(FLAG_COMMAND, opcode, bytes([seq]) + params)
        f = await wait_for_frame(
            self._bus, lambda f: f.cmd == opcode and f.flag == 0x00 and len(f.body) >= 2 and f.body[1] == seq,
            timeout=timeout, label=nome)
        print(f"   {nome}: resposta {f.body.hex(' ')}", flush=True)
        if PAUSA:
            print(f"   >>> [{datetime.datetime.now():%H:%M:%S}] PASSO '{nome}' enviado - olhe a tela (8 s)", flush=True)
            await asyncio.sleep(8)
        if f.body[0] != 0:
            raise RuntimeError(f"{nome}: relogio recusou (status {f.body[0]})")
        return f

    async def enviar(self, data: bytes, caminho: str):
        nome = caminho.strip("/")
        self._seq = 0x40
        self._file_complete_handled = False
        self._bus.clear()

        print("0) apresentacao (consultas que o app faz ao conectar)")
        await self._cmd(0x03, bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x01]), "info do dispositivo")
        await self._cmd(0x07, bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF]), "info do sistema")
        await self._cmd(0xD6, b"", "info da memoria")
        await self._cmd(0xD9, b"", "configuracao")
        if INSERIR:
            print("1) avisando criacao do arquivo")
            await self._cmd(0x1A, bytes([0x02, 0x01]) + struct.pack(">I", len(data)) + caminho.encode(), "inserir inicio")
        try:
            print("2) preparando ambiente")
            await self._cmd(0x21, bytes([0x00]), "preparar")
            print("3) parametros de transferencia")
            await self._cmd(0x27, bytes([0x00]) + struct.pack(">I", DEV) + bytes([0x01]), "parametros")
            print("4) metadados do arquivo")
            crc = crc16xmodem(data)
            if FORMATO_META == "e87":  # formato da e87_badge: 2 bytes aleatorios + nome curto
                temp = secrets.token_bytes(2) + f"{secrets.randbits(24):06x}.tmp".encode() + b"\x00"
            else:  # formato do app GS Fit
                temp = (os.environ.get("TEMP_NOME") or f"{secrets.randbits(32):08x}.tmp").encode() + b"\x00"
            ack = await self._cmd(0x1B, struct.pack(">IH", len(data), crc) + temp, "metadados")
            chunk = P.E87_DATA_CHUNK_SIZE
            if len(ack.body) >= 4:
                dica = (ack.body[2] << 8) | ack.body[3]
                if 0 < dica <= 4096:
                    chunk = dica
            print(f"5) enviando {len(data)} bytes, pacotes de {chunk} (divididos em partes de {BLE_MAX} no Bluetooth)", flush=True)
            await self.transferir(data, chunk, nome)
        except BaseException:
            await self._abort_session("erro no envio")
            raise
        finally:
            print("7) avisando fim do arquivo")
            try:
                if not INSERIR:
                    raise RuntimeError("pulado (modo sem insercao)")
                await self._cmd(0x1A, bytes([0x02, 0x00]), "inserir fim", timeout=15.0)
            except Exception as e:
                print(f"   aviso: fim nao confirmado ({e})")


async def main(arquivo, ativar):
    data = open(arquivo, "rb").read()
    caminho = "/" + os.path.basename(arquivo).upper()
    async with E87Client(ENDERECO) as c:
        cli = c._client
        flash = Flash(cli)
        orig = c._bus.push
        def push(d):
            flash.receber(bytes(d))
            orig(d)
        c._bus.push = push

        if "--direto" in sys.argv:  # sem leitura previa da flash (modo de recursos recusa envio grande depois dela)
            print("\n== envio (direto) ==")
            async def w0(frame):
                await cli.write_gatt_char(AE_WRITE_UUID, frame, response=False)
            await EnvioMostrador(w0, w0, c._bus).enviar(data, caminho)
            print("   envio direto concluido", flush=True)
            return
        print("== verificacoes (so leitura) ==")
        fs = Fat16(flash)
        await fs.montar()
        itens = await fs.listar()
        nomes = {i[0].upper() for i in itens}
        if caminho.upper() in nomes and "--substituir" not in sys.argv:
            raise SystemExit(f"ja existe {caminho} no relogio; escolha outro nome")
        ocup = sum(i[3] for i in itens)
        livre = fs.total_setores * fs.bps - ocup
        print(f"   espaco livre aprox.: {livre // 1024} KB, arquivo: {len(data) // 1024} KB")
        if len(data) > livre - 512 * 1024:
            raise SystemExit("pouco espaco livre")
        print("   backup da area de controle do FAT...")
        bkp = await flash.ler_tudo(0, fs.ini_dados)
        nome_bkp = os.path.join(AQUI, "arquivos_relogio", f"backup_fat_{datetime.datetime.now():%Y%m%d_%H%M%S}.bin")
        open(nome_bkp, "wb").write(bkp)
        print(f"   backup salvo: {nome_bkp} ({len(bkp)} B)")

        print("\n== envio ==")
        async def w(frame):
            await cli.write_gatt_char(AE_WRITE_UUID, frame, response=False)
        sess = EnvioMostrador(w, w, c._bus)
        await sess.enviar(data, caminho)

        print("\n== conferencia ==")
        await asyncio.sleep(2)
        fs2 = Fat16(flash)
        await fs2.montar()
        itens2 = await fs2.listar()
        achou = [i for i in itens2 if i[0].upper() == caminho.upper()][-1:]
        if achou and achou[0][3] == len(data):
            print(f"   OK: {caminho} esta no relogio com {achou[0][3]} bytes")
            if "--sem-releitura" not in sys.argv:
                dados_lidos = await fs2.ler_cadeia(achou[0][2], achou[0][3])
                print(f"   conteudo identico ao enviado: {dados_lidos == data}")
        else:
            print(f"   ATENCAO: {caminho} nao encontrado/tamanho diferente: {achou}")
            return

        if ativar:
            seq = 0x7E
            from e87_badge.frame import build_fe_frame
            await cli.write_gatt_char(AE_WRITE_UUID, build_fe_frame(0xC0, 0x1A, bytes([seq, 0x03, 0x01]) + caminho.encode()), response=False)
            await asyncio.sleep(2)
            print(f"   mostrador ativo trocado para {caminho}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], "--ativar" in sys.argv))
