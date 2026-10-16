"""DispatchBot: vigia a pasta sincronizada e abre chamados no ServiceNow para cada alerta.

Uso:
  DispatchBot.exe           início normal (menu Iniciar / logon)
  DispatchBot.exe --vigia   chamado pela tarefa agendada a cada 5 min: só sobe o robô se ele caiu
"""
import argparse
import ctypes
import os
import sys
import threading
import time
from datetime import datetime

# No executável sem console, stdout/stderr são None e o Playwright precisa de um fileno
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from pathlib import Path

import config
import pasta
import servicenow
from bandeja import TEXTOS as TEXTOS_STATUS, Bandeja, aviso_windows
from emergencia import arquivo_parar, escutar_atalho
from fila import HOST, Fila, Historico, PastaIndisponivel
from fila_sharepoint import FilaSharePoint
from log import configurar as configurar_log, log
from navegador import Interrompido, LoginNaoConcluido, Navegador, matar_navegadores_orfaos
from painel import Painel, registrar as registrar_painel, rotulo_alerta
from versao import VERSAO

NOME_MUTEX = "DispatchBot_Mutex"
NOME_EVENTO_SAIR = "DispatchBot_Sair"  # o instalador sinaliza este evento para encerrar o robô com segurança
FLAG_ENCERRADO = config.APP_DIR / "encerrado_pelo_usuario"  # impede o vigia de reabrir quem o usuário fechou
FLAG_LOGIN_INICIAL = config.APP_DIR / "login_inicial_ok"    # login no ServiceNow já feito na instalação


class AguardandoPasta(Exception):
    """Primeira execução: a pasta da fila ainda não chegou pelo OneDrive."""


# Tentados em ordem: o primeiro livre vira o atalho de emergência ("P de Parar")
ATALHOS_PADRAO = ["ctrl+alt+shift+p", "ctrl+alt+shift+x", "ctrl+alt+pause"]

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]


def instancia_unica() -> bool:
    kernel32.CreateMutexW(None, False, NOME_MUTEX)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


MENSAGENS_AMIGAVEIS = (  # erros técnicos (Playwright/rede) -> texto que o plantonista entende
    ("Target page, context or browser has been closed",
     "A janela do navegador do robô foi fechada. Ele abre de novo sozinho."),
    ("Browser has been closed", "A janela do navegador do robô foi fechada. Ele abre de novo sozinho."),
    ("Timeout", "O site demorou demais para responder. O robô tenta de novo."),
    ("net::ERR_INTERNET_DISCONNECTED", "Sem internet. O robô tenta de novo quando a conexão voltar."),
    ("net::ERR_NAME_NOT_RESOLVED", "Sem conexão com o site (rede ou VPN). O robô tenta de novo."),
    ("net::ERR_", "Falha de conexão com o site. O robô tenta de novo."),
    ("getaddrinfo failed", "Sem conexão com o SharePoint (rede ou VPN). O robô tenta de novo."),
)


def janela_fechada(e: Exception) -> bool:
    return "has been closed" in str(e)


def primeira_linha(e: Exception) -> str:
    texto = str(e).strip()
    for trecho, amigavel in MENSAGENS_AMIGAVEIS:
        if trecho in texto:
            return amigavel
    linha = (texto.splitlines()[0] if texto else type(e).__name__).lstrip(": ").strip()
    return linha[:500]


def minutos_desde(iso: str):
    try:
        recebido = datetime.fromisoformat(str(iso))
        if recebido.tzinfo is None:
            recebido = recebido.astimezone()
        return (datetime.now().astimezone() - recebido).total_seconds() / 60
    except (TypeError, ValueError):
        return None


