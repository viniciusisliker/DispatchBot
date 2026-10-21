# Power Automate: ajustes nos fluxos

Duas mudanças:

1. **Nos fluxos de alerta ("GMUD | Todos" e BackupBot):** depois do post no Teams, gravar o JSON do alerta em `/DispatchBot-Fila/pendentes`.
2. **Fluxo novo, DispatchBot | Retorno:** lê `/DispatchBot-Fila/retorno` e posta o número do chamado no chat em grupo do time.

Depois de abrir o chamado, **o único aviso é no Teams, no chat do time**. Não há registro na planilha CHANGES nem no Planner.

> **Fila:** site `https://camara1.sharepoint.com/sites/MANAGEDSERVICE`, biblioteca **Documentos Compartilhados**, pasta `YDUQS/N1/DispatchBot-Fila`. Onde aparece `<SITE>` e `<BIBLIOTECA>` abaixo, são esses valores.

> **PRODUCAO** ficou fora do escopo. No "GMUD | Todos", o *Default* do Switch deve ter **Control → Terminate (Succeeded)**, para nenhum alerta de outro time entrar na fila.

> **Organização (decisão de 09/10/2026):** os fluxos ficam agrupados numa Solução **DispatchBot**: "GMUD | Todos", BackupBot e `DispatchBot | Retorno` (seção 2). Não dá para juntar tudo num fluxo: cada fluxo de nuvem tem um único gatilho, e os alertas de GMUD e de Backup chegam em caixas de e-mail diferentes (o Retorno, por sua vez, é disparado por arquivo).

> **Campo `teams`:** não é necessário. O fluxo Retorno escolhe o chat pelo `tipo` do JSON, e o robô só repassa o campo. Pode ficar vazio ou nem existir.

---

## 1. Nos fluxos de alerta

Adicione as duas ações abaixo **depois** da ação que posta o card no Teams (Flow bot).

### 1.0 Fluxo unificado "GMUD | Todos"

Substitui os fluxos GMUD | INFRA, DBA e SO e as 3 regras do Outlook. **Em produção desde 05/10/2026** (testado e virado; os fluxos e regras antigos estão desligados).

```
When a new email arrives (V3) → Html to text → Time → Recorte → Filter array
  → Switch on Time: INFRA / DBA / SO (posta no chat do time) · Default: Terminate (Succeeded)
  → JSON GMUD ("tipo": Time) → Create file (<Time>_....json em pendentes)
```

- **Gatilho:** caixa de entrada, From `noreply.yduqs@service-now.com`, Subject "Uma tarefa de Mudança foi atribuída", trigger condition `@contains(triggerOutputs()?['body/body'], 'SN_TI_CHG_EXECUTOR_CIRION_')`.
- **Teste antes da virada:** um e-mail real de cada time deve gerar `INFRA_...`, `DBA_...` ou `SO_...` em `pendentes` com o `tipo` certo.
- **Virada:** desligar no mesmo momento as 3 regras do Outlook e os 3 fluxos antigos. Se sobrar um ligado, a GMUD entra duas vezes: o robô barra a segunda pela CHG, mas o Teams recebe dois cards.

### 1.1 Compose: `JSON_GMUD` (fluxos de GMUD)

O e-mail de origem é o do ServiceNow YDUQS, *"Uma tarefa de Mudança foi atribuída a você ou ao seu grupo executor"*, disparado quando a GMUD entra em **Agendado**. Cole no campo *Inputs* e troque cada `<...>` pelo trecho que o fluxo já recorta do e-mail:

```json
{
  "versao": 2,
  "id": "<CHG, ex.: CHG0047305>",
  "tipo": "INFRA",
  "grupo_executor": "<grupo executor, ex.: SN_TI_CHG_EXECUTOR_CIRION_INFRA>",
  "chg": "<CHG, ex.: CHG0047305>",
  "ctask": "<CTASK, se o e-mail trouxer>",
  "data_execucao": "<data e hora da execução da Task, ex.: 29/04/2026 09:30>",
  "tipo_mudanca": "<Normal/Programada, Padrão/Pré-aprovada ou Emergencial>",
  "titulo": "<Descrição resumida da CHG>",
  "descricao": "<Descrição geral da GMUD>",
  "recebido_em": "@{triggerOutputs()?['body/receivedDateTime']}",
  "dados_brutos": "<texto tratado do e-mail>"
}
```

O que o robô faz com esses campos, no formulário Hosting do Employee Center (POP 11):

| Campo do formulário | Vem de |
|---|---|
| Descrição resumida | `#GMUD <chg> \| <time> \| <data_execucao em dd/MM HH:mm> \|`. O nome do time vem do config (INFRA = `SOC`) |
| Descrição | `descricao` |
| Conta, CallerID, Categoria | Valores fixos do config |

