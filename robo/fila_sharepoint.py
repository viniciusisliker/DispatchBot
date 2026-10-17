"""Fila direto no SharePoint (API REST), sem OneDrive.

Mesma interface da Fila de pasta local (fila.py). A autenticação usa os cookies da sessão Microsoft
do navegador do robô (FedAuth/rtFa): o robô nunca vê nem guarda senha.

Ciclo do arquivo: pendentes -> processando -> processados | erro  (+ um arquivo em retorno)
"""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import quote, urlparse

from fila import HOST, SEP, FilaInstavel, PastaIndisponivel, agora_iso, nome_original
from log import log

SUBPASTAS = ("pendentes", "processando", "processados", "erro", "retorno", "status", "plantao", "historico")


class NaoEncontrado(Exception):
    pass


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    """Sessão vencida faz o SharePoint redirecionar para o login: tratamos como 401, não seguimos."""
    def redirect_request(self, *args, **kwargs):
        return None


_abridor = urllib.request.build_opener(_SemRedirecionar)


class ArquivoSP:
    """Arquivo da fila no SharePoint (faz o papel do Path da fila local)."""

    def __init__(self, url: str, mtime: float = 0.0):
        self.url = url  # caminho relativo ao servidor: /sites/X/Documentos/.../pendentes/A.json
        self.name = url.rsplit("/", 1)[1]
        self.stem = self.name.rsplit(".", 1)[0]
        self.mtime = mtime

    def __repr__(self):
        return self.name


def _caminho(url: str) -> str:
    """Caminho para dentro de decodedurl='...': aspas simples dobradas e o resto codificado."""
    return quote(url.replace("'", "''"), safe="/")


def _data(texto: str) -> float:
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


