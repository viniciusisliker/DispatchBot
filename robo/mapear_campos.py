"""Ferramenta de mapeamento dos formulários do ServiceNow (usa o mesmo perfil/sessão do robô).

Uso:
  DispatchBot-Mapear.exe                              abre o portal com o Playwright Inspector e lista os campos
  DispatchBot-Mapear.exe --testar exemplos\\INFRA.json  preenche o formulário do tipo, SEM enviar
  DispatchBot-Mapear.exe --testar INFRA.json --enviar   abre o chamado de verdade (não mexe nas pastas da fila)

Feche ou pause o DispatchBot antes: os dois não podem usar o mesmo perfil de navegador ao mesmo tempo.
"""
import argparse
import json
import sys

import config
import servicenow
from fila import ler_json
from log import configurar as configurar_log, screenshot
from navegador import Navegador

# Lista os campos do formulário aberto e sugere seletor e ação para o config.json
JS_CAMPOS = r"""
() => {
  const els = document.querySelectorAll(
    '[id^="sp_formfield_"], [id^="s2id_sp_formfield_"], input[name], textarea[name], select[name]');
  const out = [];
  for (const el of new Set(els)) {
    if (el.type === 'hidden') continue;
    const id = el.id || '';
    const tag = el.tagName.toLowerCase();
    const seletor = id ? '#' + CSS.escape(id) : `${tag}[name="${el.getAttribute('name')}"]`;
    let rotulo = '';
    if (id) { const l = document.querySelector(`label[for="${id}"]`); if (l) rotulo = l.innerText; }
    if (!rotulo) {
      const g = el.closest('.form-group, .sp-form-field, .form-field');
      const l = g && g.querySelector('label, .field-label');
      if (l) rotulo = l.innerText;
    }
    let acao = 'preencher';
    if (id.startsWith('s2id_')) acao = 'referencia';
    else if (tag === 'select') acao = 'selecionar';
    else if (el.type === 'checkbox' || el.type === 'radio') acao = 'marcar';
    else if (tag === 'div' || tag === 'span') acao = 'clicar';
    const item = { seletor, acao_sugerida: acao, rotulo: rotulo.trim().replace(/\s+/g, ' '),
                   tag, tipo: el.type || '', visivel: !!(el.offsetWidth || el.offsetHeight) };
    if (tag === 'select') item.opcoes = Array.from(el.options).map(o => ({ valor: o.value, texto: o.text.trim() }));
    out.push(item);
  }
  return out;
}
"""


def listar_obrigatorios(page):
    """Mostra no console os campos obrigatórios e quais ficaram vazios depois do preenchimento."""
    try:
        r = page.evaluate(servicenow.JS_OBRIGATORIOS)
    except Exception as e:
        print(f"\n(Não deu para listar os campos obrigatórios: {str(e).splitlines()[0]})")
        return
    print("\nCampos obrigatórios do formulário:")
    if not r["campos"]:
        print("  nenhum encontrado (confira o asterisco na tela)")
    for c in r["campos"]:
        situacao = "ok" if c["preenchido"] else "VAZIO"
        print(f"  [{situacao:<5}] {c['rotulo']}   ({c['seletor']})")
    vazios = [c for c in r["campos"] if not c["preenchido"]]
    if vazios:
        print(f"\n>>> {len(vazios)} obrigatório(s) VAZIO(s): o Submit vai recusar. Mapear no config.json.")
    if r["caixa"]:
        print(f"\nAviso do portal: {r['caixa']}")
    listar_botoes(page)


# Botões visíveis (para achar o seletor de "botao_enviar")
JS_BOTOES = r"""
() => {
  const visivel = el => !!(el.offsetWidth || el.offsetHeight);
  return Array.from(document.querySelectorAll('button, input[type=submit], input[type=button], a.btn'))
    .filter(visivel)
    .map(b => ({ seletor: b.id ? '#' + CSS.escape(b.id) : b.tagName.toLowerCase() + (b.name ? `[name="${b.name}"]` : ''),
                 texto: (b.innerText || b.value || '').trim().replace(/\s+/g, ' ').slice(0, 60),
                 classe: (b.className || '').toString().slice(0, 60) }));
}
"""


