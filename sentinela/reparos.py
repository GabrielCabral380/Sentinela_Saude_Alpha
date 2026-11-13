"""Reparos automáticos com backup, verificação completa e desfazer em caso de piora.

Ordem por problema:
1. snapshot: restaura a última cópia saudável do arquivo (tirada num ciclo sem problemas);
2. comando de reparo da própria IA Alpha (`comandos_saude[].reparo`);
3. LLM: pede o arquivo corrigido aos provedores em ordem de prioridade.
Uma alteração só fica se o problema sumir e nenhum problema novo aparecer; senão é desfeita.
"""

import os
import tempfile
import time
from pathlib import Path

from .llm import pedir_correcao
from .verificacoes import Problema, _rodar, verificar_estrutura

TIPOS_DE_ARQUIVO = {"arquivo_ausente", "sintaxe_python", "json_invalido", "funcao_quebrada"}

PROMPT = """Você é o reparador automático da IA Alpha. Corrija o arquivo `{arquivo}`.
Problema detectado ({tipo} em {alvo}):
{detalhe}

Conteúdo atual do arquivo:
```
{conteudo}
```
Regras: mantenha o comportamento e os nomes públicos existentes, faça a menor correção possível
e responda com o arquivo COMPLETO corrigido em um único bloco de código."""


def escrever_atomico(caminho: Path, conteudo: bytes) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    descritor, temporario = tempfile.mkstemp(dir=caminho.parent, prefix=f".{caminho.name}.", suffix=".tmp")
    try:
        with os.fdopen(descritor, "wb") as arquivo:
            arquivo.write(conteudo)
        os.replace(temporario, caminho)
    except BaseException:
        Path(temporario).unlink(missing_ok=True)
        raise


class Reparador:
    def __init__(self, cfg, problemas: list[Problema]):
        self.cfg = cfg
        self.atuais = {p.chave for p in problemas if p.tipo != "erro_log"}
        self.pasta_backup = cfg["pasta_estado"] / "backups" / time.strftime("%Y%m%d-%H%M%S")
        self.alterados: set[str] = set()

    def _caminho_seguro(self, relativo: str) -> Path:
        caminho = (self.cfg["alvo"] / relativo).resolve()
        if not caminho.is_relative_to(self.cfg["alvo"]):
            raise ValueError(f"caminho fora da IA Alpha: {relativo}")
        return caminho

    def _validar(self, problema: Problema) -> tuple[bool, str]:
        verificados = verificar_estrutura(self.cfg)
        depois = {p.chave for p in verificados}
        novos = depois - self.atuais
        if problema.chave in depois:
            return False, "problema continua"
        mesmo_arquivo = [p.chave for p in verificados if problema.arquivo and p.arquivo == problema.arquivo]
        if mesmo_arquivo:
            return False, f"o arquivo continua com problemas: {mesmo_arquivo}"
        if novos:
            return False, f"criou problemas novos: {sorted(novos)}"
        self.atuais = depois
        return True, "verificado"

    def _trocar_arquivo(self, problema: Problema, conteudo: bytes, metodo: str) -> dict:
        caminho = self._caminho_seguro(problema.arquivo)
        anterior = caminho.read_bytes() if caminho.is_file() else None
        if anterior is not None:
            escrever_atomico(self.pasta_backup / problema.arquivo, anterior)
        escrever_atomico(caminho, conteudo)
        sucesso, motivo = self._validar(problema)
        if sucesso:
            self.alterados.add(problema.arquivo)
        elif anterior is None:
            caminho.unlink()
        else:
            escrever_atomico(caminho, anterior)
        return {"problema": problema.chave, "metodo": metodo, "sucesso": sucesso, "motivo": motivo}

    def reparar(self, problema: Problema) -> list[dict]:
        if problema.tipo == "erro_log":
            if not (problema.arquivo and self.cfg["llm"].get("reparar_erros_de_log")):
                return []
            if problema.arquivo in self.alterados:
                return []
        elif problema.chave not in self.atuais:
            return []  # já resolvido por um reparo anterior neste ciclo

        tentativas = []
        if problema.tipo in TIPOS_DE_ARQUIVO and problema.arquivo:
            snapshot = self.cfg["pasta_estado"] / "snapshots" / problema.arquivo
            atual = self._caminho_seguro(problema.arquivo)
            if snapshot.is_file() and (not atual.is_file() or atual.read_bytes() != snapshot.read_bytes()):
                tentativas.append(self._trocar_arquivo(problema, snapshot.read_bytes(), "snapshot"))
                if tentativas[-1]["sucesso"]:
                    return tentativas

        if problema.tipo == "comando_falhou":
            item = next(i for i in self.cfg["comandos_saude"] if i["nome"] == problema.alvo)
            if "reparo" in item:
                codigo, saida = _rodar(self.cfg, item["reparo"], item.get("timeout", 120))
                sucesso, motivo = self._validar(problema) if codigo == 0 else (False, f"reparo saiu com código {codigo}: {saida}")
                tentativas.append({"problema": problema.chave, "metodo": "comando_reparo", "sucesso": sucesso, "motivo": motivo})
                if sucesso:
                    return tentativas

        if (
            self.cfg["llm"]["habilitado"]
            and problema.arquivo
            and problema.tipo != "arquivo_ausente"
            and Path(problema.arquivo).suffix in (".py", ".json")
        ):
            atual = self._caminho_seguro(problema.arquivo)
            if atual.is_file():
                prompt = PROMPT.format(
                    arquivo=problema.arquivo, tipo=problema.tipo, alvo=problema.alvo,
                    detalhe=problema.detalhe, conteudo=atual.read_text(encoding="utf-8", errors="replace"),
                )
                falhas = []
                for nome, codigo in pedir_correcao(self.cfg, prompt, falhas):
                    tentativas.append(self._trocar_arquivo(problema, codigo.encode("utf-8"), f"llm:{nome}"))
                    if tentativas[-1]["sucesso"]:
                        break
                for falha in falhas:
                    tentativas.append({"problema": problema.chave, "metodo": "llm", "sucesso": False, "motivo": falha})
        return tentativas