def limpar_texto(valor):
    """Tira caracteres de controle invisíveis dos textos do alerta (ex.: CSV em UTF-16 lido como UTF-8 no
    Power Automate deixa um \\0 depois de cada letra). Mantém quebra de linha e tabulação."""
    if isinstance(valor, str):
        return "".join(c for c in valor if c in "\n\r\t" or ord(c) >= 32)
    if isinstance(valor, dict):
        return {k: limpar_texto(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [limpar_texto(v) for v in valor]
    return valor


def texto_ilegivel(valor) -> bool:
    """True se o texto tem cara de arquivo binário lido como texto: caractere de substituição (�)
    ou muitos símbolos fora do comum. Texto em português normal nunca chega perto disso."""
    if not isinstance(valor, str) or not valor.strip():
        return False
    if "�" in valor:
        return True
    estranhos = sum(1 for c in valor if not (c.isprintable() or c in "\n\r\t") or ord(c) > 0x2FFF)
    return estranhos / len(valor) > 0.2


def cliente_ignorado(alerta: dict, ignorar) -> str:
    """Devolve o cliente da lista "ignorar_clientes" que aparece no alerta (ou "").
    O cliente vem na linha do CSV (4ª coluna, no id depois do #) e no título (Política | Cliente | ...).
    Compara o campo inteiro, sem diferenciar maiúsculas; "appnburj03" também casa com "appnburj03.dominio"."""
    nomes = [str(n).strip().lower() for n in (ignorar or []) if str(n).strip()]
    if not nomes:
        return ""
    # Só a coluna Cliente: as outras (ex.: Master, [1]) se repetem em todo alerta e ignorariam tudo
    id_alerta = str(alerta.get("id") or "")
    colunas = id_alerta.split("#", 1)[1].split(",") if "#" in id_alerta else []
    trechos = str(alerta.get("titulo") or "").split("|")
    campos = [colunas[3] if len(colunas) > 3 else "", trechos[1] if len(trechos) > 1 else ""]
    campos = [c.strip().strip('"').lower() for c in campos]
    for nome in nomes:
        for campo in campos:
            if not campo:
                continue
            if campo == nome or campo.startswith(nome + ".") or nome.startswith(campo + "."):
                return nome
    return ""


def usa_sharepoint(cfg: dict) -> bool:
    """Fila direto no SharePoint (padrão) ou numa pasta sincronizada pelo OneDrive (site vazio no config)."""
    return bool((cfg.get("fila_sharepoint") or {}).get("site"))


class Robo:
    def __init__(self):
        self.cfg = config.carregar()
        self.historico = Historico(config.APP_DIR / "processados.jsonl")
        self.parar = threading.Event()
        self.acordar = threading.Event()
        self.bandeja = Bandeja(
            self.alternar_pausa, self.sair, self.acordar.set, self.abrir_pasta, self.parar_pelo_menu,
            pausado=self.cfg.get("pausado", False), versao=VERSAO,
            ao_envio=self.alternar_envio, envio_ligado=self.envio_ligado,
            ao_painel=lambda: self.painel.abrir(), ao_nome=self.pedir_nome,
        )
        self.painel = Painel(
            self.estado_painel, servicenow.url_absoluta(self.cfg, self.cfg["portal_path"]),
            self.alternar_pausa, self.acordar.set, lambda: self.parar_agora("menu"),
            ao_decidir=self.decidir_antigo, ao_conferencia=self.pedir_conferencia,
            ao_login=self.pedir_login_frente, ao_assumir=self.assumir_plantao,
            ao_historico=self.ler_historico_time,
        )
        self.decisoes = {}               # alertas antigos esperando decisão: id -> dados mostrados no painel
        self.conferindo = None           # chamado preenchido esperando o Submit (dados para o painel)
        self.pedido_conferencia = None   # "mostrar" | "pular", vindo do painel; a thread do robô consome
        self.login_aviso = None          # login esperando o plantonista: {"sistema", "onde", "primeira"}
        self.pedido_login_frente = False # botão "Mostrar janela de login"
        self.pendentes = 0               # alertas na fila no último ciclo (mostrado no painel)
        self.em_andamento = ""           # alerta sendo processado agora (mostrado no painel)
        # Parada de emergência: evento visto por todas as threads; a origem decide como liberar
        self.emergencia = threading.Event()
        self.origem_emergencia = None    # "menu" | "atalho" | "remota"
        self.motivo_emergencia = ""
        self.nav = None
        self.worker = None
        self.fila = None                 # Fila (pasta local) ou FilaSharePoint; criada pela thread do robô
        self.erro_infra = False          # erro de pasta/config: some sozinho quando resolver
        self.preparando = False          # primeira execução, esperando a pasta sincronizar
        self.preparando_desde = 0.0
        self.pasta_fixada = None         # pasta já marcada como "sempre no dispositivo" nesta execução
        self.primeiro_login = False
        self.proxima_sessao = 0.0        # quando tentar abrir a sessão do ServiceNow de novo (modo sempre aberto)
        self.login_pendente = False      # última espera de login terminou sem login
        self.ultima_falha = ("", 0.0)    # evita repetir a mesma notificação a cada ciclo
        self.ultimo_status = (None, 0.0)
        self.avisos_outros = {}          # host -> hora do último aviso de "outro robô ativo"
        # Plantão: outros robôs, assumir o plantão e histórico do time (SharePoint)
        self.outros = []                 # status das outras máquinas com sinal recente (lido pela thread do robô)
        self.pedido_assumir = None       # hosts a pedir que pausem (vem do menu/painel; a thread do robô grava)
        self.assumindo = {}              # host -> {"nome", "ts"}: pedido feito, esperando o outro robô pausar
        self.proximo_pedido = 0.0        # quando olhar de novo se alguém assumiu o plantão desta máquina
        self.detalhe_pausa = ""          # ex.: "Fulano assumiu o plantão às 14:02" (mostrado enquanto pausado)
        self.ativo_desde = "" if self.cfg.get("pausado") else datetime.now().astimezone().isoformat(timespec="seconds")
        self.eventos = []                # eventos do histórico ainda não gravados no SharePoint
        self.trava_eventos = threading.Lock()
        self.esperando_login_evento = False
        self.nome_pedido = False         # pergunta do nome já feita nesta execução
        # Alertas antigos esperando o plantonista decidir (pergunta numa thread, sem travar a fila)
        self.respostas_antigos = {}      # id do alerta -> "abrir" | "manual" | ("depois", hora)
        self.aguardando_antigos = {}     # nome do arquivo -> id do alerta (não reserva de novo enquanto espera)
        self.pergunta_aberta = False     # uma pergunta por vez na tela

    # --- menu da bandeja e painel ---

    def estado_painel(self) -> dict:
        return {"status": self.bandeja.status, "texto": TEXTOS_STATUS.get(self.bandeja.status, ""),
                "detalhe": self.bandeja.detalhe,
                "pausado": bool(self.cfg.get("pausado")), "pendentes": self.pendentes,
                "em_andamento": self.em_andamento, "decisoes": list(self.decisoes.values()),
                "conferindo": self.conferindo, "login": self.login_aviso,
                "nome": config.nome_plantonista(self.cfg), "outros": self.outros_ativos(),
                "assumindo": [d["nome"] for d in self.assumindo.values()]}

    def alternar_pausa(self):
        cfg = config.carregar()
        retomando = cfg.get("pausado", False)
        if retomando and self.parada_remota():
            aviso_windows(
                "Existe um arquivo PARAR na pasta DispatchBot-Fila (parada remota).\n\n"
                "Apague o arquivo para liberar os robôs."
            )
            return
        if retomando:
            outros = self.outros_ativos()
            if outros:
                quem = "\n".join(f"• {o['nome']} ({o['host']}), último sinal há {o['ha_min']} min" for o in outros)
                resposta = ctypes.windll.user32.MessageBoxW(
                    0,
                    f"Outro robô já está ativo:\n{quem}\n\n"
                    "Sim = assumir o plantão (o robô do colega pausa depois do alerta em andamento)\n"
                    "Não = ativar mesmo assim (os dois ficam ativos)\n"
                    "Cancelar = continuar pausado",
                    "DispatchBot - outro robô ativo", 0x3 | 0x30 | 0x40000 | 0x10000)  # Sim/Não/Cancelar | alerta
                if resposta == 6:    # Sim
                    self.pedido_assumir = [o["host"] for o in outros]
                elif resposta != 7:  # Cancelar (ou fechou a caixa)
                    return
        self.aplicar_pausa(not retomando)

    def aplicar_pausa(self, pausar: bool, detalhe: str = "", por: str = "usuário"):
        """Pausa ou retoma (menu, painel, ou um colega que assumiu o plantão)."""
        cfg = config.carregar()
        cfg["pausado"] = pausar
        if not pausar:
            # Retomar pelo menu é a única forma de sair de uma parada de emergência local
            cfg.pop("emergencia", None)
            self.limpar_emergencia()
            self.ativo_desde = datetime.now().astimezone().isoformat(timespec="seconds")
        config.salvar(cfg)
        self.detalhe_pausa = detalhe if pausar else ""
        self.bandeja.pausado = pausar
        self.bandeja.definir_status("pausado" if pausar else "ativo", self.detalhe_pausa)
        log.info("Robô %s pelo %s.", "pausado" if pausar else "retomado", por)
        if por == "usuário":
            self.evento("pausou" if pausar else "ativou",
                        "Pausou o robô" if pausar else "Ativou o robô")
        self.acordar.set()

    def outros_ativos(self) -> list:
        """Outras máquinas ativas (não pausadas) com sinal nos últimos 12 min, para o painel e as perguntas."""
        lista = []
        for d in self.outros:
            if d.get("pausado"):
                continue
            try:
                ha_min = max(0, int((time.time() - float(d.get("ts", 0))) / 60))
            except (TypeError, ValueError):
                continue
            host = str(d.get("host") or "?")
            lista.append({"host": host, "nome": d.get("nome") or d.get("usuario") or host, "ha_min": ha_min,
                          "desde": d.get("ativo_desde", ""), "assumindo": host in self.assumindo})
        return lista

    def assumir_plantao(self):
        """Botão "Assumir o plantão" do painel: pede aos outros robôs ativos que pausem e ativa este."""
        outros = [o for o in self.outros_ativos() if not o["assumindo"]]
        if not outros:
            return
        self.pedido_assumir = [o["host"] for o in outros]
        if config.carregar().get("pausado"):
            self.aplicar_pausa(False)
        self.acordar.set()

    # --- histórico do time ---

    def evento(self, tipo: str, texto: str, **extra):
        """Guarda um evento para o histórico do time (gravado no SharePoint pela thread do robô)."""
        try:
            registro = {"em": datetime.now().astimezone().isoformat(timespec="seconds"), "host": HOST,
                        "nome": config.nome_plantonista(self.cfg), "usuario": os.environ.get("USERNAME", ""),
                        "evento": tipo, "texto": texto, **{k: v for k, v in extra.items() if v not in (None, "")}}
            with self.trava_eventos:
                self.eventos.append(registro)
                del self.eventos[:-500]  # sem conexão por muito tempo: guarda só os últimos
        except Exception as e:
            log.warning("Não foi possível guardar o evento do histórico: %s", e)

    def gravar_eventos(self, fila):
        """Grava os eventos pendentes no arquivo do dia desta máquina. Falhou: tenta de novo no próximo ciclo."""
        with self.trava_eventos:
            eventos = list(self.eventos)
        if not eventos or fila is None:
            return
        por_dia = {}
        for e in eventos:
            por_dia.setdefault(str(e["em"])[:10], []).append(e)
        try:
            for dia, lista in sorted(por_dia.items()):
                fila.gravar_historico(dia, lista)
                with self.trava_eventos:
                    self.eventos = [e for e in self.eventos if e not in lista]
        except Exception as e:
            log.warning("Não foi possível gravar o histórico no SharePoint (tenta de novo): %s", primeira_linha(e))

    def ler_historico_time(self, dia: str) -> list:
        """Histórico do dia de todas as máquinas (tela Histórico do painel; roda numa thread à parte)."""
        if self.fila is None:
            raise PastaIndisponivel("A fila ainda não foi aberta. Tente de novo em alguns segundos.")
        eventos = self.fila.ler_historico(dia)
        with self.trava_eventos:
            eventos += [e for e in self.eventos if str(e.get("em", "")).startswith(dia)]  # ainda não gravados
        return sorted(eventos, key=lambda e: str(e.get("em", "")))

    # --- nome do plantonista ---

    def pedir_nome(self):
        """Menu "Meu nome no DispatchBot" e primeira execução: janelinha do painel com o nome atual."""
        self.painel.iniciar()
        self.painel.pedir_nome(config.nome_plantonista(config.carregar()), self.salvar_nome)

    def salvar_nome(self, nome: str):
        nome = " ".join(str(nome or "").split())[:40]
        cfg = config.carregar()
        anterior = config.nome_plantonista(cfg)
        cfg["nome_plantonista"] = nome or anterior
        config.salvar(cfg)
        self.cfg["nome_plantonista"] = cfg["nome_plantonista"]
        self.ultimo_status = (None, 0.0)  # regrava o status com o nome novo
        if cfg["nome_plantonista"] != anterior:
            log.info("Nome do plantonista: %s", cfg["nome_plantonista"])
            self.evento("nome", f"Trocou o nome de {anterior} para {cfg['nome_plantonista']}")
        self.acordar.set()

    # --- assumir o plantão (thread do robô) ---

    def tratar_plantao(self, fila):
        """Grava os pedidos de "assumir o plantão" e olha se algum colega assumiu o desta máquina."""
        pedido, self.pedido_assumir = self.pedido_assumir, None
        if pedido:
            meu_nome = config.nome_plantonista(self.cfg)
            nomes = {o["host"]: o["nome"] for o in self.outros_ativos()}
            for host in pedido:
                try:
                    fila.pedir_plantao(host, {"de_nome": meu_nome})
                except Exception as e:
                    log.warning("Não foi possível pedir o plantão a %s: %s", host, primeira_linha(e))
                    self.bandeja.notificar("DispatchBot", f"Não consegui avisar o robô de {nomes.get(host, host)}. "
                                                          "Peça para a pessoa pausar.")
                    continue
                nome = nomes.get(host, host)
                self.assumindo[host] = {"nome": nome, "ts": time.time()}
                log.info("Pedido de plantão enviado para %s (%s).", host, nome)
                self.evento("assumiu", f"Assumiu o plantão de {nome} ({host})", de_host=host, de_nome=nome)
                self.bandeja.notificar("DispatchBot", f"Pedido enviado: o robô de {nome} pausa depois do alerta em "
                                                      "andamento. O plantão já está com você.")
            fila.esquecer_status_outros()
        self.verificar_pedido_plantao(fila)

    def verificar_pedido_plantao(self, fila) -> bool:
        """A cada 30 s: alguém assumiu o plantão desta máquina? Pausa (só entre um alerta e outro).
        True = pausou agora."""
        if time.time() < self.proximo_pedido:
            return False
        self.proximo_pedido = time.time() + 30
        try:
            pedido = fila.ler_pedido_plantao()
            if not pedido:
                return False
            fila.apagar_pedido_plantao()
        except Exception as e:
            log.warning("Não foi possível ler o pedido de plantão: %s", primeira_linha(e))
            return False
        if self.cfg.get("pausado") or time.time() - float(pedido.get("ts") or 0) > 15 * 60:
            return False  # já pausado, ou pedido velho (esta máquina estava desligada): só apaga
        nome = str(pedido.get("de_nome") or pedido.get("de_host") or "outro plantonista")
        quando = datetime.now().strftime("%H:%M")
        detalhe = f"{nome} assumiu o plantão às {quando}"
        log.info("%s (%s). Robô pausado.", detalhe, pedido.get("de_host"))
        self.aplicar_pausa(True, detalhe, por=f"plantão assumido por {nome}")
        self.cfg["pausado"] = True
        self.evento("plantao_passado", f"Robô pausado: {nome} assumiu o plantão", para_host=pedido.get("de_host"),
                    para_nome=nome)
        self.bandeja.notificar("DispatchBot", f"{detalhe}. Seu robô ficou Pausado.")
        aviso_windows(f"{detalhe}.\n\nSeu DispatchBot ficou Pausado. Para voltar a abrir chamados, "
                      "desmarque Pausado no menu do ícone (ou Retomar no painel).")
        return True

    def conferir_assumidos(self):
        """Depois de assumir: avisa quando o robô do colega pausou (ou se ele não respondeu em 12 min)."""
        for host, dados in list(self.assumindo.items()):
            atual = next((d for d in self.outros if d.get("host") == host), None)
            if atual is None or atual.get("pausado"):
                del self.assumindo[host]
                if atual is not None:
                    log.info("Robô de %s (%s) pausou: o plantão está com esta máquina.", dados["nome"], host)
                    self.bandeja.notificar("DispatchBot", f"O robô de {dados['nome']} pausou. O plantão está com você.")
                else:
                    log.info("Robô de %s (%s) parou de dar sinal.", dados["nome"], host)
            elif time.time() - dados["ts"] > 12 * 60:
                del self.assumindo[host]
                log.warning("Robô de %s (%s) não respondeu ao pedido de plantão.", dados["nome"], host)
                self.bandeja.notificar("DispatchBot", f"O robô de {dados['nome']} não respondeu em 12 min "
                                                      "(máquina desligada ou sem rede?). Confira com a pessoa.")

    def envio_ligado(self, grupo: str) -> bool:
        """Envio automático do grupo ("GMUD" ou "BACKUP"). Padrão ligado; o plantonista desliga no menu."""
        try:
            return bool((config.carregar().get("envio_automatico") or {}).get(grupo, True))
        except Exception:
            return False  # sem config legível, na dúvida o plantonista confere

    def alternar_envio(self, grupo: str):
        """Menu da bandeja: o plantonista decide se o grupo é enviado sozinho ou se ele confere antes."""
        cfg = config.carregar()
        envio = cfg.setdefault("envio_automatico", {})
        ligar = not envio.get(grupo, True)
        if ligar:
            resposta = ctypes.windll.user32.MessageBoxW(
                0,
                f"A partir de agora o DispatchBot vai clicar em enviar sozinho nos chamados de {grupo}, "
                "sem você conferir (se faltar algum campo obrigatório, ele para e chama você).\n\n"
                "Para voltar a conferir, desmarque esta opção no menu do ícone.\n\nLigar?",
                "DispatchBot", 0x4 | 0x20 | 0x40000 | 0x10000)  # Sim/Não | pergunta | por cima | em foco
            if resposta != 6:  # 6 = Sim
                return
        envio[grupo] = ligar
        config.salvar(cfg)
        log.info("Envio automático de %s %s por %s.", grupo, "LIGADO" if ligar else "desligado", os.environ.get("USERNAME", "?"))
        self.evento("envio", f"{grupo}: envio automático {'ligado' if ligar else 'desligado (plantonista confere)'}")
        self.bandeja.icone.update_menu()
        self.bandeja.notificar("DispatchBot", f"{grupo}: o robô envia sozinho." if ligar
                               else f"{grupo}: o robô preenche e você confere antes de enviar.")

    # --- parada de emergência ---

    def parar_agora(self, origem: str, motivo: str = ""):
        """Interrompe na hora, de qualquer thread. O alerta em andamento volta para a fila
        (ou vai para /erro se o Enviar já foi clicado) e o robô fica parado até alguém liberar."""
        if self.emergencia.is_set():
            return
        self.origem_emergencia, self.motivo_emergencia = origem, motivo
        self.emergencia.set()
        log.warning("PARADA DE EMERGÊNCIA (%s)%s", origem, f": {motivo}" if motivo else "")
        self.evento("emergencia", {"remota": "Parada remota (arquivo PARAR)", "atalho": "Parada de emergência pelo atalho"}
                    .get(origem, "Parada de emergência pelo menu/painel") + (f": {motivo}" if motivo else ""))
        if origem != "remota":
            # Fica gravado: nem reinício nem o vigia fazem o robô voltar sozinho
            try:
                cfg = config.carregar()
                cfg["pausado"] = True
                cfg["emergencia"] = {"origem": origem, "em": datetime.now().astimezone().isoformat(timespec="seconds")}
                config.salvar(cfg)
                self.bandeja.pausado = True
            except Exception as e:
                log.error("Não foi possível gravar a parada no config: %s", e)
        self.bandeja.definir_status("erro", self.texto_emergencia())
        self.bandeja.notificar("DispatchBot - PARADO", self.texto_emergencia())
        # Derruba o navegador do robô: a ação em andamento falha na hora, em vez de esperar terminar
        matar_navegadores_orfaos()
        self.acordar.set()

    def parar_pelo_menu(self):
        """PARAR AGORA do menu do ícone fica colado no Pausado: pede confirmação para evitar clique sem querer.
        O atalho de teclado e o botão do painel continuam parando na hora."""
        resposta = ctypes.windll.user32.MessageBoxW(
            0, "Parar o DispatchBot agora (parada de emergência)?\n\n"
               "Para só pausar no fim do plantão, use Pausado no menu.",
            "DispatchBot", 0x4 | 0x30 | 0x40000 | 0x10000)  # Sim/Não | alerta | por cima | em foco
        if resposta == 6:  # 6 = Sim
            self.parar_agora("menu")

    def limpar_emergencia(self):
        if self.emergencia.is_set():
            log.warning("Parada de emergência encerrada (%s).", self.origem_emergencia)
        self.emergencia.clear()
        self.origem_emergencia, self.motivo_emergencia = None, ""

    def texto_emergencia(self) -> str:
        if self.origem_emergencia == "remota":
            return "Parada remota (arquivo PARAR)" + (f": {self.motivo_emergencia}" if self.motivo_emergencia else "")
        return "Parada de emergência. Desmarque Pausado para retomar"

    def parada_remota(self):
        """Arquivo PARAR na raiz da fila: (nome, motivo) ou None."""
        if self.fila is not None:
            return self.fila.arquivo_parar()
        if usa_sharepoint(self.cfg):
            return None  # a fila ainda não foi aberta pela thread do robô
        return arquivo_parar(self.cfg.get("pasta_sincronizada", ""))

    def vigiar_parada_remota(self):
        """Procura o arquivo PARAR na pasta da fila: a cada 3 s na pasta local, a cada 10 s no SharePoint."""
        while not self.parar.wait(self.cfg.get("fila_sharepoint", {}).get("intervalo_parar_seg", 10)
                                  if usa_sharepoint(self.cfg) else 3):
            achado = self.parada_remota()
            if achado and not self.emergencia.is_set():
                self.parar_agora("remota", achado[1])
            elif not achado and self.origem_emergencia == "remota":
                self.limpar_emergencia()
                log.info("Arquivo PARAR removido; robô liberado.")
                self.bandeja.definir_status("pausado" if self.pausado_agora() else "ativo")
                self.bandeja.notificar("DispatchBot", "Parada remota encerrada. Robô liberado.")
                self.acordar.set()

    def escutar_atalho(self):
        def registrou(texto):
            self.bandeja.definir_atalho(texto.upper())

        def falhou(mensagem):
            log.warning("%s", mensagem)
            self.bandeja.definir_atalho("")
            self.bandeja.notificar("DispatchBot", mensagem)
        escutar_atalho(self.cfg.get("atalho_emergencia", ATALHOS_PADRAO), registrou, lambda: self.parar_agora("atalho"), falhou)

    def deve_parar(self) -> bool:
        return self.parar.is_set() or self.emergencia.is_set() or self.pausado_agora()

    def esperar(self, segundos: float) -> bool:
        """Espera interrompível. True = deve parar (encerrando ou emergência)."""
        fim = time.monotonic() + segundos
        while time.monotonic() < fim:
            if self.parar.is_set() or self.emergencia.is_set():
                return True
            time.sleep(0.5)
        return False

    def abrir_pasta(self):
        if self.fila is not None:
            os.startfile(self.fila.endereco_web())  # no SharePoint abre o navegador padrão na pasta
        elif self.cfg.get("pasta_sincronizada"):
            os.startfile(self.cfg["pasta_sincronizada"])

    def sair(self, pelo_usuario: bool = True):
        if self.parar.is_set():
            return
        log.info("Encerrando (%s)...", "usuário" if pelo_usuario else "instalador")
        if pelo_usuario:
            FLAG_ENCERRADO.touch()
        self.parar.set()
        self.acordar.set()
        if self.worker:
            self.worker.join(timeout=20)
        self.bandeja.parar()

    # --- avisos ---

    def pausado_agora(self) -> bool:
        try:
            return bool(config.carregar().get("pausado"))
        except Exception:
            return False

    def aguardando_login(self, aguardando: bool, sistema: str = "ServiceNow"):
        if aguardando:
            onde = "no SharePoint (conta da empresa)" if sistema == "SharePoint" else f"no {sistema}"
            primeira = self.primeiro_login or not FLAG_LOGIN_INICIAL.exists()
            self.bandeja.definir_status("login")
            self.bandeja.notificar("DispatchBot", f"Faça login {onde} na janela aberta.")
            self.login_aviso = {"sistema": sistema, "onde": onde, "primeira": primeira}  # janela "Precisa de você"
            if not self.esperando_login_evento:
                self.esperando_login_evento = True
                self.evento("login", f"Esperando login {onde}")
            if self.painel.disponivel():
                return
            if primeira:
                aviso_windows(
                    f"Para terminar a instalação, faça login {onde} na janela do navegador aberta pelo robô.\n\n"
                    "É só desta vez: depois ele continua sozinho."
                )
            else:
                aviso_windows(
                    f"Faça login {onde} na janela do navegador aberta pelo robô.\n\n"
                    "O robô continua sozinho assim que o login terminar."
                )
        else:
            if self.esperando_login_evento:
                self.esperando_login_evento = False
                self.evento("login_ok", "Login feito")
            self.login_aviso = None
            self.pedido_login_frente = False
            self.bandeja.definir_status("ativo")

    def pedir_login_frente(self):
        """Botão "Mostrar janela de login" da janelinha: a thread do robô traz o navegador para a frente."""
        self.pedido_login_frente = True

    def tomar_pedido_login(self) -> bool:
        pedido, self.pedido_login_frente = self.pedido_login_frente, False
        return pedido

    def falha(self, mensagem: str, infra: bool = False):
        self.erro_infra = infra
        self.bandeja.definir_status("erro", mensagem[:80])
        anterior, quando = self.ultima_falha
        if mensagem != anterior or time.time() - quando > 600:
            self.bandeja.notificar("DispatchBot - falha", mensagem)
            self.ultima_falha = (mensagem, time.time())

    # --- threads ---

    def iniciar(self):
        def ao_iniciar():
            # Painel escondido desde o início: a janela "Precisa de você" depende dele
            threading.Thread(target=self.painel.iniciar, name="painel-inicio", daemon=True).start()
            self.iniciar_worker()
            threading.Thread(target=self.supervisionar, name="supervisor", daemon=True).start()
            threading.Thread(target=self.aguardar_evento_sair, name="evento-sair", daemon=True).start()
            threading.Thread(target=self.vigiar_parada_remota, name="parada-remota", daemon=True).start()
            threading.Thread(target=self.escutar_atalho, name="atalho", daemon=True).start()
        self.bandeja.rodar(ao_iniciar)

    def iniciar_worker(self):
        self.worker = threading.Thread(target=self.loop, name="robo", daemon=True)
        self.worker.start()

    def supervisionar(self):
        """Reinicia a thread do robô se ela morrer por um erro inesperado."""
        while not self.parar.wait(30):
            if self.worker and not self.worker.is_alive():
                log.error("A thread do robô parou inesperadamente; reiniciando.")
                self.nav = None  # objetos do Playwright pertencem à thread antiga
                self.iniciar_worker()

    def aguardar_evento_sair(self):
        evento = kernel32.CreateEventW(None, True, False, NOME_EVENTO_SAIR)
        kernel32.WaitForSingleObject(evento, 0xFFFFFFFF)
        self.sair(pelo_usuario=False)

    # --- loop principal (todo o Playwright roda nesta thread) ---

    def loop(self):
        log.info("Robô %s iniciado em %s.", VERSAO, HOST)
        self.evento("iniciou", f"Robô aberto (versão {VERSAO}, {'pausado' if self.cfg.get('pausado') else 'ativo'})")
        fila = None
        try:
            while not self.parar.is_set():
                self.acordar.clear()
                try:
                    self.cfg = config.carregar()  # permite editar o config sem reiniciar
                    if not self.cfg.get("nome_plantonista") and not self.nome_pedido and self.painel.disponivel():
                        self.nome_pedido = True  # primeira vez: pergunta como o time vai ver este plantonista
                        self.pedir_nome()
                    fila = self.abrir_fila(fila)
                    fila.verificar_pasta()
                    if self.preparando:
                        self.preparando = False
                        self.bandeja.definir_status("ativo")
                    self.tratar_plantao(fila)
                    self.gravar_eventos(fila)

                    if self.cfg.get("pausado") or self.emergencia.is_set():
                        self.fechar_navegador()
                        if self.emergencia.is_set() or self.cfg.get("emergencia"):
                            self.bandeja.definir_status("erro", self.texto_emergencia())
                        else:
                            self.bandeja.definir_status("pausado", self.detalhe_pausa)
                        self.gravar_status(fila)
                    else:
                        if self.erro_infra or self.bandeja.status == "pausado":
                            self.erro_infra = False
                            self.bandeja.definir_status("ativo")
                        if not FLAG_LOGIN_INICIAL.exists():
                            self.login_inicial()
                        elif self.precisa_abrir_sessao():
                            self.login_inicial(primeira=False)
                        self.ciclo(fila)
                        self.manter_sessao()
                        self.fechar_se_ocioso()
                except AguardandoPasta as e:
                    # Primeira instalação: o OneDrive ainda está trazendo a pasta. Não é erro.
                    if not self.preparando:
                        log.info("%s", e)
                        self.preparando, self.preparando_desde = True, time.time()
                        self.bandeja.notificar("DispatchBot", "Preparando: sincronizando a pasta da fila. Não precisa fazer nada.")
                    elif self.preparando_desde and time.time() - self.preparando_desde > 600:
                        self.preparando_desde = 0.0  # avisa uma vez só
                        log.warning("A pasta da fila não apareceu em 10 minutos.")
                        self.bandeja.notificar(
                            "DispatchBot", "A pasta da fila ainda não apareceu. Confira se o OneDrive está conectado "
                            "ou fale com o time de RPA.")
                    self.bandeja.definir_status("preparando", "sincronizando a pasta da fila")
                except (PastaIndisponivel, config.ConfigInvalido) as e:
                    log.error("%s", e)
                    self.falha(str(e), infra=True)
                except Exception as e:
                    if janela_fechada(e):
                        # Alguém fechou a janela do Edge do robô (ex.: durante o login): descarta e reabre no próximo ciclo
                        log.warning("A janela do navegador do robô foi fechada; será reaberta no próximo ciclo.")
                        try:
                            self.fechar_navegador()
                        except Exception:
                            pass
                        self.nav = None
                        # Não é erro: só avisa e segue (o ícone não fica vermelho)
                        self.bandeja.notificar("DispatchBot", "A janela do navegador do robô foi fechada. "
                                                              "Ele abre de novo sozinho.")
                    else:
                        log.exception("Erro no ciclo")
                        self.falha(primeira_linha(e), infra=True)
                self.acordar.wait(self.cfg.get("intervalo_polling_seg", 15))
        finally:
            self.fechar_navegador()
            try:
                if fila:
                    fila.gravar_status({"status": "encerrado", "pausado": True, "versao": VERSAO,
                                        "nome": config.nome_plantonista(self.cfg)})
            except Exception:
                pass
            self.evento("encerrou", "Robô fechado")
            self.gravar_eventos(fila)
            log.info("Robô encerrado.")

    def abrir_fila(self, fila):
        """Fila do SharePoint (padrão) ou da pasta sincronizada. Recria se o config mudou."""
        if usa_sharepoint(self.cfg):
            sp = self.cfg["fila_sharepoint"]
            onde = (sp["site"].rstrip("/"), "/" + sp["pasta"].strip("/"))
            if not isinstance(fila, FilaSharePoint) or (fila.site, fila.raiz) != onde:
                fila = FilaSharePoint(sp["site"], sp["pasta"], self.cookies_sharepoint)
        else:
            self.garantir_pasta()
            if not isinstance(fila, Fila) or str(fila.raiz) != self.cfg["pasta_sincronizada"]:
                fila = Fila(self.cfg["pasta_sincronizada"])
        self.fila = fila
        return fila

    def cookies_sharepoint(self, renovar: bool) -> list:
        """Sessão Microsoft do navegador do robô para a API do SharePoint (só na thread do robô).
        Pausado: não abre janela de login; isso fica para quando retomar."""
        return self.navegador().cookies_sharepoint(
            self.cfg["fila_sharepoint"]["site"], renovar, permitir_janela=not self.deve_parar())

    def garantir_pasta(self):
        """Acha a pasta da fila sozinho (primeira instalação, ou se ela mudou de lugar)
        e a mantém sempre no dispositivo."""
        atual = self.cfg.get("pasta_sincronizada") or ""
        if atual and Path(atual).is_dir():
            self.fixar_pasta(Path(atual))
            return
        achada = pasta.detectar()
        if achada:
            if str(achada) != atual:
                log.info("Pasta da fila encontrada: %s", achada)
                cfg = config.carregar()
                cfg["pasta_sincronizada"] = str(achada)
                config.salvar(cfg)
                self.cfg = cfg
            self.fixar_pasta(achada)
            return
        if not atual:
            raise AguardandoPasta("Aguardando o OneDrive sincronizar a pasta DispatchBot-Fila.")
        # Configurada, mas sumiu e não há outra: verificar_pasta() avisa que está inacessível

    def fixar_pasta(self, caminho: Path):
        if self.pasta_fixada != caminho:
            pasta.manter_no_dispositivo(caminho)
            self.pasta_fixada = caminho

    def navegador(self) -> Navegador:
        if self.nav is None:
            self.nav = Navegador(self.cfg, self.aguardando_login, self.deve_parar)
            self.nav.pedir_frente = self.tomar_pedido_login
        self.nav.cfg = self.cfg
        return self.nav

    def manter_sessao(self):
        """Navegador parado há N min: recarrega o portal. Evita que a sessão do ServiceNow expire por
        inatividade e, se ela caiu mesmo assim, pede o login na hora (e não quando o alerta chegar)."""
        minutos = self.cfg["navegador"].get("manter_sessao_min", 10)
        nav = self.nav
        if minutos <= 0 or not (nav and nav.aberto and not nav._invisivel):
            return
        if time.monotonic() - nav.ultimo_uso < minutos * 60:
            return
        try:
            nav.ir_para(nav.pagina(), servicenow.url_absoluta(self.cfg, self.cfg["portal_path"]))
            if self.login_pendente:
                self.login_pendente = False
                self.bandeja.definir_status("ativo")
        except LoginNaoConcluido as e:
            log.warning("Sessão do ServiceNow caiu e o login não foi feito: %s", e)
            self.login_pendente = True
            self.proxima_sessao = time.time() + 15 * 60
            self.bandeja.definir_status("login", "falta o login no ServiceNow")
            self.fechar_navegador()
        except Interrompido:
            pass

    def precisa_abrir_sessao(self) -> bool:
        """Navegador sempre aberto (fechar_apos_ocioso_min = 0): a sessão do ServiceNow não sobrevive ao
        fechamento do navegador. Então, ao iniciar o robô (ou se a janela for fechada), abre o ServiceNow na hora,
        para o login acontecer quando o plantonista liga o robô, e não quando o alerta chega de madrugada."""
        if self.cfg["navegador"].get("fechar_apos_ocioso_min", 0) > 0:
            return False
        if self.nav and self.nav.aberto and not self.nav._invisivel:
            return False
        return time.time() >= self.proxima_sessao  # depois de um login não feito, tenta de novo em 15 min

    def login_inicial(self, primeira: bool = True):
        """Abre o ServiceNow para o login: na instalação (primeira=True) e sempre que o navegador do robô
        estiver fechado com o modo 'sempre aberto'. Em vez de esperar o primeiro alerta."""
        self.primeiro_login = primeira
        try:
            nav = self.navegador()
            nav.ir_para(nav.pagina(), servicenow.url_absoluta(self.cfg, self.cfg["portal_path"]))
            self.login_pendente = False
            self.bandeja.definir_status("ativo")
            if primeira:
                FLAG_LOGIN_INICIAL.touch()
                log.info("Login inicial no ServiceNow concluído.")
                self.bandeja.notificar("DispatchBot", "Tudo pronto. O DispatchBot já pode abrir os chamados.")
            else:
                log.info("Sessão do ServiceNow aberta; o navegador do robô fica aberto.")
            # O navegador fica aberto com a sessão: o próximo alerta não pede login de novo
        except LoginNaoConcluido as e:
            log.warning("Login no ServiceNow não concluído: %s", e)
            self.login_pendente = True
            self.proxima_sessao = time.time() + 15 * 60
            self.bandeja.definir_status("login", "falta o primeiro login" if primeira else "falta o login no ServiceNow")
            self.fechar_navegador()
        except Interrompido:
            self.fechar_navegador()
        finally:
            self.primeiro_login = False

    def ciclo(self, fila: Fila):
        fila.recuperar_reservas(self.historico)
        fila.tratar_reservas_abandonadas(self.cfg.get("reserva_abandonada_min", 60))
        self.gravar_status(fila)
        arquivos = fila.listar_pendentes(self.cfg.get("idade_minima_arquivo_seg", 5))
        self.pendentes = len(arquivos)
        for arquivo in arquivos:
            if self.deve_parar() or self.verificar_pedido_plantao(fila):
                return  # um colega assumiu o plantão: pausa entre um alerta e outro
            if self.esperando_resposta(arquivo.name):
                continue  # alerta antigo: o plantonista ainda não decidiu; os outros seguem
            self.em_andamento = arquivo.name
            try:
                if not self.processar(fila, arquivo):
                    return  # ex.: aguardando login; os próximos alertas dependem dele também
            finally:
                self.em_andamento = ""
                self.pendentes = max(0, self.pendentes - 1)

    def gravar_status(self, fila: Fila):
        """Batimento em /status a cada 5 min (ou quando o status muda), leitura dos outros robôs
        e aviso de outro robô ativo."""
        chave = (self.bandeja.status, bool(self.cfg.get("pausado")))
        try:
            if chave != self.ultimo_status[0] or time.time() - self.ultimo_status[1] > 300:
                fila.gravar_status({
                    "status": self.bandeja.status, "pausado": chave[1], "versao": VERSAO,
                    "usuario": os.environ.get("USERNAME", ""), "nome": config.nome_plantonista(self.cfg),
                    "ativo_desde": "" if chave[1] else self.ativo_desde, "detalhe": self.bandeja.detalhe,
                })
                self.ultimo_status = (chave, time.time())
        except Exception as e:
            log.warning("Não foi possível gravar o status: %s", e)
        try:
            # Lido também com o robô pausado: o painel e a pergunta ao retomar mostram quem está ativo
            self.outros = fila.status_outros(12 * 60)
            self.conferir_assumidos()
            if chave[1]:
                return
            for o in self.outros_ativos():
                if o["assumindo"]:
                    continue
                if time.time() - self.avisos_outros.get(o["host"], 0) > 3600:
                    self.avisos_outros[o["host"]] = time.time()
                    log.warning("Outro robô ativo em %s (%s).", o["host"], o["nome"])
                    self.bandeja.notificar(
                        "DispatchBot", f"O robô de {o['nome']} ({o['host']}) também está ativo. Só o Dispatcher N1 "
                                       "da vez deve ficar ativo: use \"Assumir o plantão\" no painel."
                    )
        except Exception as e:
            log.warning("Não foi possível ler o status dos outros robôs: %s", e)

    def processar(self, fila: Fila, arquivo) -> bool:
        """Processa um alerta. Retorna False para interromper o ciclo."""
        reservado = fila.reservar(arquivo)
        if not reservado or not fila.confirmar_reserva(reservado, self.cfg.get("janela_confirmacao_seg", 10)):
            return True
        log.info("Processando %s", arquivo.name)

        try:
            alerta = fila.ler(reservado)
        except PastaIndisponivel:
            fila.devolver(reservado)  # falha de conexão, não do arquivo: tenta de novo no próximo ciclo
            raise
        except Exception as e:
            fila.mover_para_erro(reservado, None, f"JSON inválido: {e}")
            registrar_painel("erro", "", None, mensagem=f"JSON inválido: {e}", arquivo=arquivo.name)
            self.falha(f"{arquivo.name}: JSON inválido")
            return True

        alerta = limpar_texto(alerta)
        id_alerta = str(alerta.get("id") or "").strip()
        tipo = str(alerta.get("tipo") or "").strip().upper()
        grupo = str(alerta.get("grupo_executor", "")).upper().strip()
        if not tipo and grupo.endswith("BACKUP_CIRION"):
            # GMUD da fila de backup: SN_TI_CHG_EXECUTOR_Backup_Cirion -> GMUD_BACKUP
            # (não é o tipo BACKUP, que é o incidente de falha de backup)
            tipo = "GMUD_BACKUP"
            alerta["tipo"] = tipo
        elif not tipo and "CIRION_" in grupo:
            # GMUD: SN_TI_CHG_EXECUTOR_CIRION_INFRA -> INFRA
            tipo = grupo.rsplit("CIRION_", 1)[1].strip()
            alerta["tipo"] = tipo
        tipo_cfg = config.tipo_efetivo(self.cfg, tipo)
        forcar = alerta.get("forcar_reprocessamento") is True
        self.em_andamento = f"{tipo} {rotulo_alerta(alerta)}".strip()

        if not id_alerta:
            return self.erro_final(fila, reservado, alerta, "", tipo, "JSON sem o campo 'id'.")
        ilegiveis = [c for c in ("chg", "titulo", "descricao") if texto_ilegivel(alerta.get(c))]
        if ilegiveis:
            # Ex.: anexo do e-mail lido como texto pelo fluxo. O robô não abre chamado com dado que não entende.
            alerta["titulo_original"], alerta["titulo"] = alerta.get("titulo"), "(texto ilegível)"
            return self.erro_final(
                fila, reservado, alerta, id_alerta, tipo,
                f"O alerta chegou com texto ilegível em: {', '.join(ilegiveis)} (o fluxo não leu o anexo/e-mail "
                "direito). Abra o chamado à mão pelo e-mail original.",
            )
        if not tipo_cfg:
            tipos = ", ".join(config.nomes_tipos(self.cfg))
            return self.erro_final(fila, reservado, alerta, "", tipo, f"Tipo desconhecido: '{tipo}'. Tipos configurados: {tipos}.")

        # Idempotência: nunca abrir dois chamados para o mesmo alerta
        status = self.historico.status(id_alerta)
        if status == "ok" and not forcar:
            reg = self.historico.registro(id_alerta)
            log.warning("Alerta já processado (chamado %s). Arquivo repetido ignorado: %s", reg.get("numero"), arquivo.name)
            fila.concluir(reservado, alerta, reg.get("numero", ""), reg.get("url", ""), gravar_retorno=False)
            return True
        if status == "enviando" and not forcar:
            return self.erro_final(
                fila, reservado, alerta, "", tipo,
                "Uma tentativa anterior foi interrompida depois do clique em Enviar. Confira no ServiceNow se o "
                "chamado existe. Para abrir mesmo assim, adicione \"forcar_reprocessamento\": true no JSON e "
                "mova-o para /pendentes.",
            )

        # Clientes que não abrem chamado (ex.: media servers do NetBackup): conclui sem chamado e sem Teams
        ignorado = cliente_ignorado(alerta, tipo_cfg.get("ignorar_clientes"))
        if ignorado and not forcar:
            self.historico.marcar(id_alerta, "ok", tipo=tipo, numero="", ignorado=ignorado)
            log.info("Alerta %s ignorado: cliente %s está em ignorar_clientes. Nada aberto.", arquivo.name, ignorado)
            self.evento("ignorado", f"{tipo} {rotulo_alerta(alerta)}: cliente {ignorado} não abre chamado", tipo=tipo)
            fila.concluir(reservado, alerta, "", "", gravar_retorno=False)
            return True

        # Alerta antigo (ex.: robô ficou pausado): pode já ter sido aberto à mão. Pergunta antes.
        idade = minutos_desde(alerta.get("recebido_em"))
        limite_antigo = self.cfg.get("perguntar_alerta_antigo_min", 120)
        if limite_antigo and idade is not None and idade > limite_antigo and not forcar:
            resposta = self.respostas_antigos.get(id_alerta)
            if resposta == "manual":
                self.respostas_antigos.pop(id_alerta, None)
                self.aguardando_antigos.pop(arquivo.name, None)
                self.historico.marcar(id_alerta, "ok", tipo=tipo, numero="", manual=True)
                log.info("Alerta antigo %s: o plantonista informou que já foi aberto à mão. Nada aberto.", arquivo.name)
                self.evento("manual", f"{tipo} {rotulo_alerta(alerta)}: alerta antigo marcado como já aberto à mão",
                            tipo=tipo)
                fila.concluir(reservado, alerta, "", "", gravar_retorno=False)
                return True
            if resposta != "abrir":
                fila.devolver(reservado)
                self.aguardando_antigos[arquivo.name] = id_alerta
                self.perguntar_alerta_antigo(id_alerta, tipo, alerta, idade)
                return True
            # "abrir" fica guardado: se o alerta voltar para a fila (ex.: login), não pergunta de novo
            self.aguardando_antigos.pop(arquivo.name, None)
            log.info("Alerta antigo %s: o plantonista pediu para abrir.", arquivo.name)

        # Sem login há muito tempo: não segura o alerta, avisa o Teams para abrir manualmente
        limite_login = self.cfg.get("max_espera_login_min", 60)
        if self.login_pendente and idade is not None and idade > limite_login:
            return self.erro_final(
                fila, reservado, alerta, id_alerta, tipo,
                f"O login no ServiceNow não foi feito em {limite_login} min. Abra o chamado manualmente.",
            )

        tentativas = max(1, int(self.cfg.get("tentativas_por_alerta", 3)))
        for tentativa in range(1, tentativas + 1):
            if self.emergencia.is_set():
                return self.interromper(fila, reservado, alerta, arquivo)
            try:
                numero, url = self.abrir_chamado(tipo_cfg, alerta, id_alerta, tipo, forcar)
                # O time trata o chamado na interface clássica: o link abre o RITM/INC direto, sem buscar
                url = servicenow.link_tratamento(self.cfg, numero) or url
            except Interrompido:
                return self.interromper(fila, reservado, alerta, arquivo)
            except LoginNaoConcluido as e:
                self.login_pendente = True
                self.bandeja.definir_status("login", "alertas aguardando")
                log.warning("%s Alerta devolvido para /pendentes: %s", e, arquivo.name)
                fila.devolver(reservado)
                return False
            except (servicenow.TipoNaoMapeado, servicenow.FormularioRecusado, servicenow.DadoFaltando) as e:
                # Não adianta repetir; nada foi aberto no ServiceNow
                self.screenshot(tipo, arquivo)
                return self.erro_final(fila, reservado, alerta, id_alerta, tipo, primeira_linha(e))
            except servicenow.Pulado as e:
                # Status "enviando" é mantido: bloqueia reenvio automático (o plantonista abre à mão)
                return self.erro_final(fila, reservado, alerta, "", tipo, primeira_linha(e))
            except servicenow.NaoConfirmado as e:
                # Status "enviando" é mantido: o plantonista pode ter clicado no último segundo
                self.screenshot(tipo, arquivo)
                return self.erro_final(
                    fila, reservado, alerta, "", tipo,
                    f"{primeira_linha(e)} Confira no ServiceNow; se o chamado não existir, abra manualmente.",
                )
            except Exception as e:
                if self.emergencia.is_set():
                    # O erro foi causado pela própria parada (navegador derrubado)
                    return self.interromper(fila, reservado, alerta, arquivo)
                log.exception("Tentativa %s/%s falhou: %s", tentativa, tentativas, arquivo.name)
                self.screenshot(tipo, arquivo)
                mensagem = primeira_linha(e)
                if self.historico.status(id_alerta) == "enviando":
                    # Status "enviando" é mantido: bloqueia reenvio automático
                    return self.erro_final(
                        fila, reservado, alerta, "", tipo,
                        "Falha depois do clique em Enviar; o chamado pode ter sido aberto. Confira no ServiceNow. " + mensagem,
                    )
                if tentativa == tentativas:
                    return self.erro_final(fila, reservado, alerta, id_alerta, tipo, f"{mensagem} ({tentativas} tentativas)")
                # Falha passageira (rede, navegador travado, página lenta): recomeça com navegador novo
                self.fechar_navegador()
                if self.esperar(self.cfg.get("espera_entre_tentativas_seg", 20) * tentativa):
                    if self.emergencia.is_set():
                        return self.interromper(fila, reservado, alerta, arquivo)
                    return False  # encerrando: o arquivo volta para /pendentes na próxima execução
                continue

            self.login_pendente = False
            self.historico.marcar(id_alerta, "ok", tipo=tipo, numero=numero, url=url)
            registrar_painel("ok", tipo, alerta, numero=numero, url=url)
            log.info("Chamado %s aberto (%s, %s).", numero, tipo, arquivo.name)
            self.evento("chamado", f"Chamado {numero} aberto: {tipo} {rotulo_alerta(alerta)}", tipo=tipo,
                        numero=numero, url=url)
            self.bandeja.definir_status("ativo")
            try:
                fila.concluir(reservado, alerta, numero, url)
            except Exception:
                # O chamado existe e está no histórico: o próximo ciclo refaz esta parte
                log.exception("Chamado %s aberto, mas falhou ao gravar o resultado. Será refeito no próximo ciclo.", numero)
            return True
        return True

    def esperando_resposta(self, nome_arquivo: str) -> bool:
        """True se o arquivo é um alerta antigo ainda sem decisão (não reserva de novo a cada ciclo).
        "Decidir depois" volta a perguntar depois de 30 min."""
        id_alerta = self.aguardando_antigos.get(nome_arquivo)
        if id_alerta is None:
            return False
        resposta = self.respostas_antigos.get(id_alerta)
        if resposta in ("abrir", "manual"):
            return False
        if isinstance(resposta, tuple):
            if time.time() - resposta[1] > 30 * 60:
                self.respostas_antigos.pop(id_alerta, None)
                return False  # pergunta de novo
            return True
        if id_alerta in self.decisoes or self.pergunta_aberta:
            return True  # a pergunta está na tela
        return False  # a pergunta sumiu sem resposta (ex.: robô reiniciado): pergunta de novo

    def perguntar_alerta_antigo(self, id_alerta: str, tipo: str, alerta: dict, idade_min: float):
        """Mostra a decisão na janela "Precisa de você" (a fila não trava). Sem o painel, usa uma caixa do Windows."""
        if id_alerta in self.decisoes or isinstance(self.respostas_antigos.get(id_alerta), tuple):
            return
        horas = idade_min / 60
        quando = f"há {horas:.0f} h" if horas >= 1.5 else f"há {idade_min:.0f} min"
        log.info("Alerta antigo (%s): perguntando ao plantonista se ainda deve abrir.", quando)
        self.bandeja.notificar("DispatchBot", f"Alerta antigo de {tipo}: o robô precisa da sua decisão.")
        if self.painel.disponivel():
            recebido = ""
            try:
                recebido = datetime.fromisoformat(str(alerta.get("recebido_em"))).astimezone().strftime("%d/%m %H:%M")
            except (TypeError, ValueError):
                pass
            descricao = " ".join(str(alerta.get("descricao") or "").split())
            self.decisoes[id_alerta] = {
                "id": id_alerta, "tipo": tipo, "rotulo": rotulo_alerta(alerta), "idade": quando,
                "recebido": recebido, "descricao": descricao[:220] + ("…" if len(descricao) > 220 else ""),
            }
            return
        self.perguntar_por_caixa(id_alerta, tipo, alerta, quando)

    def decidir_antigo(self, id_alerta: str, resposta: str):
        """Resposta vinda do painel: "abrir" | "manual" (já aberto à mão) | "depois"."""
        self.decisoes.pop(id_alerta, None)
        self.respostas_antigos[id_alerta] = ("depois", time.time()) if resposta == "depois" else resposta
        log.info("Alerta antigo %s: resposta do plantonista = %s.", id_alerta,
                 {"abrir": "abrir", "manual": "já aberto à mão"}.get(resposta, "decidir depois"))
        self.acordar.set()  # trata a resposta já no próximo ciclo

    def perguntar_por_caixa(self, id_alerta: str, tipo: str, alerta: dict, quando: str):
        """Reserva: caixa do Windows (Sim/Não/Cancelar), uma por vez, se o painel não estiver disponível."""
        if self.pergunta_aberta:
            return
        self.pergunta_aberta = True
        texto = (
            f"Alerta antigo na fila: {tipo} {rotulo_alerta(alerta)}\n"
            f"Recebido {quando}. O robô pode ter ficado pausado ou desligado.\n\n"
            "Abrir o chamado agora?\n\n"
            "Sim = abrir agora\n"
            "Não = já foi aberto à mão (o robô não abre)\n"
            "Cancelar = decidir depois (pergunta de novo em 30 min)"
        )

        def perguntar():
            try:
                resposta = ctypes.windll.user32.MessageBoxW(
                    0, texto, "DispatchBot - alerta antigo", 0x3 | 0x20 | 0x40000 | 0x10000)  # Sim/Não/Cancelar
                self.decidir_antigo(id_alerta, {6: "abrir", 7: "manual"}.get(resposta, "depois"))
            except Exception:
                # Sem a janela não há decisão: segura o alerta (pergunta de novo em 30 min) em vez de repetir a cada ciclo
                log.exception("Não foi possível mostrar a pergunta do alerta antigo %s.", id_alerta)
                self.respostas_antigos[id_alerta] = ("depois", time.time())
                self.bandeja.notificar("DispatchBot", "Não consegui mostrar a pergunta de um alerta antigo. Veja o log.")
            finally:
                self.pergunta_aberta = False
                self.acordar.set()
        threading.Thread(target=perguntar, daemon=True, name="pergunta-alerta-antigo").start()

    def abrir_chamado(self, tipo_cfg, alerta, id_alerta, tipo, forcar):
        self.navegador()
        if self.cfg["checar_duplicado_servicenow"].get("ativo") and not forcar:
            existente = servicenow.buscar_existente(self.nav, self.cfg, alerta)
            if existente:
                log.warning("Chamado %s já existe no ServiceNow para o alerta %s.", existente, id_alerta)
                return existente, ""

        # Quem decide se confere é o plantonista (menu do ícone), não o config do tipo
        grupo = "GMUD" if tipo == "_GMUD" or (self.cfg["tipos"].get(tipo) or {}).get("herda") == "_GMUD" else tipo
        tipo_cfg = dict(tipo_cfg, confirmar_antes_de_enviar=not self.envio_ligado(grupo))
        self.pedido_conferencia = None
        return servicenow.abrir_chamado(
            self.nav, self.cfg, tipo_cfg, alerta,
            antes_de_enviar=lambda: self.historico.marcar(id_alerta, "enviando", tipo=tipo),
            ao_pedir_confirmacao=lambda pedindo, motivo="": self.pedindo_confirmacao(pedindo, tipo, alerta, tipo_cfg, motivo),
            verificar_pedido=self.tomar_pedido_conferencia,
        )

    def pedir_conferencia(self, acao: str):
        """Botão da janela "Precisa de você": "mostrar" (traz o formulário) ou "pular"."""
        self.pedido_conferencia = acao

    def tomar_pedido_conferencia(self):
        pedido, self.pedido_conferencia = self.pedido_conferencia, None
        return pedido

    def pedindo_confirmacao(self, pedindo: bool, tipo: str, alerta: dict = None, tipo_cfg: dict = None, motivo: str = ""):
        if pedindo:
            self.bandeja.definir_status("confirmar", f"confira o chamado de {tipo}")
            # motivo = o envio era automático e o robô parou (ex.: campo obrigatório vazio)
            self.bandeja.notificar("DispatchBot", (f"Não enviei sozinho: {motivo}. " if motivo else "")
                                   + f"Confira o chamado de {tipo} e clique em Submit.")
            alerta = alerta or {}
            detalhe = [f"Não enviei sozinho: {motivo}"] if motivo else []
            if alerta.get("data_execucao"):
                detalhe.append(f"Execução {alerta['data_execucao']}")
            try:
                detalhe.append("recebido " + datetime.fromisoformat(str(alerta.get("recebido_em"))).astimezone().strftime("%d/%m %H:%M"))
            except (TypeError, ValueError):
                pass
            minutos = (tipo_cfg or {}).get("confirmacao_timeout_min", 30)
            self.conferindo = {
                "tipo": tipo, "rotulo": rotulo_alerta(alerta), "detalhe": " · ".join(detalhe),
                "campos": servicenow.resumo_campos(tipo_cfg or {}, alerta), "limite": time.time() + minutos * 60,
            }
            if not self.painel.disponivel():
                aviso_windows(
                    f"O DispatchBot preencheu um chamado de {tipo}.\n\n"
                    "Confira os campos na janela do navegador e clique em Submit no formulário. "
                    "Se algo estiver errado, corrija antes de enviar."
                )
        else:
            self.conferindo = None
            self.pedido_conferencia = None
            self.bandeja.definir_status("ativo")

    def interromper(self, fila, reservado, alerta, arquivo) -> bool:
        """Parada no meio de um alerta (emergência ou pausa). Nunca reenvia: se o Enviar já foi
        clicado, vai para /erro para conferência; senão volta intacto para /pendentes."""
        self.fechar_navegador()
        if self.historico.status(str(alerta.get("id") or "")) == "enviando":
            mensagem = ("Parada de emergência depois do clique em Enviar; o chamado pode ter sido aberto. "
                        "Confira no ServiceNow.")
            log.warning("%s -> /erro: %s", arquivo.name, mensagem)
            fila.mover_para_erro(reservado, alerta, mensagem)
        else:
            log.warning("Interrompido antes do envio; alerta devolvido para /pendentes: %s", arquivo.name)
            fila.devolver(reservado)
        return False

    def erro_final(self, fila, reservado, alerta, id_alerta, tipo, mensagem) -> bool:
        """Move para /erro, avisa o Teams (via /retorno) e a bandeja. id_alerta vazio = não mexe no histórico."""
        if id_alerta:
            self.historico.marcar(id_alerta, "erro", tipo=tipo, mensagem=mensagem)
        log.error("%s -> /erro: %s", reservado.name, mensagem)
        fila.mover_para_erro(reservado, alerta, mensagem)
        registrar_painel("erro", tipo, alerta, mensagem=mensagem, arquivo=reservado.name)
        self.evento("erro", f"Não abriu {tipo} {rotulo_alerta(alerta) or reservado.name}: {mensagem}", tipo=tipo)
        self.falha(mensagem)
        return True

    def screenshot(self, tipo, arquivo):
        if self.nav:
            self.nav.screenshot(f"erro_{tipo}_{arquivo.stem}")

    def fechar_se_ocioso(self):
        minutos = self.cfg["navegador"].get("fechar_apos_ocioso_min", 10)
        if minutos <= 0:
            return  # 0 = nunca fecha: mantém a sessão do ServiceNow viva (útil se o SSO não entra sozinho)
        if self.nav and self.nav.aberto and time.monotonic() - self.nav.ultimo_uso > minutos * 60:
            log.info("Navegador ocioso; fechando (o perfil mantém a sessão).")
            self.nav.fechar()

    def fechar_navegador(self):
        if self.nav and self.nav.aberto:
            self.nav.fechar()


def mensagem(texto: str):
    ctypes.windll.user32.MessageBoxW(0, texto, "DispatchBot", 0x40)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vigia", action="store_true", help="início pela tarefa agendada")
    args, _ = parser.parse_known_args()

    if not instancia_unica():
        if not args.vigia:
            mensagem("O DispatchBot já está em execução (veja o ícone na bandeja).")
        return
    if args.vigia and FLAG_ENCERRADO.exists():
        return  # o usuário fechou pelo menu; o vigia respeita
    FLAG_ENCERRADO.unlink(missing_ok=True)

    configurar_log()
    threading.excepthook = lambda a: log.error(
        "Exceção não tratada na thread %s", a.thread.name if a.thread else "?",
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback),
    )
    try:
        cfg = config.carregar()
    except config.ConfigInvalido as e:
        log.error("%s", e)
        if not args.vigia:
            mensagem(f"{e}\n\nCorrija o arquivo {config.CONFIG_PATH} e abra o robô de novo.")
        return

    if not usa_sharepoint(cfg) and not cfg.get("pasta_sincronizada"):
        # Primeira execução: acha a pasta sozinho; se ainda não estiver sincronizada, pede ao OneDrive.
        # Só pergunta ao usuário se não houver como fazer isso sozinho.
        achada = pasta.detectar()
        if achada:
            cfg["pasta_sincronizada"] = str(achada)
            config.salvar(cfg)
            log.info("Pasta da fila encontrada: %s", achada)
        elif cfg.get("link_sincronizar"):
            try:
                pasta.pedir_sincronizacao(cfg["link_sincronizar"])
            except Exception as e:
                log.warning("Não foi possível abrir o link de sincronização: %s", e)
        elif args.vigia or not config.pedir_pasta_sincronizada(cfg):
            log.info("Pasta sincronizada não informada.")
            return

    log.info("Iniciando DispatchBot %s%s.", VERSAO, " (reaberto pelo vigia)" if args.vigia else "")
    Robo().iniciar()
    # Saída garantida: a thread do painel (Tk) pode travar o fim do Python (processo ficava aberto depois de
    # "Robô encerrado" e prendia o exe)
    import logging
    logging.shutdown()
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Erro fatal")
        raise
