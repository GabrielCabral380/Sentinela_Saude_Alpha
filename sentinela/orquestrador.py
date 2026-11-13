"""Ciclo verificar → reparar → verificar de novo → registrar, e o loop contínuo."""

import json
import sys
import time
import traceback

from .reparos import Reparador, escrever_atomico
from .trava import Trava, TravaOcupada
from .verificacoes import listar_arquivos, verificar_estrutura, verificar_logs


def _ler_estado(pasta) -> dict:
    caminho = pasta / "estado.json"
    if not caminho.is_file():
        return {}
    return json.loads(caminho.read_text(encoding="utf-8"))


def _tirar_snapshot(cfg) -> int:
    """Guarda a cópia dos arquivos monitorados; só é chamado quando o ciclo terminou saudável."""
    arquivos = {cfg["alvo"] / r for r in cfg["arquivos"]["obrigatorios"]}
    arquivos.update(listar_arquivos(cfg, cfg["arquivos"]["verificar_python"]))
    arquivos.update(listar_arquivos(cfg, cfg["arquivos"]["verificar_json"]))
    copiados = 0
    for caminho in arquivos:
        relativo = caminho.resolve().relative_to(cfg["alvo"])
        destino = cfg["pasta_estado"] / "snapshots" / relativo
        conteudo = caminho.read_bytes()
        if not destino.is_file() or destino.read_bytes() != conteudo:
            escrever_atomico(destino, conteudo)
            copiados += 1
    return copiados


def executar_ciclo(cfg) -> dict:
    pasta = cfg["pasta_estado"]
    with Trava(pasta / "sentinela.lock"):
        inicio = time.time()
        estado = _ler_estado(pasta)
        problemas = verificar_estrutura(cfg) + verificar_logs(cfg, estado)
        reparos = []
        if problemas and cfg["reparo_automatico"]:
            reparador = Reparador(cfg, problemas)
            for problema in problemas:
                reparos += reparador.reparar(problema)
            corrigidos = {tuple(r["problema"]) for r in reparos if r["sucesso"]}
            logs_pendentes = [p for p in problemas if p.tipo == "erro_log" and p.chave not in corrigidos]
            restantes = verificar_estrutura(cfg) + logs_pendentes
        else:
            restantes = problemas

        relatorio = {
            "inicio": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(inicio)),
            "duracao_segundos": round(time.time() - inicio, 2),
            "saudavel": not restantes,
            "problemas_encontrados": [p.como_dict() for p in problemas],
            "reparos": reparos,
            "problemas_restantes": [p.como_dict() for p in restantes],
            "arquivos_no_snapshot": _tirar_snapshot(cfg) if not restantes else 0,
        }
        estado["ultimo_ciclo"] = {k: relatorio[k] for k in ("inicio", "saudavel")}
        escrever_atomico(pasta / "estado.json", json.dumps(estado, ensure_ascii=False, indent=2).encode("utf-8"))
        with open(pasta / "relatorios.jsonl", "a", encoding="utf-8") as historico:
            historico.write(json.dumps(relatorio, ensure_ascii=False) + "\n")
    return relatorio


def executar_loop(cfg, max_ciclos=None, dormir=time.sleep, ao_terminar_ciclo=None) -> list[dict]:
    """Roda ciclos para sempre (ou `max_ciclos`). Uma falha inesperada num ciclo é registrada
    no stderr e o loop segue: o vigia não pode morrer por causa do que vigia."""
    relatorios = []
    ciclo = 0
    while max_ciclos is None or ciclo < max_ciclos:
        ciclo += 1
        try:
            relatorio = executar_ciclo(cfg)
        except TravaOcupada as erro:
            relatorio = {"saudavel": None, "ignorado": str(erro)}
        except Exception as erro:  # registrado e reportado; o próximo ciclo tenta de novo
            traceback.print_exc(file=sys.stderr)
            relatorio = {"saudavel": False, "erro_interno": f"{type(erro).__name__}: {erro}"}
        if ao_terminar_ciclo:
            ao_terminar_ciclo(relatorio)
        if max_ciclos is not None:
            relatorios.append(relatorio)
        if max_ciclos is None or ciclo < max_ciclos:
            dormir(cfg["intervalo_segundos"])
    return relatorios
