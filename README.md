# Hybrid AI Animation Studio

Fábrica local-first para episódios bíblicos infantis em pt-BR, destinada a crianças de 6–10 anos.

## Princípios operacionais

- A passagem bíblica é a fonte narrativa; paráfrases, inferências e reflexões são identificadas separadamente.
- Nenhum vídeo é publicado sem aprovações hash-bound independentes de thumbnail e vídeo, seguidas de comando explícito e separado de publicação.
- Imagens finais são referência/edição; áudio aprovado, frames e recibos são imutáveis.
- Custos, leases, QA e recuperação de provedores seguem os contratos em `docs/`.

## Verificação local

```bash
uv run --extra dev pytest tests/unit -m "not slow" -q
```

Não coloque credenciais em arquivos versionados. Copie `.env.example` para um ambiente seguro e informe apenas o status `CONFIGURED`/`NOT CONFIGURED` em logs ou relatórios.
