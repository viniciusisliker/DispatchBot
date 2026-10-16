"""Demonstração: janela com dois botões que preenchem o formulário de GMUD ou de Backup no portal real, SEM enviar.

Uso (da pasta do projeto):  python robo\\demo.py
O navegador do robô fica aberto entre um clique e outro, então o login só é pedido uma vez.
Feche ou pause o DispatchBot antes: os dois não podem usar o mesmo perfil de navegador ao mesmo tempo.
"""
import tkinter as tk
from pathlib import Path

import config
import servicenow
from fila import ler_json
from log import configurar as configurar_log
from navegador import Navegador

EXEMPLOS = Path(__file__).resolve().parent.parent / "exemplos"
DEMOS = [
    ("GMUD (INFRA)", EXEMPLOS / "INFRA.json"),
    ("Falha de Backup", EXEMPLOS / "BACKUP.json"),
]


def main():
    configurar_log()
    cfg = config.carregar()

    janela = tk.Tk()
    janela.title("DispatchBot · Demonstração")
    janela.resizable(False, False)
    janela.attributes("-topmost", True)  # fica na frente do navegador durante a apresentação

    status = tk.StringVar(value="Abrindo o ServiceNow...")

    def mostrar(texto: str):
        status.set(texto)
        janela.update()

    def avisar_login(aguardando: bool, sistema: str = "ServiceNow"):
        if aguardando:
            mostrar(f"Faça login no {sistema} na janela do navegador...")

    nav = Navegador(cfg, avisar_login)
    botoes = []

    def travar(travado: bool):
        for b in botoes:
            b.config(state="disabled" if travado else "normal")
        janela.update()

    def demonstrar(nome: str, caminho: Path):
        travar(True)
        mostrar(f"{nome}: preenchendo o formulário...")
        try:
            alerta = ler_json(caminho)
            tipo_cfg = config.tipo_efetivo(cfg, str(alerta.get("tipo", "")).upper())
            # enviar=False: só preenche. Nada é enviado ao ServiceNow.
            servicenow.abrir_chamado(nav, cfg, tipo_cfg, alerta, enviar=False)
            mostrar(f"{nome}: formulário preenchido e NÃO enviado.")
        except Exception as e:
            mostrar(f"{nome}: falhou. {str(e).splitlines()[0]}")
        travar(False)

    def entrar():
        # Login no começo, para a demonstração não parar no meio
        travar(True)
        try:
            nav.ir_para(nav.pagina(), servicenow.url_absoluta(cfg, cfg["portal_path"]))
            mostrar("Pronto. Clique em um dos botões.")
        except Exception as e:
            mostrar(f"Não abriu o ServiceNow: {str(e).splitlines()[0]}")
        travar(False)

    def sair():
        mostrar("Fechando o navegador...")
        try:
            nav.fechar()
        finally:
            janela.destroy()

    quadro = tk.Frame(janela, padx=20, pady=16)
    quadro.pack()
    tk.Label(quadro, text="DispatchBot", font=("Segoe UI", 16, "bold")).pack(anchor="w")
    tk.Label(quadro, text="Preenche o formulário no ServiceNow sem enviar.",
             font=("Segoe UI", 10), fg="#56635E").pack(anchor="w", pady=(0, 12))

    for nome, caminho in DEMOS:
        b = tk.Button(quadro, text=nome, font=("Segoe UI", 12), width=24, pady=6,
                      bg="#0E6B66", fg="white", activebackground="#0B5551", activeforeground="white",
                      command=lambda n=nome, c=caminho: demonstrar(n, c))
        b.pack(pady=4)
        botoes.append(b)

    tk.Label(quadro, textvariable=status, font=("Segoe UI", 10), wraplength=280,
             justify="left").pack(anchor="w", pady=(12, 0))
    tk.Button(quadro, text="Fechar", command=sair).pack(anchor="e", pady=(12, 0))
    janela.protocol("WM_DELETE_WINDOW", sair)

    janela.after(200, entrar)
    janela.mainloop()


if __name__ == "__main__":
    main()
