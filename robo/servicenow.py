"""Preenche o formulário do ServiceNow conforme o tipo (config.json) e captura o número do chamado."""
import hashlib
import json
import re
import time
from datetime import datetime

from log import log
from navegador import Interrompido

# {campo} ou {campo|formato de data}, ex.: {data_execucao|dd/MM HH:mm}
PLACEHOLDER = re.compile(r"\{(\w+)(?:\|([^{}]+))?\}")
FORMATOS_ENTRADA = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


class TipoNaoMapeado(Exception):
    pass


class FormularioRecusado(Exception):
    """O ServiceNow mostrou erro de validação depois do clique em Enviar: nada foi aberto."""


class DadoFaltando(Exception):
    """O alerta não trouxe um dado que o formulário exige. O robô não inventa: para e avisa."""


class NaoConfirmado(Exception):
    """Modo com confirmação: ninguém clicou em Enviar dentro do prazo."""


class Pulado(Exception):
    """Modo com confirmação: o plantonista escolheu "Pular este alerta" (abre à mão)."""


def resumo_campos(tipo_cfg: dict, alerta: dict, limite: int = 120) -> list:
    """O que o robô preencheu, para o plantonista conferir: [(campo, valor)]. Nunca levanta erro."""
    v = variaveis(alerta, tipo_cfg)
    campos = []
    for passo in tipo_cfg.get("passos", []):
        if passo.get("acao") in ("aguardar", "clicar") or not passo.get("campo"):
            continue
        try:
            valor = resolver(str(passo.get("valor", "")), v)
            if "mapa" in passo:
                valor = passo["mapa"].get(valor, passo["mapa"].get("*", valor))
        except Exception:
            continue
        if not valor:
            continue
        valor = " ".join(valor.split())
        # "Categoria (lista fixa: ...)" -> "Categoria": tira as anotações longas do config, mantém "(Conta)"
        nome = re.sub(r"\s*\([^)]{20,}\)", "", str(passo["campo"])).replace("*", "").strip()
        campos.append((nome, valor if len(valor) <= limite else valor[:limite - 1] + "…"))
    return campos


def variaveis(alerta: dict, tipo_cfg: dict = None) -> dict:
    """Valores disponíveis para {campo}, em ordem de prioridade crescente:
    "padroes" do tipo (o alerta pode trocar), campos simples do JSON, e "variaveis" do tipo (fixas, o alerta não troca)."""
    tipo_cfg = tipo_cfg or {}
    v = {k: str(val) for k, val in (tipo_cfg.get("padroes") or {}).items()}
    v.update({k: str(val).strip() for k, val in alerta.items() if val not in (None, "") and not isinstance(val, (dict, list))})
    v["id_curto"] = hashlib.sha1(str(alerta.get("id", "")).encode("utf-8")).hexdigest()[:10]
    v.update({k: str(val) for k, val in (tipo_cfg.get("variaveis") or {}).items()})
    return v


def ler_data(texto: str):
    texto = texto.strip()
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        pass
    for formato in FORMATOS_ENTRADA:
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    raise ValueError(f"data em formato não reconhecido: '{texto}'")


def formatar_data(texto: str, formato: str) -> str:
    """'29/04/2026 09:30' + 'dd/MM HH:mm' -> '29/04 09:30' (formato no estilo do Excel/planilha CHANGES)."""
    py = formato.replace("yyyy", "%Y").replace("dd", "%d").replace("MM", "%m").replace("HH", "%H").replace("mm", "%M").replace("ss", "%S")
    return ler_data(texto).strftime(py)


def resolver(texto: str, v: dict) -> str:
    def trocar(m):
        nome, formato = m.group(1), m.group(2)
        if nome not in v:
            return m.group(0)  # desconhecido fica visível; faltando() já barrou antes de enviar
        return formatar_data(v[nome], formato) if formato and v[nome] else v[nome]
    return PLACEHOLDER.sub(trocar, texto)


def faltando(tipo_cfg: dict, v: dict) -> list:
    """Dados que o formulário usa e o alerta não trouxe (ou trouxe inválidos)."""
    problemas = []
    textos = [tipo_cfg.get("url_formulario", "")]
    for passo in tipo_cfg.get("passos", []):
        if not passo.get("pular_se_vazio"):
            textos += [str(passo.get("valor", "")), str(passo.get("busca", ""))]
    for texto in textos:
        for m in PLACEHOLDER.finditer(texto):
            nome, formato = m.group(1), m.group(2)
            if not v.get(nome):
                problemas.append(nome)
            elif formato:
                try:
                    ler_data(v[nome])
                except ValueError as e:
                    problemas.append(f"{nome} ({e})")
    for nome, padrao in (tipo_cfg.get("validar") or {}).items():
        if v.get(nome) and not re.fullmatch(padrao, v[nome]):
            problemas.append(f"{nome} ('{v[nome]}' fora do padrão esperado)")
    return list(dict.fromkeys(problemas))


