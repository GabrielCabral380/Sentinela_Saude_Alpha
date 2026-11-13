"""Verificações de saúde da IA Alpha: arquivos, logs, funções e comandos."""

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

LINHA_TRACEBACK = re.compile(r'File "([^"]+)", line \d+')

SCRIPT_FUNCAO = """
import importlib, sys
objeto = importlib.import_module(sys.argv[1])
for parte in sys.argv[2].split("."):
    objeto = getattr(objeto, parte)
if not callable(objeto):
    sys.exit(f"{sys.argv[2]} existe mas não é chamável")
"""


@dataclass
class Problema:
    tipo: str
    alvo: str
    detalhe: str
    arquivo: str | None = None

    @property
    def chave(self) -> tuple:
        return (self.tipo, self.alvo)

    def como_dict(self) -> dict:
        return asdict(self)


def _relativo(cfg, caminho: Path) -> str:
    return caminho.resolve().relative_to(cfg["alvo"]).as_posix()


def _ignorado(cfg, relativo: str) -> bool:
    caminho = PurePosixPath(relativo)
    return any(caminho.full_match(padrao) for padrao in cfg["arquivos"]["ignorar"])


def listar_arquivos(cfg, padroes) -> list[Path]:
    encontrados = {}
    limite = cfg["arquivos"]["tamanho_maximo_bytes"]
    for padrao in padroes:
        for caminho in cfg["alvo"].glob(padrao):
            if not caminho.is_file():
                continue
            relativo = _relativo(cfg, caminho)
            if not _ignorado(cfg, relativo) and caminho.stat().st_size <= limite:
                encontrados[relativo] = caminho
    return [encontrados[k] for k in sorted(encontrados)]


def verificar_python(caminho: Path) -> str | None:
    try:
        compile(caminho.read_bytes(), str(caminho), "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as erro:
        return f"{type(erro).__name__}: {erro}"
    return None


def verificar_json(caminho: Path) -> str | None:
    try:
        json.loads(caminho.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as erro:
        return f"{type(erro).__name__}: {erro}"
    return None


def verificar_arquivos(cfg) -> list[Problema]:
    problemas = []
    for relativo in cfg["arquivos"]["obrigatorios"]:
        if not (cfg["alvo"] / relativo).is_file():
            problemas.append(Problema("arquivo_ausente", relativo, "arquivo obrigatório não existe", relativo))
    for caminho in listar_arquivos(cfg, cfg["arquivos"]["verificar_python"]):
        erro = verificar_python(caminho)
        if erro:
            relativo = _relativo(cfg, caminho)
            problemas.append(Problema("sintaxe_python", relativo, erro, relativo))
    for caminho in listar_arquivos(cfg, cfg["arquivos"]["verificar_json"]):
        erro = verificar_json(caminho)
        if erro:
            relativo = _relativo(cfg, caminho)
            problemas.append(Problema("json_invalido", relativo, erro, relativo))
    return problemas


def _arquivo_do_traceback(cfg, linhas: list[str]) -> str | None:
    """Último quadro do traceback que pertence à IA Alpha (o mais próximo do erro)."""
    for linha in reversed(linhas):
        for nome in reversed(LINHA_TRACEBACK.findall(linha)):
            caminho = Path(nome)
            if not caminho.is_absolute():
                caminho = cfg["alvo"] / caminho
            try:
                relativo = _relativo(cfg, caminho)
            except ValueError:
                continue
            if not _ignorado(cfg, relativo):
                return relativo
    return None


def verificar_logs(cfg, estado: dict) -> list[Problema]:
    """Lê só o que foi escrito desde a última leitura; `estado['logs']` guarda as posições."""
    posicoes = estado.setdefault("logs", {})
    regex = re.compile("|".join(cfg["logs"]["regex_erro"]))
    problemas = []
    for caminho in listar_arquivos(cfg, cfg["logs"]["padroes"]):
        relativo = _relativo(cfg, caminho)
        tamanho = caminho.stat().st_size
        inicio = posicoes.get(relativo, 0)
        if tamanho < inicio:  # log rotacionado ou truncado
            inicio = 0
        with open(caminho, "rb") as arquivo:
            arquivo.seek(inicio)
            novo = arquivo.read()
        # só avança até a última linha completa; uma linha pela metade é lida no próximo ciclo
        fim = novo.rfind(b"\n") + 1
        posicoes[relativo] = inicio + fim
        linhas = novo[:fim].decode("utf-8", errors="replace").splitlines()
        erros = [i for i, linha in enumerate(linhas) if regex.search(linha)]
        if not erros:
            continue
        trecho = linhas[erros[0]: erros[-1] + 6][-40:]
        problemas.append(
            Problema(
                "erro_log",
                relativo,
                f"{len(erros)} linha(s) de erro:\n" + "\n".join(trecho),
                _arquivo_do_traceback(cfg, trecho),
            )
        )
    return problemas


def _arquivo_do_modulo(cfg, modulo: str) -> str:
    base = modulo.replace(".", "/")
    if (cfg["alvo"] / base / "__init__.py").is_file():
        return f"{base}/__init__.py"
    return f"{base}.py"


def _rodar(cfg, comando: list[str], timeout) -> tuple[int, str]:
    ambiente = dict(os.environ, PYTHONPATH=str(cfg["alvo"]), PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run(
            comando, cwd=cfg["alvo"], env=ambiente, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except FileNotFoundError:
        return 127, f"executável não encontrado: {comando[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"tempo esgotado após {timeout}s"
    saida = (r.stdout + r.stderr).strip()
    return r.returncode, saida[-2000:]


def verificar_funcao(cfg, item: dict) -> Problema | None:
    codigo, saida = _rodar(
        cfg, [cfg["python"], "-c", SCRIPT_FUNCAO, item["modulo"], item["funcao"]], cfg["timeout_funcao"]
    )
    if codigo == 0:
        return None
    return Problema(
        "funcao_quebrada",
        f"{item['modulo']}.{item['funcao']}",
        f"código {codigo}: {saida}",
        _arquivo_do_modulo(cfg, item["modulo"]),
    )


def verificar_funcoes(cfg) -> list[Problema]:
    return [p for p in (verificar_funcao(cfg, item) for item in cfg["funcoes"]) if p]


def verificar_comando(cfg, item: dict) -> Problema | None:
    codigo, saida = _rodar(cfg, item["comando"], item.get("timeout", 120))
    if codigo == 0:
        return None
    return Problema("comando_falhou", item["nome"], f"código {codigo}: {saida}")


def verificar_comandos(cfg) -> list[Problema]:
    return [p for p in (verificar_comando(cfg, item) for item in cfg["comandos_saude"]) if p]


def verificar_estrutura(cfg) -> list[Problema]:
    """Tudo que pode ser verificado de novo a qualquer momento (logs não entram: são históricos)."""
    return verificar_arquivos(cfg) + verificar_funcoes(cfg) + verificar_comandos(cfg)
