"""Trava de arquivo do sistema operacional: só uma instância roda um ciclo por vez.

A trava é liberada pelo SO se o processo morrer, então não há trava "velha" para limpar.
"""

import os
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class TravaOcupada(RuntimeError):
    pass


class Trava:
    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self._arquivo = None

    def __enter__(self):
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        arquivo = open(self.caminho, "a+b")
        try:
            if os.name == "nt":
                arquivo.seek(0)
                msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            arquivo.close()
            raise TravaOcupada(f"outra instância do Sentinela está em execução ({self.caminho})") from None
        self._arquivo = arquivo
        return self

    def __exit__(self, *exc):
        if os.name == "nt":
            self._arquivo.seek(0)
            msvcrt.locking(self._arquivo.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self._arquivo.fileno(), fcntl.LOCK_UN)
        self._arquivo.close()
        self._arquivo = None
        return False