# Tabela do ServiceNow de cada tipo de número (para o link de tratamento)
TABELAS = {"RITM": "sc_req_item", "INC": "incident", "REQ": "sc_request", "CS": "sn_customerservice_case"}


def link_tratamento(cfg: dict, numero: str) -> str:
    """Link que abre o chamado direto na tela de tratamento (interface clássica). Vazio se o tipo não é conhecido."""
    modelo = cfg.get("link_tratamento", "")
    prefixo = re.match(r"[A-Z]+", numero or "")
    tabela = TABELAS.get(prefixo.group(0)) if prefixo else None
    if not modelo or not tabela:
        return ""
    return url_absoluta(cfg, modelo.format(tabela=tabela, numero=numero))


def url_absoluta(cfg: dict, caminho: str) -> str:
    if caminho.startswith("http"):
        return caminho
    return cfg["instancia_url"].rstrip("/") + "/" + caminho.lstrip("/")


def texto_pagina(page, seletor: str = "") -> str:
    try:
        if seletor:
            # Todos os elementos do seletor, mesmo escondidos (o RITM fica na aba Related Records, fechada)
            return " ".join(page.locator(seletor).all_text_contents())
        return page.url + "\n" + page.inner_text("body", timeout=2000)
    except Exception:
        return page.url  # página ainda navegando


def salvar_diagnostico(page, nome: str):
    """Screenshot + HTML da página (inclusive o escondido) junto do log, para achar onde o número aparece."""
    from log import SCREENSHOT_DIR, screenshot
    screenshot(page, nome)
    try:
        nome = "".join(c if c.isalnum() or c in "-_" else "_" for c in nome)[:80]
        caminho = SCREENSHOT_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{nome}.html"
        caminho.write_text(page.content(), encoding="utf-8")
        log.info("HTML da página salvo: %s", caminho)
    except Exception as e:
        log.warning("Não foi possível salvar o HTML da página: %s", e)


def verificar_parada(nav):
    """Pausa ou parada de emergência pedida: interrompe entre um passo e outro."""
    if getattr(nav, "deve_parar", None) and nav.deve_parar():
        raise Interrompido("Robô pausado ou parado durante o preenchimento.")


def executar_passo(page, passo: dict, v: dict):
    acao = passo["acao"]
    valor = resolver(str(passo.get("valor", "")), v)
    if "mapa" in passo:
        valor = passo["mapa"].get(valor, passo["mapa"].get("*", valor))
    if passo.get("pular_se_vazio") and not valor:
        return

    if acao == "aguardar":
        if passo.get("seletor"):
            page.locator(passo["seletor"]).first.wait_for(state="visible")
        else:
            page.wait_for_timeout(int(valor or 1000))
        return

    alvo = page.locator(passo["seletor"]).first
    if acao == "preencher":
        alvo.fill(valor)
    elif acao == "selecionar":
        alvo.select_option(valor)  # aceita value ou texto da opção
    elif acao == "marcar":
        marcado = "valor" not in passo or valor.strip().lower() not in ("false", "0", "nao", "não", "")
        alvo.set_checked(marcado)
    elif acao == "clicar":
        alvo.click()
    elif acao == "referencia":
        busca = resolver(str(passo.get("busca", "")), v) or valor
        tentativas = [(busca, valor, False)]
        # Alternativas (ex.: o portal em português mostra a opção com outro texto)
        for alt in passo.get("alternativas", []):
            tentativas.append((resolver(str(alt.get("busca", "")), v),
                               resolver(str(alt.get("regex") or alt.get("valor", "")), v), bool(alt.get("regex"))))
        preencher_referencia(page, alvo, tentativas, passo)
    elif acao == "escolher":
        # Lista fixa do Service Portal (select2 sobre um <select>): abre e clica na opção com o texto exato
        # (ou a tradução dele em "traducoes": o portal em português mostra as opções traduzidas)
        alvo.click()
        textos = [valor] + ([passo["traducoes"][valor]] if valor in passo.get("traducoes", {}) else [])
        exato = "|".join(re.escape(t) for t in textos)
        opcoes = page.locator(passo.get("seletor_opcao", ".select2-drop-active .select2-result-label"))
        opcoes.filter(has_text=re.compile(rf"^\s*({exato})\s*$")).first.click()
    else:
        raise ValueError(f"Ação desconhecida no config: '{acao}'")


