"""
trocar_sidebar.py - Troca um arquivo de sistema (SIDEBAR ou JL) dentro do modo de recursos: apaga o atual e grava o novo.
Se a gravacao do novo falhar, regrava o ORIGINAL do backup (ate 3 tentativas cada).
No fim, confere a lista de arquivos e sai do modo de recursos.

Uso: python trocar_sidebar.py NOVO [NOME]   (ex.: meus_arquivos/SIDEBAR   ou   meus_arquivos/JL JL)
"""
import subprocess
import sys
import time

import ota_recursos as o

NOME = (sys.argv[2] if len(sys.argv) > 2 else "SIDEBAR").upper()
assert NOME in ("SIDEBAR", "JL")
ORIGINAL = f"arquivos_relogio/{NOME}"


def rodar(args, filtro=None):
    r = subprocess.run([sys.executable, "-u"] + args, capture_output=True, text=True)
    s = r.stdout + r.stderr
    linhas = [l for l in s.splitlines() if not any(k in l for k in ("start_notify", "Scanner", "TX FE", "INFO |"))]
    if filtro:
        linhas = [l for l in linhas if any(k in l for k in filtro)]
    print("\n".join(linhas[-25:]), flush=True)
    return s


def existe_sidebar():
    s = rodar(["leitor_arquivos.py", "listar"], (f"/{NOME}", "arquivos,", ".TMP"))
    return any(l.strip().endswith(f"/{NOME}") for l in s.splitlines()), s


def gravar(arq):
    for t in range(6):  # mesmo nome temporario: cada tentativa retoma de onde parou
        s = rodar(["enviar_recursos.py", arq, f"/{NOME}", "--sem-sair"], ("op7", "metadados", "100%", "nome", "fechou", "RESULTADO", "FALHOU", "CONCLUIDO"))
        if "ENVIO CONCLUIDO" in s and "status 0" in s:
            return True
        print(f"   tentativa {t + 1} falhou", flush=True)
        time.sleep(10)
    return False


def main():
    novo = sys.argv[1]
    print("== conferindo modo", flush=True)
    if not o.entrar():
        print("NAO ESTA NO MODO DE RECURSOS; nada foi alterado")
        return
    tem, _ = existe_sidebar()
    if tem:
        print(f"== apagando /{NOME} atual", flush=True)
        rodar(["apagar_arquivo.py", NOME, "--sistema-sidebar"])
        tem, _ = existe_sidebar()
        if tem:
            print("!!! nao consegui apagar; parando sem gravar")
            print("SAIU DO MODO" if o.sair() else "!!! NAO SAIU DO MODO")
            return
    print(f"== gravando {novo}", flush=True)
    ok = gravar(novo)
    if not ok:
        print("== novo falhou -> regravando ORIGINAL", flush=True)
        ok_o = gravar(ORIGINAL)
        print("ORIGINAL RESTAURADO" if ok_o else "!!! ORIGINAL NAO RESTAURADO (o relogio segue no modo de recursos; tentar de novo)", flush=True)
    else:
        print(f"NOVO {NOME} GRAVADO", flush=True)
    tem, _ = existe_sidebar()
    print(f"{NOME} presente:", tem, flush=True)
    if tem:
        print("SAIU DO MODO" if o.sair() else "!!! NAO SAIU DO MODO", flush=True)


if __name__ == "__main__":
    main()
