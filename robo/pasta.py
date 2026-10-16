"""Acha, sincroniza e mantém offline a pasta DispatchBot-Fila, para o plantonista não precisar fazer nada.

O OneDrive registra as pastas do SharePoint sincronizadas em dois lugares do registro do usuário:
- HKCU\\Software\\SyncEngines\\Providers\\OneDrive\\<id>: MountPoint (pasta local) e UrlNamespace (endereço no SharePoint)
- HKCU\\Software\\Microsoft\\OneDrive\\Accounts\\Business*\\Tenants\\<organização>: o nome de cada valor é uma pasta local
"""
import os
import subprocess
import winreg
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode

from log import log

NOME = "DispatchBot-Fila"
CHAVE_SYNC = r"Software\SyncEngines\Providers\OneDrive"
CHAVE_CONTAS = r"Software\Microsoft\OneDrive\Accounts"
LIMITE_PASTAS_VARRIDAS = 3000  # evita varrer uma biblioteca enorme inteira


def _subchaves(caminho):
    try:
        chave = winreg.OpenKey(winreg.HKEY_CURRENT_USER, caminho)
    except OSError:
        return []
    nomes = []
    with chave:
        i = 0
        while True:
            try:
                nomes.append(winreg.EnumKey(chave, i))
            except OSError:
                return nomes
            i += 1


def _valores(caminho) -> dict:
    try:
        chave = winreg.OpenKey(winreg.HKEY_CURRENT_USER, caminho)
    except OSError:
        return {}
    valores = {}
    with chave:
        i = 0
        while True:
            try:
                nome, dado, _ = winreg.EnumValue(chave, i)
            except OSError:
                return valores
            valores[nome] = dado
            i += 1


def raizes_do_onedrive() -> list:
    """Pastas locais sincronizadas pelo OneDrive corporativo: [(pasta, endereço no SharePoint ou "")]."""
    raizes = []
    for id_ in _subchaves(CHAVE_SYNC):
        v = _valores(CHAVE_SYNC + "\\" + id_)
        if v.get("MountPoint"):
            raizes.append((Path(v["MountPoint"]), str(v.get("UrlNamespace") or "")))
    for conta in _subchaves(CHAVE_CONTAS):
        if not conta.lower().startswith("business"):
            continue
        tenants = CHAVE_CONTAS + "\\" + conta + "\\Tenants"
        for org in _subchaves(tenants):
            for nome in _valores(tenants + "\\" + org):
                if nome and ":" in nome:
                    raizes.append((Path(nome), ""))
    # Pasta da organização ao lado do OneDrive corporativo (C:\Users\x\<Organização>)
    comercial = os.environ.get("OneDriveCommercial")
    if comercial and " - " in Path(comercial).name:
        org = Path(comercial).parent / Path(comercial).name.split(" - ", 1)[1]
        raizes.append((org, ""))
    return raizes


def _e_a_pasta(p: Path) -> bool:
    nome = p.name.lower()
    # Sincronizar só a pasta cria "<Biblioteca> - DispatchBot-Fila"
    return nome == NOME.lower() or nome.endswith(" - " + NOME.lower())


def _procurar(raiz: Path, profundidade: int) -> list:
    """Busca em largura por pastas com o nome certo, sem descer em pastas ocultas."""
    achadas, fila, vistas = [], [(raiz, 0)], 0
    while fila and vistas < LIMITE_PASTAS_VARRIDAS:
        atual, nivel = fila.pop(0)
        try:
            filhos = [e for e in os.scandir(atual) if e.is_dir(follow_symlinks=False) and not e.name.startswith((".", "~"))]
        except OSError:
            continue
        for e in filhos:
            vistas += 1
            p = Path(e.path)
            if _e_a_pasta(p):
                achadas.append(p)
            elif nivel + 1 < profundidade:
                fila.append((p, nivel + 1))
    return achadas


def detectar():
    """Procura a pasta DispatchBot-Fila entre as pastas sincronizadas. Retorna o caminho ou None."""
    candidatas = []
    for raiz, url in raizes_do_onedrive():
        if not raiz.is_dir():
            continue
        if _e_a_pasta(raiz) or url.rstrip("/").split("/")[-1].lower() == NOME.lower():
            candidatas.append(raiz)
        else:
            candidatas += _procurar(raiz, 3)
    unicas = list(dict.fromkeys(p.resolve() for p in candidatas))
    # Preferência para a que já tem a estrutura da fila
    unicas.sort(key=lambda p: not (p / "pendentes").is_dir())
    if len(unicas) > 1:
        log.warning("Mais de uma pasta %s encontrada; usando %s. Outras: %s", NOME, unicas[0], unicas[1:])
    return unicas[0] if unicas else None


def email_onedrive() -> str:
    """E-mail da conta corporativa conectada ao OneDrive nesta máquina."""
    for conta in _subchaves(CHAVE_CONTAS):
        if conta.lower().startswith("business"):
            email = _valores(CHAVE_CONTAS + "\\" + conta).get("UserEmail")
            if email:
                return str(email)
    return ""


def link_para_este_usuario(link: str) -> str:
    """O link é gerado uma vez pelo time, com o e-mail de quem gerou. Troca pelo e-mail
    da conta do OneDrive desta máquina, para funcionar com qualquer plantonista."""
    email = email_onedrive()
    if not email:
        return link
    base, _, consulta = link.partition("?")
    partes = [(k, v) for k, v in parse_qsl(consulta, keep_blank_values=True) if k.lower() != "useremail"]
    partes.append(("userEmail", email))
    return base + "?" + urlencode(partes, quote_via=quote)


def pedir_sincronizacao(link: str):
    """Abre o link de sincronização (odopen://...): o OneDrive começa a sincronizar a pasta sozinho."""
    log.info("Pedindo ao OneDrive para sincronizar a pasta %s.", NOME)
    os.startfile(link_para_este_usuario(link))


def manter_no_dispositivo(pasta: Path):
    """Equivale a 'Sempre manter neste dispositivo' (atributo fixado do OneDrive). Em pasta comum não faz nada."""
    for args in (["attrib", "+P", "-U", str(pasta)], ["attrib", "+P", "-U", str(pasta / "*"), "/S", "/D"]):
        try:
            subprocess.run(args, capture_output=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            log.warning("Não foi possível marcar a pasta para ficar no dispositivo: %s", e)
            return
    log.info("Pasta marcada para ficar sempre no dispositivo: %s", pasta)
