"""Pastas da fila, reserva de arquivos, histórico de ids (idempotência) e JSON de retorno.

Ciclo do arquivo: pendentes -> processando -> processados | erro  (+ um arquivo em retorno)
"""
import json
import os
import socket
import time
from datetime import datetime
from pathlib import Path

from log import log

HOST = socket.gethostname().upper()
SEP = "__"  # separa o nome original do hostname no arquivo reservado: <nome>__<HOST>.json


def agora_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ler_json(caminho: Path) -> dict:
    # utf-8-sig aceita arquivo com ou sem BOM
    dados = json.loads(Path(caminho).read_text(encoding="utf-8-sig"))
    if not isinstance(dados, dict):
        raise ValueError("o conteúdo não é um objeto JSON")
    return dados


def gravar_json(caminho: Path, dados: dict):
    Path(caminho).write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def _linhas_jsonl(texto: str) -> list:
    """Linhas JSON de um .jsonl; linhas quebradas são ignoradas."""
    eventos = []
    for linha in texto.splitlines():
        try:
            e = json.loads(linha)
            if isinstance(e, dict):
                eventos.append(e)
        except ValueError:
            pass
    return eventos


def nome_original(reservado: Path) -> str:
    """processando/INFRA_123__HOST.json -> INFRA_123.json"""
    return reservado.stem.rsplit(SEP, 1)[0] + ".json"


class Historico:
    """processados.jsonl local: uma linha por mudança de status; vale a última linha de cada id.

    Status: enviando (clicou em Enviar, ainda sem número) | ok | erro
    """

    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        self.dados = {}
        if self.caminho.exists():
            for linha in self.caminho.read_text(encoding="utf-8").splitlines():
                try:
                    registro = json.loads(linha)
                    self.dados[registro["id"]] = registro
                except (ValueError, KeyError):
                    pass

    def status(self, id_alerta: str):
        return self.dados.get(id_alerta, {}).get("status")

    def registro(self, id_alerta: str) -> dict:
        return self.dados.get(id_alerta, {})

    def marcar(self, id_alerta: str, status: str, **extra):
        registro = {"id": id_alerta, "status": status, "em": agora_iso(), "maquina": HOST, **extra}
        with open(self.caminho, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())  # garante que está no disco antes de clicar em Enviar
        self.dados[id_alerta] = registro


class PastaIndisponivel(Exception):
    pass