class FilaSharePoint:
    def __init__(self, site: str, pasta: str, obter_cookies):
        """site: https://empresa.sharepoint.com/sites/TIME
        pasta: caminho relativo ao servidor da DispatchBot-Fila (/sites/TIME/Documentos Compartilhados/...)
        obter_cookies(renovar) -> lista de cookies do navegador (só pode ser chamado pela thread do robô)."""
        self.site = site.rstrip("/")
        self.raiz = "/" + pasta.strip("/")
        self.obter_cookies = obter_cookies
        self._cookie = ""
        self._digest = ("", 0.0)
        self._ultima_renovacao = 0.0
        self._pasta_ok_em = 0.0
        self._outros = ([], 0.0)
        self._hist = ("", [])  # dia e linhas do histórico desta máquina já gravadas no SharePoint

    # --- HTTP ---

    def _carregar_cookies(self, renovar: bool):
        cookies = self.obter_cookies(renovar)
        self._cookie = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        self._digest = ("", 0.0)
        if renovar:
            self._ultima_renovacao = time.time()

    def _req(self, metodo: str, api: str, corpo: bytes = None, extra: dict = None, pode_renovar: bool = True) -> bytes:
        """Chama {site}/_api/{api}. Sessão vencida: renova os cookies uma vez e repete.
        pode_renovar=False: chamadas de outras threads (não podem mexer no navegador)."""
        if not self._cookie:
            if not pode_renovar:
                raise PastaIndisponivel("Sessão do SharePoint ainda não carregada.")
            self._carregar_cookies(False)
        cabecalhos = {"Accept": "application/json;odata=nometadata", "Cookie": self._cookie}
        if metodo == "POST" and api != "contextinfo":
            cabecalhos["X-RequestDigest"] = self._form_digest()
        cabecalhos.update(extra or {})
        if metodo == "POST" and corpo is None:
            corpo = b""
        pedido = urllib.request.Request(f"{self.site}/_api/{api}", data=corpo, headers=cabecalhos, method=metodo)
        try:
            with _abridor.open(pedido, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NaoEncontrado(api) from e
            if e.code in (301, 302, 401, 403):
                # Sessão vencida (ou digest vencido). Renova no máximo uma vez a cada 2 min,
                # para não ficar abrindo o navegador em loop se o problema for falta de permissão.
                if pode_renovar and time.time() - self._ultima_renovacao > 120:
                    log.info("Sessão do SharePoint vencida (HTTP %s); renovando.", e.code)
                    self._carregar_cookies(True)
                    return self._req(metodo, api, corpo, extra, pode_renovar=False)
                raise PastaIndisponivel(
                    f"Sem acesso à pasta da fila no SharePoint (HTTP {e.code}). Confira se a conta "
                    "logada no navegador do robô tem acesso ao site do time.") from e
            tecnico = f"SharePoint respondeu HTTP {e.code} em {api.split('(')[0]}."
            if e.code == 429 or e.code >= 500:  # sobrecarga ou fora do ar do lado da Microsoft: passa sozinho
                raise FilaInstavel("O SharePoint da Microsoft está instável. O robô tenta de novo sozinho, "
                                   "não precisa fazer nada.", tecnico) from e
            raise PastaIndisponivel(f"O SharePoint recusou o pedido do robô (HTTP {e.code}). "
                                    "Se continuar, avise o time de RPA.") from e
        except (urllib.error.URLError, OSError) as e:
            raise FilaInstavel("Sem conexão com o SharePoint (internet ou VPN). O robô tenta de novo sozinho.",
                               f"Sem conexão com o SharePoint: {getattr(e, 'reason', e)}") from e

    def _form_digest(self) -> str:
        """Token exigido pelo SharePoint em toda gravação; vale ~30 min."""
        valor, validade = self._digest
        if valor and time.time() < validade:
            return valor
        d = json.loads(self._req("POST", "contextinfo"))
        self._digest = (d["FormDigestValue"], time.time() + int(d.get("FormDigestTimeoutSeconds", 1800)) - 120)
        return self._digest[0]

    def _pasta(self, sub: str = "") -> str:
        return f"{self.raiz}/{sub}" if sub else self.raiz

    def _listar(self, sub: str = "", pode_renovar: bool = True) -> list:
        api = (f"web/GetFolderByServerRelativePath(decodedurl='{_caminho(self._pasta(sub))}')/Files"
               "?$select=Name,ServerRelativeUrl,TimeLastModified")
        dados = json.loads(self._req("GET", api, pode_renovar=pode_renovar))
        return [ArquivoSP(f["ServerRelativeUrl"], _data(f.get("TimeLastModified"))) for f in dados.get("value", [])]

    def _ler_bytes(self, url: str, pode_renovar: bool = True) -> bytes:
        return self._req("GET", f"web/GetFileByServerRelativePath(decodedurl='{_caminho(url)}')/$value",
                         pode_renovar=pode_renovar)

    def _gravar(self, sub: str, nome: str, conteudo: bytes):
        self._req("POST", f"web/GetFolderByServerRelativePath(decodedurl='{_caminho(self._pasta(sub))}')"
                          f"/Files/AddUsingPath(decodedurl='{_caminho(nome)}',overwrite=true)", conteudo)

    def _gravar_json(self, sub: str, nome: str, dados: dict):
        self._gravar(sub, nome, json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8"))

    def _mover(self, url: str, destino: str, sobrescrever: bool):
        """MoveTo é atômico no servidor: se outra máquina já moveu o arquivo, este falha com 404."""
        self._req("POST", f"web/GetFileByServerRelativePath(decodedurl='{_caminho(url)}')"
                          f"/MoveTo(newurl=@d,flags={1 if sobrescrever else 0})?@d='{_caminho(destino)}'")

    def _apagar(self, url: str):
        try:
            self._req("POST", f"web/GetFileByServerRelativePath(decodedurl='{_caminho(url)}')",
                      extra={"X-HTTP-Method": "DELETE", "IF-MATCH": "*"})
        except NaoEncontrado:
            pass

    # --- interface da fila (igual a fila.Fila) ---

    def verificar_pasta(self):
        """A cada 5 min confere a pasta e cria as subpastas que faltarem. Nunca cria a raiz."""
        if time.time() - self._pasta_ok_em < 300:
            return
        try:
            dados = json.loads(self._req(
                "GET", f"web/GetFolderByServerRelativePath(decodedurl='{_caminho(self.raiz)}')/Folders?$select=Name"))
        except NaoEncontrado:
            raise PastaIndisponivel(f"Pasta da fila não encontrada no SharePoint: {self.site} {self.raiz}")
        existentes = {f["Name"].lower() for f in dados.get("value", [])}
        for sub in SUBPASTAS:
            if sub not in existentes:
                log.info("Criando a subpasta %s no SharePoint.", sub)
                self._req("POST", f"web/folders/AddUsingPath(decodedurl='{_caminho(self._pasta(sub))}')")
        self._pasta_ok_em = time.time()

    def ler(self, arquivo: ArquivoSP) -> dict:
        dados = json.loads(self._ler_bytes(arquivo.url).decode("utf-8-sig"))
        if not isinstance(dados, dict):
            raise ValueError("o conteúdo não é um objeto JSON")
        return dados

    def listar_pendentes(self, idade_minima_seg: float) -> list:
        """JSONs em /pendentes, mais antigos primeiro. Ignora arquivos recém-criados."""
        agora = time.time()
        arquivos = [a for a in self._listar("pendentes")
                    if a.name.lower().endswith(".json") and not a.name.startswith(("~", "."))
                    and agora - a.mtime >= idade_minima_seg]
        return sorted(arquivos, key=lambda a: a.mtime)

    def reservar(self, arquivo: ArquivoSP):
        destino = f"{self._pasta('processando')}/{arquivo.stem}{SEP}{HOST}.json"
        try:
            self._mover(arquivo.url, destino, sobrescrever=False)
        except (NaoEncontrado, PastaIndisponivel) as e:
            log.info("Não foi possível reservar %s: %s", arquivo.name, e)
            return None
        reservado = ArquivoSP(destino, time.time())
        try:
            # Regrava o conteúdo para a data do arquivo virar a hora da reserva
            # (é ela que as outras máquinas usam para achar reservas abandonadas)
            self._gravar("processando", reservado.name, self._ler_bytes(destino))
        except Exception as e:
            log.warning("Não foi possível atualizar a data da reserva de %s: %s", arquivo.name, e)
        return reservado

    def confirmar_reserva(self, reservado: ArquivoSP, espera_seg: float) -> bool:
        # No SharePoint o MoveTo é atômico: só uma máquina consegue mover o mesmo arquivo.
        # Não precisa da janela de espera que a pasta sincronizada pelo OneDrive exige.
        return True

    def devolver(self, reservado: ArquivoSP):
        self._mover(reservado.url, f"{self._pasta('pendentes')}/{nome_original(reservado)}", sobrescrever=True)

    def tratar_reservas_abandonadas(self, minutos: float):
        limite = time.time() - minutos * 60
        for reservado in self._listar("processando"):
            if SEP not in reservado.stem:
                continue
            host = reservado.stem.rsplit(SEP, 1)[1]
            if host == HOST or reservado.mtime > limite:
                continue
            try:
                alerta = self.ler(reservado)
            except NaoEncontrado:
                continue  # a outra máquina terminou agora
            except Exception:
                alerta = None
            log.warning("Reserva abandonada por %s: %s", host, reservado.name)
            self.mover_para_erro(
                reservado, alerta,
                f"A máquina {host} reservou este alerta e não terminou em {minutos:.0f} min. "
                "Confira no ServiceNow se o chamado foi aberto antes de reprocessar.",
            )

    def gravar_status(self, dados: dict):
        self._gravar_json("status", f"{HOST}.json", dict(dados, host=HOST, atualizado_em=agora_iso(), ts=time.time()))

    def status_outros(self, max_idade_seg: float) -> list:
        """Status das outras máquinas com sinal recente (ativas e pausadas).
        Lê /status no máximo uma vez por minuto (cada arquivo é uma chamada ao SharePoint)."""
        lista, quando = self._outros
        if time.time() - quando < 60:
            return lista
        lista = []
        for arquivo in self._listar("status"):
            if arquivo.stem == HOST or time.time() - arquivo.mtime > max_idade_seg:
                continue
            try:
                d = self.ler(arquivo)
                if time.time() - float(d.get("ts", 0)) < max_idade_seg:
                    lista.append(dict(d, host=d.get("host") or arquivo.stem))
            except Exception:
                pass
        self._outros = (lista, time.time())
        return lista

    def esquecer_status_outros(self):
        """Força ler /status de novo no próximo pedido (ex.: logo depois de pedir o plantão)."""
        self._outros = (self._outros[0], 0.0)

    # --- assumir o plantão ---

    def pedir_plantao(self, alvo: str, dados: dict):
        """Pede ao robô da máquina alvo que pause (ele lê plantao/<alvo>.json)."""
        self._gravar_json("plantao", f"{alvo}.json", dict(dados, de_host=HOST, em=agora_iso(), ts=time.time()))

    def ler_pedido_plantao(self):
        """Pedido para esta máquina pausar, ou None."""
        try:
            dados = json.loads(self._ler_bytes(f"{self._pasta('plantao')}/{HOST}.json").decode("utf-8-sig"))
            return dados if isinstance(dados, dict) else None
        except (NaoEncontrado, ValueError):
            return None

    def apagar_pedido_plantao(self):
        self._apagar(f"{self._pasta('plantao')}/{HOST}.json")

    # --- histórico do time ---

    def gravar_historico(self, dia: str, eventos: list):
        """Acrescenta eventos ao arquivo do dia desta máquina. O SharePoint não deixa só acrescentar:
        o robô guarda as linhas do dia e regrava o arquivo inteiro (só esta máquina grava nele)."""
        nome = f"{dia}_{HOST}.jsonl"
        if self._hist[0] != dia:
            try:  # robô reaberto no mesmo dia: continua o arquivo que já existe
                anteriores = self._ler_bytes(f"{self._pasta('historico')}/{nome}").decode("utf-8-sig").splitlines()
            except NaoEncontrado:
                anteriores = []
            self._hist = (dia, [l for l in anteriores if l.strip()])
        linhas = self._hist[1] + [json.dumps(e, ensure_ascii=False) for e in eventos]
        self._gravar("historico", nome, ("\n".join(linhas) + "\n").encode("utf-8"))
        self._hist = (dia, linhas)  # só depois de gravar: se falhar, os eventos são tentados de novo

    def ler_historico(self, dia: str) -> list:
        """Eventos do dia de todas as máquinas, em ordem de hora. Pode ser chamado de outra thread
        (não mexe no navegador: sem sessão, dá PastaIndisponivel)."""
        from fila import _linhas_jsonl
        eventos = []
        for arquivo in self._listar("historico", pode_renovar=False):
            if arquivo.name.startswith(f"{dia}_") and arquivo.name.endswith(".jsonl"):
                texto = self._ler_bytes(arquivo.url, pode_renovar=False).decode("utf-8-sig", errors="replace")
                eventos += _linhas_jsonl(texto)
        return sorted(eventos, key=lambda e: str(e.get("em", "")))

    def recuperar_reservas(self, historico):
        """Arquivos desta máquina que ficaram em /processando (robô fechado no meio)."""
        for reservado in self._listar("processando"):
            if not reservado.name.endswith(f"{SEP}{HOST}.json"):
                continue
            try:
                alerta = self.ler(reservado)
            except NaoEncontrado:
                continue
            except Exception:
                alerta = None
            id_alerta = str((alerta or {}).get("id") or "")
            status = historico.status(id_alerta) if id_alerta else None

            if status == "ok":
                reg = historico.registro(id_alerta)
                log.info("Recuperando %s: já tinha chamado %s.", reservado.name, reg.get("numero"))
                # Sem número = ignorado ou aberto à mão: conclui sem retorno (senão o Teams diz "chamado aberto")
                self.concluir(reservado, alerta, reg.get("numero", ""), reg.get("url", ""),
                              gravar_retorno=bool(reg.get("numero")))
            elif status == "enviando":
                self.mover_para_erro(
                    reservado, alerta,
                    "O robô foi interrompido depois de clicar em Enviar. Confira no ServiceNow se o chamado foi aberto.",
                )
            else:
                log.info("Recuperando %s: devolvendo para /pendentes.", reservado.name)
                self.devolver(reservado)

    def concluir(self, reservado: ArquivoSP, alerta: dict, numero: str, url: str, gravar_retorno: bool = True):
        nome = nome_original(reservado)
        resultado = {"numero": numero, "url": url, "maquina": HOST, "processado_em": agora_iso()}
        self._gravar_json("processados", f"{numero}_{nome}" if numero else nome, dict(alerta, resultado=resultado))
        self._apagar(reservado.url)
        if gravar_retorno:
            self.gravar_retorno(nome, alerta, "ok", numero=numero, url=url)

    def mover_para_erro(self, reservado: ArquivoSP, alerta, mensagem: str):
        nome = nome_original(reservado)
        if alerta is None:
            # JSON ilegível: grava o motivo e depois move o arquivo como veio
            # (nessa ordem: se a conexão cair no meio, o arquivo continua em /processando e é refeito)
            self._gravar("erro", nome.rsplit(".", 1)[0] + ".erro.txt", mensagem.encode("utf-8"))
            self._mover(reservado.url, f"{self._pasta('erro')}/{nome}", sobrescrever=True)
            return
        erro = {"mensagem": mensagem, "maquina": HOST, "em": agora_iso()}
        self._gravar_json("erro", nome, dict(alerta, erro=erro))
        self._apagar(reservado.url)
        self.gravar_retorno(nome, alerta, "erro", mensagem=mensagem)

    def gravar_retorno(self, nome: str, alerta: dict, status: str, numero: str = "", url: str = "", mensagem: str = ""):
        self._gravar_json("retorno", nome, {
            "id": alerta.get("id", ""),
            "tipo": alerta.get("tipo", ""),
            "titulo": alerta.get("titulo", ""),  # o Teams mostra o título (no Backup o id é ilegível)
            "status": status,
            "numero": numero,
            "url": url,
            "mensagem": mensagem,
            "maquina": HOST,
            "processado_em": agora_iso(),
            "teams": alerta.get("teams") or {},
        })

    # --- extras usados pelo main ---

    def arquivo_parar(self):
        """Parada remota: arquivo PARAR* na raiz da fila. Chamado por outras threads: nunca renova a sessão.
        Retorna (nome, motivo) ou None."""
        try:
            for arquivo in self._listar(pode_renovar=False):
                if arquivo.name.upper().startswith("PARAR"):
                    try:
                        texto = self._ler_bytes(arquivo.url, pode_renovar=False).decode("utf-8-sig", errors="replace")
                        motivo = texto.strip().splitlines()[0][:200]
                    except Exception:
                        motivo = ""
                    return arquivo.name, motivo
        except Exception:
            pass
        return None

    def endereco_web(self) -> str:
        """Link da pasta para abrir no navegador (menu da bandeja)."""
        origem = "{0.scheme}://{0.netloc}".format(urlparse(self.site))
        return origem + quote(self.raiz, safe="/")
