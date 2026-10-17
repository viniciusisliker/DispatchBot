# Gera dist\DispatchBot (PyInstaller) e o instalador dist\instalador\DispatchBot-Setup-<versao>.exe (Inno Setup 6).
# Uso (na raiz do projeto):  powershell -ExecutionPolicy Bypass -File build\build.ps1
# Com -Publicar: no fim cria a Release v<versao> no GitHub com o instalador, o manual e a documentação técnica em PDF (precisa do gh logado e do código commitado).
# Assinatura opcional: defina $env:DISPATCHBOT_CERT_THUMBPRINT com o thumbprint de um certificado de
# assinatura de código do repositório do usuário (reduz bloqueios do antivírus/SmartScreen).
param([switch]$Publicar)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

# 1. Ambiente virtual com as dependências (sem "playwright install": usamos o navegador do sistema)
if (-not (Test-Path ".venv")) { python -m venv .venv }
$py = Join-Path $raiz ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependências" }

# 2. Versão (fonte única: robo\versao.py)
$versao = (& $py -c "import sys; sys.path.insert(0, 'robo'); from versao import VERSAO; print(VERSAO)").Trim()
Write-Host "Versão $versao"

# 3. Checagem rápida de sintaxe antes de empacotar
& $py -m py_compile (Get-ChildItem robo\*.py | ForEach-Object FullName)
if ($LASTEXITCODE -ne 0) { throw "Erro de sintaxe no código do robô" }

# 4. Identidade visual (ícone e imagens do instalador) e executáveis
& $py build\gerar_imagens.py
& $py build\versao_exe.py  # nome "DispatchBot" (em vez de DispatchBot.exe) nas notificações do Windows
& $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build\tmp build\robo.spec
if ($LASTEXITCODE -ne 0) { throw "Falha no PyInstaller" }
# O avatar (ícone da bandeja e das notificações) tem que abrir no exe; sem ele aparece o robô desenhado
& dist\DispatchBot\DispatchBot-Mapear.exe --verificar-imagens
if ($LASTEXITCODE -ne 0) { throw "O avatar não abre no exe empacotado" }

function Assinar($arquivos) {
    if (-not $env:DISPATCHBOT_CERT_THUMBPRINT) { return }
    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) { Write-Warning "signtool.exe não encontrado (Windows SDK); pulando assinatura."; return }
    & $signtool.FullName sign /sha1 $env:DISPATCHBOT_CERT_THUMBPRINT /fd sha256 /tr http://timestamp.digicert.com /td sha256 $arquivos
    if ($LASTEXITCODE -ne 0) { throw "Falha ao assinar" }
}
Assinar @("dist\DispatchBot\DispatchBot.exe", "dist\DispatchBot\DispatchBot-Mapear.exe")

# 5. Instalador
$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup 6 não encontrado. Instale com: winget install JRSoftware.InnoSetup --scope user"
    exit 0
}
& $iscc /Q "/DAppVersion=$versao" installer\DispatchBot.iss
if ($LASTEXITCODE -ne 0) { throw "Falha no Inno Setup" }
Assinar @("dist\instalador\DispatchBot-Setup-$versao.exe")
Write-Host "Pronto: dist\instalador\DispatchBot-Setup-$versao.exe"

# 6. Release no GitHub (só com -Publicar)
if ($Publicar) {
    if (git status --porcelain) { throw "Há mudanças sem commit: faça o commit antes de publicar" }
    git push
    if ($LASTEXITCODE -ne 0) { throw "Falha no git push" }
    # Anexos: instalador, manual do plantonista e documentação técnica (os PDFs são gerados à parte, a partir dos .html)
    $anexos = @("dist\instalador\DispatchBot-Setup-$versao.exe")
    $pdfs = @(
        @("docs\manual\Manual-DispatchBot.pdf", "docs\manual\manual.html"),
        @("docs\tecnico\DispatchBot-Documentacao-Tecnica.pdf", "docs\tecnico\documentacao-tecnica.html")
    )
    foreach ($par in $pdfs) {
        $pdf, $fonte = $par
        if (Test-Path $pdf) {
            if ((Get-Item $pdf).LastWriteTime -lt (Get-Item $fonte).LastWriteTime) {
                Write-Warning "$pdf é mais antigo que $fonte`: gere o PDF de novo se mudou algo."
            }
            $anexos += $pdf
        } else { Write-Warning "PDF não encontrado ($pdf); publicando sem ele." }
    }
    gh release view "v$versao" *> $null
    if ($LASTEXITCODE -eq 0) {
        # A Release já existe: só troca os anexos
        gh release upload "v$versao" @anexos --clobber
    } else {
        gh release create "v$versao" @anexos --target (git rev-parse HEAD) --title "DispatchBot $versao" --generate-notes
    }
    if ($LASTEXITCODE -ne 0) { throw "Falha ao publicar a Release" }
    Write-Host "Release publicada: v$versao"
}