def preencher_referencia(page, alvo, tentativas: list, passo: dict):
    """Campo de referência do Service Portal (select2): clica, digita a busca e escolhe a opção
    que contém o valor (ex.: busca 'SOCIEDADE DE ENSINO', opção com '3-9R2V6QXWXV').
    tentativas = [(busca, valor, é_regex)]: se uma não acha a opção ("Nenhuma correspondência"),
    apaga a busca e tenta a próxima. Nenhuma achou: erro com as opções que apareceram."""
    alvo.click()
    campo = page.locator(passo.get("seletor_busca", ".select2-drop-active input.select2-input")).first
    opcoes = page.locator(passo.get("seletor_opcao", ".select2-drop-active .select2-result-label"))
    sem_resultado = page.locator(".select2-drop-active .select2-no-results")
    so_uma = len(tentativas) == 1
    vistas = []
    for i, (busca, valor, regex) in enumerate(tentativas):
        if i:
            campo.fill("")
        campo.press_sequentially(busca, delay=60)  # select2 busca por tecla digitada
        filtro = re.compile(valor, re.IGNORECASE) if regex else valor
        opcao = opcoes.filter(has_text=filtro).first
        if so_uma:
            opcao.click()  # comportamento de sempre: espera o tempo padrão do Playwright
            return
        # O select2 só refaz a busca um pouco depois da última tecla: sem a pausa, o
        # "Nenhuma correspondência" da busca anterior ainda está na tela
        page.wait_for_timeout(int(passo.get("pausa_ms", 800)))
        try:
            # Para assim que aparecer a opção certa ou o "Nenhuma correspondência encontrada"
            opcao.or_(sem_resultado).first.wait_for(state="visible", timeout=int(passo.get("espera_seg", 8)) * 1000)
        except Exception:
            pass
        if opcao.is_visible():
            opcao.click()
            return
        vistas = [t.strip() for t in opcoes.all_inner_texts() if t.strip()] or vistas
    raise ValueError(f"Campo '{passo.get('campo', passo['seletor'])}': nenhuma opção encontrada "
                     f"(tentei {len(tentativas)} buscas). Opções que apareceram: {vistas[:8] or 'nenhuma'}")


# Campos obrigatórios do formulário (asterisco do Service Portal) e se ficaram preenchidos.
# Em campos de referência (select2) o valor fica no input escondido #sp_formfield_<nome>.
JS_OBRIGATORIOS = r"""
() => {
  const visivel = el => !!(el.offsetWidth || el.offsetHeight);
  const grupos = document.querySelectorAll('.form-group, .sp-form-field');
  const out = [];
  const vistos = new Set();
  for (const g of grupos) {
    if (!visivel(g)) continue;
    const campo = g.querySelector('[id^="sp_formfield_"]');
    if (!campo || vistos.has(campo.id)) continue;
    const l = g.querySelector('label, .field-label');
    const rotulo = l ? l.innerText.trim().replace(/\s+/g, ' ') : campo.id;
    // O asterisco do Service Portal existe em todo campo e só aparece (ng-show) nos obrigatórios
    const asterisco = Array.from(g.querySelectorAll('.mandatory, [class*="mandatory"], .fa-asterisk')).some(visivel);
    const obrigatorio = asterisco || /is-required/.test(g.className)
      || !!g.querySelector('[required], [aria-required="true"]')
      || /\*\s*$/.test(rotulo);
    if (!obrigatorio) continue;
    vistos.add(campo.id);
    let preenchido;
    const marcaveis = g.querySelectorAll('input[type=checkbox], input[type=radio]');
    if (marcaveis.length) preenchido = Array.from(marcaveis).some(m => m.checked);
    // Em campo de referência (select2) o primeiro sp_formfield_ é a caixa de busca: vale qualquer valor do grupo
    else preenchido = Array.from(g.querySelectorAll(
      'input:not(.select2-input):not(.select2-focusser), select, textarea')).some(e => (e.value || '').trim() !== '');
    out.push({ rotulo, seletor: '#' + CSS.escape(campo.id), preenchido });
  }
  // Caixa "Required information" / "Informações obrigatórias" ao lado do botão de enviar
  let caixa = '';
  for (const el of document.querySelectorAll('div, span, p')) {
    const t = (el.childElementCount === 0 ? el.innerText : '') || '';
    if (/^(Required information|Informações obrigatórias|Informação obrigatória)/i.test(t.trim()) && visivel(el)) {
      caixa = (el.parentElement || el).innerText.trim().replace(/\s+/g, ' ');
      break;
    }
  }
  return { campos: out, caixa };
}
"""


