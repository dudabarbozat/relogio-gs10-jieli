"""
coracao.py - Monitor de batimento cardiaco ao vivo do GS10 mini.

Conecta no relogio (autenticacao Jieli via biblioteca e87_badge), le o byte
de batimento do canal FE (notificacoes 0x80 com body[2]==0x01), e serve uma
pagina web local com um coracao que pulsa no ritmo do seu batimento real.

Uso:
    1. Desligue o Bluetooth do iPhone (pra nao roubar o relogio)
    2. Acorde a tela do relogio e deixe perto do PC
    3. python coracao.py
    4. Abra http://localhost:8000 no navegador
"""
from __future__ import annotations

import os
import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from e87_badge.client import E87Client
from e87_badge.const import AE_NOTIFY_UUID
from e87_badge.frame import parse_fe_frame

ENDERECO = os.environ.get("RELOGIO_MAC", "00:00:00:00:00:00")  # endereco Bluetooth do seu relogio
PORTA = 8000

# Estado compartilhado entre a thread BLE e o servidor web
estado = {"bpm": 0, "sig": 0, "ts": 0.0}

PAGINA = r"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Meu Coracao ao Vivo</title>
<style>
  :root { --beat: 0.85s; }
  * { margin:0; padding:0; box-sizing:border-box; }
  html,body { height:100%; }
  body { background: radial-gradient(circle at 50% 30%, #2a0a12 0%, #0a0608 70%);
    color:#fff; font-family:-apple-system,"Segoe UI",system-ui,sans-serif;
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    height:100vh; overflow:hidden; }
  .stage { display:flex; flex-direction:column; align-items:center; gap:18px; }
  .heart-wrap { position:relative; width:340px; height:340px; display:flex;
    align-items:center; justify-content:center; }
  .heart { width:240px; height:240px; animation: beat var(--beat) ease-in-out infinite;
    filter: drop-shadow(0 0 40px rgba(255,40,80,0.55)); transform-origin:center; }
  .pulse-ring { position:absolute; width:240px; height:240px; border-radius:50%;
    border:2px solid rgba(255,60,90,0.5); animation: ring var(--beat) ease-out infinite; }
  @keyframes beat { 0%{transform:scale(1)} 14%{transform:scale(1.18)} 28%{transform:scale(1)}
    42%{transform:scale(1.12)} 60%{transform:scale(1)} 100%{transform:scale(1)} }
  @keyframes ring { 0%{transform:scale(0.7);opacity:0.7} 100%{transform:scale(1.6);opacity:0} }
  .bpm { font-size:120px; font-weight:800; line-height:1; letter-spacing:-2px;
    text-shadow:0 0 30px rgba(255,60,90,0.6); }
  .label { font-size:22px; letter-spacing:6px; color:#ff9bb0; text-transform:uppercase; }
  .sig { font-size:15px; color:#8a6a72; margin-top:6px; letter-spacing:1px; }
  .dead .heart { animation-play-state:paused; opacity:0.25; filter:none; }
  .dead .pulse-ring { display:none; }
</style></head><body>
  <div class="stage" id="stage">
    <div class="heart-wrap">
      <div class="pulse-ring" id="ring"></div>
      <svg class="heart" id="heart" viewBox="0 0 32 29.6" xmlns="http://www.w3.org/2000/svg">
        <path fill="#ff2b50" d="M23.6,0c-2.7,0-5.2,1.3-6.8,3.4C15.2,1.3,12.7,0,10,0C5.1,0,1.2,3.9,1.2,8.8
          c0,6,5.4,10.9,13.6,18.4l1.2,1.1l1.2-1.1c8.2-7.5,13.6-12.4,13.6-18.4C30.8,3.9,26.9,0,23.6,0z"/>
      </svg>
    </div>
    <div class="bpm" id="bpm">--</div>
    <div class="label">bpm</div>
    <div class="sig" id="sig">aguardando leitura do relogio...</div>
  </div>
<script>
  async function tick(){
    try{
      const r = await fetch('/bpm'); const d = await r.json();
      const stage = document.getElementById('stage');
      if(d.bpm && d.bpm > 0){
        document.getElementById('bpm').textContent = d.bpm;
        document.documentElement.style.setProperty('--beat', (60/d.bpm).toFixed(2)+'s');
        document.getElementById('sig').textContent =
          'sinal: ' + (d.sig||'--') + ' | atualizado ' + d.ago + 's atras';
        stage.classList.remove('dead');
      } else {
        document.getElementById('sig').textContent='sem leitura - ajuste o relogio no pulso';
        stage.classList.add('dead');
      }
    }catch(e){
      document.getElementById('sig').textContent='conexao com o script perdida';
      document.getElementById('stage').classList.add('dead');
    }
  }
  setInterval(tick,1000); tick();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silencia o log do servidor
        pass

    def do_GET(self):
        if self.path == "/bpm":
            ago = round(time.time() - estado["ts"], 1) if estado["ts"] else 999
            # se faz mais de 15s sem leitura nova, considera "sem sinal"
            bpm = estado["bpm"] if ago < 15 else 0
            payload = json.dumps({"bpm": bpm, "sig": estado["sig"], "ago": ago})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload.encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGINA.encode("utf-8"))


def servidor():
    HTTPServer(("127.0.0.1", PORTA), Handler).serve_forever()


async def ble_loop():
    print(f"Conectando em {ENDERECO} ...")
    async with E87Client(ENDERECO) as c:
        cli = c._client
        print(">>> Autenticado! Lendo batimento. <<<")
        print(f">>> Abra http://localhost:{PORTA} no navegador. <<<\n")

        def aceita(bpm, sinal=100):
            if 30 < bpm < 220:  # faixa plausivel; ignora lixo
                estado["bpm"] = bpm
                estado["sig"] = sinal
                estado["ts"] = time.time()
                print(f"  HR {bpm} bpm")

        def on_rx(_ch, data):
            raw = bytes(data)
            f = parse_fe_frame(raw)
            if f is not None:
                # via FE: notificacao 0x80, body [contador, ff, 01, VALOR]
                if f.flag == 0x80 and len(f.body) == 4 and f.body[2] == 0x01:
                    aceita(f.body[3])
                return
            # via raw: batimento na posicao 10; qualidade na posicao 14
            if len(raw) >= 15:
                bpm = raw[10]
                sinal = raw[14] if raw[14] else 100
                aceita(bpm, sinal)

        await cli.start_notify(AE_NOTIFY_UUID, on_rx)
        # mantem vivo pra sempre (Ctrl+C pra sair)
        while True:
            await asyncio.sleep(1)


def main():
    threading.Thread(target=servidor, daemon=True).start()
    try:
        asyncio.run(ble_loop())
    except KeyboardInterrupt:
        print("\nEncerrado.")


if __name__ == "__main__":
    main()
