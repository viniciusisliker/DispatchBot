# DispatchBot

Transforma os alertas de **GMUD** (INFRA, DBA e SO) e de **Backup** em chamados no ServiceNow da Cirion (Employee Center, `/esc`). O robô preenche o formulário no Edge/Chrome da máquina do plantonista, usando a sessão SSO dele.

```
Power Automate ──JSON──▶ SharePoint /DispatchBot-Fila/pendentes ──API REST──▶ Robô (máquina do N1) ──▶ ServiceNow
       ▲                                                                       │
       └──── mensagem no chat do time (Teams) ◀── /retorno ◀───────────────────┘
```

Guia para os plantonistas: [docs/guia.html](docs/guia.html) · Arquitetura e decisões: [docs/tecnico.html](docs/tecnico.html) · Documentação técnica detalhada: [docs/tecnico/documentacao-tecnica.html](docs/tecnico/documentacao-tecnica.html) (PDF anexo às Releases) · Ajustes nos fluxos: [docs/power-automate.md](docs/power-automate.md)

## Instalar (plantonista)

O plantonista só roda `DispatchBot-Setup-x.y.z.exe` e clica em **Instalar**. Não precisa de admin. O resto é automático:

1. **O instalador deixa tudo ligado:** o robô abre ao entrar no Windows e tem um vigia, uma tarefa agendada que o reabre a cada 5 minutos se ele cair. Se o plantonista fechar pelo menu *Sair*, o vigia respeita.
2. **O robô lê a fila direto no SharePoint**, pela API REST, com a sessão Microsoft (cookies FedAuth/rtFa) do perfil do navegador do robô. Não usa OneDrive nem pasta sincronizada.
   - Na maioria das vezes os cookies já estão salvos no perfil e o robô nem abre o site.
   - Se a sessão Microsoft vencer, o robô visita o site para renová-la. Se a Microsoft pedir login, abre uma janela e espera o plantonista (ícone **amarelo**). Ele nunca digita usuário nem senha.
3. **O robô abre o ServiceNow logo ao iniciar**, para o login ser no começo do plantão, e não no primeiro alerta. Na tela do portal ele clica em *Use external login* e, na tela *External login*, em **Submit** (sem preencher nada); o resto (conta Microsoft, Windows Hello) é com o plantonista. Ele nunca digita senha.
4. **O navegador do robô fica sempre aberto** (`fechar_apos_ocioso_min: 0`), porque a sessão do ServiceNow não sobrevive ao fechamento. Com ele parado, o robô recarrega o portal a cada `manter_sessao_min` (10) para a sessão não expirar; se ela caiu, pede o login na hora.

### Onde fica a fila (config, time de RPA)

```json
"fila_sharepoint": {
  "site": "https://camara1.sharepoint.com/sites/MANAGEDSERVICE",
  "pasta": "/sites/MANAGEDSERVICE/Documentos Compartilhados/YDUQS/N1/DispatchBot-Fila"
}
```

`pasta` é o caminho da `DispatchBot-Fila` a partir do servidor, como aparece na barra de endereço (com espaços no lugar de `%20`). Para reservar um alerta, o robô move o arquivo de `pendentes` para `processando` (operação atômica do SharePoint), então dois robôs nunca pegam o mesmo arquivo.

Com `site` vazio, o robô volta ao modo antigo: pasta sincronizada pelo OneDrive (`pasta_sincronizada` / `link_sincronizar`, detecção pelo registro e [docs/gerar-link-sincronizacao.js](docs/gerar-link-sincronizacao.js)). Esse modo fica só como reserva.

A leitura no SharePoint real foi testada em 01/10/2026 (sem janela e sem login), e a renovação da sessão vencida aconteceu sozinha em 14/10/2026.

Para **atualizar**, rode o instalador da versão nova por cima, inclusive em modo silencioso (`/VERYSILENT`). O instalador pede para o robô sair: ele termina o alerta em andamento, e o instalador só força o encerramento se ele não sair em 30 s. Depois o robô é aberto de novo. Configuração, sessão e histórico são preservados, e chaves novas do config entram sozinhas. Na desinstalação, o instalador pergunta se deve apagar também esses dados.

