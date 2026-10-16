"""Abre o Edge/Chrome instalado com perfil próprio do robô e cuida do login SSO.

O robô nunca digita nem guarda senha: se cair na tela de login, avisa o usuário e espera.
"""
import subprocess
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import config
from log import log, screenshot


class LoginNaoConcluido(Exception):
    pass


class Interrompido(Exception):
    """O robô foi pausado ou encerrado durante a espera do login."""


def matar_navegadores_orfaos():
    """Fecha Edge/Chrome que ficaram presos usando o perfil do robô (ex.: depois de uma queda).
    Só atinge processos com o perfil do robô na linha de comando; o navegador pessoal não é tocado."""
    perfil = str(config.PERFIL_NAVEGADOR).replace("'", "''")
    comando = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{perfil}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
            capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        log.warning("Não foi possível limpar navegadores órfãos: %s", e)


class Navegador:
    def __init__(self, cfg: dict, ao_aguardar_login=None, deve_parar=None):
        self.cfg = cfg
        self.ao_aguardar_login = ao_aguardar_login  # callback(bool, sistema) para avisar a bandeja / console
        self.deve_parar = deve_parar  # callback() -> True se o robô foi pausado/encerrado
        self.pedir_frente = None  # callback() -> True quando o plantonista pede para ver a janela de login
        self.ultimo_uso = 0.0
        self._pw = None
        self._contexto = None
        self._invisivel = False  # aberto sem janela, só para buscar a sessão do SharePoint

    @property
    def aberto(self) -> bool:
        return self._contexto is not None

    def _lancar(self, invisivel: bool = False):
        erros = []
        for canal in self.cfg["navegador"]["canais"]:
            try:
                self._contexto = self._pw.chromium.launch_persistent_context(
                    str(config.PERFIL_NAVEGADOR),
                    channel=canal,
                    headless=invisivel,
                    no_viewport=True,
                    args=["--start-maximized", "--no-first-run", "--no-default-browser-check",
                          "--hide-crash-restore-bubble"],
                )
                self._invisivel = invisivel
                log.info("Navegador aberto: %s%s", canal, " (sem janela)" if invisivel else "")
                return []
            except Exception as e:
                erros.append(f"{canal}: {str(e).splitlines()[0]}")
        return erros

    def abrir(self, invisivel: bool = False):
        if self._contexto and self._invisivel and not invisivel:
            self.fechar()  # precisa de janela: troca o navegador invisível por um normal
        if self._contexto:
            return
        if self._pw is None:
            self._pw = sync_playwright().start()
        config.PERFIL_NAVEGADOR.mkdir(parents=True, exist_ok=True)

        erros = self._lancar(invisivel)
        if erros:
            # Causa mais comum: uma janela do robô que ficou aberta depois de uma queda segura o perfil
            log.warning("Falha ao abrir o navegador (%s). Limpando processos órfãos e tentando de novo.", " | ".join(erros))
            matar_navegadores_orfaos()
            time.sleep(2)
            erros = self._lancar(invisivel)
        if erros:
            raise RuntimeError(
                "Não foi possível abrir o Edge nem o Chrome. Se o Mapear estiver aberto, feche-o. "
                "Detalhes: " + " | ".join(erros)
            )
        self._contexto.set_default_timeout(self.cfg["navegador"]["timeout_acao_seg"] * 1000)
        # Se o usuário fechar a janela na mão, abre de novo no próximo uso
        self._contexto.on("close", lambda _: self._marcar_fechado())

    def _marcar_fechado(self):
        self._contexto = None
        self._invisivel = False

    def pagina(self):
        self.abrir()
        self.ultimo_uso = time.monotonic()
        paginas = self._contexto.pages
        return paginas[0] if paginas else self._contexto.new_page()

    def fechar(self):
        try:
            if self._contexto:
                self._contexto.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._contexto = None
        self._invisivel = False
        self._pw = None

    def screenshot(self, nome: str):
        if self._contexto and self._contexto.pages:
            screenshot(self._contexto.pages[0], nome)

    # --- login ---

    def na_tela_de_login(self, page) -> bool:
        url = page.url.lower()
        if any(p.lower() in url for p in self.cfg["login"]["padroes_url_login"]):
            return True
        if urlparse(url).hostname != urlparse(self.cfg["instancia_url"]).hostname:
            return True
        # O Service Portal mostra o login no MESMO endereço da página pedida: só o conteúdo denuncia
        return self._algum_visivel(page, self.cfg["login"].get("seletores_tela_login", []))

    @staticmethod
    def _algum_visivel(page, seletores) -> bool:
        for seletor in seletores:
            try:
                if page.locator(seletor).first.is_visible():
                    return True
            except Exception:
                pass
        return False

    def _clicar_sso(self, page) -> bool:
        """Tela de login do ServiceNow: escolhe o login pela empresa (ex.: 'Log in with Azure B2C').
        Só escolhe o provedor; usuário e senha nunca são digitados pelo robô."""
        seletor = self.cfg["login"].get("botao_sso")
        if not seletor:
            return False
        try:
            botao = page.locator(seletor).first
            if botao.is_visible():
                log.info("Tela de login do ServiceNow: clicando em '%s'.", botao.inner_text(timeout=2000).strip())
                botao.click()
                return True
        except Exception as e:
            log.warning("Não foi possível clicar no botão de login da empresa: %s", str(e).splitlines()[0])
        return False

    def _enviar_email_sso(self, page) -> bool:
        """Tela "External login" do ServiceNow (campo Email + Submit): o robô só clica em Submit, mesmo com o
        campo vazio (pedido do usuário: o login segue pela conta Microsoft). Se login.email_sso estiver no
        config, preenche antes (opcional, vazio por padrão). Nunca digita senha."""
        lg = self.cfg["login"]
        try:
            if not page.locator(lg.get("tela_email", "text=External login")).first.is_visible():
                return False
            email = str(lg.get("email_sso") or "").strip()
            if email:
                campo = page.locator(lg.get("campo_email", "input[type=email], input[name*=mail i], input[id*=mail i]")).first
                if campo.is_visible() and not campo.input_value().strip():
                    campo.fill(email)
            botao = page.locator(lg.get("botao_email", "button:has-text('Submit'), input[type=submit]")).first
            if botao.is_visible():
                log.info("Tela External login: clicando em Submit.")
                botao.click()
                return True
        except Exception as e:
            log.warning("Não foi possível enviar o e-mail na tela External login: %s", str(e).splitlines()[0])
        return False

    def _portal_pronto(self, page) -> bool:
        seletor = self.cfg["login"].get("seletor_portal_pronto")
        if not seletor:
            return True
        try:
            return page.locator(seletor).first.is_visible()
        except Exception:
            return False

    def _esperar_redirecionamentos(self, page):
        # O SSO redireciona via JS depois do carregamento; networkidle pode nunca chegar no portal
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

    @staticmethod
    def _mesma_pagina(atual: str, alvo: str) -> bool:
        a, b = urlparse(atual), urlparse(alvo)
        return a.path.rstrip("/") == b.path.rstrip("/") and a.query == b.query

    def ir_para(self, page, url: str):
        """Navega para a URL. Se cair no SSO, espera o login e navega de novo.
        O SSO às vezes devolve para a página inicial do portal, e não para a pedida: nesse caso abre de novo."""
        page.goto(url, wait_until="domcontentloaded")
        self._esperar_redirecionamentos(page)
        if self.na_tela_de_login(page):
            self.esperar_login(page)
        elif self._mesma_pagina(page.url, url):
            return
        # Passou pelo login (com ou sem ajuda do usuário) e pode ter parado em outra página
        page.goto(url, wait_until="domcontentloaded")
        self._esperar_redirecionamentos(page)
        if self.na_tela_de_login(page):
            raise LoginNaoConcluido("Após o login, o ServiceNow voltou para a tela de login.")

    def esperar_login(self, page, pronto=None, sistema: str = "ServiceNow"):
        """Espera o login terminar. pronto() diz quando terminou (padrão: de volta ao ServiceNow)."""
        if pronto is None:
            pronto = lambda: not self.na_tela_de_login(page) and self._portal_pronto(page)
        minutos = self.cfg["navegador"]["timeout_login_min"]
        # O SSO costuma concluir sozinho (sessão do Microsoft Entra lembrada); só avisa se não concluir
        carencia = self.cfg["login"].get("aviso_apos_seg", 15)
        inicio = time.monotonic()
        avisado = False
        cliques_sso, ultimo_clique = 0, 0.0
        envios_email, ultimo_envio = 0, 0.0
        seguidas = 0  # verificações seguidas fora da tela de login
        try:
            limite = inicio + minutos * 60
            while time.monotonic() < limite:
                # ServiceNow: escolhe o login pela empresa; no máximo 2 vezes, para não brigar com o usuário
                if sistema == "ServiceNow" and cliques_sso < 2 and time.monotonic() - ultimo_clique > 20:
                    if self._clicar_sso(page):
                        cliques_sso, ultimo_clique = cliques_sso + 1, time.monotonic()
                        seguidas = 0
                # Depois do "Use external login": e-mail + Submit (no máximo 2 vezes, para não brigar com o usuário)
                if sistema == "ServiceNow" and envios_email < 2 and time.monotonic() - ultimo_envio > 15:
                    if self._enviar_email_sso(page):
                        envios_email, ultimo_envio = envios_email + 1, time.monotonic()
                        seguidas = 0
                page.wait_for_timeout(2000)
                if self.deve_parar and self.deve_parar():
                    raise Interrompido("Robô pausado ou encerrado durante a espera do login.")
                if self.pedir_frente and self.pedir_frente():
                    try:
                        page.bring_to_front()  # botão "Mostrar janela de login" da janelinha
                    except Exception:
                        pass
                # Durante os redirecionamentos do SSO a página fica um instante "sem tela de login":
                # só conta como concluído depois de 3 verificações seguidas (~6 s) estável
                seguidas = seguidas + 1 if pronto() else 0
                if seguidas >= 3:
                    log.info("Login no %s concluído%s.", sistema, "" if avisado else " automaticamente pelo SSO")
                    return
                if not avisado and time.monotonic() - inicio >= carencia:
                    avisado = True
                    log.warning("Login no %s necessário. Aguardando o usuário (até %s min)...", sistema, minutos)
                    try:
                        page.bring_to_front()
                    except Exception:
                        pass
                    if self.ao_aguardar_login:
                        self.ao_aguardar_login(True, sistema)
            raise LoginNaoConcluido(f"Login no {sistema} não concluído em {minutos} minutos.")
        finally:
            if avisado and self.ao_aguardar_login:
                self.ao_aguardar_login(False, sistema)

    # --- sessão do SharePoint (fila) ---

    def cookies_sharepoint(self, site: str, renovar: bool, permitir_janela: bool = True) -> list:
        """Cookies da sessão Microsoft para chamar a API do SharePoint.

        - Navegador aberto e sem pedido de renovação: usa os cookies que ele já tem.
        - Senão, visita o site para o SSO emitir cookies novos. Se o navegador estiver fechado,
          faz isso sem janela; só abre a janela se a Microsoft pedir login de verdade."""
        if not self._contexto:
            self.abrir(invisivel=True)
        try:
            if not renovar:
                # Os cookies da sessão ficam salvos no perfil: na maioria das vezes nem precisa abrir o site
                cookies = self._contexto.cookies(site)
                if _tem_sessao_sharepoint(cookies):
                    return cookies
            return self._visitar_sharepoint(site)
        except LoginNaoConcluido:
            if not self._invisivel:
                raise
            # Sem janela não dá para o usuário logar: abre o navegador normal e espera o login
            self.fechar()
            if not permitir_janela:
                raise LoginNaoConcluido("O SharePoint pede login (o robô está pausado; faz o login ao retomar).")
            self.abrir()
            return self._visitar_sharepoint(site)
        finally:
            if self._invisivel:
                self.fechar()

    def _visitar_sharepoint(self, site: str) -> list:
        host = urlparse(site).hostname
        page = self._contexto.new_page()
        try:
            def pronto():
                return urlparse(page.url).hostname == host and _tem_sessao_sharepoint(self._contexto.cookies(site))
            try:
                page.goto(site, wait_until="domcontentloaded")
            except Exception as e:
                # Sem janela, o Edge às vezes recusa abrir o SharePoint (net::ERR_FAILED); com janela abre normal
                if self._invisivel:
                    raise LoginNaoConcluido(f"SharePoint não abriu sem janela: {str(e).splitlines()[0]}") from e
                raise
            self._esperar_redirecionamentos(page)
            if not pronto():
                if self._invisivel:
                    page.wait_for_timeout(3000)  # dá um tempo para o SSO terminar sozinho
                    if not pronto():
                        raise LoginNaoConcluido("O SharePoint pede login.")
                else:
                    self.esperar_login(page, pronto, "SharePoint")
            self.ultimo_uso = time.monotonic()
            return self._contexto.cookies(site)
        finally:
            try:
                page.close()
            except Exception:
                pass


def _tem_sessao_sharepoint(cookies: list) -> bool:
    return any(c["name"] in ("FedAuth", "rtFa") for c in cookies)
