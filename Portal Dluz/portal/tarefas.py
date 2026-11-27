"""Execução das ferramentas em segundo plano.

Cada clique em "Gerar" vira uma tarefa: os parâmetros e arquivos enviados vão
para DATA_DIR/tarefas/<id>/, a ferramenta roda numa thread e tudo o que ela
gravar nessa pasta vira um arquivo para baixar. O que os scripts imprimem
(print) aparece no registro da tarefa, na tela, enquanto ela roda.
"""

import io
import json
import shutil
import sys
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from . import db, erp
from .carregador import carregar, preparar

PASTA_ENTRADA = "_entrada"
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tarefa")
_local = threading.local()
_logs_vivos = {}  # id -> lista de linhas, enquanto a tarefa roda
_trava_logs = threading.Lock()


class _SaidaPorTarefa(io.TextIOBase):
    """Substitui sys.stdout: o que uma tarefa imprime vai para o registro dela."""

    def __init__(self, original):
        self.original = original

    def write(self, texto):
        tid = getattr(_local, "tarefa", None)
        if tid is None:
            return self.original.write(texto)
        with _trava_logs:
            linhas = _logs_vivos.setdefault(tid, [""])
            partes = texto.split("\n")
            linhas[-1] += partes[0]
            linhas.extend(partes[1:])
        return len(texto)

    def flush(self):
        if getattr(_local, "tarefa", None) is None:
            self.original.flush()

    @property
    def encoding(self):
        return getattr(self.original, "encoding", "utf-8")


def _instalar_saida():
    if not isinstance(sys.stdout, _SaidaPorTarefa):
        sys.stdout = _SaidaPorTarefa(sys.stdout)


class ErroAmigavel(Exception):
    """Erro de uso (ex.: nenhum dado no período): mostrado sem o rastro técnico."""


class Contexto:
    """O que cada ferramenta recebe para trabalhar."""

    def __init__(self, tid, pasta, params, empresas, usuario):
        self.id = tid
        self.pasta = Path(pasta)
        self.data_dir = self.pasta.parent.parent  # DATA_DIR/tarefas/<id>
        self.params = params
        self.empresas = empresas  # {sigla: {nome, host, real, fiscal}}
        self.usuario = usuario
        self.resumo = None
        self.pagina = None

    def log(self, msg=""):
        print(msg)

    def arquivo(self, nome):
        return self.pasta / nome

    def script(self, arquivo):
        return carregar(arquivo, self.empresas)

    def entradas(self, campo):
        """Caminhos dos arquivos enviados num campo de upload."""
        return [self.pasta / PASTA_ENTRADA / n for n in self.params.get(campo) or []]


def pasta_tarefas(data_dir):
    p = Path(data_dir) / "tarefas"
    p.mkdir(parents=True, exist_ok=True)
    return p


def nova_tarefa(app, ferramenta, usuario, params, arquivos_enviados):
    """Cria a tarefa, salva os uploads e põe na fila. Devolve o id."""
    tid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    pasta = pasta_tarefas(app.config["DATA_DIR"]) / tid
    (pasta / PASTA_ENTRADA).mkdir(parents=True)
    for campo, lista in arquivos_enviados.items():
        nomes = []
        for nome_seguro, arq in lista:
            destino = pasta / PASTA_ENTRADA / nome_seguro
            n = 1
            while destino.exists():
                destino = destino.with_name(f"{destino.stem}_{n}{destino.suffix}")
                n += 1
            arq.save(destino)
            nomes.append(destino.name)
        params[campo] = nomes
    conn = db.conectar(app.config["DB_PATH"])
    try:
        conn.execute(
            "INSERT INTO tarefa (id, ferramenta, usuario_id, parametros, situacao, criada_em) VALUES (?, ?, ?, ?, 'fila', ?)",
            (tid, ferramenta.id, usuario["id"], json.dumps(params, ensure_ascii=False), db.agora()),
        )
        conn.commit()
        empresas = erp.empresas(conn)
    finally:
        conn.close()
    usuario_dict = {k: usuario[k] for k in usuario.keys() if k != "senha_hash"}
    _instalar_saida()
    preparar()  # imports pesados antes da primeira thread (evita deadlock de import)
    _executor.submit(_rodar, app.config["DB_PATH"], ferramenta, tid, pasta, params, empresas, usuario_dict)
    return tid


def _rodar(db_path, ferramenta, tid, pasta, params, empresas, usuario):
    _local.tarefa = tid
    with _trava_logs:
        _logs_vivos[tid] = [""]
    conn = db.conectar(db_path)
    situacao, erro = "ok", None
    ctx = Contexto(tid, pasta, params, empresas, usuario)
    try:
        conn.execute("UPDATE tarefa SET situacao = 'rodando', iniciada_em = ? WHERE id = ?", (db.agora(), tid))
        conn.commit()
        ferramenta.executar(ctx)
    except ErroAmigavel as e:
        situacao, erro = "erro", str(e)
    except SystemExit as e:
        situacao, erro = "erro", str(e.code or "O script foi interrompido.")
    except Exception as e:
        situacao = "erro"
        erro = _mensagem_de_erro(e)
        print(traceback.format_exc())
    finally:
        _local.tarefa = None
        with _trava_logs:
            log = "\n".join(_logs_vivos.pop(tid, [])).strip()
        conn.execute(
            "UPDATE tarefa SET situacao = ?, terminada_em = ?, log = ?, erro = ?, resumo = ?, pagina = ? WHERE id = ?",
            (situacao, db.agora(), log[-200_000:], erro, ctx.resumo, ctx.pagina, tid),
        )
        conn.commit()
        conn.close()


def _mensagem_de_erro(e):
    texto = f"{type(e).__name__}: {e}"
    baixo = texto.lower()
    if any(p in baixo for p in ("timed out", "connection refused", "unreachable", "10061", "10060", "no route")):
        return ("Não foi possível conectar ao banco do ATS. Confira se o servidor está ligado e na rede, "
                "e os endereços em Configurações. Detalhe: " + texto)
    return texto


def log_ao_vivo(tid):
    with _trava_logs:
        linhas = _logs_vivos.get(tid)
        return None if linhas is None else "\n".join(linhas)


def arquivos_gerados(pasta):
    pasta = Path(pasta)
    if not pasta.exists():
        return []
    return sorted(
        (p for p in pasta.iterdir() if p.is_file() and not p.name.startswith((".", "~$"))),
        key=lambda p: p.name,
    )


def limpar_antigas(app, dias=30):
    """Apaga arquivos de tarefas com mais de `dias` (o registro fica no banco)."""
    limite = (datetime.now() - timedelta(days=dias)).strftime("%Y%m%d")
    for p in pasta_tarefas(app.config["DATA_DIR"]).iterdir():
        if p.is_dir() and p.name[:8] < limite:
            shutil.rmtree(p, ignore_errors=True)