- **id = número da CHG.** É ele que impede chamado duplicado: a mesma CHG nunca vira dois RITMs, mesmo que o e-mail chegue repetido.
- **Obrigatórios:** `chg`, `data_execucao` e `descricao`. Se um deles vier vazio, ou se a CHG não tiver o formato `CHG` + 7 dígitos, o robô **não abre o chamado**: manda para `/erro` e o Teams recebe o aviso com o que faltou.
- **data_execucao:** pode vir como no e-mail (`29/04/2026 09:30`) ou no formato ISO (`2026-04-29T09:30:00`). O robô converte para o título.
- **tipo:** se não for preenchido, o robô deduz pelo `grupo_executor` (o que vem depois de `CIRION_`).
- **CHG por expressão**, se o fluxo ainda não recorta esse campo: `substring(variables('Texto'), indexOf(variables('Texto'), 'CHG'), 10)`. Troque `Texto` pela variável com o texto do e-mail.

### 1.1b Compose: `JSON_BACKUP` (fluxo de Backup)

```json
{
  "versao": 2,
  "id": "@{triggerOutputs()?['body/internetMessageId']}#@{item()}",
  "tipo": "BACKUP",
  "titulo": "@{take(concat(trim(split(item(), ',')[2]), ' | ', trim(split(item(), ',')[3]), ' | Job ', trim(split(item(), ',')[0]), ' | Status ', trim(last(split(item(), ',')))), 130)}",
  "descricao": "<descrição clara: job, servidor, cliente, ferramenta, horário>",
  "servico_afetado": "<opcional: ex.: Veeam. Sem este campo, vai Veritas Netbackup>",
  "recebido_em": "@{triggerOutputs()?['body/receivedDateTime']}",
  "dados_brutos": "<texto tratado do e-mail>"
}
```

O `id` junta o id do e-mail com a linha inteira do CSV (um e-mail traz várias falhas; a linha tem o JobID, então o `id` não se repete e o robô não abre o mesmo chamado duas vezes).

O título do chamado sai como `#FALHA DE BACKUP <titulo>`, ex.: `#FALHA DE BACKUP YDUQS_VMWARE_RJO_VCLOUD-02_NEW | appflexrj01-inst01.smpdc.gblx | Job 26661937 | Status 4287` (~120 caracteres; o `take(...,130)` garante no máximo 147 com o prefixo, abaixo dos 160 da Short Description). **Aplicada no BackupBot em 09/10/2026** (substituiu o `take(item(),80)` provisório).

**Colunas do CSV (confirmadas com linha real em 09/10/2026, execução de 08/10/2026):** sem cabeçalho; `[0]` JobID, `[1]` Master (`NBMASTERBRARJ01`), `[2]` Política, `[3]` Cliente (servidor), `[4]` Agendamento (veio `UNKNOWN`), depois Início e Fim **entre aspas e com vírgula dentro** (`"Oct 8, 2026 8:23:55 AM"`), Status (`Failed`) e Código (`4287`). Exemplo real:
`26661937,NBMASTERBRARJ01,YDUQS_VMWARE_RJO_VCLOUD-02_NEW,appflexrj01-inst01.smpdc.gblx,UNKNOWN,"Oct 8, 2026 8:23:55 AM","Oct 8, 2026 8:24:24 AM",Failed,4287`.
Por causa das vírgulas nas datas, o `split(item(), ',')` só é confiável até o índice `[4]`; Status e Código se pegam do fim (Código = `last(...)`). Não use `[7]`/`[8]`. Enquanto os POPs não fecharem as lacunas de Backup, **o robô só preenche o formulário, e o plantonista confere e clica em Enviar.**

- **Quem é avisado:** o ícone fica amarelo e aparece um aviso na tela do plantonista.
- **Se ninguém clicar em 30 minutos:** o alerta vai para `/erro` e o Teams avisa.
- **Quebras de linha e aspas:** o Compose monta um objeto JSON, então quebras de linha e aspas no texto do e-mail já saem escapadas. Não coloque o objeto inteiro entre aspas.
- **Caractere nulo (`\u0000`) no texto:** o CSV anexo vem em UTF-16 e o fluxo lê como UTF-8, então cada letra sai seguida de um `\u0000` no JSON. Desde a 1.7.2 o robô limpa esses caracteres ao ler o alerta (título, descrição e id); não precisa mexer no fluxo.

### 1.2 SharePoint · Create file (Criar arquivo)

| Campo | Valor |
|---|---|
| Site Address | `<SITE>` |
| Folder Path | `/<BIBLIOTECA>/DispatchBot-Fila/pendentes` |
| File Name | `concat('INFRA_', formatDateTime(utcNow(), 'yyyyMMdd_HHmmss'), '_', substring(guid(), 0, 8), '.json')` (troque `INFRA_` pelo tipo do fluxo) |
| File Content | `outputs('JSON_GMUD')` (ou `outputs('JSON_BACKUP')`) |

Se o chamado precisar ser aberto mesmo quando o post no Teams falhar, configure o *Run after* do Compose para "is successful" **e** "has failed". O retorno continua funcionando, porque ele escolhe o chat pelo `tipo`.

---

## 2. Fluxo novo: DispatchBot | Retorno

Um único fluxo atende todos os tipos. Os alertas são postados em **chats em grupo** (não canais), que não têm thread: o Retorno posta uma **mensagem nova** no chat do time, citando a CHG (ou a falha de Backup) e o número do chamado.