**Atualização automática (1.10.8):** o robô instalado olha a última Release deste repositório de hora em hora (`atualizacao` no config), baixa o instalador para `%LOCALAPPDATA%\DispatchBot\atualizacao` (confere tamanho e o SHA-256 publicado pelo GitHub) e roda `/VERYSILENT` sozinho quando está parado: pausado, ou ativo sem alerta na fila, conferência, pergunta ou login na tela. Fecha e abre de novo em cerca de 1 minuto; no histórico do time aparecem ATUALIZANDO e ATUALIZOU. Menu do ícone: **Procurar atualização agora**. `atualizacao.automatica: false` desliga (só pelo menu). Quem está numa versão anterior à 1.10.8 instala a 1.10.8 à mão uma vez.

O robô fica no ícone da bandeja. **Verde** = ativo, **azul** = preparando (só na instalação) ou instável (SharePoint/internet falhou por um momento; o robô tenta de novo sozinho e só fica vermelho se passar de 5 min), **amarelo** = precisa do plantonista (login, ou conferir um chamado que o robô não enviou sozinho), **vermelho** = erro (detalhe no painel e no log), **cinza** = pausado. **Só o Dispatcher N1 da vez deixa o robô ativo**. Nas outras máquinas, use *Pausado*. Se dois robôs ficarem ativos ao mesmo tempo, os dois mostram um aviso.

### Painel do plantonista (tema escuro, desde a 1.10.0)

Clicar no ícone (ou **Abrir painel** no menu) abre uma janela escura (CustomTkinter, ícones do próprio Windows) com:

- **Cabeçalho:** o avatar com a bolinha de status, o status em uma palavra (Ativo, Erro, Aguardando login...), o detalhe logo abaixo (ex.: o motivo do erro), o resumo da fila e o botão *Pausar/Retomar*.
- **Contadores do dia:** na fila, abertos hoje, erros hoje e "precisa de você".
- **Precisa de você:** login esperando, chamado preenchido para conferir (*Ver formulário*, *Pular*; só quando o envio automático está desligado ou o robô achou campo obrigatório vazio) e alerta antigo (*Abrir chamado*, *Já foi aberto*, *Depois*).
- **Chamados abertos hoje:** CHG (ou título do Backup), número em verde, *Tratar* (abre o chamado na tela de tratamento) e *Copiar*.
- **Erros de hoje: abrir à mão:** o motivo de cada um, *Abrir no ServiceNow*, *Copiar* e *Resolvido* (tira da lista depois de abrir à mão ou quando era alarme falso; sem outro erro, o ícone sai do vermelho).
- **Rodapé:** *Verificar agora*, *Logs* e **PARAR AGORA** (pede confirmação).

Quando o robô precisa do plantonista, uma **janelinha "Precisa de você"** aparece sozinha no canto da tela, mesmo com o painel fechado (com os campos preenchidos e a contagem do tempo para enviar).

Fechar a janela só esconde o painel; o robô continua rodando. O painel só lê `painel.jsonl` (o robô grava uma linha a cada chamado aberto ou erro) e não muda nada no config nem na fila.

