; Instalador do DispatchBot (Inno Setup 6). Instalação por usuário, sem admin.
; Compile com build\build.ps1: ele gera dist\DispatchBot e passa a versão (robo\versao.py) via /DAppVersion.

#define AppName "DispatchBot"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppExe "DispatchBot.exe"
#define TarefaVigia "DispatchBot Vigia"

[Setup]
AppId={{62CC2ABE-6DD6-4C7E-8102-5A2A71E16C81}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Time RPA
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Instalador do {#AppName}
DefaultDirName={localappdata}\Programs\DispatchBot
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\instalador
OutputBaseFilename=DispatchBot-Setup-{#AppVersion}
SetupIconFile=..\assets\icone.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
; Visual: acompanha o tema claro/escuro do Windows; imagens próprias (várias resoluções para telas com zoom)
WizardStyle=modern dynamic windows11 includetitlebar
WizardImageFile=..\assets\instalador\grande-202.png,..\assets\instalador\grande-336.png,..\assets\instalador\grande-534.png
WizardSmallImageFile=..\assets\instalador\pequena-58.png,..\assets\instalador\pequena-97.png,..\assets\instalador\pequena-159.png
; No tema escuro o Inno usa imagens próprias; as nossas servem nos dois temas
WizardImageFileDynamicDark=..\assets\instalador\grande-202.png,..\assets\instalador\grande-336.png,..\assets\instalador\grande-534.png
WizardSmallImageFileDynamicDark=..\assets\instalador\pequena-58.png,..\assets\instalador\pequena-97.png,..\assets\instalador\pequena-159.png
DisableWelcomePage=no
DisableReadyPage=yes
; O robô é encerrado pelo [Code] abaixo (com segurança), então o Restart Manager não é necessário
CloseApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Messages]
; Textos das páginas, escritos para o plantonista (substituem os genéricos do Inno Setup)
WelcomeLabel1=Instalar o DispatchBot
WelcomeLabel2=O DispatchBot abre chamados no ServiceNow para os alertas de GMUD e de Backup.%n%nClique em Instalar. Depois, o DispatchBot prepara tudo sozinho: sincroniza a pasta da fila e, se precisar, pede o login no ServiceNow uma única vez.%n%nNão precisa de administrador.
WizardInstalling=Instalando o DispatchBot
InstallingLabel=Leva menos de um minuto.
FinishedHeadingLabel=Pronto
FinishedLabelNoIcons=O DispatchBot vai abrir e preparar tudo sozinho. O ícone perto do relógio fica azul enquanto ele prepara e verde quando estiver pronto.%n%nSe aparecer a janela do ServiceNow, faça o login: é só desta vez.
FinishedLabel=O DispatchBot vai abrir e preparar tudo sozinho. O ícone perto do relógio fica azul enquanto ele prepara e verde quando estiver pronto.%n%nSe aparecer a janela do ServiceNow, faça o login: é só desta vez.

[InstallDelete]
; Atualização limpa: remove binários da versão anterior (config, logs e sessão ficam em %LOCALAPPDATA%\DispatchBot)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\DispatchBot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\exemplos\*.json"; DestDir: "{app}\exemplos"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{#AppName}\Mapear campos do ServiceNow"; Filename: "{app}\DispatchBot-Mapear.exe"
Name: "{autoprograms}\{#AppName}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"

[Registry]
; Sempre abre ao entrar no Windows (em máquina que não é de plantão, o robô fica em Pausado)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DispatchBot"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue

[Run]
; Vigia: tarefa agendada do próprio usuário (sem admin), só com o usuário logado.
; Com --vigia o robô sai na hora se já estiver rodando ou se o usuário o fechou pelo menu.
Filename: "{sys}\schtasks.exe"; Parameters: "/Create /F /TN ""{#TarefaVigia}"" /SC MINUTE /MO 5 /IT /TR ""\""{app}\{#AppExe}\"" --vigia"""; Flags: runhidden
; Também roda na instalação silenciosa (atualização distribuída por script)
Filename: "{app}\{#AppExe}"; Description: "Abrir o DispatchBot agora"; Flags: nowait postinstall

[UninstallRun]
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /F /TN ""{#TarefaVigia}"""; Flags: runhidden; RunOnceId: "RemoverVigia"

[Code]
const
  EVENT_MODIFY_STATE = $0002;

function OpenEvent(dwDesiredAccess: DWORD; bInheritHandle: BOOL; lpName: String): THandle;
  external 'OpenEventW@kernel32.dll stdcall';
function SetEvent(hEvent: THandle): BOOL;
  external 'SetEvent@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

function NavegadorInstalado(): Boolean;
var
  Chave: String;
begin
  Chave := 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\';
  Result := RegKeyExists(HKLM, Chave + 'msedge.exe') or RegKeyExists(HKCU, Chave + 'msedge.exe') or
            RegKeyExists(HKLM, Chave + 'chrome.exe') or RegKeyExists(HKCU, Chave + 'chrome.exe');
end;

{ Pede ao robô para sair pelo evento DispatchBot_Sair: ele termina o alerta em andamento e fecha o navegador.
  Se não sair em 30 s, encerra à força (o histórico impede chamado duplicado na próxima execução). }
procedure EncerrarRobo();
var
  Evento: THandle;
  Espera, Codigo: Integer;
begin
  Evento := OpenEvent(EVENT_MODIFY_STATE, False, 'DispatchBot_Sair');
  if Evento <> 0 then
  begin
    SetEvent(Evento);
    CloseHandle(Evento);
    Espera := 0;
    while CheckForMutexes('DispatchBot_Mutex') and (Espera < 30000) do
    begin
      Sleep(500);
      Espera := Espera + 500;
    end;
  end;
  if CheckForMutexes('DispatchBot_Mutex') then
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM DispatchBot.exe', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM DispatchBot-Mapear.exe', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
end;

{ Um botão só: na tela de boas-vindas, "Avançar" vira "Instalar" (não há outras páginas antes da instalação) }
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpWelcome then
    WizardForm.NextButton.Caption := 'Instalar';
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not NavegadorInstalado() then
    Result := SuppressibleMsgBox(
      'Não encontrei o Microsoft Edge nem o Google Chrome nesta máquina.' + #13#10 +
      'O DispatchBot precisa de um deles para abrir os chamados.' + #13#10#13#10 +
      'Continuar a instalação mesmo assim?', mbConfirmation, MB_YESNO, IDYES) = IDYES;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  EncerrarRobo();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  EncerrarRobo();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and
     (SuppressibleMsgBox(
       'Remover também as configurações, os logs e a sessão do navegador do robô?' + #13#10 +
       ExpandConstant('({localappdata}\DispatchBot)'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES) then
    DelTree(ExpandConstant('{localappdata}\DispatchBot'), True, True, True);
end;
