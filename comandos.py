"""
comandos.py - Comandos oficiais do GS10 mini, tirados do codigo do app GS Fit.

Formato de todo comando (protocolo RCSP da Jieli):
    FE DC BA | C0 | opcode | tamanho (2 bytes) | SEQ | parametros | EF
O SEQ e um contador que sobe a cada comando (faltava nos testes antigos).

Comandos do fabricante usam opcode 0xFF e parametros comecando com FF.

NUNCA MANDAR:
    FF 38  -> desvincula o relogio
    FF 08  -> (provavel) restaurar padroes de fabrica

Uso:
    python comandos.py achar      -> relogio vibra/toca por alguns segundos
    python comandos.py agua       -> consulta o lembrete de agua
    python comandos.py agua-on    -> liga lembrete 08:00-20:00 a cada 1h
    python comandos.py agua-off   -> desliga lembrete
"""
import os
import asyncio
import sys

from e87_badge.client import E87Client
from e87_badge.const import AE_WRITE_UUID, AE_NOTIFY_UUID
from e87_badge.frame import build_fe_frame, parse_fe_frame

ENDERECO = os.environ.get("RELOGIO_MAC", "00:00:00:00:00:00")  # endereco Bluetooth do seu relogio
PROIBIDOS = {(0xFF, 0x38), (0xFF, 0x08)}
# opcodes destrutivos: apagar arquivo, formatar, reiniciar, firmware (OTA)
OPCODES_PROIBIDOS = {0x1F, 0x22, 0x23, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8}


class Relogio:
    def __init__(self, cli):
        self.cli = cli
        self.seq = 0x10

    async def enviar(self, opcode, params: bytes):
        if opcode in OPCODES_PROIBIDOS:
            raise ValueError(f"opcode proibido: 0x{opcode:02x}")
        # 0x1A (memoria externa): so leitura. 1=ler, 11=info de arquivo,
        # 12=espaco livre, 3+0 = consultar mostrador, 3+3 = versao do mostrador
        if opcode == 0x1A:
            op = params[0] if params else -1
            ok = op in (1, 11, 12) or (op == 3 and len(params) > 1 and params[1] in (0, 3))
            if not ok:
                raise ValueError(f"0x1A op {op} bloqueada (grava/apaga/restaura)")
        if opcode == 0xFF and tuple(params[:2]) in PROIBIDOS:
            raise ValueError(f"comando proibido: {params[:2].hex(' ')}")
        corpo = bytes([self.seq & 0xFF]) + params
        self.seq += 1
        quadro = build_fe_frame(0xC0, opcode, corpo)
        print(f"-> {quadro.hex(' ')}")
        await self.cli.write_gatt_char(AE_WRITE_UUID, quadro, response=False)

    # Encontrar relogio: opcode 0x19, [tipo=1, liga/desliga, tempo(2 bytes), modo=0, tocar=1]
    async def achar(self, segundos=10):
        await self.enviar(0x19, bytes([1, 1, segundos >> 8, segundos & 0xFF, 0, 1]))

    async def parar_achar(self):
        await self.enviar(0x19, bytes([1, 0, 0, 0, 0, 0]))

    async def enviar_troca_mostrador(self, caminho: str):
        # unica escrita liberada no 0x1A: trocar o mostrador ativo (op 3, flag 1, caminho)
        corpo = bytes([self.seq & 0xFF, 0x03, 0x01]) + caminho.encode()
        self.seq += 1
        quadro = build_fe_frame(0xC0, 0x1A, corpo)
        print(f"-> {quadro.hex(' ')}  (trocar mostrador para {caminho})")
        await self.cli.write_gatt_char(AE_WRITE_UUID, quadro, response=False)

    async def consultar_agua(self):
        await self.enviar(0xFF, bytes([0xFF, 0x32]))

    # Lembrete de agua: FF 02 01 | inicio h m | fim h m | intervalo h m | ligado
    async def config_agua(self, ligado, ini=(8, 0), fim=(20, 0), intervalo=(1, 0)):
        await self.enviar(0xFF, bytes([0xFF, 0x02, 0x01, *ini, *fim, *intervalo, 1 if ligado else 0]))


def ao_receber(_ch, data):
    raw = bytes(data)
    f = parse_fe_frame(raw)
    if f is None:
        print(f"<- cru ({len(raw)}b): {raw.hex(' ')}")
        return
    b = f.body
    # esconde batimento continuo
    if f.flag == 0x80 and len(b) == 4 and b[2] == 0x01:
        return
    print(f"<- flag=0x{f.flag:02x} cmd=0x{f.cmd:02x} corpo={b.hex(' ')}")
    # resposta do lembrete de agua: [status seq] FF 32 xx hi mi hf mf ih im ligado
    i = b.find(b"\xff\x32")
    if i != -1:
        r = b[i + 3:]
        if len(r) >= 7:
            print(f"   AGUA: {r[0]:02d}:{r[1]:02d} ate {r[2]:02d}:{r[3]:02d}, "
                  f"a cada {r[4]}h{r[5]:02d}, {'LIGADO' if r[6] else 'DESLIGADO'}")


async def main(acao):
    async with E87Client(ENDERECO) as c:
        cli = c._client
        # a biblioteca ja assina o AE02 e joga tudo numa fila propria;
        # interceptamos essa fila em vez de assinar de novo
        push_original = c._bus.push
        def push(data):
            ao_receber(None, data)
            push_original(data)
        c._bus.push = push
        r = Relogio(cli)
        print(f"Conectado. Acao: {acao}\n")
        if acao == "achar":
            await r.achar(10)
            await asyncio.sleep(6)
            await r.parar_achar()
        elif acao == "agua":
            await r.consultar_agua()
        elif acao == "agua-on":
            await r.config_agua(True)
            await asyncio.sleep(1)
            await r.consultar_agua()
        elif acao == "agua-off":
            await r.config_agua(False)
            await asyncio.sleep(1)
            await r.consultar_agua()
        elif acao == "hora":
            # acerta o relogio com a hora do PC (SetSysInfo 0x08, funcao 2 = RTC)
            import datetime
            t = datetime.datetime.now()
            await r.enviar(0x08, bytes([0x02, 0x08, 0x00, t.year >> 8, t.year & 0xFF,
                                        t.month, t.day, t.hour, t.minute, t.second]))
            print(f"   hora enviada: {t:%d/%m/%Y %H:%M:%S}")
        elif acao == "mostrador":
            # python comandos.py mostrador            -> mostra o ativo
            # python comandos.py mostrador WAT3719    -> troca (comando oficial op 3 flag 1)
            conhecidos = {"WATCH6", "WATCH7", "WATCH36", "WATCH473", "WAT1733", "WAT2386", "WAT3801", "WAT3719"} | {f"WAT90{n:02d}" for n in range(100)}
            if len(sys.argv) > 2:
                nome = sys.argv[2].upper()
                if nome not in conhecidos:
                    raise ValueError(f"mostrador desconhecido: {nome}")
                await r.enviar_troca_mostrador("/" + nome)
                await asyncio.sleep(2)
            await r.enviar(0x1A, bytes([0x03, 0x00]))
        elif acao == "op":
            # python comandos.py op D6 [parametros em hex]
            opcode = int(sys.argv[2], 16)
            params = bytes.fromhex("".join(sys.argv[3:]))
            await r.enviar(opcode, params)
        else:
            print(__doc__)
        await asyncio.sleep(8)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))