Arquivos locais em `%LOCALAPPDATA%\DispatchBot`: `config.json`, `logs\robo.log`, `logs\screenshots\` (capturas de erro), `processados.jsonl` (ids já atendidos), `painel.jsonl` (histórico do painel) e `browser-profile\`.

## Parada de emergência

Se o robô estiver abrindo chamados errados, pare na hora por qualquer um destes caminhos:

| Como | Onde funciona | Como liberar |
|---|---|---|
| **PARAR AGORA**, primeiro item do menu do botão direito (pede confirmação desde a 1.7.1, para ninguém clicar sem querer ao mexer no *Pausado*), ou o botão do painel (imediato) | Na máquina do robô | Desmarcar *Pausado* no menu |
| **`Ctrl+Alt+Shift+P`** ("P de Parar"), com qualquer janela na frente, inclusive o navegador do robô | Na máquina do robô | Desmarcar *Pausado* no menu |
| Criar um arquivo **`PARAR`** (ou `PARAR.txt`) na raiz da pasta `DispatchBot-Fila` do SharePoint. O texto dentro dele vira o motivo exibido | Em **todas** as máquinas com o robô, em até 10 s (`intervalo_parar_seg`) | Apagar o arquivo |

- **Em qualquer caminho:** o robô interrompe no meio do passo, sem esperar o alerta terminar, fecha o navegador e fica com o ícone vermelho mostrando o motivo.
- **Antes do clique em Enviar:** o alerta volta intacto para `/pendentes`.
- **Depois do clique em Enviar:** o alerta vai para `/erro` com "confira no ServiceNow" e nunca é reenviado sozinho.
- **A parada local fica gravada:** nem reiniciar o computador nem o vigia fazem o robô voltar sozinho.

O atalho que ficou ativo aparece ao lado do item PARAR AGORA no menu. Se `Ctrl+Alt+Shift+P` já for usado por outro programa, o robô tenta os próximos da lista `atalho_emergencia` do config (`Ctrl+Alt+Shift+X`, depois `Ctrl+Alt+Pause`).

## Quando algo dá errado

| Situação | O que o robô faz |
|---|---|
| Rede, instância lenta, navegador travado | Tenta de novo até `tentativas_por_alerta` vezes (padrão 3), com navegador novo, antes de mandar para `/erro` |
| ServiceNow recusa o formulário (campo obrigatório etc.) | Vai direto para `/erro` com a mensagem da tela, sem repetir. Configure `seletor_erro_validacao` no tipo |
| Falha **depois** do clique em Enviar | Vai para `/erro` com "confira no ServiceNow" e **nunca** reenvia sozinho |
| Ninguém faz o login | Devolve o alerta para `/pendentes` e continua esperando. Depois de `max_espera_login_min` (60) desde o recebimento, manda para `/erro` e o Teams avisa para abrir manualmente |
| Pausar durante a espera do login | Para na hora e devolve o alerta para `/pendentes` |
| SharePoint fora do ar ou pasta inacessível | Ícone vermelho. Volta sozinho quando o SharePoint responder |
| Sessão Microsoft vencida (SharePoint) | Renova sozinho; se a Microsoft pedir login, abre a janela e o ícone fica amarelo |
| `config.json` com erro de digitação | Mostra a linha do erro e continua com a última configuração válida |
| Alerta antigo na fila (recebido há mais de `perguntar_alerta_antigo_min`, padrão 120 min; ex.: robô ficou pausado) | **Pergunta antes de abrir** (1.7.1): *Sim* abre; *Não* = já foi aberto à mão, vai para `processados` sem chamado e sem mensagem no Teams; *Cancelar* pergunta de novo em 30 min. Sem resposta, o alerta espera em `/pendentes` e os alertas novos seguem normalmente. Uma pergunta por vez; `forcar_reprocessamento` não pergunta; `0` desliga |
| Alerta com texto ilegível (ex.: anexo do e-mail lido errado pelo fluxo) | Não abre chamado nem pergunta: vai para `/erro` e o Teams avisa "abra à mão pelo e-mail original" (título trocado por "(texto ilegível)") |
| A janela do Edge do robô foi fechada | Não é erro: o robô avisa e reabre a janela no próximo ciclo |
| Robô fechado no meio de um alerta | Na volta, devolve o alerta para a fila, conclui o que já tinha sido aberto, ou manda para `/erro` se parou depois do Enviar |
| Máquina de outro plantonista caiu com um alerta reservado | Depois de `reserva_abandonada_min` (60), o alerta vai para `/erro` para conferência |
| Edge preso no perfil do robô | Fecha só os processos que usam o perfil do robô e abre de novo |
| Erro interno inesperado | A thread do robô é reiniciada, e o vigia reabre o processo se ele morrer |

Cada máquina grava `DispatchBot-Fila/status/<MÁQUINA>.json` com o status, a versão e o horário do último batimento (a cada 5 min). Assim o time vê qual robô está ativo.

## Tipos de alerta

Os chamados são abertos pelo **Employee Center** da Cirion (`/esc`), seguindo os POPs. Se dois POPs divergirem, vale o mais recente.

| Tipo | Formulário | Situação no `config.default.json` |
|---|---|---|
| INFRA, DBA, SO (GMUD) | **Hosting** (POP 11). Os três herdam do modelo `_GMUD` | **Pronto.** Conta, CallerID, Categoria (`Other`), título, descrição e botão Submit mapeados. INFRA preenchido no portal real em 01/10/2026. Nome no título: INFRA = `SOC`, DBA = `DBA`, SO = `SO`. O Hosting cria um caso **CS** e um **RITM** ligado a ele; o robô guarda o **RITM** (é o número que o time usa) e só aceita o CS se o RITM não aparecer em 30 s. Envio automático por padrão (ver *Envio automático* abaixo) |
| BACKUP | **Incidente** pelo cartão Backup, como no BackupBot (Power Automate Desktop, 15/07/2026) | **Pronto.** Conferido no portal real em 05/10/2026 com os 6 obrigatórios preenchidos. O *Failure Type* é obrigatório e entra como `Execution failure` (o alerta pode mandar outro em `failure_type`). URL, seletores e valores vêm do BackupBot. Envio automático por padrão (ver *Envio automático* abaixo). Serviço afetado padrão: Veritas Netbackup; o alerta pode mandar outro em `servico_afetado`. O Incidente cria um caso **CS** e o **INC** fica na aba Related Records (escondida); desde a 1.10.6 o robô lê também o texto escondido e guarda o **INC** (é o número que o time usa), aceitando o CS só se o INC não aparecer em 60 s |

PRODUCAO ficou fora do escopo: o tipo continua no config com `PREENCHER`, então um alerta PRODUCAO vai para `/erro` sem abrir chamado.

Onde o BackupBot e os POPs divergem, vale o mais recente. O impacto segue o BackupBot (impacto mínimo, e não "Nenhum" como no POP 03). O título segue o POP 08 (`#FALHA DE BACKUP`).

