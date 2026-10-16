# Fluxos do Power Automate (backup)

Cópias exportadas dos fluxos de nuvem do DispatchBot. Servem de backup e para recriar os fluxos em outro ambiente ou conta.

| Arquivo | Fluxo | Como foi exportado |
|---|---|---|
| `GMUD - Todos.zip` | GMUD \| Todos (e-mail da CHG → Teams + JSON em `pendentes`) | Export package (.zip), "Create as new" |
| `BackupBot.zip` | BackupBot (e-mail de falha de backup → Teams + JSON em `pendentes`) | Export package (.zip), "Create as new" |
| `DispatchBot - Retorno.zip` | DispatchBot \| Retorno (arquivo em `retorno` → mensagem com o número no chat do time) | Export package (.zip), "Create as new" |

## Como importar

- Power Automate → **Meus fluxos** → **Importar** → **Importar pacote (herdado)** → escolher o `.zip` → em cada conexão, **Selecionar durante a importação** e escolher as suas (Outlook, SharePoint, Teams, Conversão de Conteúdo) → **Importar** → abrir o fluxo e **Ligar**.
- Importe os três: os dois de entrada (GMUD e Backup) gravam na fila, e o Retorno avisa no Teams.

## Cuidados

- O gatilho do "GMUD | Todos" lê a caixa de **quem é dono da conexão do Outlook**: os e-mails das GMUDs precisam chegar nessa caixa.
- Os chats do Teams e a pasta do SharePoint (`MANAGEDSERVICE` → `YDUQS/N1/DispatchBot-Fila`) ficam fixos dentro dos fluxos: em outra empresa, é preciso editar.
- Ao mudar um fluxo, exporte de novo e substitua o arquivo aqui (com a data na descrição do pacote).
