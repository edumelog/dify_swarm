# Dify no Docker Swarm (single-node)

Este guia adapta o deploy oficial por `docker compose` para `docker stack` no Swarm, focado em **1 manager** e **volumes locais**.

## 1) Pre-requisitos

- Docker Engine com Swarm habilitável (`docker swarm init`)
- Docker CLI com suporte a `docker stack`
- Host Linux com recursos minimos recomendados pelo Dify

Referência oficial de deploy base (Compose): [Deploy Dify with Docker Compose](https://docs.dify.ai/en/self-host/quick-start/docker-compose).

## 2) Preparar ambiente

No diretório `docker` do projeto:

```bash
cd docker
cp .env.example .env
```

Opcionalmente ajuste valores no `.env` (domínio, portas, chaves e integrações).

## 3) Inicializar Swarm

Se ainda não estiver ativo:

```bash
docker swarm init
```

## 4) Validar manifesto

Antes do deploy, valide sintaxe/interpolação:

```bash
docker stack config -c docker-stack.yml > /dev/null
```

## 5) Deploy da stack

```bash
docker stack deploy -c docker-stack.yml dify
```

Observacao: para evitar problemas de bind mount no Swarm (comum no Docker Desktop/WSL), este stack usa **volumes nomeados locais** para dados e **configs do Swarm** para templates/scripts (incluindo `nginx/conf.d/default.conf.template`, necessario para o proxy HTTP funcionar na porta 80).

## 6) Verificação

```bash
docker stack services dify
docker service ls | rg dify
docker service ps dify_api
```

Se for o primeiro deploy, existe um job `init_permissions` (modo `replicated-job`) que inicializa permissões no volume de storage do Dify. Você pode checar a execução com:

```bash
docker service ps dify_init_permissions
```

Quando os serviços principais estiverem `Running`, acesse:

- `http://localhost/install` (bootstrap admin)
- `http://localhost` (console após instalação)

## 7) Operações do dia a dia

- Atualizar stack após mudanças:

```bash
docker stack deploy -c docker-stack.yml dify
```

- Reiniciar um serviço:

```bash
docker service update --force dify_api
```

- Remover stack:

```bash
docker stack rm dify
```

## 8) Imagens da base de conhecimento (kb_assets)

O serviço `dify_kb_assets` (nginx) serve as imagens dos manuais da base de conhecimento em `<protocolo>://<domínio-do-dify>/kb-assets/<slug>/<arquivo>`, para que o chatbot as exiba nas respostas. As imagens ficam no volume `dify_kb_assets`.

Ordem de uso:

1. Faça o deploy da stack (seção 5), que já inclui o serviço `dify_kb_assets`.
2. No NGPM, crie a custom location `/kb-assets/` do proxy host do Dify apontando para `dify_kb_assets:80`.
3. Publique o manual, respondendo às perguntas de domínio e protocolo:

```bash
docker/kb_assets/publish.sh kb/<manual> <slug>
```

4. Envie ao Dify o arquivo gerado `kb/<manual>/build/<nome>.dify.md`.

Detalhes (formato do manual, segmentação, prompt do LLM): [`kb_assets/README.md`](kb_assets/README.md).

## Observações importantes para Swarm

- `depends_on` do Compose não controla ordem no Swarm; os serviços sobem de forma independente.
- Este setup usa **volumes nomeados locais**, adequado para **single-node**.
- Para produção multi-nó, migre para storage compartilhado (NFS/driver distribuído) e políticas de placement.