Título da GMUD: `#GMUD <CHG> | <TIME> | <dd/MM HH:mm> |`, ex.: `#GMUD CHG0047305 | SOC | 29/04 09:30 |`. Depois de aberto, o chamado **só é avisado no Teams**: o fluxo *DispatchBot | Retorno* posta uma mensagem nova no chat em grupo do time (chat em grupo não tem thread), com o número e o **link de tratamento** do chamado. Não há registro na planilha CHANGES nem no Planner.

**Link de tratamento:** o `url` do retorno, do histórico e do painel abre o chamado direto na tela clássica do ServiceNow, onde o plantonista trata. O formato vem de `link_tratamento` no config (`/now/nav/ui/classic/params/target/<tabela>.do%3Fsysparm_query%3Dnumber%3D<número>`); a tabela sai do prefixo do número (RITM, INC, CS...).

**O robô não inventa dados.** Se o alerta não trouxer algo que o formulário usa (CHG, data de execução, descrição), ou trouxer fora do padrão (CHG que não é `CHG` + 7 dígitos), o chamado não é aberto: o alerta vai para `/erro` com o que faltou, e o Teams avisa.

## Mapear os campos de cada tipo

Enquanto um tipo tiver `PREENCHER` no `config.json`, os alertas dele vão para `/erro` sem abrir chamado.

