"""Log rotativo em %LOCALAPPDATA%\\DispatchBot\\logs e screenshots de erro."""
import logging
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

from config import APP_DIR

LOG_DIR = APP_DIR / "logs"
SCREENSHOT_DIR = LOG_DIR / "screenshots"
DIAS_MANTER_SCREENSHOTS = 30

log = logging.getLogger("robo")


def configurar():
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    formato = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")

    arquivo = RotatingFileHandler(LOG_DIR / "robo.log", maxBytes=2_000_000, backupCount=10, encoding="utf-8")
    arquivo.setFormatter(formato)
    log.addHandler(arquivo)

    # No executável sem console não há stdout
    if sys.stdout and sys.stdout.isatty():
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formato)
        log.addHandler(console)

    log.setLevel(logging.INFO)
    _limpar_screenshots_antigos()


def screenshot(page, nome: str):
    """Salva a tela atual junto do log. Nunca levanta exceção."""
    try:
        nome = "".join(c if c.isalnum() or c in "-_" else "_" for c in nome)[:80]
        caminho = SCREENSHOT_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{nome}.png"
        page.screenshot(path=str(caminho), full_page=True)
        log.info("Screenshot salvo: %s", caminho)
        return caminho
    except Exception as e:
        log.warning("Não foi possível salvar o screenshot: %s", e)
        return None


def _limpar_screenshots_antigos():
    limite = time.time() - DIAS_MANTER_SCREENSHOTS * 86400
    for arquivo in SCREENSHOT_DIR.glob("*.png"):
        try:
            if arquivo.stat().st_mtime < limite:
                arquivo.unlink()
        except OSError:
            pass
