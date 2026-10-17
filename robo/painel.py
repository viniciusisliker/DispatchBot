"""Painel do plantonista (tema escuro, CustomTkinter) e a janela "Precisa de você".

Painel: abre pelo clique no ícone da bandeja (ou "Abrir painel" no menu). Mostra:
- cabeçalho com o avatar, o status do robô e o botão Pausar/Retomar;
- contadores do dia (na fila, abertos, erros, precisa de você);
- "Precisa de você": login, chamado para conferir, alerta antigo;
- "Chamados abertos hoje" (Tratar / Copiar) e "Erros de hoje" (abrir à mão, com o motivo);
- rodapé: Verificar agora, Logs e PARAR AGORA.
Fechar a janela só esconde o painel; o robô continua rodando.

"Precisa de você": janelinha no canto da tela que aparece sozinha quando o robô precisa de uma decisão.

O Tk roda numa thread própria, iniciada junto com o robô (janela escondida). Toda chamada ao Tk
acontece nela; as outras threads só mudam dados que o painel lê a cada segundo (obter_estado) ou
levantam um aviso (Event).
"""
import json
import os
import threading
import time
import webbrowser
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont, ImageTk

import config
from fila import agora_iso
from bandeja import CORES, imagem
from log import LOG_DIR, log

ARQUIVO = config.APP_DIR / "painel.jsonl"
ATUALIZAR_MS = 3000
MAX_LINHAS = 2000  # só as últimas linhas do arquivo interessam (o painel mostra o dia)

# Tema escuro
FUNDO, CARTAO, CARTAO2, BORDA = "#0F1215", "#171B20", "#1F252B", "#2A3139"
TEXTO, APAGADO = "#E7EBEE", "#8C97A1"
TEAL, TEAL_HOVER = "#14B8A6", "#0F9488"
VERDE, AMARELO, VERMELHO, VERMELHO_HOVER = "#22C55E", "#F5A524", "#EF4444", "#B91C1C"
STATUS_COR = {"ativo": VERDE, "login": AMARELO, "confirmar": AMARELO, "erro": VERMELHO,
              "pausado": "#9AA4AD", "preparando": "#60A5FA"}
FONTE = "Segoe UI"

# Ícones do próprio Windows (fonte Segoe Fluent Icons; no Windows 10, Segoe MDL2 Assets)
ICONES = {"pausar": 0xE769, "retomar": 0xE768, "atualizar": 0xE72C, "logs": 0xE8A5, "copiar": 0xE8C8,
          "abrir": 0xE8A7, "parar": 0xE71A, "fila": 0xE823, "ok": 0xE930, "erro": 0xE7BA, "voce": 0xE77B,
          "login": 0xE72E, "ver": 0xE890, "pular": 0xE72A, "check": 0xE73E, "mais": 0xE710, "info": 0xE946,
          "depois": 0xE823, "time": 0xE716, "historico": 0xE81C, "nome": 0xE77B}

# Tela Histórico: como cada evento aparece (rótulo e cor)
EVENTOS = {"chamado": ("CHAMADO", "#22C55E"), "erro": ("ERRO", "#EF4444"), "assumiu": ("ASSUMIU", "#F5A524"),
           "plantao_passado": ("PASSOU", "#F5A524"), "ativou": ("ATIVOU", "#14B8A6"), "pausou": ("PAUSOU", "#9AA4AD"),
           "emergencia": ("PARADA", "#EF4444"), "manual": ("À MÃO", "#9AA4AD"), "ignorado": ("IGNORADO", "#9AA4AD"),
           "login": ("LOGIN", "#F5A524"), "login_ok": ("LOGIN OK", "#9AA4AD"), "iniciou": ("ABRIU", "#9AA4AD"),
           "encerrou": ("FECHOU", "#9AA4AD"), "envio": ("ENVIO", "#9AA4AD"), "nome": ("NOME", "#9AA4AD"),
           "atualizacao": ("ATUALIZANDO", "#14B8A6"), "atualizou": ("ATUALIZOU", "#14B8A6")}


def fonte_icones(tam: int):
    for arquivo in ("SegoeIcons.ttf", "segmdl2.ttf"):
        try:
            return ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", arquivo), tam)
        except OSError:
            continue
    return None


def rotulo_alerta(alerta) -> str:
    """O que o plantonista reconhece: a CHG na GMUD, o título no Backup."""
    if not isinstance(alerta, dict):
        return ""
    for campo in ("chg", "titulo", "id"):
        valor = str(alerta.get(campo) or "").strip()
        if valor:
            return valor
    return ""


