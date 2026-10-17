"""Atualização automática pela Release do GitHub.

O robô olha a última Release (sem a API do GitHub, que tem limite de consultas por IP), baixa o instalador
em segundo plano e, quando está parado (pausado ou sem alerta em andamento), roda o instalador em modo
silencioso. O instalador já encerra o robô com segurança (evento DispatchBot_Sair) e o abre de novo no fim.
"""
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from config import APP_DIR
from versao import VERSAO

PASTA = APP_DIR / "atualizacao"
MARCA = PASTA / "instalando.json"  # gravado antes de rodar o instalador; lido pela versão nova ao abrir
CABECALHOS = {"User-Agent": f"DispatchBot/{VERSAO}"}


def numeros(versao: str) -> tuple:
    """'v1.10.8' -> (1, 10, 8). Texto fora do padrão vira () (nunca é "mais novo")."""
    achado = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (versao or "").strip())
    return tuple(int(n) for n in achado.group(1).split(".")) if achado else ()


def disponivel() -> bool:
    """Só no executável instalado: rodando pelo código-fonte não há o que atualizar."""
    return bool(getattr(sys, "frozen", False))


def ultima_versao(repositorio: str, timeout: int = 20) -> str:
    """Versão da última Release, lida do redirecionamento de /releases/latest para /releases/tag/v<versão>."""
    pedido = urllib.request.Request(f"https://github.com/{repositorio}/releases/latest", headers=CABECALHOS,
                                    method="HEAD")
    with urllib.request.urlopen(pedido, timeout=timeout) as resp:
        final = resp.geturl()
    achado = re.search(r"/releases/tag/([^/?#]+)$", final)
    return achado.group(1).lstrip("v") if achado else ""


def sha256_publicado(repositorio: str, versao: str, nome: str, timeout: int = 20) -> str:
    """SHA-256 do anexo informado pelo GitHub (campo digest). Vazio se não der para consultar."""
    try:
        pedido = urllib.request.Request(f"https://api.github.com/repos/{repositorio}/releases/tags/v{versao}",
                                        headers=dict(CABECALHOS, Accept="application/vnd.github+json"))
        with urllib.request.urlopen(pedido, timeout=timeout) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
        for anexo in dados.get("assets") or []:
            if anexo.get("name") == nome and str(anexo.get("digest") or "").startswith("sha256:"):
                return anexo["digest"].split(":", 1)[1].lower()
    except Exception:
        pass
    return ""


def baixar(repositorio: str, versao: str, timeout: int = 60) -> Path:
    """Baixa o instalador da versão para %LOCALAPPDATA%\\DispatchBot\\atualizacao. Confere tamanho, cabeçalho
    de executável e, quando o GitHub informa, o SHA-256. Já baixado e conferido = não baixa de novo."""
    PASTA.mkdir(parents=True, exist_ok=True)
    nome = f"DispatchBot-Setup-{versao}.exe"
    destino = PASTA / nome
    esperado = sha256_publicado(repositorio, versao, nome)
    if destino.exists() and (not esperado or _sha256(destino) == esperado):
        return destino
    for velho in PASTA.glob("DispatchBot-Setup-*"):  # versões antigas e downloads pela metade
        velho.unlink(missing_ok=True)
    parcial = destino.with_suffix(".part")
    url = f"https://github.com/{repositorio}/releases/download/v{versao}/{nome}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=CABECALHOS), timeout=timeout) as resp, \
            open(parcial, "wb") as arq:
        tamanho = int(resp.headers.get("Content-Length") or 0)
        while bloco := resp.read(1024 * 1024):
            arq.write(bloco)
    recebido = parcial.stat().st_size
    if (tamanho and recebido != tamanho) or recebido < 1_000_000:
        parcial.unlink(missing_ok=True)
        raise ValueError(f"download incompleto ({recebido} de {tamanho or '?'} bytes)")
    with open(parcial, "rb") as arq:
        if arq.read(2) != b"MZ":
            parcial.unlink(missing_ok=True)
            raise ValueError("o arquivo baixado não é um instalador")
    if esperado and _sha256(parcial) != esperado:
        parcial.unlink(missing_ok=True)
        raise ValueError("o instalador baixado não confere com o publicado (SHA-256)")
    parcial.replace(destino)
    return destino


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as arq:
        while bloco := arq.read(1024 * 1024):
            h.update(bloco)
    return h.hexdigest()


def instalar(instalador: Path, de: str, para: str, ts: float):
    """Roda o instalador em modo silencioso, solto do robô: o instalador encerra o robô (e, se travar,
    usa taskkill /T, que mataria um processo filho). Por isso sobe pelo "start" do cmd, que sai na hora."""
    MARCA.write_text(json.dumps({"de": de, "para": para, "ts": ts}), encoding="utf-8")
    log_instalador = PASTA / f"instalador-{para}.log"
    subprocess.Popen(
        ["cmd.exe", "/c", "start", "", str(instalador), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
         f"/LOG={log_instalador}"],
        creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True,
    )


def resultado_anterior() -> dict:
    """Lê (e apaga) a marca da última atualização. {} se não houve."""
    try:
        dados = json.loads(MARCA.read_text(encoding="utf-8"))
    except Exception:
        return {}
    MARCA.unlink(missing_ok=True)
    return dados if isinstance(dados, dict) else {}