1. Pause o robô (ou saia dele) pelo ícone da bandeja.
2. Abra **Menu Iniciar → DispatchBot → Mapear campos do ServiceNow**. O Edge abre no portal junto com o Playwright Inspector.
3. Navegue até o formulário e clique em **Resume (▶)** no Inspector. O console lista cada campo com o seletor e a ação sugerida. A lista completa, com as opções dos selects, fica em `%LOCALAPPDATA%\DispatchBot\campos_mapeados.json`. Para um campo específico, use **Pick locator** no Inspector.
4. No `config.json`, em `tipos`, troque cada `PREENCHER` pelo valor real. Cada passo tem um `"campo"` com o nome que aparece na tela, para saber qual seletor vai em qual passo. As chaves disponíveis são:
   - `url_formulario`: a URL do formulário sem o domínio (ex.: `/esc?id=sc_cat_item&sys_id=...`).
   - `passos`: um item por campo. As ações possíveis são:
     - `preencher`: texto.
     - `selecionar`: `<select>`; aceita o valor ou o texto da opção.
     - `referencia`: campo com busca. Digita `busca` (ou `valor`, se não houver `busca`) e clica na opção que contém `valor`. Ex.: busca "SOCIEDADE DE ENSINO SUPERIOR ESTACIO", opção com "3-9R2V6QXWXV".
     - `marcar`: checkbox.
     - `clicar`.
     - `aguardar`: espera um seletor ou os milissegundos informados em `valor`.
   - Em `valor` e `busca`, `{campo}` vem do JSON do alerta: `{chg}`, `{data_execucao}`, `{titulo}`, `{descricao}`, `{tipo}` etc. Datas aceitam formato, como `{data_execucao|dd/MM HH:mm}`.
   - `variaveis`: valores fixos do tipo, usados como `{nome}` (Conta, CallerID, o nome do time no título...).
   - `herda`: usa outro bloco como modelo e sobrescreve só o que mudar (os times de GMUD herdam de `_GMUD`). Blocos com `_` no nome são modelos, não tipos.
   - `validar`: formato exigido de um campo do alerta (ex.: `"chg": "CHG\\d{7}"`).
   - Opções extras de cada passo:
     - `"mapa": {"Alta": "1", "*": "2"}` traduz o valor (`*` é o padrão).
     - `"opcional": true` segue em frente se o passo falhar.
     - `"pular_se_vazio": true` pula o passo, e não exige o dado, quando o valor está vazio.
   - `botao_enviar`: seletor do botão de envio.
   - Quem decide se o plantonista confere é a chave `envio_automatico` (fora de `tipos`, ver abaixo), não o tipo. Na conferência, se ninguém clicar em `confirmacao_timeout_min` (padrão 30), o alerta vai para `/erro`.
   - `seletor_erro_validacao` (opcional): mensagem de erro que o ServiceNow mostra quando recusa o envio (ex.: `.sp-form-errors, .alert-danger`).
   - `captura_numero.regex`: formato do número (ex.: `RITM\\d{7}`). Com `captura_numero.seletor`, ele procura só dentro daquele elemento.
5. **Envio automático** (desde 16/10/2026): `"envio_automatico": {"GMUD": true, "BACKUP": true}`. Ligado, o robô clica em enviar sozinho, mas antes confere os campos obrigatórios do formulário (asterisco do Service Portal, o mesmo detector do `--testar`). Se algum estiver vazio, se o `botao_enviar` do tipo estiver vazio ou se a conferência falhar, ele **não clica**: o ícone fica amarelo e a janelinha "Precisa de você" mostra o motivo ("Não enviei sozinho: campo obrigatório vazio: …"), e o plantonista completa e envia. `GMUD` vale para todos os tipos que herdam `_GMUD`. O plantonista liga e desliga pelo menu do ícone (**Enviar GMUD sem conferir**, **Enviar Backup sem conferir**). Não muda: o status `enviando` é gravado antes do clique e o robô nunca reenvia sozinho. A chave antiga `envio_automatico_gmud` e o `confirmar_antes_de_enviar` dos tipos não são mais lidos.
6. Para criar um tipo novo, crie um bloco em `tipos`, se quiser com `"herda"` de um modelo. Não precisa mexer no código.

## Testar

**Formulário, sem enviar** (valida os seletores; não mexe nas pastas):

```bat
"%LOCALAPPDATA%\Programs\DispatchBot\DispatchBot-Mapear.exe" --testar "%LOCALAPPDATA%\Programs\DispatchBot\exemplos\INFRA.json"
```

