"""Uso:
    python -m sentinela --config sentinela.json --uma-vez     # um ciclo; sai 0 se saudável, 1 se não
    python -m sentinela --config sentinela.json               # loop contínuo (Ctrl+C para parar)
    python -m sentinela --config sentinela.json --max-ciclos 3
Saída 2: configuração inválida. Saída 3: outra instância já está rodando um ciclo.
"""

import argparse
import json
import sys

from .config import ConfigInvalida, carregar_config
from .orquestrador import executar_ciclo, executar_loop
from .trava import TravaOcupada


def _resumo(relatorio: dict) -> str:
    resumo = {k: v for k, v in relatorio.items() if k not in ("problemas_encontrados", "problemas_restantes")}
    resumo["problemas_restantes"] = [f"{p['tipo']}: {p['alvo']}" for p in relatorio.get("problemas_restantes", [])]
    return json.dumps(resumo, ensure_ascii=False)


def main(argv=None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        fluxo.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="sentinela", description="Sentinela de saúde da IA Alpha")
    parser.add_argument("--config", default="sentinela.json")
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--uma-vez", action="store_true", help="roda um único ciclo")
    modo.add_argument("--max-ciclos", type=int, help="para depois de N ciclos")
    args = parser.parse_args(argv)
    if args.max_ciclos is not None and args.max_ciclos < 1:
        parser.error("--max-ciclos deve ser pelo menos 1")

    try:
        cfg = carregar_config(args.config)
    except ConfigInvalida as erro:
        print(f"config inválida: {erro}", file=sys.stderr)
        return 2

    if args.uma_vez:
        try:
            relatorio = executar_ciclo(cfg)
        except TravaOcupada as erro:
            print(erro, file=sys.stderr)
            return 3
        print(_resumo(relatorio))
        return 0 if relatorio["saudavel"] else 1

    try:
        relatorios = executar_loop(cfg, args.max_ciclos, ao_terminar_ciclo=lambda r: print(_resumo(r), flush=True))
    except KeyboardInterrupt:
        print("Sentinela encerrado pelo usuário.", file=sys.stderr)
        return 0
    return 0 if relatorios and relatorios[-1]["saudavel"] else 1


if __name__ == "__main__":
    sys.exit(main())
