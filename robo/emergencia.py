"""Gatilhos da parada de emergência: atalho de teclado global e arquivo PARAR na pasta da fila."""
import ctypes
from ctypes import wintypes
from pathlib import Path

from log import log

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
MODIFICADORES = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
TECLAS = {"pause": 0x13, "end": 0x23, "home": 0x24, "insert": 0x2D, "delete": 0x2E}


def interpretar_atalho(texto: str):
    """'ctrl+alt+f12' -> (modificadores, código da tecla). Levanta ValueError se não entender."""
    partes = [p.strip().lower() for p in texto.split("+") if p.strip()]
    mods = 0
    for p in partes[:-1]:
        if p not in MODIFICADORES:
            raise ValueError(f"modificador desconhecido '{p}'")
        mods |= MODIFICADORES[p]
    tecla = partes[-1] if partes else ""
    if tecla.startswith("f") and tecla[1:].isdigit() and 1 <= int(tecla[1:]) <= 24:
        vk = 0x70 + int(tecla[1:]) - 1
    elif len(tecla) == 1 and tecla.isalnum():
        vk = ord(tecla.upper())
    elif tecla in TECLAS:
        vk = TECLAS[tecla]
    else:
        raise ValueError(f"tecla desconhecida '{tecla}'")
    if not mods:
        raise ValueError("use pelo menos um modificador (ctrl, alt, shift)")
    return mods, vk


def escutar_atalho(candidatos, ao_registrar, ao_disparar, ao_falhar):
    """Registra o primeiro atalho livre da lista e fica esperando (bloqueia: rodar numa thread própria).
    Funciona com qualquer janela em primeiro plano, inclusive o navegador do robô."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if isinstance(candidatos, str):
        candidatos = [candidatos]
    ocupados = []
    for texto in candidatos:
        try:
            mods, vk = interpretar_atalho(texto)
        except ValueError as e:
            log.warning("Atalho de emergência inválido no config ('%s'): %s", texto, e)
            continue
        if user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
            break
        ocupados.append(texto)  # outro programa já usa esta combinação
    else:
        ao_falhar("Nenhum atalho de emergência ficou livre (" + ", ".join(ocupados or candidatos) + "). "
                  "Use o menu da bandeja ou troque 'atalho_emergencia' no config.")
        return
    if ocupados:
        log.warning("Atalho(s) ocupado(s) por outro programa: %s", ", ".join(ocupados))
    log.info("Atalho de emergência ativo: %s", texto)
    ao_registrar(texto)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            ao_disparar()


def arquivo_parar(raiz: str):
    """Parada remota: qualquer arquivo na raiz da fila cujo nome comece com PARAR (PARAR, PARAR.txt...).
    Retorna (caminho, motivo) ou None."""
    if not raiz:
        return None  # pasta da fila ainda não configurada (primeira execução)
    try:
        for p in Path(raiz).iterdir():
            if p.is_file() and p.name.upper().startswith("PARAR"):
                try:
                    motivo = p.read_text(encoding="utf-8-sig", errors="replace").strip().splitlines()[0][:200]
                except (OSError, IndexError):
                    motivo = ""
                return p, motivo
    except OSError:
        pass
    return None
