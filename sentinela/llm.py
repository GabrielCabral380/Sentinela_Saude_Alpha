"""Provedores de LLM em ordem de prioridade (Free Claude Code, FreeLLMAPI, Claude, Hermes...).

Tipos:
- "cli": roda um programa; o prompt vai no stdin, ou no argumento que contiver "{prompt}".
- "openai": POST em <url>/chat/completions (API compatível com OpenAI, como a FreeLLMAPI).
Chaves e URLs vêm de variáveis de ambiente (`chave_env`, `url_env`), nunca do arquivo.
"""

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request

BLOCO_CODIGO = re.compile(r"```[a-zA-Z0-9_+-]*[ \t]*\r?\n(.*?)```", re.S)


class ProvedorIndisponivel(RuntimeError):
    pass


def _perguntar_cli(provedor: dict, prompt: str) -> str:
    comando = list(provedor["comando"])
    if not shutil.which(comando[0]):
        raise ProvedorIndisponivel(f"executável não encontrado: {comando[0]}")
    usa_argumento = any("{prompt}" in arg for arg in comando)
    comando = [arg.replace("{prompt}", prompt) for arg in comando]
    ambiente = dict(os.environ, **{k: str(v) for k, v in provedor.get("ambiente", {}).items()})
    r = subprocess.run(
        comando, input=None if usa_argumento else prompt, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=provedor.get("timeout", 600), env=ambiente,
    )
    if r.returncode != 0:
        raise RuntimeError(f"saiu com código {r.returncode}: {r.stderr.strip()[-500:]}")
    return r.stdout


def _perguntar_openai(provedor: dict, prompt: str) -> str:
    url = os.environ.get(provedor["url_env"], "") if provedor.get("url_env") else provedor.get("url", "")
    if not url:
        raise ProvedorIndisponivel(f"URL não definida (variável {provedor.get('url_env')})")
    cabecalhos = {"Content-Type": "application/json"}
    if provedor.get("chave_env"):
        chave = os.environ.get(provedor["chave_env"])
        if not chave:
            raise ProvedorIndisponivel(f"variável {provedor['chave_env']} não definida")
        cabecalhos["Authorization"] = f"Bearer {chave}"
    corpo = json.dumps({
        "model": provedor.get("modelo", "auto"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode("utf-8")
    pedido = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=corpo, headers=cabecalhos)
    try:
        with urllib.request.urlopen(pedido, timeout=provedor.get("timeout", 300)) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
    except urllib.error.URLError as erro:
        raise RuntimeError(f"falha HTTP: {erro}") from erro
    return dados["choices"][0]["message"]["content"]


def extrair_codigo(texto: str) -> str | None:
    blocos = BLOCO_CODIGO.findall(texto)
    return max(blocos, key=len) if blocos else None


def pedir_correcao(cfg, prompt: str, falhas: list):
    """Gera (nome_provedor, código) por provedor que respondeu com um bloco de código.

    É um gerador: quem chama valida cada proposta e só pede a próxima se ela for rejeitada.
    `falhas` recebe o motivo de cada provedor que não ajudou.
    """
    for provedor in cfg["llm"]["provedores"]:
        perguntar = _perguntar_cli if provedor["tipo"] == "cli" else _perguntar_openai
        try:
            texto = perguntar(provedor, prompt)
        except (ProvedorIndisponivel, RuntimeError, OSError, subprocess.TimeoutExpired,
                KeyError, IndexError, json.JSONDecodeError) as erro:
            falhas.append(f"{provedor['nome']}: {erro}")
            continue
        codigo = extrair_codigo(texto)
        if codigo is None:
            falhas.append(f"{provedor['nome']}: resposta sem bloco de código")
            continue
        yield provedor["nome"], codigo
