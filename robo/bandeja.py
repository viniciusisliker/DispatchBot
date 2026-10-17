"""Ícone na bandeja do sistema: status (ativo / aguardando login / erro / pausado), painel, pausar e sair."""
import ctypes
import os
import threading

import sys
from pathlib import Path

import pystray
from PIL import Image, ImageDraw
from PIL import PngImagePlugin  # noqa: F401  (registra o PNG já no import: no exe o Pillow pode não achá-lo sozinho)

import config
from log import LOG_DIR, log

CORES = {"ativo": "#2E9E5B", "login": "#E0A100", "confirmar": "#E0A100", "erro": "#D23C3C", "pausado": "#8A8F8C",
         "preparando": "#2F7FD1"}
TEXTOS = {"ativo": "Ativo", "login": "Aguardando login", "confirmar": "Aguardando sua confirmação",
          "erro": "Erro", "pausado": "Pausado", "preparando": "Preparando"}


_AVATAR = None


def caminhos_avatar() -> list:
    """Onde o avatar pode estar: pasta dos recursos do exe e, por garantia, ao lado do exe."""
    caminhos = [config.pasta_recursos() / "assets" / "avatar.png"]
    if getattr(sys, "frozen", False):
        pasta_exe = Path(sys.executable).resolve().parent
        caminhos += [pasta_exe / "_internal" / "assets" / "avatar.png", pasta_exe / "assets" / "avatar.png"]
    return list(dict.fromkeys(caminhos))


def avatar():
    """Avatar do robô (assets/avatar.png, imagem inteira), carregado uma vez. None se não abrir
    (aí o ícone é o robô desenhado; o motivo fica no log)."""
    global _AVATAR
    if _AVATAR is None:
        _AVATAR = False
        erros = []
        for caminho in caminhos_avatar():
            try:
                with Image.open(caminho) as img:
                    _AVATAR = img.convert("RGBA")
                break
            except Exception as e:  # arquivo faltando, Pillow sem o PNG...: tenta o próximo
                erros.append(f"{caminho}: {type(e).__name__}: {e}")
        if not _AVATAR:
            log.warning("Avatar não carregou; usando o ícone desenhado. %s", " | ".join(erros))
    return _AVATAR or None


def imagem(cor: str, lado: int = 64) -> Image.Image:
    """Ícone do robô: o avatar inteiro (quadrado) com uma bolinha de status
    no canto inferior direito, como o "online" do Teams (verde, amarelo, vermelho, cinza...)."""
    rosto = avatar()
    s = lado * 4  # desenha grande e reduz (borda lisa)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rosto is None:
        # sem o arquivo: o robô desenhado de antes, no fundo da cor do status
        u = s / 64
        d.rounded_rectangle((2 * u, 2 * u, 62 * u, 62 * u), radius=14 * u, fill=cor)
        d.rounded_rectangle((16 * u, 18 * u, 48 * u, 46 * u), radius=6 * u, outline="white", width=round(5 * u))
        d.ellipse((24 * u, 27 * u, 30 * u, 33 * u), fill="white")
        d.ellipse((34 * u, 27 * u, 40 * u, 33 * u), fill="white")
        return img.resize((lado, lado), Image.LANCZOS)
    img.paste(rosto.resize((s, s), Image.LANCZOS), (0, 0))  # quadrado, sem cantos arredondados (pedido do usuário)
    # bolinha de status com borda branca (visível no fundo escuro do avatar e na barra de tarefas)
    r = round(s * (0.2 if lado <= 24 else 0.14))  # maior no ícone pequeno da bandeja, para continuar visível
    borda = round(s * (0.05 if lado <= 24 else 0.035))
    cx = cy = s - r - borda
    d.ellipse((cx - r - borda, cy - r - borda, cx + r + borda, cy + r + borda), fill="white")
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=cor)
    return img.resize((lado, lado), Image.LANCZOS)


