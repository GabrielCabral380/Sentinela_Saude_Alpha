"""Carregamento e validação da configuração do Sentinela."""

import copy
import json
import os
import sys
from pathlib import Path

PADROES = {
    "intervalo_segundos": 300,
    "reparo_automatico": True,
    "python": sys.executable,
    "timeout_funcao": 60,
    "logs": {
        "padroes": ["logs/**/*.log", "*.log"],
        "regex_erro": ["Traceback",r"\bERROR\b", r"\bCRITICAL\b", r"\bFATAL\b"],
    },
    "arquivos": {
        "obrigatorios": [],
        "verificar_python": ["**/*.py"],
        "verificar_json": ["**/*.json"],
        "ignorar": [".git/**", "venv/**", ".venv/**", "node_modules/**", "**/__pycache__/**", "**/site-packages/**"],
        "tamanho_maximo_bytes": 5_000_000,
    },
    "funcoes": [],
    "comandos_saude": [],
    "llm": {"habilitado": False, "provedores": []},
}


class ConfigInvalida(ValueError):
    pass


def _mesclar(base: dict, extra: dict) -> dict:
    resultado = copy.deepcopy(base)
    for chave, valor in extra.items():
        if isinstance(valor, dict) and isinstance(resultado.get(chave), dict):
            resultado[chave] = _mesclar(resultado[chave], valor)
        else:
            resultado[chave] = valor
    return resultado


def carregar_config(caminho) -> dict:
    caminho = Path(caminho)
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ConfigInvalida(f"arquivo de config não encontrado: {caminho}") from None
    except json.JSONDecodeError as erro:
        raise ConfigInvalida(f"config com JSON inválido em {caminho}: {erro}") from None
    if not isinstance(bruto, dict):
        raise ConfigInvalida("config deve ser um objeto JSON")

    cfg = _mesclar(PADROES, bruto)
    alvo = os.environ.get("SENTINELA_ALVO") or cfg.get("alvo")
    if not alvo:
        raise ConfigInvalida("campo 'alvo' (pasta da IA Alpha) é obrigatório")
    alvo = Path(alvo).expanduser()
    if not alvo.is_absolute():
        alvo = caminho.parent / alvo
    if not alvo.is_dir():
        raise ConfigInvalida(f"pasta alvo não existe: {alvo}")
    cfg["alvo"] = alvo.resolve()

    pasta_estado = Path(cfg.get("pasta_estado") or ".sentinela")
    if not pasta_estado.is_absolute():
        pasta_estado = caminho.parent / pasta_estado
    cfg["pasta_estado"] = pasta_estado.resolve()

    intervalo = cfg["intervalo_segundos"]
    if isinstance(intervalo, bool) or not isinstance(intervalo, (int, float)) or intervalo <= 0:
        raise ConfigInvalida("intervalo_segundos deve ser um número maior que zero")

    for item in cfg["funcoes"]:
        if not isinstance(item, dict) or not item.get("modulo") or not item.get("funcao"):
            raise ConfigInvalida("cada item de 'funcoes' precisa de 'modulo' e 'funcao'")

    for item in cfg["comandos_saude"]:
        comando = item.get("comando") if isinstance(item, dict) else None
        if not item.get("nome") or not isinstance(comando, list) or not comando:
            raise ConfigInvalida("cada item de 'comandos_saude' precisa de 'nome' e 'comando' (lista)")
        if "reparo" in item and (not isinstance(item["reparo"], list) or not item["reparo"]):
            raise ConfigInvalida(f"'reparo' de '{item['nome']}' deve ser uma lista não vazia")

    for provedor in cfg["llm"]["provedores"]:
        if provedor.get("tipo") not in ("cli", "openai") or not provedor.get("nome"):
            raise ConfigInvalida("cada provedor de llm precisa de 'nome' e 'tipo' ('cli' ou 'openai')")
    return cfg
