# Dify no Docker Swarm (single-node)

Este guia adapta o deploy oficial por `docker compose` para `docker stack` no Swarm, focado em **1 manager**, **volumes locais** e exposição via **Nginx Proxy Manager** (rede externa `net_nginx_pm`).

Referência oficial de deploy base (Compose): [Deploy Dify with Docker Compose](https://docs.dify.ai/en/self-host/quick-start/docker-compose).

## 1) Pré-requisitos

- Docker Engine com Swarm ativo (`docker swarm init`, se ainda não estiver)
- Docker CLI com suporte a `docker stack`
- Host Linux com os recursos mínimos recomendados pelo Dify
- Rede overlay externa `net_nginx_pm` (compartilhada com o Nginx Proxy Manager). Se não existir:

```bash
docker network create -d overlay --attachable net_nginx_pm
```

Confira o estado do Swarm e da rede:

```bash
docker info --format '{{.Swarm.LocalNodeState}}'   # deve retornar "active"
docker network ls --filter name=net_nginx_pm
```

## 2) Preparar o `.env`

Todos os comandos abaixo são executados no diretório `docker` do projeto:

```bash
cd docker
cp .env.example .env
```

Ajuste no mínimo:

- `SECRET_KEY` — gere com `openssl rand -base64 42`
- Senhas e chaves padrão (os valores do `.env.example` são públicos): `DB_PASSWORD`, `REDIS_PASSWORD`, `PLUGIN_DAEMON_KEY`, `PLUGIN_DIFY_INNER_API_KEY`, `SANDBOX_API_KEY`, `WEAVIATE_API_KEY` / `WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS`
- URLs públicas, conforme o domínio usado (ex.: `http://dify.dev.dti`): `CONSOLE_API_URL`, `CONSOLE_WEB_URL`, `APP_API_URL`, `APP_WEB_URL`, `FILES_URL`

## 3) Exportar o `.env` no shell

O `docker stack deploy` **não carrega o `.env` automaticamente** para interpolar as variáveis `${VAR:-default}` do `docker-stack.yml`. O `env_file:` só injeta variáveis dentro dos containers. Sem exportar, o YAML usa os defaults e, por exemplo, a senha do Postgres/Redis pode divergir da que a API recebe.

```bash
while IFS= read -r line; do [[ $line =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] && export "$line"; done < .env
```

O laço exporta cada linha `CHAVE=valor` literalmente, sem executá-la. **Não use `source .env`**: o arquivo tem valores com espaços e caracteres especiais sem aspas (ex.: `LOG_DATEFORMAT=%Y-%m-%d %H:%M:%S`, `NGINX_SSL_PROTOCOLS=TLSv1.2 TLSv1.3`), que o bash tenta interpretar como comandos.

Repita este passo em todo novo shell antes de um deploy.

## 4) Validar o manifesto

```bash
docker stack config -c docker-stack.yml > /dev/null
```

## 5) Deploy da stack

```bash
docker stack deploy -c docker-stack.yml dify
```

Para evitar problemas de bind mount no Swarm (comum no Docker Desktop/WSL), este stack usa **volumes nomeados locais** para dados e **configs do Swarm** para templates/scripts (incluindo `nginx/conf.d/default.conf.template`).

## 6) Verificação

```bash
docker stack services dify
docker service ps dify_init_permissions   # job único; deve terminar em "Complete"
docker service logs -f dify_api           # no primeiro deploy, as migrations rodam aqui
```

O `depends_on` não existe no Swarm: é normal `api`, `worker` e `plugin_daemon` reiniciarem algumas vezes até Postgres e Redis ficarem prontos.

## 7) Expor via Nginx Proxy Manager

O serviço `nginx` do Dify **não publica portas** no host — ele está apenas na rede `net_nginx_pm`. No Nginx Proxy Manager, crie um Proxy Host:

- Domínio: `dify.dev.dti` (ou o domínio configurado no `.env`)
- Scheme: `http`
- Forward Hostname: `dify_nginx`
- Forward Port: `80`

Depois acesse:

- `http://dify.dev.dti/install` (criação do admin)
- `http://dify.dev.dti` (console após instalação)

## 8) SSR do `web`: domínio público e CA interna

Os domínios `*.dti` não estão no DNS — são resolvidos pelo arquivo hosts de quem navega. Mas o SSR do Next.js, **dentro do container `web`**, também chama `CONSOLE_API_URL` (ex.: `https://dify.hmg.dti`). Sem ajuste, o log do `web` mostra `getaddrinfo ENOTFOUND` e o browser exibe "Ocorreu um erro inesperado ao renderizar este componente".

Configure no `.env`:

```env
# Entrada de hosts injetada no container web (extra_hosts)
DIFY_PUBLIC_HOST=dify.hmg.dti
DIFY_PUBLIC_HOST_IP=10.100.2.25        # IP do host onde o Nginx Proxy Manager publica a 443

# Certificado PÚBLICO da CA interna que assina *.hmg.dti (nunca a chave .key)
DIFY_EXTRA_CA_FILE=./certs/localCA.pem
```

E copie o certificado público da CA para `docker/certs/` (a pasta é ignorada pelo git):

```bash
cp /caminho/para/localCA.pem docker/certs/localCA.pem
```

Sem essas variáveis, o padrão é o ambiente de dev: `dify.dev.dti -> 10.0.2.2` (VIP do nginx interno) e nenhuma CA extra (`certs/no-extra-ca.pem`).

O certificado vira o config `dify_web_extra_ca` e é carregado via `NODE_EXTRA_CA_CERTS`. Como configs do Swarm são imutáveis, ao **trocar** o certificado é preciso remover a stack antes do deploy (ou renomear o config).

Para testar de dentro do container:

```bash
W=$(docker ps -q -f name=<stack>_web | head -1)
docker exec $W node -e 'fetch(process.env.CONSOLE_API_URL + "/console/api/system-features").then(r => console.log(r.status)).catch(e => console.log(e.cause?.code || e.message))'
```

## 9) Operações do dia a dia

- Aplicar mudanças no stack ou no `.env`:

```bash
while IFS= read -r line; do [[ $line =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] && export "$line"; done < .env
docker stack deploy -c docker-stack.yml dify
```

- Reiniciar um serviço:

```bash
docker service update --force dify_api
```

- Remover a stack (os volumes `dify_*` e os dados são preservados):

```bash
docker stack rm dify
```

- Alterações em arquivos de `nginx/` ou `ssrf_proxy/`: os `configs` do Swarm são imutáveis, então um redeploy com conteúdo alterado falha. Remova a stack, aguarde a remoção terminar e faça o deploy de novo:

```bash
docker stack rm dify
# aguarde até `docker stack ls` não listar mais "dify"
while IFS= read -r line; do [[ $line =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] && export "$line"; done < .env
docker stack deploy -c docker-stack.yml dify
```

## 10) Imagens da base de conhecimento (kb_assets)

O serviço `dify_kb_assets` (nginx) serve as imagens dos manuais da base de conhecimento em `<protocolo>://<domínio-do-dify>/kb-assets/<slug>/<arquivo>`, para que o chatbot as exiba nas respostas. As imagens ficam no volume `dify_kb_assets`.

Ordem de uso:

1. Prepare o manual em `kb/<manual>/` (um `.md` e a pasta `images/`). Para gerá-lo a partir de um PDF, use o prompt [`kb_assets/docs/PDF_TO_RAG.md`](kb_assets/docs/PDF_TO_RAG.md).
2. Faça o deploy da stack (seção 5), que já inclui o serviço `dify_kb_assets`.
3. No NGPM, crie a custom location `/kb-assets/` do proxy host do Dify apontando para `dify_kb_assets:80`.
4. Publique o manual, respondendo às perguntas de domínio e protocolo:

```bash
docker/kb_assets/publish.sh kb/<manual> <slug>
```

5. Envie ao Dify o arquivo gerado `kb/<manual>/build/<nome>.dify.md`.

Detalhes (formato do manual, segmentação, prompt do LLM): [`kb_assets/README.md`](kb_assets/README.md).

## Colisão de nomes na rede compartilhada (502 Bad Gateway)

O serviço `nginx` fica em duas redes: `default` (da stack) e `net_nginx_pm` (compartilhada com outras stacks). No Swarm, o nome curto de um serviço (ex.: `api`) vira alias DNS em toda rede à qual ele pertence. Se outra stack tiver um serviço chamado `api` em `net_nginx_pm`, o nginx pode resolver `api` para o serviço errado ao iniciar e passar a responder **502** (`connect() failed (111: Connection refused)` no log do nginx), com o front exibindo "Ocorreu um erro inesperado ao renderizar este componente".

Por isso este stack usa um template de nginx próprio para Swarm, `nginx/swarm/default.conf.template`, que aponta para os **nomes completos** dos serviços (`${DIFY_STACK_NAME}_api`, `${DIFY_STACK_NAME}_web`, `${DIFY_STACK_NAME}_plugin_daemon`). A variável `DIFY_STACK_NAME` é preenchida automaticamente pelo Swarm com o nome da stack usado no `docker stack deploy` (ex.: `dify_hmg`). O `nginx/conf.d/default.conf.template` original continua sendo usado pelo `docker-compose.yaml`.

Não use `aliases` de rede para isso: no Docker 27, aliases adicionados a serviços com `update_config.order: start-first` não são registrados no DNS do Swarm, e o nginx entra em loop com `host not found in upstream`.

Para diagnosticar, compare o IP do upstream no log do nginx com os VIPs dos serviços:

```bash
docker service logs --tail 50 <stack>_nginx | grep upstream
docker service inspect <stack>_api --format '{{json .Endpoint.VirtualIPs}}'
docker network inspect net_nginx_pm --verbose --format '{{range $k,$v := .Services}}{{$k}} VIP={{$v.VIP}}{{println}}{{end}}'
```

## Observações importantes para Swarm

- `depends_on` do Compose não controla ordem no Swarm; os serviços sobem de forma independente.
- Este setup usa **volumes nomeados locais**, adequado para **single-node**.
- Para produção multi-nó, migre para storage compartilhado (NFS/driver distribuído) e políticas de placement.
