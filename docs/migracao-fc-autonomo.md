# Migração dos fluxos para a conta FCamara Autonomo

Conta nova: **FCamara Autonomo** (`fc_autonomo@fcamara.com`). Objetivo: os 3 fluxos (GMUD | Todos, BackupBot, DispatchBot | Retorno) deixam de depender das contas pessoais do Vinícius.

## O que depende de quem hoje (lido nos pacotes de `fluxos/`, 14/10/2026)

| Fluxo | Conexão | Conta hoje | Observação |
|---|---|---|---|
| GMUD \| Todos | Outlook (gatilho) | `morais.vinicius.ter@estacio.br` | **outro tenant (Estácio)**; gatilho: From `noreply.yduqs@service-now.com`, assunto "Uma tarefa de Mudança foi atribuída", pasta fixa por Id |
| GMUD \| Todos | SharePoint, Teams | `vinicius.morais@fcamara.com` | grava em `pendentes`; posta como Flow bot em 3 chats em grupo |
| GMUD \| Todos | Conversão de Conteúdo | (sem conta) | Html to text |
| BackupBot | Outlook (gatilho, Reply, Move) | `vinicius.morais@fcamara.com` | From `operacaobkp@ciriontechnologies.com;vinicius.morais@fcamara.com`, assunto "Relatório informa as Execuções dos backups", pasta do gatilho e pasta do Move por Id |
| BackupBot | SharePoint, Teams | `vinicius.morais@fcamara.com` | grava em `pendentes`; posta no (FC) NETBACKUP |
| Retorno | SharePoint, Teams | `vinicius.morais@fcamara.com` | gatilho em `retorno`; posta em 5 chats (TESTE, INFRA, DBA, SO, BACKUP) + o Case GMUD_BACKUP de 16/10 |

O robô (máquina do N1) **não muda**: ele lê a fila com a sessão do plantonista.

## Atenção antes de começar

1. **Os pacotes de `fluxos/` estão velhos.** Em 16/10/2026 mudaram o gatilho, o `Time` e o Case `GMUD_BACKUP` do "GMUD | Todos" e o Case `GMUD_BACKUP` do Retorno. Exporte os 3 de novo **antes** de mexer (e troque os zips aqui).
2. **GMUD vem de outro tenant.** O e-mail da CHG chega em `morais.vinicius.ter@estacio.br` (Estácio). A conta `fc_autonomo@fcamara.com` não consegue ler essa caixa. Opções:
   - **A (rápida):** regra no Outlook da Estácio com **Redirecionar para** `fc_autonomo@fcamara.com`. Use *redirecionar*, não *encaminhar*: o redirecionamento mantém o remetente `noreply.yduqs@service-now.com` e o filtro do gatilho continua valendo. Risco: o tenant da Estácio pode bloquear o envio automático para fora (o e-mail não chega e às vezes volta um NDR). Além disso, a regra continua dependendo da sua conta da Estácio.
   - **B (definitiva):** pedir à YDUQS/Cirion que o e-mail do grupo `SN_TI_CHG_EXECUTOR_CIRION_*` / `..._Backup_Cirion` no ServiceNow também vá para `fc_autonomo@fcamara.com` (ou para uma lista que a inclua).
   - Recomendação: ligar a A já e pedir a B em paralelo.
3. **Backup:** mesma ideia, só que no mesmo tenant. Regra em `vinicius.morais@fcamara.com` com **Redirecionar para** `fc_autonomo@fcamara.com` (assunto contém "Relatório informa as Execuções dos backups"), ou pedir à Cirion (`operacaobkp@ciriontechnologies.com`) que inclua a conta na distribuição.
4. **O BackupBot responde o e-mail** (Reply com Cc para o time e `operacao.yduqs@ciriontechnologies.com`). Depois da troca, a resposta sai **de `fc_autonomo@fcamara.com`**. O `To` está fixo em `vinicius.morais@fcamara.com`: decidir se fica assim.

## Pré-requisitos da conta nova

- [ ] Caixa de e-mail ativa e licença com Power Automate (os 3 fluxos usam só conectores standard).
- [ ] Permissão de **editar** na pasta `MANAGEDSERVICE` → `Documentos Compartilhados/YDUQS/N1/DispatchBot-Fila` (pendentes e retorno).
- [ ] **Membro dos 5 chats em grupo** (o Flow bot só posta em chat do qual o dono da conexão participa): Automação DispatchBot (TESTE), (FC) NET / SOC, (FC) BANCO DE DADOS, (FC) LINUX - WINDOWS - VMWARE, (FC) NETBACKUP.
- [ ] E-mails de GMUD e de Backup chegando na caixa `fc_autonomo` (item 2 e 3 acima). Conferir com 1 e-mail real de cada.

## Como passar os fluxos (recomendado: coproprietário + trocar conexões)

Mantém o mesmo fluxo (mesmo id e histórico), sem importar cópia e sem risco de 2 fluxos rodando juntos.

Para cada fluxo, logado como **vinicius.morais@fcamara.com**:

1. Abrir o fluxo → **Compartilhar** (Coproprietários) → adicionar `fc_autonomo@fcamara.com`.

Depois, logado como **fc_autonomo@fcamara.com** (Power Automate → Meus fluxos → **Compartilhados comigo**):

2. **Retorno primeiro** (não tem e-mail): Editar → em cada ação de SharePoint e Teams, **Alterar conexão** → criar/escolher a de `fc_autonomo` → Salvar. Para testar, copie `exemplos\RETORNO_TESTE.json` para a pasta **retorno** (nunca para pendentes) e confira se a mensagem aparece no chat "Automação DispatchBot" postada pelo Flow bot.
3. **BackupBot** (só depois que o e-mail de Backup chegar na caixa nova): trocar as conexões de Outlook, SharePoint e Teams. No gatilho, escolher de novo a **Pasta** (o Id antigo é da caixa do Vinícius) e conferir o **From**. No **Move email**, escolher a pasta de destino na caixa nova (crie uma igual à de hoje). Salvar.
4. **GMUD | Todos** (só depois que o e-mail da GMUD chegar na caixa nova): trocar Outlook (gatilho), SharePoint e Teams. Pasta do gatilho = Caixa de Entrada da `fc_autonomo`. Conferir que a **trigger condition** e o filtro From/assunto continuam iguais. Salvar.
5. Desligar as regras de redirecionamento só se a opção B (definitiva) estiver valendo.

Troque a conexão do Outlook no mesmo momento em que o e-mail passa a chegar na caixa nova. Se trocar antes, o gatilho fica olhando uma caixa vazia e nenhum alerta entra na fila.

## Alternativa: importar uma cópia

Se o coproprietário não funcionar (por exemplo, por política do ambiente): exportar os 3 de novo, importar logado como `fc_autonomo` (Importar pacote (herdado), "Criar como novo", conexões da conta nova), e **desligar o antigo e ligar o novo no mesmo minuto**, como na virada de 05/10. Com os dois ligados, cada e-mail vira 2 JSONs em `pendentes` e 2 cards no Teams.

## Depois da troca

- [ ] 1ª GMUD e 1º Backup reais passando pela conta nova (card no Teams, JSON em pendentes, ✅ do Retorno).
- [ ] O dono principal (criador) continua sendo o Vinícius. Para tirar de vez: Power Platform admin center (ou `Set-AdminFlowOwnerRole`) com um admin do ambiente, ou ficar com a importação da alternativa.
- [ ] Exportar os 3 fluxos de novo e trocar os zips em `fluxos/`.
- [ ] Atualizar CLAUDE.md, `docs/power-automate.md` e `docs/tecnico` (conta dona dos fluxos).
