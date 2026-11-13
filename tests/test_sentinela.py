"""Testes do Sentinela_Saude_Alpha contra uma IA Alpha falsa criada em pasta temporária."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from sentinela.config import ConfigInvalida, carregar_config  # noqa: E402
from sentinela.orquestrador import executar_ciclo, executar_loop  # noqa: E402
from sentinela.trava import Trava, TravaOcupada  # noqa: E402
from sentinela.verificacoes import (  # noqa: E402
    verificar_arquivos,
    verificar_comandos,
    verificar_funcoes,
    verificar_logs,
)

MODULO_OK = "def executar():\n    return 'ok'\n"


def criar_alpha(base: Path) -> Path:
    alvo = base / "alpha"
    (alvo / "core" / "motor").mkdir(parents=True)
    (alvo / "logs").mkdir()
    (alvo / "core" / "__init__.py").write_text("", encoding="utf-8")
    (alvo / "core" / "motor" / "__init__.py").write_text("", encoding="utf-8")
    (alvo / "core" / "motor" / "agentes_skill.py").write_text(MODULO_OK, encoding="utf-8")
    (alvo / "config.json").write_text('{"nome": "Alpha ação"}', encoding="utf-8")
    (alvo / "logs" / "alpha.log").write_text("INFO iniciado\n", encoding="utf-8")
    return alvo


def escrever_config(base: Path, alvo: Path, **extra) -> Path:
    dados = {
        "alvo": str(alvo),
        "pasta_estado": str(base / "estado"),
        "intervalo_segundos": 1,
        "logs": {"padroes": ["logs/*.log"]},
        "arquivos": {"obrigatorios": ["config.json", "core/motor/agentes_skill.py"]},
        "funcoes": [{"modulo": "core.motor.agentes_skill", "funcao": "executar"}],
    }
    dados.update(extra)
    caminho = base / "sentinela.json"
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    return caminho


class BaseTeste(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.alvo = criar_alpha(self.base)

    def tearDown(self):
        self._tmp.cleanup()

    def cfg(self, **extra):
        return carregar_config(escrever_config(self.base, self.alvo, **extra))


class TestConfig(BaseTeste):
    def test_config_valida_carrega_padroes(self):
        cfg = self.cfg()
        self.assertEqual(cfg["alvo"], self.alvo.resolve())
        self.assertTrue(cfg["reparo_automatico"])
        self.assertIn("Traceback", cfg["logs"]["regex_erro"])

    def test_sem_alvo(self):
        caminho = self.base / "c.json"
        caminho.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ConfigInvalida, "alvo"):
            carregar_config(caminho)

    def test_alvo_inexistente(self):
        with self.assertRaisesRegex(ConfigInvalida, "não existe"):
            carregar_config(escrever_config(self.base, self.base / "nada"))

    def test_intervalo_invalido(self):
        for valor in (0, -5, "dez"):
            with self.assertRaisesRegex(ConfigInvalida, "intervalo_segundos"):
                carregar_config(escrever_config(self.base, self.alvo, intervalo_segundos=valor))

    def test_json_malformado(self):
        caminho = self.base / "c.json"
        caminho.write_text("{alvo:", encoding="utf-8")
        with self.assertRaisesRegex(ConfigInvalida, "JSON"):
            carregar_config(caminho)

    def test_funcao_sem_modulo(self):
        with self.assertRaisesRegex(ConfigInvalida, "funcoes"):
            self.cfg(funcoes=[{"funcao": "x"}])


class TestVerificacoes(BaseTeste):
    def test_projeto_saudavel_sem_problemas(self):
        cfg = self.cfg()
        self.assertEqual(verificar_arquivos(cfg), [])
        self.assertEqual(verificar_funcoes(cfg), [])

    def test_arquivo_ausente_sintaxe_e_json(self):
        (self.alvo / "config.json").unlink()
        (self.alvo / "core" / "quebrado.py").write_text("def x(:\n", encoding="utf-8")
        (self.alvo / "dados.json").write_text("{ruim", encoding="utf-8")
        tipos = {(p.tipo, p.arquivo) for p in verificar_arquivos(self.cfg())}
        self.assertEqual(
            tipos,
            {
                ("arquivo_ausente", "config.json"),
                ("sintaxe_python", "core/quebrado.py"),
                ("json_invalido", "dados.json"),
            },
        )

    def test_pastas_ignoradas(self):
        (self.alvo / "venv").mkdir()
        (self.alvo / "venv" / "lixo.py").write_text("def (:", encoding="utf-8")
        self.assertEqual(verificar_arquivos(self.cfg()), [])

    def test_funcao_modulo_ausente_e_funcao_ausente(self):
        cfg = self.cfg(
            funcoes=[
                {"modulo": "alfred2.kernel", "funcao": "iniciar"},
                {"modulo": "core.motor.agentes_skill", "funcao": "nao_existe"},
            ]
        )
        problemas = verificar_funcoes(cfg)
        self.assertEqual(len(problemas), 2)
        self.assertTrue(all(p.tipo == "funcao_quebrada" for p in problemas))
        self.assertIn("alfred2", problemas[0].detalhe)
        self.assertEqual(problemas[1].arquivo, "core/motor/agentes_skill.py")

    def test_logs_le_so_linhas_novas_e_mapeia_traceback(self):
        cfg = self.cfg()
        estado = {}
        self.assertEqual(verificar_logs(cfg, estado), [])
        arquivo_py = self.alvo / "core" / "motor" / "agentes_skill.py"
        with open(self.alvo / "logs" / "alpha.log", "a", encoding="utf-8") as log:
            log.write("Traceback (most recent call last):\n")
            log.write(f'  File "{arquivo_py}", line 2, in executar\n')
            log.write("ValueError: ação inválida\n")
        problemas = verificar_logs(cfg, estado)
        self.assertEqual(len(problemas), 1)
        self.assertEqual(problemas[0].tipo, "erro_log")
        self.assertEqual(problemas[0].arquivo, "core/motor/agentes_skill.py")
        self.assertIn("ação inválida", problemas[0].detalhe)
        self.assertEqual(verificar_logs(cfg, estado), [])

    def test_log_rotacionado_volta_ao_inicio(self):
        cfg = self.cfg()
        estado = {}
        log = self.alvo / "logs" / "alpha.log"
        log.write_text("INFO " + "x" * 200 + "\n", encoding="utf-8")
        verificar_logs(cfg, estado)
        log.write_text("ERROR curto\n", encoding="utf-8")
        self.assertEqual(len(verificar_logs(cfg, estado)), 1)

    def test_comando_de_saude_falha(self):
        cfg = self.cfg(
            comandos_saude=[{"nome": "falha", "comando": [sys.executable, "-c", "raise SystemExit(3)"]}]
        )
        problemas = verificar_comandos(cfg)
        self.assertEqual(len(problemas), 1)
        self.assertIn("código 3", problemas[0].detalhe)

    def test_comando_inexistente_vira_problema(self):
        cfg = self.cfg(comandos_saude=[{"nome": "x", "comando": ["programa_que_nao_existe_123"]}])
        self.assertEqual(verificar_comandos(cfg)[0].tipo, "comando_falhou")


class TestCiclo(BaseTeste):
    def test_ciclo_saudavel_gera_snapshot_e_relatorio(self):
        relatorio = executar_ciclo(self.cfg())
        self.assertTrue(relatorio["saudavel"])
        snapshot = self.base / "estado" / "snapshots" / "core" / "motor" / "agentes_skill.py"
        self.assertEqual(snapshot.read_text(encoding="utf-8"), MODULO_OK)
        linhas = (self.base / "estado" / "relatorios.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(linhas[-1])["saudavel"], True)

    def test_restaura_modulo_apagado_do_snapshot(self):
        cfg = self.cfg()
        executar_ciclo(cfg)
        modulo = self.alvo / "core" / "motor" / "agentes_skill.py"
        modulo.unlink()
        relatorio = executar_ciclo(cfg)
        self.assertTrue(relatorio["saudavel"], relatorio)
        self.assertEqual(modulo.read_text(encoding="utf-8"), MODULO_OK)
        self.assertTrue(any(r["sucesso"] and r["metodo"] == "snapshot" for r in relatorio["reparos"]))

    def test_restaura_sintaxe_quebrada_e_guarda_backup(self):
        cfg = self.cfg()
        executar_ciclo(cfg)
        modulo = self.alvo / "core" / "motor" / "agentes_skill.py"
        modulo.write_text("def executar(:\n", encoding="utf-8")
        self.assertTrue(executar_ciclo(cfg)["saudavel"])
        self.assertEqual(modulo.read_text(encoding="utf-8"), MODULO_OK)
        backups = list((self.base / "estado" / "backups").rglob("agentes_skill.py"))
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "def executar(:\n")

    def test_sem_reparo_automatico_nao_altera(self):
        cfg = self.cfg(reparo_automatico=False)
        executar_ciclo(cfg)
        modulo = self.alvo / "core" / "motor" / "agentes_skill.py"
        modulo.unlink()
        relatorio = executar_ciclo(cfg)
        self.assertFalse(relatorio["saudavel"])
        self.assertFalse(modulo.exists())

    def test_comando_de_reparo_da_ia_e_usado(self):
        marcador = self.alvo / "ok.flag"
        cfg = self.cfg(
            comandos_saude=[
                {
                    "nome": "flag",
                    "comando": [sys.executable, "-c", "import os,sys; sys.exit(0 if os.path.exists('ok.flag') else 1)"],
                    "reparo": [sys.executable, "-c", "open('ok.flag','w').close()"],
                }
            ]
        )
        relatorio = executar_ciclo(cfg)
        self.assertTrue(relatorio["saudavel"], relatorio)
        self.assertTrue(marcador.exists())

    def _provedor_falso(self, codigo: str) -> dict:
        script = self.base / "llm_falso.py"
        script.write_text(
            "import sys\nsys.stdin.read()\nprint('Aqui:')\nprint('```python')\nprint(" + repr(codigo) + ")\nprint('```')\n",
            encoding="utf-8",
        )
        return {"llm": {"habilitado": True, "provedores": [{"nome": "falso", "tipo": "cli", "comando": [sys.executable, str(script)]}]}}

    def test_llm_corrige_quando_nao_ha_snapshot(self):
        modulo = self.alvo / "core" / "motor" / "agentes_skill.py"
        modulo.write_text("def executar(:\n", encoding="utf-8")
        relatorio = executar_ciclo(self.cfg(**self._provedor_falso("def executar():\n    return 'consertado'\n")))
        self.assertTrue(relatorio["saudavel"], relatorio)
        self.assertIn("consertado", modulo.read_text(encoding="utf-8"))
        self.assertTrue(any(r["metodo"] == "llm:falso" and r["sucesso"] for r in relatorio["reparos"]))

    def test_llm_com_resposta_ruim_e_desfeito(self):
        modulo = self.alvo / "core" / "motor" / "agentes_skill.py"
        modulo.write_text("def executar(:\n", encoding="utf-8")
        relatorio = executar_ciclo(self.cfg(**self._provedor_falso("def outra():\n    pass\n")))
        self.assertFalse(relatorio["saudavel"])
        self.assertEqual(modulo.read_text(encoding="utf-8"), "def executar(:\n")

    def test_loop_respeita_max_ciclos(self):
        relatorios = executar_loop(self.cfg(), max_ciclos=2, dormir=lambda s: None)
        self.assertEqual(len(relatorios), 2)


class TestTrava(BaseTeste):
    def test_trava_exclusiva_entre_processos(self):
        caminho = self.base / "x.lock"
        with Trava(caminho):
            codigo = (
                "import sys; sys.path.insert(0, sys.argv[1]);"
                "from sentinela.trava import Trava, TravaOcupada\n"
                "try:\n    Trava(sys.argv[2]).__enter__()\nexcept TravaOcupada:\n    sys.exit(7)\nsys.exit(0)"
            )
            r = subprocess.run([sys.executable, "-c", codigo, str(RAIZ), str(caminho)], timeout=30)
            self.assertEqual(r.returncode, 7)
        with Trava(caminho):
            pass

    def test_mesmo_processo_segunda_trava_ocupada(self):
        caminho = self.base / "y.lock"
        with Trava(caminho):
            with self.assertRaises(TravaOcupada):
                with Trava(caminho):
                    pass


class TestCLI(BaseTeste):
    def rodar(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "sentinela", *args],
            cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )

    def test_uma_vez_saudavel_sai_zero(self):
        r = self.rodar("--config", str(escrever_config(self.base, self.alvo)), "--uma-vez")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("saudavel", r.stdout)

    def test_problema_sem_reparo_sai_um(self):
        (self.alvo / "config.json").unlink()
        r = self.rodar("--config", str(escrever_config(self.base, self.alvo)), "--uma-vez")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_config_invalida_sai_dois(self):
        r = self.rodar("--config", str(self.base / "nao_existe.json"), "--uma-vez")
        self.assertEqual(r.returncode, 2)
        self.assertIn("config", r.stderr.lower())


if __name__ == "__main__":
    unittest.main()