def listar_botoes(page):
    try:
        botoes = page.evaluate(JS_BOTOES)
    except Exception as e:
        print(f"\n(Não deu para listar os botões: {str(e).splitlines()[0]})")
        return
    print("\nBotões visíveis (o de enviar vai em 'botao_enviar'):")
    for b in botoes:
        if b["texto"]:
            print(f"  {b['seletor']:<35} {b['texto']:<30} {b['classe']}")


def avisar_login(aguardando: bool, sistema: str = "ServiceNow"):
    if aguardando:
        print(f"\n>>> Faça login no {sistema} na janela aberta. Aguardando...\n")


def modo_mapear(nav: Navegador, cfg: dict):
    page = nav.pagina()
    nav.ir_para(page, servicenow.url_absoluta(cfg, cfg["portal_path"]))
    saida = config.APP_DIR / "campos_mapeados.json"
    print(
        "\nO Playwright Inspector vai abrir junto com o navegador.\n"
        " 1. No navegador, abra o formulário do tipo (INFRA, DBA ou SO).\n"
        " 2. No Inspector, use 'Pick locator' para ver o seletor de um campo,\n"
        "    ou 'Record' para gravar o preenchimento.\n"
        " 3. Clique em 'Resume' (▶) no Inspector para listar todos os campos da página aqui.\n"
        "    Copie a URL da barra de endereço para 'url_formulario' (sem o domínio).\n"
    )
    while True:
        page.pause()
        campos = page.evaluate(JS_CAMPOS)
        saida.write_text(json.dumps({"url": page.url, "campos": campos}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nURL: {page.url}\n")
        print(f"{'SELETOR':<55} {'AÇÃO':<11} RÓTULO")
        for c in campos:
            if c["visivel"] or c["acao_sugerida"] == "referencia":
                print(f"{c['seletor']:<55} {c['acao_sugerida']:<11} {c['rotulo']}")
        print(f"\nLista completa (com opções dos selects) salva em: {saida}")
        if input("\nEnter = pausar de novo | s + Enter = sair: ").strip().lower() == "s":
            return


def modo_testar(nav: Navegador, cfg: dict, caminho: str, enviar: bool):
    alerta = ler_json(caminho)
    tipo = str(alerta.get("tipo", "")).upper()
    tipo_cfg = config.tipo_efetivo(cfg, tipo)
    if not tipo_cfg:
        print(f"Tipo '{tipo}' não existe no config.json. Tipos: {', '.join(config.nomes_tipos(cfg))}")
        return
    try:
        numero, url = servicenow.abrir_chamado(nav, cfg, tipo_cfg, alerta, enviar=enviar)
        if enviar:
            print(f"\nChamado aberto: {numero}\n{url}")
        else:
            print("\nFormulário preenchido e NÃO enviado. Confira os campos no navegador.")
    except Exception as e:
        print(f"\nFALHOU: {e}")
        if nav.aberto:
            screenshot(nav.pagina(), f"teste_{tipo}")
    if not enviar and nav.aberto:
        listar_obrigatorios(nav.pagina())
    input("\nEnter para fechar o navegador...")


def main():
    parser = argparse.ArgumentParser(description="Mapeamento e teste dos formulários do ServiceNow")
    parser.add_argument("--testar", metavar="JSON", help="JSON de alerta para preencher o formulário do tipo")
    parser.add_argument("--enviar", action="store_true", help="com --testar: envia de verdade")
    args = parser.parse_args()

    configurar_log()
    cfg = config.carregar()
    nav = Navegador(cfg, avisar_login)
    try:
        if args.testar:
            modo_testar(nav, cfg, args.testar, args.enviar)
        else:
            modo_mapear(nav, cfg)
    except Exception as e:
        print(f"\nErro: {e}")
        input("Enter para sair...")
        sys.exit(1)
    finally:
        nav.fechar()


if __name__ == "__main__":
    main()