Depois de preencher, o console lista os **campos obrigatórios** do formulário (os que têm asterisco) e marca `VAZIO` nos que ficaram sem valor. Um obrigatório vazio faz o Submit recusar: mapeie esse campo no `config.json`. Se algum passo falhar, a lista sai do mesmo jeito, com o print em `logs\screenshots\`.

Adicione `--enviar` para abrir o chamado de verdade e ver o número no console. Há exemplos para `INFRA.json`, `DBA.json`, `SO.json` e `BACKUP.json` (o `PRODUCAO.json` só serve para ver o alerta ir para `erro\`). Os de GMUD usam CHGs fictícias (`CHG999000x`). O `GMUD_SEM_DATA.json` deve ir para `erro\` sem abrir chamado.

**Fluxo completo:** suba um exemplo para `DispatchBot-Fila/pendentes` no SharePoint com o robô ativo. Em alguns segundos, o arquivo vai para `processados\<NUMERO>_<nome>.json` e aparece um JSON em `retorno\`. O `exemplos\TIPO_INVALIDO.json` deve ir para `erro\`.

- **Mesmo alerta de novo:** o id do alerta fica registrado. Um novo arquivo com o mesmo id é ignorado. Para testar de novo, troque o `id` no arquivo.
- **Reprocessar um alerta que foi para `/erro`:** corrija a causa e mova o JSON de volta para `pendentes`.
  - Se a falha foi **depois** do clique em Enviar, o robô bloqueia a repetição, porque o chamado pode existir. Confira no ServiceNow e, se precisar mesmo reabrir, adicione `"forcar_reprocessamento": true` no JSON.

## Gerar o instalador (desenvolvedor)

Precisa de Python 3.12 e do Inno Setup 6 (`winget install JRSoftware.InnoSetup --scope user`).

```bat
powershell -ExecutionPolicy Bypass -File build\build.ps1
```

O script cria a `.venv`, confere a sintaxe e gera `dist\DispatchBot\` (PyInstaller onedir: `DispatchBot.exe` + `DispatchBot-Mapear.exe`, ~150 MB sem navegador) e `dist\instalador\DispatchBot-Setup-<versão>.exe` (~39 MB). A versão vem só de `robo\versao.py`.

**Antivírus:** nos testes, o antivírus corporativo bloqueou e depois removeu o desinstalador (`unins000.exe`) de uma instalação feita em `%TEMP%`. Na pasta padrão, `%LOCALAPPDATA%\Programs\DispatchBot`, instalar, atualizar e desinstalar funcionaram. Para evitar bloqueios, peça à segurança um certificado de assinatura de código e defina `DISPATCHBOT_CERT_THUMBPRINT` antes do build: os executáveis e o instalador saem assinados.

Para rodar a partir do código: `pip install -r requirements.txt` e `python robo\main.py`. Não rode `playwright install`, porque o robô usa o Edge/Chrome do sistema.

## Estrutura

```
robo/main.py            loop, reserva, idempotência, novas tentativas, supervisor, vigia
robo/versao.py          versão única (log, status, instalador)
robo/emergencia.py      atalho global e arquivo PARAR (parada de emergência)
robo/fila_sharepoint.py fila direto na API REST do SharePoint (reserva com MoveTo, renova a sessão)
robo/fila.py            fila em pasta (modo antigo), reserva por hostname, processados.jsonl, JSON de retorno
robo/pasta.py           só no modo antigo: acha e sincroniza a pasta do OneDrive
robo/config.py          carrega e mescla o config, herança entre tipos
robo/navegador.py       Edge→Chrome, perfil persistente, login SSO, cookies do SharePoint
robo/servicenow.py      executa os passos do tipo e captura o número
robo/bandeja.py         ícone e menu da bandeja
robo/painel.py          painel do plantonista e janela "Precisa de você" (CustomTkinter, tema escuro)
robo/demo.py            demonstração: preenche GMUD e Backup no portal real sem enviar
robo/mapear_campos.py   ferramenta de mapeamento e teste
config.default.json     modelo copiado para %LOCALAPPDATA%\DispatchBot na 1ª execução
build/  installer/  exemplos/  docs/
```