def registrar(status: str, tipo: str, alerta, numero: str = "", url: str = "", mensagem: str = "", arquivo: str = ""):
    """Grava um resultado para o painel. status: ok | erro. Nunca derruba o robô."""
    registro = {"em": agora_iso(), "status": status, "tipo": tipo or "", "alerta": rotulo_alerta(alerta) or arquivo,
                "numero": numero, "url": url, "mensagem": mensagem}
    try:
        with open(ARQUIVO, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("Não foi possível gravar no painel: %s", e)


def ler_hoje() -> list:
    """Registros de hoje, mais recentes primeiro."""
    if not ARQUIVO.exists():
        return []
    hoje = datetime.now().date().isoformat()
    try:
        linhas = ARQUIVO.read_text(encoding="utf-8").splitlines()[-MAX_LINHAS:]
    except OSError:
        return []
    registros = []
    for linha in linhas:
        try:
            r = json.loads(linha)
        except ValueError:
            continue
        if str(r.get("em", "")).startswith(hoje):
            registros.append(r)
    return list(reversed(registros))


def hora(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def resto(segundos: float) -> str:
    """Tempo que falta, ex.: '28 min' ou '45 s'."""
    segundos = max(0, int(segundos))
    return f"{segundos // 60} min" if segundos >= 60 else f"{segundos} s"


def curto(texto: str, limite: int) -> str:
    texto = " ".join(str(texto or "").split())
    return texto if len(texto) <= limite else texto[:limite - 1] + "…"


class Painel:
    def __init__(self, obter_estado, portal_url: str, ao_pausar, ao_verificar, ao_emergencia,
                 ao_decidir=None, ao_conferencia=None, ao_login=None, ao_assumir=None, ao_historico=None):
        """obter_estado() devolve {"status", "texto", "pausado", "pendentes", "em_andamento",
        "decisoes": [{"id", "tipo", "rotulo", "idade", "recebido", "descricao"}],
        "conferindo": {"tipo", "rotulo", "detalhe", "campos": [(campo, valor)], "limite"} ou None,
        "login": {"sistema", "onde", "primeira"} ou None}.
        ao_decidir(id, "abrir" | "manual" | "depois"); ao_conferencia("mostrar" | "pular"); ao_login()."""
        self.obter_estado = obter_estado
        self.portal_url = portal_url
        self.ao_pausar = ao_pausar
        self.ao_verificar = ao_verificar
        self.ao_emergencia = ao_emergencia
        self.ao_decidir = ao_decidir or (lambda *a: None)
        self.ao_conferencia = ao_conferencia or (lambda *a: None)
        self.ao_login = ao_login or (lambda: None)
        self.ao_assumir = ao_assumir or (lambda: None)
        self.ao_historico = ao_historico            # ao_historico(dia "AAAA-MM-DD") -> lista de eventos do time
        self._pedido_nome = None                    # (nome atual, ao_salvar): janelinha do nome, aberta pela thread do Tk
        self._historico_resultado = None            # resposta da leitura do histórico (thread à parte)
        self._pedido_abrir = threading.Event()
        self._pronto = threading.Event()
        self._thread = None
        self._trava = threading.Lock()

    # --- chamado de qualquer thread ---

    def iniciar(self):
        """Sobe a thread do Tk com o painel escondido (a janela "Precisa de você" depende dela)."""
        with self._trava:
            if self._thread is None or not self._thread.is_alive():
                self._pronto.clear()
                self._thread = threading.Thread(target=self._rodar, name="painel", daemon=True)
                self._thread.start()
        self._pronto.wait(10)

    def abrir(self):
        self._pedido_abrir.set()
        self.iniciar()

    def disponivel(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._pronto.is_set()

    def pedir_nome(self, atual: str, ao_salvar):
        """Abre a janelinha "Como o time vê você" (na thread do Tk). ao_salvar(nome) ao clicar em Salvar."""
        self._pedido_nome = (atual, ao_salvar)

    # --- daqui para baixo, só na thread do painel ---

    def _rodar(self):
        try:
            ctk.set_appearance_mode("dark")
            self.janela = ctk.CTk(fg_color=FUNDO)
            self._fotos = {}
            self._imagens = {}
            self._montar()
            self._montar_aviso()
            if not self._pedido_abrir.is_set():
                self.janela.withdraw()
            self._pronto.set()
            self._vigiar_pedidos()
            self._atualizar()
            self._tique()
            self.janela.mainloop()
        except Exception:
            log.exception("Erro no painel")
        finally:
            self._pronto.clear()

    # Imagens do avatar (ícone da janela e cabeçalho)

    def _foto(self, status: str):
        if status not in self._fotos:
            self._fotos[status] = ImageTk.PhotoImage(imagem(CORES.get(status, CORES["ativo"]), 64))
        return self._fotos[status]

    def _imagem(self, status: str, lado: int):
        chave = (status, lado)
        if chave not in self._imagens:
            img = imagem(CORES.get(status, CORES["ativo"]), lado * 2)
            self._imagens[chave] = ctk.CTkImage(light_image=img, dark_image=img, size=(lado, lado))
        return self._imagens[chave]

    def _ico(self, nome: str, cor: str = TEXTO, tam: int = 16):
        """Ícone do Windows desenhado como imagem (fica nítido em qualquer escala). None se a fonte faltar."""
        chave = ("ico", nome, cor, tam)
        if chave not in self._imagens:
            fonte = fonte_icones(tam * 4)
            if fonte is None:
                self._imagens[chave] = None
            else:
                img = Image.new("RGBA", (tam * 4, tam * 4), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                caixa = d.textbbox((0, 0), chr(ICONES[nome]), font=fonte)
                x = (tam * 4 - (caixa[2] - caixa[0])) / 2 - caixa[0]
                y = (tam * 4 - (caixa[3] - caixa[1])) / 2 - caixa[1]
                d.text((x, y), chr(ICONES[nome]), font=fonte, fill=cor)
                self._imagens[chave] = ctk.CTkImage(light_image=img, dark_image=img, size=(tam, tam))
        return self._imagens[chave]

    def _icone_janela(self, janela, status: str):
        # O CustomTkinter troca o ícone da janela logo depois de abrir: reaplica um pouco depois
        janela.after(250, lambda: janela.iconphoto(False, self._foto(status)))

    # Componentes

    def _fonte(self, tam=13, negrito=False):
        return ctk.CTkFont(family=FONTE, size=tam, weight="bold" if negrito else "normal")

    def _botao(self, pai, texto, comando, tipo="sec", largura=0, icone=None):
        estilos = {
            "pri": dict(fg_color=TEAL, hover_color=TEAL_HOVER, text_color="#04201C"),
            "sec": dict(fg_color=CARTAO2, hover_color=BORDA, text_color=TEXTO),
            "perigo": dict(fg_color="transparent", hover_color="#3A1416", text_color=VERMELHO,
                           border_width=1, border_color=VERMELHO),
            "link": dict(fg_color="transparent", hover_color=CARTAO2, text_color=APAGADO),
        }
        cor_icone = {"pri": "#04201C", "perigo": VERMELHO, "link": APAGADO}.get(tipo, TEXTO)
        imagem_icone = self._ico(icone, cor_icone, 15) if icone else None
        return ctk.CTkButton(pai, text=texto, command=comando, corner_radius=8, height=32, width=largura or 0,
                             font=self._fonte(12, tipo in ("pri", "perigo")), image=imagem_icone, compound="left",
                             **estilos[tipo])

    def _rotulo(self, pai, texto="", tam=13, negrito=False, cor=TEXTO, **kw):
        return ctk.CTkLabel(pai, text=texto, font=self._fonte(tam, negrito), text_color=cor, anchor="w",
                            justify="left", **kw)

    def _chip(self, pai, texto, cor=APAGADO, fundo=CARTAO2):
        return ctk.CTkLabel(pai, text=f" {texto} ", font=self._fonte(11, True), text_color=cor, fg_color=fundo,
                            corner_radius=6, height=22)

    def _titulo_secao(self, pai, texto, icone, cor):
        linha = ctk.CTkFrame(pai, fg_color="transparent")
        linha.pack(fill="x", pady=(16, 6))
        ctk.CTkLabel(linha, text="", image=self._ico(icone, cor, 18)).pack(side="left", padx=(0, 8))
        rotulo = self._rotulo(linha, texto, 15, True)
        rotulo.pack(side="left")
        contagem = self._rotulo(linha, "", 12, cor=APAGADO)
        contagem.pack(side="left", padx=10)
        return contagem

    def _vazio(self, pai, texto):
        self._rotulo(pai, texto, 12, cor=APAGADO).pack(fill="x", padx=4, pady=(0, 4))

    def _cartao(self, pai, borda=BORDA, **kw):
        return ctk.CTkFrame(pai, fg_color=CARTAO, corner_radius=12, border_width=1, border_color=borda, **kw)

    # --- painel ---

    def _montar(self):
        j = self.janela
        j.title("DispatchBot")
        j.geometry("820x720")
        j.minsize(640, 500)
        j.protocol("WM_DELETE_WINDOW", j.withdraw)  # fechar = esconder; o robô continua
        self._icone_janela(j, "ativo")

        raiz = ctk.CTkFrame(j, fg_color=FUNDO)
        raiz.pack(fill="both", expand=True, padx=18, pady=16)

        # Rodapé (montado primeiro: fica sempre visível embaixo)
        rodape = ctk.CTkFrame(raiz, fg_color=FUNDO)
        rodape.pack(fill="x", side="bottom", pady=(10, 0))
        self._botao(rodape, "Verificar agora", self._seguro(self.ao_verificar), "sec", icone="atualizar").pack(side="left")
        self._botao(rodape, "Logs", self._seguro(lambda: os.startfile(LOG_DIR)), "sec", icone="logs").pack(side="left", padx=8)
        if self.ao_historico:
            self._botao(rodape, "Histórico do time", self._abrir_historico, "sec", icone="historico").pack(side="left")
        self._botao(rodape, "PARAR AGORA", self._parar, "perigo", icone="parar").pack(side="right")
        self.lbl_aviso = self._rotulo(rodape, "", 12, cor=APAGADO)
        self.lbl_aviso.pack(side="right", padx=12)

        # Cabeçalho: avatar, status e Pausar/Retomar
        topo = self._cartao(raiz)
        topo.pack(fill="x")
        dentro = ctk.CTkFrame(topo, fg_color="transparent")
        dentro.pack(fill="x", padx=16, pady=14)
        self.lbl_avatar = ctk.CTkLabel(dentro, text="", image=self._imagem("ativo", 48))
        self.lbl_avatar.pack(side="left", padx=(0, 14))
        # Botão antes do texto: fica sempre visível, o texto é que se ajusta ao espaço que sobra
        self.btn_pausar = self._botao(dentro, "Pausar", self._seguro(self.ao_pausar), "sec", 110, icone="pausar")
        self.btn_pausar.pack(side="right")
        textos = ctk.CTkFrame(dentro, fg_color="transparent")
        textos.pack(side="left", fill="x", expand=True)
        self.lbl_status = self._rotulo(textos, "", 20, True)
        self.lbl_status.pack(fill="x")
        self.lbl_detalhe = self._rotulo(textos, "", 12, wraplength=460)
        self.lbl_resumo = self._rotulo(textos, "", 12, cor=APAGADO, wraplength=460)
        self.lbl_resumo.pack(fill="x")

        # Faixa "Também ativo": outro robô ativo em outra máquina (só aparece quando há)
        self.faixa_outros = self._cartao(raiz, borda="#5A4310")
        linha = ctk.CTkFrame(self.faixa_outros, fg_color="transparent")
        linha.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(linha, text="", image=self._ico("time", AMARELO, 18)).pack(side="left", padx=(0, 10))
        self.btn_assumir = self._botao(linha, "Assumir o plantão", self._assumir, "pri", icone="check")
        self.btn_assumir.pack(side="right")
        self.lbl_outros = self._rotulo(linha, "", 12, cor=AMARELO, wraplength=520)
        self.lbl_outros.pack(side="left", fill="x", expand=True)
        self._outros_atual = None

        # Contadores do dia
        numeros = ctk.CTkFrame(raiz, fg_color=FUNDO)
        numeros.pack(fill="x", pady=(10, 0))
        self.quadro_numeros = numeros
        self.kpis = {}
        for i, (chave, texto, icone, cor) in enumerate((
                ("fila", "Na fila", "fila", APAGADO), ("ok", "Abertos hoje", "ok", VERDE),
                ("erro", "Erros hoje", "erro", VERMELHO), ("voce", "Precisa de você", "voce", AMARELO))):
            cartao = self._cartao(numeros)
            cartao.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            numeros.columnconfigure(i, weight=1)
            cab = ctk.CTkFrame(cartao, fg_color="transparent")
            cab.pack(fill="x", padx=12, pady=(10, 0))
            ctk.CTkLabel(cab, text="", image=self._ico(icone, cor, 15)).pack(side="left", padx=(0, 6))
            self._rotulo(cab, texto, 12, cor=APAGADO).pack(side="left")
            valor = self._rotulo(cartao, "0", 22, True)
            valor.pack(fill="x", padx=12, pady=(0, 8))
            self.kpis[chave] = valor

        # Corpo com rolagem: Precisa de você, Chamados abertos hoje, Erros de hoje
        corpo = ctk.CTkScrollableFrame(raiz, fg_color=FUNDO, scrollbar_button_color=CARTAO2,
                                       scrollbar_button_hover_color=BORDA)
        corpo.pack(fill="both", expand=True, pady=(4, 0))
        self.cont_voce = self._titulo_secao(corpo, "Precisa de você", "voce", AMARELO)
        self.quadro_voce = ctk.CTkFrame(corpo, fg_color="transparent")
        self.quadro_voce.pack(fill="x")
        self.cont_ok = self._titulo_secao(corpo, "Chamados abertos hoje", "ok", VERDE)
        self.lista_ok = ctk.CTkFrame(corpo, fg_color="transparent")
        self.lista_ok.pack(fill="x")
        self.cont_erro = self._titulo_secao(corpo, "Erros de hoje: abrir à mão", "erro", VERMELHO)
        self.lista_erro = ctk.CTkFrame(corpo, fg_color="transparent")
        self.lista_erro.pack(fill="x")

        self._assinatura_voce = None
        self._contagens = []
        self._chaves_ok = self._chaves_erro = None
        self._status_atual = None

    def _parar(self):
        if messagebox.askyesno("DispatchBot", "Parar o robô agora (parada de emergência)?", parent=self.janela):
            self._seguro(self.ao_emergencia)()

    # --- plantão: outros robôs ativos e nome ---

    def _assumir(self):
        if messagebox.askyesno("DispatchBot", "Assumir o plantão?\n\nO robô do colega pausa depois do alerta em "
                                              "andamento e o seu fica ativo.", parent=self.janela):
            self._seguro(self.ao_assumir)()

    def _mostrar_outros(self, e: dict):
        outros = e.get("outros") or []
        assinatura = tuple((o["host"], o["ha_min"], o["assumindo"]) for o in outros)
        if assinatura == self._outros_atual:
            return
        self._outros_atual = assinatura
        if not outros:
            self.faixa_outros.pack_forget()
            return
        partes = []
        for o in outros:
            desde = f", ativo desde {hora(o['desde'])}" if hora(o.get("desde")) else ""
            situacao = " (pedido enviado, esperando pausar)" if o["assumindo"] else ""
            partes.append(f"{o['nome']} ({o['host']}){desde} · último sinal há {o['ha_min']} min{situacao}")
        self.lbl_outros.configure(text="Também ativo: " + "\n".join(partes))
        if all(o["assumindo"] for o in outros):
            self.btn_assumir.pack_forget()
        else:
            self.btn_assumir.pack(side="right", before=self.lbl_outros)
        self.faixa_outros.pack(fill="x", pady=(10, 0), before=self.quadro_numeros)

    def _janela_nome(self, atual: str, ao_salvar):
        """Como o time vê este plantonista (painel, histórico, pergunta de plantão)."""
        j = ctk.CTkToplevel(self.janela, fg_color=FUNDO)
        j.title("DispatchBot · seu nome")
        j.resizable(False, False)
        quadro = ctk.CTkFrame(j, fg_color=FUNDO)
        quadro.pack(fill="both", expand=True, padx=20, pady=18)
        self._rotulo(quadro, "Como o time vai ver você?", 16, True).pack(fill="x")
        self._rotulo(quadro, "Aparece para os colegas no painel e no histórico do DispatchBot.", 12,
                     cor=APAGADO).pack(fill="x", pady=(2, 10))
        campo = ctk.CTkEntry(quadro, width=320, height=34, font=self._fonte(13), fg_color=CARTAO2, border_color=BORDA)
        campo.insert(0, atual)
        campo.pack(fill="x")

        def salvar(*_):
            try:
                ao_salvar(campo.get())
            except Exception:
                log.exception("Erro ao salvar o nome do plantonista")
            j.destroy()
        j.protocol("WM_DELETE_WINDOW", lambda: (ao_salvar(""), j.destroy()))  # fechou: fica o nome sugerido
        campo.bind("<Return>", salvar)
        botoes = ctk.CTkFrame(quadro, fg_color="transparent")
        botoes.pack(fill="x", pady=(14, 0))
        self._botao(botoes, "Salvar", salvar, "pri", 100, icone="check").pack(side="right")
        j.update_idletasks()
        x = (j.winfo_screenwidth() - j.winfo_reqwidth()) // 2
        y = (j.winfo_screenheight() - j.winfo_reqheight()) // 3
        j.geometry(f"+{max(0, x)}+{max(0, y)}")
        self._icone_janela(j, "ativo")
        j.attributes("-topmost", True)
        j.lift()
        j.after(300, lambda: (campo.focus_force(), campo.select_range(0, "end")))

    # --- histórico do time ---

    def _abrir_historico(self):
        if getattr(self, "janela_hist", None) is not None and self.janela_hist.winfo_exists():
            self.janela_hist.deiconify()
            self.janela_hist.lift()
            return
        j = ctk.CTkToplevel(self.janela, fg_color=FUNDO)
        j.title("DispatchBot · histórico do time")
        j.geometry("860x600")
        j.minsize(600, 400)
        self.janela_hist = j
        self._icone_janela(j, "ativo")
        raiz = ctk.CTkFrame(j, fg_color=FUNDO)
        raiz.pack(fill="both", expand=True, padx=18, pady=16)
        topo = ctk.CTkFrame(raiz, fg_color="transparent")
        topo.pack(fill="x")
        self._rotulo(topo, "Histórico do time", 18, True).pack(side="left")
        self.hist_dia = ctk.CTkSegmentedButton(topo, values=["Hoje", "Ontem"], command=lambda _: self._carregar_historico(),
                                               font=self._fonte(12), selected_color=TEAL,
                                               selected_hover_color=TEAL_HOVER, unselected_color=CARTAO2)
        self.hist_dia.set("Hoje")
        self.hist_dia.pack(side="right")
        self._botao(topo, "Atualizar", self._carregar_historico, "sec", icone="atualizar").pack(side="right", padx=8)
        self.hist_info = self._rotulo(raiz, "", 12, cor=APAGADO)
        self.hist_info.pack(fill="x", pady=(6, 4))
        self.hist_lista = ctk.CTkScrollableFrame(raiz, fg_color=FUNDO, scrollbar_button_color=CARTAO2,
                                                 scrollbar_button_hover_color=BORDA)
        self.hist_lista.pack(fill="both", expand=True)
        j.after(150, j.lift)
        self._carregar_historico()

    def _carregar_historico(self):
        """Lê o histórico numa thread à parte (SharePoint) e desenha quando chegar."""
        from datetime import timedelta
        ontem = self.hist_dia.get() == "Ontem"
        dia = (datetime.now().date() - timedelta(days=1 if ontem else 0)).isoformat()
        self.hist_info.configure(text="Carregando...", text_color=APAGADO)
        self._historico_resultado = None

        def ler():
            try:
                self._historico_resultado = (dia, self.ao_historico(dia), None)
            except Exception as e:
                self._historico_resultado = (dia, [], str(e) or type(e).__name__)
        threading.Thread(target=ler, name="historico", daemon=True).start()
        self._esperar_historico()

    def _esperar_historico(self):
        if not self.janela_hist.winfo_exists():
            return
        if self._historico_resultado is None:
            self.janela_hist.after(300, self._esperar_historico)
            return
        dia, eventos, erro = self._historico_resultado
        for filho in self.hist_lista.winfo_children():
            filho.destroy()
        if erro:
            self.hist_info.configure(text=f"Não consegui ler o histórico: {curto(erro, 160)}", text_color=VERMELHO)
            return
        maquinas = sorted({e.get("nome") or e.get("host") for e in eventos})
        self.hist_info.configure(
            text=f"{len(eventos)} evento{'s' if len(eventos) != 1 else ''} em {datetime.fromisoformat(dia):%d/%m}"
                 + (f" · {', '.join(maquinas)}" if maquinas else "") + " · mais recentes primeiro",
            text_color=APAGADO)
        if not eventos:
            self._vazio(self.hist_lista, "Nada registrado neste dia.")
        for e in list(reversed(eventos))[:300]:  # mais recentes primeiro; 300 linhas bastam e o Tk continua leve
            linha = ctk.CTkFrame(self.hist_lista, fg_color=CARTAO, corner_radius=8)
            linha.pack(fill="x", pady=2, padx=(0, 6))
            rotulo, cor = EVENTOS.get(e.get("evento"), (str(e.get("evento") or "?").upper(), APAGADO))
            self._rotulo(linha, hora(e.get("em")), 12, cor=APAGADO, width=44).pack(side="left", padx=(10, 4), pady=6)
            ctk.CTkLabel(linha, text=f" {rotulo} ", font=self._fonte(10, True), text_color=cor, fg_color=CARTAO2,
                         corner_radius=6, height=20, width=78).pack(side="left", padx=(0, 8))
            self._rotulo(linha, curto(f"{e.get('nome') or '?'} ({e.get('host') or '?'})", 34), 12, True,
                         width=210).pack(side="left")
            self._rotulo(linha, curto(e.get("texto"), 160), 12, wraplength=420).pack(
                side="left", fill="x", expand=True, padx=(4, 10))

    # --- listas do dia ---

    def _desenhar_listas(self, registros: list):
        ok = [r for r in registros if r.get("status") == "ok"]
        erros = [r for r in registros if r.get("status") != "ok"]
        for lista, itens, atributo, montar, vazio in (
                (self.lista_ok, ok, "_chaves_ok", self._linha_ok,
                 "Nenhum chamado aberto hoje ainda. Os que o robô abrir aparecem aqui."),
                (self.lista_erro, erros, "_chaves_erro", self._linha_erro, "Nenhum erro hoje.")):
            chaves = [f"{r.get('em')}|{r.get('alerta')}" for r in itens]
            if chaves == getattr(self, atributo):
                continue
            setattr(self, atributo, chaves)
            for filho in lista.winfo_children():
                filho.destroy()
            if not itens:
                self._vazio(lista, vazio)
            for r in itens:
                montar(lista, r)
        self.cont_ok.configure(text=str(len(ok)) if ok else "")
        self.cont_erro.configure(text=str(len(erros)) if erros else "")
        return ok, erros

    def _linha_ok(self, pai, r: dict):
        cartao = self._cartao(pai)
        cartao.pack(fill="x", pady=3, padx=(0, 6))
        linha = ctk.CTkFrame(cartao, fg_color="transparent")
        linha.pack(fill="x", padx=12, pady=8)
        self._rotulo(linha, hora(r.get("em")), 12, cor=APAGADO, width=44).pack(side="left")
        self._chip(linha, r.get("tipo") or "?").pack(side="left", padx=(4, 10))
        self._rotulo(linha, curto(r.get("alerta"), 40), 13, True).pack(side="left", fill="x", expand=True)
        url = r.get("url") or self.portal_url
        self._botao(linha, "", lambda: self._copiar(r.get("numero", ""), f"{r.get('numero')} copiado"),
                    "sec", 36, icone="copiar").pack(side="right")
        self._botao(linha, "Tratar", self._seguro(lambda: webbrowser.open(url)), "pri", 90,
                    icone="abrir").pack(side="right", padx=6)
        self._chip(linha, r.get("numero") or "", VERDE, "#12301E").pack(side="right", padx=6)

    def _linha_erro(self, pai, r: dict):
        cartao = self._cartao(pai, borda="#4A2326")
        cartao.pack(fill="x", pady=3, padx=(0, 6))
        linha = ctk.CTkFrame(cartao, fg_color="transparent")
        linha.pack(fill="x", padx=12, pady=(8, 2))
        self._rotulo(linha, hora(r.get("em")), 12, cor=APAGADO, width=44).pack(side="left")
        self._chip(linha, r.get("tipo") or "?").pack(side="left", padx=(4, 10))
        self._rotulo(linha, curto(r.get("alerta"), 40), 13, True).pack(side="left", fill="x", expand=True)
        self._botao(linha, "", lambda: self._copiar(f"{r.get('alerta')}: {r.get('mensagem')}", "Motivo copiado"),
                    "sec", 36, icone="copiar").pack(side="right")
        self._botao(linha, "Abrir no ServiceNow", self._seguro(lambda: webbrowser.open(self.portal_url)), "sec",
                    icone="abrir").pack(side="right", padx=6)
        self._rotulo(cartao, curto(r.get("mensagem"), 220), 12, cor="#F3A6A6", wraplength=640).pack(
            fill="x", padx=14, pady=(0, 10))

    def _copiar(self, texto: str, aviso: str):
        if not texto:
            return
        self.janela.clipboard_clear()
        self.janela.clipboard_append(texto)
        self.lbl_aviso.configure(text=aviso, text_color=TEAL)
        self.janela.after(3000, lambda: self.lbl_aviso.configure(text=""))

    # --- "Precisa de você" (painel e janelinha) ---

    def _montar_aviso(self):
        a = ctk.CTkToplevel(self.janela, fg_color=FUNDO)
        a.title("DispatchBot · precisa de você")
        a.resizable(False, False)
        a.protocol("WM_DELETE_WINDOW", self._dispensar_aviso)
        a.withdraw()
        self.aviso = a
        # Com rolagem: com vários alertas a janelinha não passa de ~65% da altura da tela
        self.rolagem_aviso = ctk.CTkScrollableFrame(a, fg_color=FUNDO, scrollbar_button_color=CARTAO2,
                                                    scrollbar_button_hover_color=BORDA)
        self.rolagem_aviso.pack(fill="both", expand=True, padx=(14, 4), pady=14)
        self.quadro_aviso = ctk.CTkFrame(self.rolagem_aviso, fg_color=FUNDO)
        self.quadro_aviso.pack(fill="both", expand=True, padx=(0, 6))
        self._assinatura_aviso = None
        self._aviso_dispensado = None
        self._contagens_aviso = []

    def _dispensar_aviso(self):
        """Fechar a janelinha esconde até aparecer algo novo (a decisão continua no painel)."""
        self._aviso_dispensado = self._assinatura_aviso
        self.aviso.withdraw()

    @staticmethod
    def _assinatura(e: dict):
        c, lg = e.get("conferindo"), e.get("login")
        return ((c.get("tipo"), c.get("rotulo"), c.get("limite")) if c else None,
                tuple(d.get("id") for d in e.get("decisoes") or []),
                (lg.get("sistema"), lg.get("primeira")) if lg else None)

    def _caixa_voce(self, pai, chip, titulo, linhas, detalhado):
        cartao = self._cartao(pai, borda="#5A4310")
        cartao.pack(fill="x", pady=(0, 8))
        topo = ctk.CTkFrame(cartao, fg_color="transparent")
        topo.pack(fill="x", padx=14, pady=(12, 0))
        self._chip(topo, chip, AMARELO, "#3A2C0C").pack(side="left")
        self._rotulo(topo, curto(titulo, 70 if detalhado else 56), 14 if detalhado else 13, True).pack(
            side="left", padx=10)
        contagem = self._rotulo(topo, "", 12, cor=AMARELO)
        contagem.pack(side="right")
        for texto in linhas if detalhado else linhas[:1]:
            self._rotulo(cartao, texto, 12, cor=APAGADO, wraplength=480 if detalhado else 640).pack(
                fill="x", padx=14, pady=(4, 0))
        botoes = ctk.CTkFrame(cartao, fg_color="transparent")
        botoes.pack(fill="x", padx=14, pady=12)
        return cartao, botoes, contagem

    def _itens(self, pai, e: dict, detalhado: bool):
        """Monta o que precisa do plantonista (no painel e na janelinha). Devolve as contagens regressivas."""
        for filho in pai.winfo_children():
            filho.destroy()
        contagens = []
        lg = e.get("login")
        if lg:
            linhas = ["Entre na janela do navegador que o robô abriu."
                      + (" É só desta vez." if lg.get("primeira") else " Ele continua sozinho depois.")]
            if lg.get("sistema") == "ServiceNow":
                linhas.append("O robô já escolheu o login da empresa: falta a conta Microsoft e o Windows Hello.")
            _, botoes, _ = self._caixa_voce(pai, "LOGIN", f"Faça login {lg.get('onde', '')}", linhas, detalhado)
            self._botao(botoes, "Mostrar janela de login", self.ao_login, "pri", icone="login").pack(side="left")
        c = e.get("conferindo")
        if c:
            linhas = ["Confira o chamado preenchido e clique em Submit no navegador."]
            if c.get("detalhe"):
                linhas.append(c["detalhe"])
            cartao, botoes, contagem = self._caixa_voce(pai, c.get("tipo", ""), c.get("rotulo", ""), linhas, detalhado)
            contagens.append((contagem, c.get("limite", 0)))
            if detalhado and c.get("campos"):
                grade = ctk.CTkFrame(cartao, fg_color=CARTAO2, corner_radius=8)
                grade.pack(fill="x", padx=14, pady=(8, 0), before=botoes)
                for i, (campo, valor) in enumerate(c["campos"]):
                    self._rotulo(grade, campo, 12, cor=APAGADO).grid(row=i, column=0, sticky="nw", padx=(10, 12), pady=2)
                    self._rotulo(grade, valor, 12, wraplength=330).grid(row=i, column=1, sticky="w", padx=(0, 10), pady=2)
            self._botao(botoes, "Ver formulário", lambda: self.ao_conferencia("mostrar"), "pri", icone="ver").pack(side="left")
            self._botao(botoes, "Pular", self._pular, "sec", icone="pular").pack(side="left", padx=8)
        for d in e.get("decisoes") or []:
            linhas = [f"Alerta antigo, recebido {d.get('idade', '')} ({d.get('recebido', '')}). "
                      "Esse chamado já foi aberto?"]
            if d.get("descricao"):
                linhas.append(curto(d["descricao"], 220))
            _, botoes, _ = self._caixa_voce(pai, d.get("tipo", ""), d.get("rotulo", ""), linhas, detalhado)
            ident = d.get("id")
            self._botao(botoes, "Abrir chamado", lambda i=ident: self._decidir(i, "abrir"), "pri", icone="mais").pack(side="left")
            self._botao(botoes, "Já foi aberto", lambda i=ident: self._decidir(i, "manual"), "sec", icone="check").pack(
                side="left", padx=8)
            self._botao(botoes, "Depois", lambda i=ident: self._decidir(i, "depois"), "link", icone="depois").pack(side="left")
        return contagens

    def _decidir(self, ident, resposta):
        try:
            self.ao_decidir(ident, resposta)
        except Exception:
            log.exception("Erro ao registrar a decisão do alerta antigo")
        self._assinatura_aviso = self._assinatura_voce = None  # redesenha no próximo tique

    def _pular(self):
        pai = self.aviso if self.aviso.winfo_viewable() else self.janela
        if messagebox.askyesno("DispatchBot", "Pular este alerta?\n\nO robô não envia o chamado e avisa no Teams "
                                              "que ele precisa ser aberto à mão.", parent=pai):
            self.ao_conferencia("pular")

    def _tique(self):
        """A cada segundo: janelinha "Precisa de você", seção do painel e contagem regressiva."""
        try:
            e = self.obter_estado()
            assinatura = self._assinatura(e)
            tem_algo = bool(e.get("conferindo") or e.get("decisoes") or e.get("login"))
            if assinatura != self._assinatura_aviso:
                self._assinatura_aviso = assinatura
                self._contagens_aviso = self._itens(self.quadro_aviso, e, detalhado=True)  # também limpa quando vazio
                if tem_algo and assinatura != self._aviso_dispensado:
                    self._mostrar_aviso()
                elif not tem_algo:
                    self.aviso.withdraw()
                    self._aviso_dispensado = None
            if self.janela.winfo_viewable() and assinatura != self._assinatura_voce:
                self._assinatura_voce = assinatura
                # _itens já limpa a seção; sem pendências, limpa aqui (senão o último cartão fica na tela)
                self._contagens = self._itens(self.quadro_voce, e, detalhado=False)
                if not tem_algo:
                    self._vazio(self.quadro_voce, "Nada pendente. O robô avisa aqui (e numa janelinha) quando precisar de você.")
            agora = time.time()
            for rotulo, limite in self._contagens + self._contagens_aviso:
                try:
                    rotulo.configure(text=f"faltam {resto(limite - agora)}")
                except Exception:
                    pass
        except Exception:
            log.exception("Erro ao atualizar a janela de avisos")
        self.janela.after(1000, self._tique)

    def _mostrar_aviso(self):
        a = self.aviso
        a.deiconify()
        a.update_idletasks()
        # Tamanho da área de rolagem = conteúdo, limitado a ~65% da altura da tela (pixels reais → unidades do CTk)
        escala = a._get_window_scaling() or 1
        conteudo_l = self.quadro_aviso.winfo_reqwidth() + 6
        conteudo_a = self.quadro_aviso.winfo_reqheight()
        maximo_a = int(a.winfo_screenheight() * 0.65)
        self.rolagem_aviso.configure(width=conteudo_l / escala, height=min(conteudo_a, maximo_a) / escala)
        self.rolagem_aviso._parent_canvas.yview_moveto(0)
        a.update_idletasks()
        largura, altura = a.winfo_reqwidth(), a.winfo_reqheight()
        x = a.winfo_screenwidth() - largura - 24
        y = a.winfo_screenheight() - altura - 72  # acima da barra de tarefas
        a.geometry(f"+{max(0, x)}+{max(0, y)}")
        self._icone_janela(a, "confirmar")
        a.attributes("-topmost", True)  # fica visível enquanto o plantonista confere o formulário
        a.lift()

    # --- atualização do painel ---

    def _seguro(self, acao):
        def executar():
            try:
                acao()
            except Exception as e:
                self.lbl_aviso.configure(text=f"Não foi possível: {e}", text_color=VERMELHO)
            self.janela.after(300, self._atualizar_uma_vez)
        return executar

    def _vigiar_pedidos(self):
        if self._pedido_abrir.is_set():
            self._pedido_abrir.clear()
            self.janela.deiconify()
            self.janela.lift()
            self.janela.attributes("-topmost", True)  # traz para frente sem ficar sempre por cima
            self.janela.after(200, lambda: self.janela.attributes("-topmost", False))
            self.janela.focus_force()
            self._assinatura_voce = None
            self._atualizar_uma_vez()
        if self._pedido_nome:
            atual, ao_salvar = self._pedido_nome
            self._pedido_nome = None
            try:
                self._janela_nome(atual, ao_salvar)
            except Exception:
                log.exception("Erro ao abrir a janela do nome")
        self.janela.after(300, self._vigiar_pedidos)

    def _atualizar(self):
        self._atualizar_uma_vez()
        self.janela.after(ATUALIZAR_MS, self._atualizar)

    def _atualizar_uma_vez(self):
        try:
            if not self.janela.winfo_viewable():
                return
            e = self.obter_estado()
            self._mostrar_estado(e)
            ok, erros = self._desenhar_listas(ler_hoje())
            voce = len(e.get("decisoes") or []) + (1 if e.get("conferindo") else 0) + (1 if e.get("login") else 0)
            for chave, valor, cor in (("fila", e.get("pendentes", 0), TEXTO), ("ok", len(ok), TEXTO),
                                      ("erro", len(erros), VERMELHO), ("voce", voce, AMARELO)):
                self.kpis[chave].configure(text=str(valor), text_color=cor if valor else TEXTO)
            self.cont_voce.configure(text=str(voce) if voce else "")
        except Exception:
            log.exception("Erro ao atualizar o painel")

    def _mostrar_estado(self, e: dict):
        status = e.get("status", "ativo")
        if status != self._status_atual:
            self._status_atual = status
            self.lbl_avatar.configure(image=self._imagem(status, 48))
            self.janela.iconphoto(False, self._foto(status))
        cor = STATUS_COR.get(status, TEXTO)
        self.lbl_status.configure(text=e.get("texto", ""), text_color=cor)
        detalhe = curto(e.get("detalhe"), 160)
        if detalhe:  # ex.: o motivo do erro, abaixo do título curto
            self.lbl_detalhe.configure(text=detalhe[:1].upper() + detalhe[1:], text_color=cor)
            self.lbl_detalhe.pack(fill="x", before=self.lbl_resumo)
        else:
            self.lbl_detalhe.pack_forget()
        pendentes = e.get("pendentes", 0)
        partes = [f"{pendentes} alerta{'s' if pendentes != 1 else ''} na fila" if pendentes else "Fila vazia"]
        if e.get("em_andamento"):
            partes.append(f"agora: {e['em_andamento']}")
        if e.get("nome"):
            partes.append(f"plantonista: {e['nome']}")
        partes.append(datetime.now().strftime("atualizado às %H:%M"))
        self.lbl_resumo.configure(text=" · ".join(partes))
        self._mostrar_outros(e)
        pausado = bool(e.get("pausado"))
        if getattr(self, "_pausado_atual", None) != pausado:
            self._pausado_atual = pausado
            self.btn_pausar.configure(text="Retomar" if pausado else "Pausar",
                                      image=self._ico("retomar" if pausado else "pausar", TEXTO, 15))