def aviso_windows(texto: str):
    """Caixa de mensagem por cima de tudo, sem travar o robô."""
    flags = 0x40 | 0x40000 | 0x10000  # ícone info | topmost | foreground
    threading.Thread(
        target=lambda: ctypes.windll.user32.MessageBoxW(0, texto, "DispatchBot", flags), daemon=True
    ).start()


def _seguro(acao):
    """Erro num item de menu não pode derrubar a bandeja."""
    def executar():
        try:
            acao()
        except Exception as e:
            aviso_windows(f"Não foi possível executar a ação:\n{e}")
    return executar


class Bandeja:
    def __init__(self, ao_pausar, ao_sair, ao_verificar, ao_abrir_pasta, ao_emergencia, pausado: bool, versao: str,
                 ao_envio=None, envio_ligado=lambda grupo: False, ao_painel=None, ao_nome=None, ao_atualizar=None):
        self.pausado = pausado
        self.status = "pausado" if pausado else "ativo"
        self.detalhe = ""
        self.atalho = ""  # preenchido quando o atalho global é registrado
        menu = pystray.Menu(
            # Primeiro item de propósito: achar rápido numa emergência ("\t" alinha o atalho à direita)
            pystray.MenuItem(lambda item: f"PARAR AGORA (emergência)\t{self.atalho}".rstrip(), _seguro(ao_emergencia)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: f"Status: {self.texto_status()}", None, enabled=False),
            pystray.MenuItem(f"Versão {versao}", None, enabled=False),
            pystray.MenuItem("Procurar atualização agora", _seguro(ao_atualizar or (lambda: None))),
            # default=True: clicar no ícone abre o painel
            pystray.MenuItem("Abrir painel", _seguro(ao_painel or (lambda: None)), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Verificar pendentes agora", _seguro(ao_verificar)),
            pystray.MenuItem("Pausado", _seguro(ao_pausar), checked=lambda item: self.pausado),
            # Decisão do plantonista: desmarcado = o robô preenche e ele confere e clica em Submit
            pystray.MenuItem("Enviar GMUD sem conferir", _seguro(lambda: (ao_envio or (lambda g: None))("GMUD")),
                             checked=lambda item: envio_ligado("GMUD")),
            pystray.MenuItem("Enviar Backup sem conferir", _seguro(lambda: (ao_envio or (lambda g: None))("BACKUP")),
                             checked=lambda item: envio_ligado("BACKUP")),
            pystray.MenuItem("Meu nome no DispatchBot...", _seguro(ao_nome or (lambda: None))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Abrir pasta DispatchBot-Fila", _seguro(ao_abrir_pasta)),
            pystray.MenuItem("Abrir pasta de logs", _seguro(lambda: os.startfile(LOG_DIR))),
            pystray.MenuItem("Abrir config.json", _seguro(lambda: os.startfile(config.CONFIG_PATH))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", _seguro(ao_sair)),
        )
        self.icone = pystray.Icon("DispatchBot", imagem(CORES[self.status]), self.titulo(), menu)

    def texto_status(self) -> str:
        texto = TEXTOS[self.status]
        return f"{texto}: {self.detalhe}" if self.detalhe else texto

    def titulo(self) -> str:
        return f"DispatchBot - {self.texto_status()}"[:127]  # limite do Windows

    def definir_status(self, status: str, detalhe: str = ""):
        if (status, detalhe) == (self.status, self.detalhe):
            return
        self.status, self.detalhe = status, detalhe
        self.icone.icon = imagem(CORES[status])
        self.icone.title = self.titulo()
        self.icone.update_menu()

    def definir_atalho(self, texto: str):
        self.atalho = texto
        self.icone.update_menu()

    def notificar(self, titulo: str, mensagem: str):
        try:
            self.icone.notify(mensagem[:250], titulo)
        except Exception:
            pass

    def rodar(self, ao_iniciar):
        def setup(icone):
            icone.visible = True
            ao_iniciar()
        self.icone.run(setup=setup)

    def parar(self):
        self.icone.stop()
