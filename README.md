# relogio-gs10-jieli

Ferramentas em Python para comunicação com o smartwatch GS10 mini (SoC Jieli) via BLE, sem o app oficial. Implementa os comandos do protocolo RCSP, leitura do sistema de arquivos, codificação de mostradores e envio de arquivos.

## Requisitos

- Python 3.10+
- Adaptador Bluetooth com suporte a BLE

```bash
pip install -r requirements.txt
export RELOGIO_MAC="XX:XX:XX:XX:XX:XX"
```

## Uso

```bash
python comandos.py achar                  # vibra o relógio
python comandos.py hora                   # sincroniza a hora
python comandos.py mostrador WATCH6       # ativa um mostrador
python leitor_arquivos.py listar          # lista os arquivos do FAT
python leitor_arquivos.py copiar          # copia os arquivos para arquivos_relogio/
python enviar_mostrador.py ARQUIVO --dev=3 --ativar
```

## Módulos

| Arquivo | Descrição |
|---|---|
| `comandos.py` | Comandos gerais (localizar, hora, lembretes, troca de mostrador, opcode bruto) |
| `coracao.py` | Leitura de frequência cardíaca em tempo real, servida em HTTP local |
| `armazenamento.py` | Consulta dos dispositivos de armazenamento anunciados |
| `leitor_arquivos.py` | Leitor FAT16 sobre leitura de blocos via BLE |
| `abrir_mostrador.py` | Extração dos arquivos internos de um pacote de mostrador |
| `imagens_jieli.py` | Decodificador de imagens (RLE, RGB565 + alfa, ARGB8888) |
| `montar_mostrador.py` | Codificador de imagens e montagem de pacotes |
| `catalogo.py` | Exportação das imagens dos pacotes de sistema |
| `enviar_mostrador.py` | Transferência de arquivos |
| `apagar_arquivo.py` | Exclusão de arquivos |
| `ota_recursos.py` | Entrada e saída do modo de atualização de recursos |
| `enviar_recursos.py`, `trocar_sidebar.py` | Gravação de arquivos de sistema no modo de recursos |
| `sonda_tamanho.py` | Teste do tamanho máximo aceito no início de uma transferência |

## Protocolo

Quadros no formato `FE DC BA <flag> <cmd> <len> <corpo> EF`. O corpo começa com o número de sequência, seguido dos parâmetros.

| Operação | Sequência |
|---|---|
| Leitura | `0x1A` op1 (bloco) |
| Envio | `0x1A` op2 início → `0x21 [00]` → `0x27` → `0x1B` (tamanho, CRC, nome temporário) → respostas a `0x1D` → `0x20` (nome) → `0x1C` → `0x1A` op2 fim |
| Exclusão | `0x1A` op5 início → `0x21 [01]` → `0x1F` (cluster) → `0x1A` op5 fim |
| Modo de recursos | `0x1A` op9 flag 1 / flag 0 |
| Troca de mostrador | `0x1A` op3 + caminho |

Observações:

- Pacotes de dados de até 512 B, fragmentados em escritas BLE de no máximo 240 B.
- O nome final não pode existir no destino; o arquivo anterior deve ser removido antes.
- Escrita direta de setores não é suportada pelo firmware.

## Formato de mostrador

Pacote com `index.idx` e arquivos internos `.sty`, `.res`, `.str`, `.view` e `.json`, todos com o mesmo nome base do pacote. Integridade por CRC-16/XMODEM em cada entrada.

Imagens com RLE por linha (sequências de 3 ou mais pixels iguais são codificadas como repetição). Formatos de pixel:

| Código | Formato |
|---|---|
| `0x20` | ARGB8888 |
| `0x24` | RGB565 |
| `0x30` | Alfa + RGB565 (big-endian) |

## Dependências

- [bleak](https://github.com/hbldh/bleak)
- [e87-badge](https://pypi.org/project/e87-badge/) (conexão e autenticação Jieli)
- [Pillow](https://python-pillow.org/)

## Aviso

Projeto independente, sem vínculo com os fabricantes do dispositivo, do chip ou do aplicativo. Os scripts de escrita alteram a memória do relógio; use por sua conta e risco.

## Licença

[MIT](LICENSE)