| Tipo | Chat em grupo |
|---|---|
| INFRA | (FC) NET / SOC |
| DBA | (FC) BANCO DE DADOS (DBA'S + N1 + Gestão) |
| SO | (FC) LINUX - WINDOWS - VMWARE |
| BACKUP | (FC) NETBACKUP |

1. **Gatilho:** SharePoint · *When a file is created (properties only)*
   - Site Address: `MANAGED SERVICE` (`https://camara1.sharepoint.com/sites/MANAGEDSERVICE`)
   - Library Name: **Documentos** (é a "Documentos Compartilhados": a lista do Power Automate mostra o nome de exibição; é a única biblioteca do site)
   - Folder (em *Advanced parameters → Show all*): pelo ícone 📁 → `/Documentos Compartilhados/YDUQS/N1/DispatchBot-Fila/retorno` (descer até **retorno**, não parar na DispatchBot-Fila)
   - Em *Settings → Trigger conditions*: `@endsWith(triggerOutputs()?['body/{FilenameWithExtension}'], '.json')`
2. **SharePoint · Get file content** (mesmo site)
   - File Identifier: **Identifier** (conteúdo dinâmico do gatilho)
3. **Compose**, renomeado para `Retorno`
   - Inputs (expressão): `json(string(body('Get_file_content')))` (o `string()` cobre os dois jeitos que o conector entrega o arquivo: texto ou JSON já lido, com *Infer content type* = Yes). Se der erro de tipo: `json(base64ToString(body('Get_file_content')?['$content']))`. Se o erro disser que `json` espera texto (o conector já entregou o JSON pronto), use só `body('Get_file_content')`.
   - O nome dentro de `body('...')` é o nome da ação com `_` no lugar dos espaços. Com o portal em português, a ação vem como `Obter conteúdo do arquivo` → `body('Obter_conteúdo_do_arquivo')`; ou renomeie a ação para `Get file content`.
4. **Compose**, renomeado para `Mensagem`
   - Inputs (expressão):
     ```
     if(equals(outputs('Retorno')?['status'], 'ok'),
        concat('✅ <b>', outputs('Retorno')?['tipo'], ' ', if(equals(outputs('Retorno')?['tipo'], 'BACKUP'), outputs('Retorno')?['titulo'], outputs('Retorno')?['id']), '</b><br><br>Chamado <b>', outputs('Retorno')?['numero'], '</b> aberto.<br><br><a href="', outputs('Retorno')?['url'], '">Abrir o chamado no ServiceNow</a>'),
        concat('⚠️ <b>', outputs('Retorno')?['tipo'], ' ', if(equals(outputs('Retorno')?['tipo'], 'BACKUP'), outputs('Retorno')?['titulo'], outputs('Retorno')?['id']), '</b><br><br>O robô NÃO abriu o chamado. Abrir manualmente.<br><br>Motivo: ', outputs('Retorno')?['mensagem']))
     ```
   - A mensagem do Teams aceita HTML: `<b>` = negrito, `<br><br>` = uma linha em branco, `<a href>` = link clicável com texto (o `url` abre o chamado direto na tela de tratamento, interface clássica). Formato com espaçamento pedido pelo usuário em 21/10/2026 (antes era uma linha só).
   - Exemplo (sucesso):
     > ✅ **BACKUP YDUQS_VMWARE_RJO_VCLOUD-02_NEW | ESRVDTDOCKER-rtfz | Job 26819484 | Status 1**
     >
     > Chamado **CS0735598** aberto.
     >
     > [Abrir o chamado no ServiceNow](https://envdatacenter.service-now.com/now/nav/ui/classic/...)
   - Exemplo (erro): `⚠️ BACKUP YDUQS_VMWARE_... | Job 26509786` em negrito, linha em branco, "O robô NÃO abriu o chamado. Abrir manualmente.", linha em branco, "Motivo: ...".
5. **Switch** · On: `outputs('Retorno')?['tipo']`
   - Um *Case* por linha da tabela acima (`INFRA`, `DBA`, `SO`, `BACKUP`), cada um com **Teams · Post message in a chat or channel**:
     - Post as: **Flow bot** · Post in: **Chat em grupo** · Chat em grupo: o da tabela
     - Message: `outputs('Mensagem')` (aba Expression) ou **Mensagem → Outputs** (conteúdo dinâmico)
   - *Default*: não faz nada (são os testes com tipos desconhecidos).
   - Case extra `TESTE` → chat em grupo de teste ("Automação DispatchBot"): para testar o fluxo sem postar no chat real do time, subindo `exemplos\RETORNO_TESTE.json` em `/retorno` (testado em 09/10/2026).
6. **SharePoint · Delete file** (opcional) com o mesmo Identifier do gatilho. O histórico fica em `/processados` e `/erro`; `/retorno` pode ficar limpa.

> O arquivo de retorno traz: `id` (CHG ou id do e-mail), `tipo`, `titulo`, `status` (`ok`/`erro`), `numero`, `url` (link de tratamento do RITM/INC na interface clássica), `mensagem`, `maquina`, `processado_em` e `teams`.
