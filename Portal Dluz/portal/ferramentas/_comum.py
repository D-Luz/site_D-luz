"""Ajudantes usados por várias ferramentas."""

import contextlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from .. import erp, tarefas
from ..tarefas import ErroAmigavel
from . import TODAS

_trava_pasta = threading.Lock()


def siglas(valor, permitidas):
    """'TODAS' vira a lista de siglas permitidas; uma sigla vira [sigla]."""
    if valor == TODAS:
        return list(permitidas)
    if valor not in permitidas:
        raise ErroAmigavel("Empresa inválida.")
    return [valor]


def do_script(empresas_script, escolhidas):
    """[(chave, cfg)] do dicionário EMPRESAS de um script, nas siglas escolhidas e nessa ordem."""
    por_sigla = {}
    for chave, cfg in empresas_script.items():
        nome = cfg.get("nome", chave) if isinstance(cfg, dict) else chave
        sigla = erp.chave_da_empresa(nome) or erp.chave_da_empresa(chave)
        if sigla:
            por_sigla.setdefault(sigla, (chave, cfg))
    return [por_sigla[s] for s in escolhidas if s in por_sigla]


def em_paralelo(alvos, funcao, rotulo=lambda a: a[0]):
    """Roda funcao(alvo) para cada alvo; devolve (resultados, falhas) e registra o andamento."""
    resultados, falhas = [], []
    if not alvos:
        return resultados, falhas
    tid = getattr(tarefas._local, "tarefa", None)

    def rodar(alvo):
        tarefas._local.tarefa = tid  # o que a thread imprimir vai para o registro da tarefa
        try:
            return funcao(alvo)
        finally:
            tarefas._local.tarefa = None

    with ThreadPoolExecutor(max_workers=len(alvos)) as pool:
        futuros = {pool.submit(rodar, a): a for a in alvos}
        for fut in as_completed(futuros):
            nome = rotulo(futuros[fut])
            try:
                resultados.append((futuros[fut], fut.result()))
                print(f"✔ {nome}")
            except Exception as exc:
                falhas.append((nome, str(exc)))
                print(f"✗ {nome}: {exc}")
    ordem = {id(a): i for i, a in enumerate(alvos)}
    resultados.sort(key=lambda r: ordem[id(r[0])])
    return resultados, falhas


def juntar(resultados, falhas, vazio_msg="Nenhum registro encontrado no período."):
    frames = [df for _, df in resultados if df is not None and not df.empty]
    if not frames:
        if falhas:
            raise ErroAmigavel(
                "Não foi possível consultar: " + "; ".join(f"{n} ({e})" for n, e in falhas)
            )
        raise ErroAmigavel(vazio_msg)
    return pd.concat(frames, ignore_index=True)


@contextlib.contextmanager
def na_pasta(pasta):
    """Para scripts que gravam na pasta atual: troca o diretório com exclusividade."""
    with _trava_pasta:
        antes = os.getcwd()
        os.chdir(pasta)
        try:
            yield
        finally:
            os.chdir(antes)


def aviso_falhas(falhas):
    if falhas:
        return " Atenção — não consultadas: " + "; ".join(n for n, _ in falhas) + "."
    return ""
