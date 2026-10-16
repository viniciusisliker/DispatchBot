"""Gera build/versao_exe.txt (recurso de versão do Windows) para o PyInstaller.

Sem isso o Windows mostra "DispatchBot.exe" nas notificações e no Gerenciador de Tarefas:
o nome amigável vem do FileDescription do executável.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "robo"))
from versao import VERSAO  # noqa: E402

partes = [int(p) for p in VERSAO.split(".")] + [0] * 4
numeros = tuple(partes[:4])
texto = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numeros}, prodvers={numeros}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041604B0', [
      StringStruct('CompanyName', 'Time de RPA'),
      StringStruct('FileDescription', 'DispatchBot'),
      StringStruct('FileVersion', '{VERSAO}'),
      StringStruct('InternalName', 'DispatchBot'),
      StringStruct('OriginalFilename', 'DispatchBot.exe'),
      StringStruct('ProductName', 'DispatchBot'),
      StringStruct('ProductVersion', '{VERSAO}')])]),
    VarFileInfo([VarStruct('Translation', [0x0416, 1200])])
  ]
)
"""
(RAIZ / "build" / "versao_exe.txt").write_text(texto, encoding="utf-8")
print("versao_exe.txt gerado:", VERSAO)
