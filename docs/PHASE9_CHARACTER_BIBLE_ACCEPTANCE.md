# Fase 9 — Character Bible — Relatório de aceitação

**Estado:** APROVADA  
**Data:** 2026-08-20

## Critérios verificados

- Fichas canônicas YAML existem e estão marcadas como `approved`.
- Imagens canônicas de rosto existem no repositório.
- Adão e Eva possuem identidade física, segurança infantil e método final image-to-image definidos.
- O resolvedor de personagens carrega as fichas YAML aprovadas em vez de depender de descrições duplicadas.
- Aliases em português e inglês são suportados: Adão/Adam e Eva/Eve.
- Chamadas legadas de `get_character_description()` recebem a identidade canônica e o caminho da referência.

## Defeito encontrado e corrigido

O dicionário legado descrevia Adão com cabelo curto, contrariando a ficha aprovada (`castanho-escuro, ondulado, até os ombros`). A fonte de verdade passou a ser a ficha YAML versionada.

## Testes

- `test_adam_is_loaded_from_approved_canonical_card`
- `test_legacy_description_uses_adam_canonical_identity`
- Suíte unitária completa: **141 passed**, 1 aviso de depreciação externo.

## Ativos canônicos

- `assets/characters/creation/adam/character_v1.yaml`
- `assets/characters/creation/adam/face_v1.png`
- `assets/characters/creation/eve/character_v1.yaml`
- `assets/characters/creation/eve/face_v1.png`

## Regra permanente

Toda história futura que citar Adão ou Eva deve carregar estas mesmas fichas e imagens canônicas. Seeds e descrições textuais não podem substituir as referências.
