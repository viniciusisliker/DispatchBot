# PyInstaller: dois executáveis (robô sem console + mapeador com console) numa única pasta onedir.
# Nenhum navegador é empacotado: o robô usa o Edge/Chrome do sistema (channel="msedge"/"chrome").
import os
from PyInstaller.utils.hooks import collect_all

RAIZ = os.path.abspath(os.path.join(SPECPATH, ".."))
ICONE = os.path.join(RAIZ, "assets", "icone.ico")

# Driver do Playwright (node + pacote). Não inclui navegadores, que ficam em %LOCALAPPDATA%\ms-playwright.
pw_datas, pw_binaries, pw_hidden = collect_all("playwright")
# CustomTkinter (painel moderno/escuro): temas .json e fontes vão junto
ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")
pw_datas, pw_binaries, pw_hidden = pw_datas + ctk_datas, pw_binaries + ctk_binaries, pw_hidden + ctk_hidden

comum = dict(
    pathex=[os.path.join(RAIZ, "robo")],
    binaries=pw_binaries,
    datas=pw_datas + [(os.path.join(RAIZ, "config.default.json"), "."),
                      (os.path.join(RAIZ, "assets", "avatar.png"), "assets")],
    hiddenimports=pw_hidden + ["pystray._win32"],
)

a_robo = Analysis([os.path.join(RAIZ, "robo", "main.py")], **comum)
a_mapear = Analysis([os.path.join(RAIZ, "robo", "mapear_campos.py")], **comum)

VERSAO_EXE = os.path.join(RAIZ, "build", "versao_exe.txt")  # nome "DispatchBot" nas notificações do Windows

exe_robo = EXE(
    PYZ(a_robo.pure), a_robo.scripts, [],
    exclude_binaries=True, name="DispatchBot", console=False, icon=ICONE, version=VERSAO_EXE,
)
exe_mapear = EXE(
    PYZ(a_mapear.pure), a_mapear.scripts, [],
    exclude_binaries=True, name="DispatchBot-Mapear", console=True, icon=ICONE,
)

coll = COLLECT(
    exe_robo, a_robo.binaries, a_robo.datas,
    exe_mapear, a_mapear.binaries, a_mapear.datas,
    name="DispatchBot",
)