def obrigatorios_vazios(page) -> list:
    """Rótulos dos campos obrigatórios que ficaram vazios. Levanta erro se não deu para conferir."""
    r = page.evaluate(JS_OBRIGATORIOS)
    return [c["rotulo"].replace("*", "").strip() for c in r["campos"] if not c["preenchido"]]


def abrir_chamado(nav, cfg: dict, tipo_cfg: dict, alerta: dict, enviar: bool = True, antes_de_enviar=None,
                  ao_pedir_confirmacao=None, verificar_pedido=None):
    """Preenche e envia o formulário. Retorna (numero, url). Com enviar=False só preenche (teste).
    Com "confirmar_antes_de_enviar" no tipo, quem clica em Enviar é o plantonista, depois de conferir.
    Sem ele (envio automático), o robô só clica se todos os obrigatórios estiverem preenchidos;
    senão cai para a conferência. ao_pedir_confirmacao(pedindo, motivo="") avisa o robô.
    verificar_pedido() devolve "mostrar" (traz o formulário para a frente), "pular" ou None."""
    # No modo teste deixa passar, para validar os seletores aos poucos durante o mapeamento
    sem_ajuda = {k: val for k, val in tipo_cfg.items() if not k.startswith("_")}
    if enviar and "PREENCHER" in json.dumps(sem_ajuda):
        raise TipoNaoMapeado(
            f"Tipo {alerta.get('tipo')} ainda não mapeado: existem valores PREENCHER no config.json."
        )

    v = variaveis(alerta, tipo_cfg)
    problemas = faltando(tipo_cfg, v)
    if problemas:
        raise DadoFaltando("O alerta não trouxe dados necessários para o chamado: " + ", ".join(problemas) + ".")

    page = nav.pagina()
    nav.ir_para(page, url_absoluta(cfg, resolver(tipo_cfg["url_formulario"], v)))
    if tipo_cfg.get("seletor_formulario_pronto"):
        page.locator(tipo_cfg["seletor_formulario_pronto"]).first.wait_for(state="visible")

    for i, passo in enumerate(tipo_cfg["passos"], 1):
        verificar_parada(nav)
        try:
            executar_passo(page, passo, v)
        except Exception as e:
            descricao = f"passo {i} ({passo.get('acao')} {passo.get('seletor', '')})"
            if passo.get("opcional"):
                log.warning("%s falhou e foi ignorado (opcional): %s", descricao, str(e).splitlines()[0])
                continue
            raise RuntimeError(f"Falha no {descricao}: {str(e).splitlines()[0]}") from e

    if not enviar:
        log.info("Modo teste: formulário preenchido e NÃO enviado.")
        return None, page.url

    captura = tipo_cfg["captura_numero"]
    regex = re.compile(captura["regex"])
    seletor_numero = captura.get("seletor", "")
    # Números que já estavam na tela antes do envio não contam
    ja_na_tela = {m.group(0) for m in regex.finditer(texto_pagina(page) + " " + texto_pagina(page, seletor_numero))}

    confirmar = bool(tipo_cfg.get("confirmar_antes_de_enviar"))
    motivo = ""  # por que o envio automático virou conferência (vai para a notificação)
    if not confirmar:
        # Envio automático: só clica se tudo que o formulário exige estiver preenchido.
        # Na dúvida, não envia: deixa o formulário aberto para o plantonista conferir.
        if not tipo_cfg.get("botao_enviar"):
            motivo = "o botão de enviar deste tipo não está mapeado no config"
        else:
            try:
                vazios = obrigatorios_vazios(page)
                if vazios:
                    motivo = "campo obrigatório vazio: " + ", ".join(vazios)
            except Exception as e:
                motivo = "não deu para conferir os campos obrigatórios"
                log.warning("Conferência dos obrigatórios falhou: %s", str(e).splitlines()[0])
        if motivo:
            confirmar = True
            log.warning("Envio automático suspenso (%s): o plantonista confere e envia.", motivo)

    verificar_parada(nav)  # última chance de parar sem ter enviado nada
    url_formulario = page.url  # recarregar aqui não adianta (só mostra o formulário vazio)
    if antes_de_enviar:
        antes_de_enviar()  # a partir daqui o chamado pode existir: nunca reenviar sozinho
    if confirmar:
        log.info("Formulário preenchido. Aguardando o plantonista conferir e clicar em Submit.")
        try:
            page.bring_to_front()
        except Exception:
            pass
        if ao_pedir_confirmacao:
            ao_pedir_confirmacao(True, motivo)
        limite = time.monotonic() + tipo_cfg.get("confirmacao_timeout_min", 30) * 60
    else:
        log.info("Formulário preenchido e conferido pelo robô. Clicando em enviar (%s).", tipo_cfg["botao_enviar"])
        page.locator(tipo_cfg["botao_enviar"]).first.click()
        limite = time.monotonic() + captura.get("timeout_seg", 60)

    seletor_erro = tipo_cfg.get("seletor_erro_validacao", "")
    preferir = captura.get("preferir") or []  # ex.: ["INC", "RITM"]: a tela pode mostrar o REQ antes
    if isinstance(preferir, str):
        preferir = [preferir]
    visto_em = None
    reserva = None  # 1º número que não é o preferido (ex.: o CS), caso a página recarregada não mostre mais
    recarregar_seg = captura.get("recarregar_seg", 0)  # 0 = nunca recarrega
    recarregou_em = None
    try:
        while time.monotonic() < limite:
            page.wait_for_timeout(1000)
            novos = [m.group(0) for m in regex.finditer(texto_pagina(page, seletor_numero)) if m.group(0) not in ja_na_tela]
            if novos:
                visto_em = visto_em or time.monotonic()
                reserva = reserva or novos[0]
            if visto_em:
                melhor = next((n for pref in preferir for n in novos if n.startswith(pref)), None)  # na ordem da lista
                # Sem o preferido, espera um pouco por ele (padrão 10 s); depois aceita o que apareceu
                if melhor or not preferir:
                    return melhor or novos[0], page.url
                if time.monotonic() - visto_em > captura.get("espera_preferido_seg", 10):
                    log.warning("O %s não apareceu em %s s: fica o %s.", "/".join(preferir),
                                captura.get("espera_preferido_seg", 10), reserva)
                    salvar_diagnostico(page, f"sem_{preferir[0]}_{reserva}")
                    return reserva, page.url
                # O INC do Backup é criado depois do CS e a página do caso não se atualiza sozinha: recarrega
                if recarregar_seg and page.url != url_formulario and \
                        time.monotonic() - (recarregou_em or visto_em) > recarregar_seg:
                    recarregou_em = time.monotonic()
                    log.info("Chamado %s criado; recarregando a página para achar o %s.", reserva, "/".join(preferir))
                    try:
                        page.reload(wait_until="domcontentloaded")
                    except Exception as e:
                        log.warning("Não deu para recarregar a página do chamado: %s", str(e).splitlines()[0])
            # Pedidos da janela "Precisa de você" (depois de olhar o número: se já saiu, o chamado existe)
            pedido = verificar_pedido() if (confirmar and verificar_pedido) else None
            if pedido == "mostrar":
                try:
                    page.bring_to_front()
                except Exception:
                    pass
            elif pedido == "pular" and not visto_em:
                raise Pulado("O plantonista pulou este alerta no robô; o chamado não foi enviado. Abra à mão.")
            # Mensagem de validação = o ServiceNow não aceitou o envio. Com confirmação, o plantonista corrige e reenvia.
            if seletor_erro and not confirmar:
                erro = page.locator(seletor_erro).first
                try:
                    if erro.is_visible():
                        raise FormularioRecusado("O ServiceNow recusou o formulário: " + erro.inner_text(timeout=2000).strip()[:300])
                except FormularioRecusado:
                    raise
                except Exception:
                    pass
    finally:
        if confirmar and ao_pedir_confirmacao:
            ao_pedir_confirmacao(False)
    if confirmar:
        minutos = tipo_cfg.get("confirmacao_timeout_min", 30)
        raise NaoConfirmado(f"Ninguém clicou em Submit no chamado preenchido em {minutos} min.")
    raise RuntimeError("Formulário enviado, mas o número do chamado não apareceu na tela a tempo.")


def buscar_existente(nav, cfg: dict, alerta: dict):
    """Checagem opcional (D.3): procura o id do alerta no portal. Retorna o número encontrado ou None."""
    chk = cfg["checar_duplicado_servicenow"]
    v = variaveis(alerta)
    page = nav.pagina()
    nav.ir_para(page, url_absoluta(cfg, resolver(chk["url_busca"], v)))
    page.wait_for_timeout(chk.get("espera_ms", 3000))
    m = re.search(chk["regex_numero"], page.inner_text("body"))
    return m.group(0) if m else None
