"""Leitura/gravação do config.json e diálogo de primeira execução."""
import json
import os
import shutil
import sys
from pathlib import Path

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DispatchBot"
CONFIG_PATH = APP_DIR / "config.json"
PERFIL_NAVEGADOR = APP_DIR / "browser-profile"
SUBPASTAS = ("pendentes", "processando", "processados", "erro", "retorno", "status", "plantao", "historico")


class ConfigInvalido(Exception):
    pass


def pasta_recursos() -> Path:
    """Onde ficam os arquivos empacotados (config.default.json)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def _mesclar(padrao: dict, usuario: dict) -> bool:
    """Completa o config do usuário com chaves novas do modelo (após uma atualização).
    Os valores do usuário sempre prevalecem. Retorna True se algo foi acrescentado."""
    mudou = False
    for chave, valor in padrao.items():
        if chave not in usuario:
            usuario[chave] = valor
            mudou = True
        elif isinstance(valor, dict) and isinstance(usuario[chave], dict):
            mudou |= _mesclar(valor, usuario[chave])
    return mudou


def carregar() -> dict:
    # Na primeira execução copia o modelo para %LOCALAPPDATA%\DispatchBot
    APP_DIR.mkdir(parents=True, exist_ok=True)
    modelo = pasta_recursos() / "config.default.json"
    if not CONFIG_PATH.exists():
        shutil.copy(modelo, CONFIG_PATH)
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ConfigInvalido(f"config.json inválido (linha {e.lineno}, coluna {e.colno}): {e.msg}") from e
    padrao = json.loads(modelo.read_text(encoding="utf-8-sig"))
    mudou = _mesclar(padrao, cfg)
    # botao_enviar vazio (ex.: Backup antes de 16/10/2026) e já mapeado no modelo: usa o do modelo
    for nome, tipo in (padrao.get("tipos") or {}).items():
        meu = (cfg.get("tipos") or {}).get(nome)
        if isinstance(meu, dict) and isinstance(tipo, dict) and meu.get("botao_enviar") == "" and tipo.get("botao_enviar"):
            meu["botao_enviar"] = tipo["botao_enviar"]
            mudou = True
        # ignorar_clientes: clientes novos do modelo entram na lista já instalada (a mescla não troca listas)
        if isinstance(meu, dict) and isinstance(tipo, dict) and isinstance(meu.get("ignorar_clientes"), list):
            atuais = {str(n).strip().lower() for n in meu["ignorar_clientes"]}
            for n in tipo.get("ignorar_clientes") or []:
                if str(n).strip().lower() not in atuais:
                    meu["ignorar_clientes"].append(n)
                    mudou = True
    if mudou:
        salvar(cfg)
    return cfg


def nome_plantonista(cfg: dict) -> str:
    """Nome que o time vê no painel e no histórico. Sem nome no config: o usuário do Windows
    arrumado (vinicius.morais -> Vinicius Morais)."""
    nome = " ".join(str(cfg.get("nome_plantonista") or "").split())
    if nome:
        return nome[:40]
    usuario = os.environ.get("USERNAME", "") or "?"
    return " ".join(p.capitalize() for p in usuario.replace("_", ".").split(".") if p) or usuario


def nomes_tipos(cfg: dict) -> list:
    """Tipos aceitos nos alertas. Chaves começando com "_" são modelos ou ajuda, não tipos."""
    return [k for k in cfg["tipos"] if not k.startswith("_")]


def tipo_efetivo(cfg: dict, nome: str):
    """Configuração do tipo já com o modelo aplicado ("herda": "_GMUD"). None se o tipo não existe."""
    if nome.startswith("_") or nome not in cfg["tipos"]:
        return None
    proprio = cfg["tipos"][nome]
    base_nome = proprio.get("herda")
    if not base_nome:
        return proprio
    base = cfg["tipos"].get(base_nome)
    if base is None:
        raise ConfigInvalido(f"O tipo {nome} herda de '{base_nome}', que não existe no config.")
    efetivo = json.loads(json.dumps(base))  # cópia
    for chave, valor in proprio.items():
        if isinstance(valor, dict) and isinstance(efetivo.get(chave), dict):
            efetivo[chave] = {**efetivo[chave], **valor}  # ex.: "variaveis" soma as do modelo
        elif chave != "herda":
            efetivo[chave] = valor
    return efetivo


def salvar(cfg: dict):
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def pedir_pasta_sincronizada(cfg: dict) -> bool:
    """Pergunta onde está a pasta DispatchBot-Fila sincronizada. Retorna False se o usuário cancelar."""
    import tkinter as tk
    from tkinter import filedialog, messagebox

    raiz = tk.Tk()
    raiz.withdraw()
    raiz.attributes("-topmost", True)
    messagebox.showinfo(
        "DispatchBot",
        "Selecione a pasta DispatchBot-Fila sincronizada do SharePoint "
        "(a que contém 'pendentes', 'retorno' etc.).\n\n"
        "Dica: no Explorer, clique com o botão direito na pasta e marque "
        "'Sempre manter neste dispositivo'.",
        parent=raiz,
    )
    while True:
        escolhida = filedialog.askdirectory(title="Pasta DispatchBot-Fila sincronizada", parent=raiz)
        if not escolhida:
            raiz.destroy()
            return False
        pasta = Path(escolhida)
        if (pasta / "pendentes").is_dir():
            break
        if messagebox.askyesno(
            "DispatchBot",
            f"A pasta\n{pasta}\nnão tem a subpasta 'pendentes'.\n\nCriar as subpastas do robô nela?",
            parent=raiz,
        ):
            break
    raiz.destroy()

    for nome in SUBPASTAS:
        (pasta / nome).mkdir(exist_ok=True)
    cfg["pasta_sincronizada"] = str(pasta)
    salvar(cfg)
    return True
