# Sentinela_Saude_Alpha

Criar um orquestrador de verificação, reparo e manutenção constante na IA Alpha. O projeto deve rodar em loops verificando logs, arquivos e funções, executando reparos automáticos em conjunto com as funções já existentes na IA, garantindo a saúde do projeto. Utilizar Claude e Hermes, priorizando Free Claude Code e FreeLLMAPI se necessário, mas garantindo que os arquivos e funções sejam criados e funcionem corretamente após verificação rigorosa.

## Uso

Só Python 3.13+ (biblioteca padrão). Copie `sentinela.exemplo.json` para `sentinela.json` e ajuste `alvo` (pasta da IA Alpha) — ou defina a variável `SENTINELA_ALVO`.

```
python -m sentinela --config sentinela.json --uma-vez     # um ciclo: sai 0 saudável, 1 com problemas
python -m sentinela --config sentinela.json               # loop contínuo a cada intervalo_segundos
python -m sentinela --config sentinela.json --max-ciclos 3
python -m unittest discover -s tests                      # testes
```
Códigos de saída: 2 = config inválida, 3 = outra instância já está no meio de um ciclo.

## O que cada ciclo faz

1. **Verifica**: arquivos obrigatórios, sintaxe de todo `.py`, validade de todo `.json`, importa cada função de `funcoes` num processo separado, roda `comandos_saude` (funções de diagnóstico que a própria Alpha já tem) e lê só as linhas novas dos logs (`Traceback`, `ERROR`, `CRITICAL`, `FATAL`).
2. **Repara** (se `reparo_automatico`), nesta ordem:
   - restaura a última cópia saudável (snapshot) do arquivo;
   - roda o comando `reparo` da própria Alpha, quando configurado em `comandos_saude`;
   - pede o arquivo corrigido aos LLMs, na ordem de `llm.provedores`: Free Claude Code → FreeLLMAPI → Claude → Hermes. Provedor sem executável ou sem variável de ambiente é pulado.
3. **Confere com rigor**: cada alteração só fica se o problema sumir, o arquivo ficar sem nenhum problema e nenhum problema novo surgir na verificação completa. Senão é desfeita. O arquivo anterior fica em `.sentinela/backups/<data-hora>/`.
4. **Registra**: `.sentinela/relatorios.jsonl` (histórico) e `.sentinela/estado.json`. Só um ciclo 100% saudável atualiza os snapshots.

Uma trava de arquivo do sistema (`.sentinela/sentinela.lock`) garante que duas instâncias nunca rodem um ciclo ao mesmo tempo; o SO libera a trava se o processo morrer.

## Provedores de LLM

- `tipo: "cli"`: o prompt vai pelo stdin, ou no argumento que contém `{prompt}`. `ambiente` define variáveis só para aquele processo (ex.: Free Claude Code é o `claude -p` apontado para o proxy local em `ANTHROPIC_BASE_URL`).
- `tipo: "openai"`: API compatível com OpenAI (FreeLLMAPI). URL e chave vêm de variáveis de ambiente (`url_env`, `chave_env`); nunca coloque chave no JSON.
- O comando do Hermes no exemplo (`hermes chat -q {prompt}`) deve ser ajustado à instalação local.
- `reparar_erros_de_log` vem desligado: um erro de log não pode ser reproduzido pelo Sentinela, então a correção por LLM só pode ser conferida contra regressão, não contra o erro original.