class Fila:
    def __init__(self, raiz: str):
        self.raiz = Path(raiz)
        self.pendentes = self.raiz / "pendentes"
        self.processando = self.raiz / "processando"
        self.processados = self.raiz / "processados"
        self.erro = self.raiz / "erro"
        self.retorno = self.raiz / "retorno"
        self.status = self.raiz / "status"
        self.plantao = self.raiz / "plantao"      # pedidos de "assumir o plantão": plantao/<HOST do alvo>.json
        self.historico = self.raiz / "historico"  # um arquivo por dia e por máquina: <AAAA-MM-DD>_<HOST>.jsonl

    def verificar_pasta(self):
        """Chamado a cada ciclo. Nunca cria a raiz: se ela sumiu, o OneDrive não está sincronizando."""
        if not self.raiz.is_dir():
            raise PastaIndisponivel(f"Pasta sincronizada inacessível: {self.raiz}. Verifique se o OneDrive está rodando.")
        for pasta in (self.pendentes, self.processando, self.processados, self.erro, self.retorno, self.status,
                      self.plantao, self.historico):
            pasta.mkdir(exist_ok=True)

    def ler(self, arquivo: Path) -> dict:
        return ler_json(arquivo)

    def arquivo_parar(self):
        from emergencia import arquivo_parar
        return arquivo_parar(str(self.raiz))

    def endereco_web(self) -> str:
        return str(self.raiz)

    def listar_pendentes(self, idade_minima_seg: float) -> list:
        """JSONs em /pendentes, mais antigos primeiro. Ignora arquivos recém-criados (podem estar incompletos)."""
        agora = time.time()
        arquivos = []
        for p in self.pendentes.glob("*.json"):
            if p.name.startswith(("~", ".")):
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if agora - mtime >= idade_minima_seg:
                arquivos.append((mtime, p))
        return [p for _, p in sorted(arquivos)]

    def reservar(self, arquivo: Path):
        """Move para /processando com o hostname no nome. Retorna o novo caminho ou None se não conseguiu."""
        destino = self.processando / f"{arquivo.stem}{SEP}{HOST}.json"
        try:
            os.rename(arquivo, destino)
            os.utime(destino)  # a data do arquivo passa a ser a hora da reserva (usada para achar reservas abandonadas)
            return destino
        except OSError as e:
            # Arquivo sumiu (outra máquina pegou) ou está bloqueado pelo OneDrive
            log.info("Não foi possível reservar %s: %s", arquivo.name, e)
            return None

    def confirmar_reserva(self, reservado: Path, espera_seg: float) -> bool:
        """Espera a sincronização e confere se outra máquina reservou o mesmo arquivo.
        Desempate: vence o menor hostname."""
        if espera_seg > 0:
            time.sleep(espera_seg)
        if not reservado.exists():
            return False
        prefixo = reservado.stem.rsplit(SEP, 1)[0] + SEP
        hosts = sorted(
            p.stem.rsplit(SEP, 1)[1]
            for p in self.processando.glob("*.json")
            if p.stem.startswith(prefixo)
        )
        if hosts and hosts[0] != HOST:
            log.warning("%s também foi reservado por %s; deixando para essa máquina.", nome_original(reservado), hosts[0])
            reservado.unlink(missing_ok=True)
            return False
        return True

    def devolver(self, reservado: Path):
        """Devolve para /pendentes para tentar de novo no próximo ciclo."""
        os.replace(reservado, self.pendentes / nome_original(reservado))

    def tratar_reservas_abandonadas(self, minutos: float):
        """Reservas de OUTRAS máquinas paradas há muito tempo (máquina desligou no meio).
        Vão para /erro, e não para /pendentes: o chamado pode ter sido aberto antes da queda."""
        limite = time.time() - minutos * 60
        for reservado in self.processando.glob("*.json"):
            if SEP not in reservado.stem:
                continue
            host = reservado.stem.rsplit(SEP, 1)[1]
            try:
                if host == HOST or reservado.stat().st_mtime > limite:
                    continue
                alerta = ler_json(reservado)
            except Exception:
                alerta = None
            if not reservado.exists():
                continue
            log.warning("Reserva abandonada por %s: %s", host, reservado.name)
            self.mover_para_erro(
                reservado, alerta,
                f"A máquina {host} reservou este alerta e não terminou em {minutos:.0f} min. "
                "Confira no ServiceNow se o chamado foi aberto antes de reprocessar.",
            )

    def gravar_status(self, dados: dict):
        """Batimento desta máquina em /status/<HOST>.json: mostra ao time qual robô está ativo."""
        gravar_json(self.status / f"{HOST}.json", dict(dados, host=HOST, atualizado_em=agora_iso(), ts=time.time()))

    def status_outros(self, max_idade_seg: float) -> list:
        """Status das outras máquinas com sinal recente (ativas e pausadas)."""
        lista = []
        for arquivo in self.status.glob("*.json"):
            if arquivo.stem == HOST:
                continue
            try:
                d = ler_json(arquivo)
                if time.time() - float(d.get("ts", 0)) < max_idade_seg:
                    lista.append(dict(d, host=d.get("host") or arquivo.stem))
            except Exception:
                pass
        return lista

    def esquecer_status_outros(self):
        pass  # na pasta local não há cache

    # --- assumir o plantão ---

    def pedir_plantao(self, alvo: str, dados: dict):
        """Pede ao robô da máquina alvo que pause (ele lê plantao/<alvo>.json)."""
        gravar_json(self.plantao / f"{alvo}.json", dict(dados, de_host=HOST, em=agora_iso(), ts=time.time()))

    def ler_pedido_plantao(self):
        """Pedido para esta máquina pausar, ou None."""
        try:
            return ler_json(self.plantao / f"{HOST}.json")
        except (OSError, ValueError):
            return None

    def apagar_pedido_plantao(self):
        (self.plantao / f"{HOST}.json").unlink(missing_ok=True)

    # --- histórico do time ---

    def gravar_historico(self, dia: str, eventos: list):
        """Acrescenta eventos ao arquivo do dia desta máquina."""
        with open(self.historico / f"{dia}_{HOST}.jsonl", "a", encoding="utf-8") as f:
            for e in eventos:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def ler_historico(self, dia: str) -> list:
        """Eventos do dia de todas as máquinas, em ordem de hora."""
        eventos = []
        for arquivo in self.historico.glob(f"{dia}_*.jsonl"):
            try:
                eventos += _linhas_jsonl(arquivo.read_text(encoding="utf-8-sig"))
            except OSError:
                pass
        return sorted(eventos, key=lambda e: str(e.get("em", "")))

    def recuperar_reservas(self, historico: Historico):
        """Trata arquivos desta máquina que ficaram em /processando (robô fechado no meio, ou falha
        ao gravar o resultado depois de abrir o chamado). Roda no início de cada ciclo."""
        for reservado in self.processando.glob(f"*{SEP}{HOST}.json"):
            try:
                alerta = ler_json(reservado)
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
                os.replace(reservado, self.pendentes / nome_original(reservado))

    def concluir(self, reservado: Path, alerta: dict, numero: str, url: str, gravar_retorno: bool = True):
        nome = nome_original(reservado)
        resultado = {"numero": numero, "url": url, "maquina": HOST, "processado_em": agora_iso()}
        gravar_json(self.processados / (f"{numero}_{nome}" if numero else nome), dict(alerta, resultado=resultado))
        reservado.unlink(missing_ok=True)
        if gravar_retorno:
            self.gravar_retorno(nome, alerta, "ok", numero=numero, url=url)

    def mover_para_erro(self, reservado: Path, alerta, mensagem: str):
        nome = nome_original(reservado)
        destino = self.erro / nome
        if alerta is None:
            # JSON ilegível: move como veio e grava o motivo ao lado
            os.replace(reservado, destino)
            destino.with_suffix(".erro.txt").write_text(mensagem, encoding="utf-8")
            return
        erro = {"mensagem": mensagem, "maquina": HOST, "em": agora_iso()}
        gravar_json(destino, dict(alerta, erro=erro))
        reservado.unlink(missing_ok=True)
        self.gravar_retorno(nome, alerta, "erro", mensagem=mensagem)

    def gravar_retorno(self, nome: str, alerta: dict, status: str, numero: str = "", url: str = "", mensagem: str = ""):
        gravar_json(self.retorno / nome, {
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
