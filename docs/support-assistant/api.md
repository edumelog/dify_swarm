# API do assistente de atendimento (webhooks do n8n)

API usada pelo Chatflow de atendimento do Dify para identificar o usuário, cadastrá-lo e gravar
os atendimentos encerrados. É implementada por três workflows do n8n sobre o banco `support`
(Postgres 18 da stack do n8n). O modelo de dados e as instruções de criação estão em
[`new-database-for-dify.md`](new-database-for-dify.md).

## Visão geral

| Operação | Caminho | Para que serve |
|---|---|---|
| `identify` | `POST /webhook/support/identify` | Descobre quem está usando o navegador |
| `register` | `POST /webhook/support/register` | Cadastra/atualiza o usuário e liga o navegador a ele |
| `save-ticket` | `POST /webhook/support/save-ticket` | Grava o atendimento quando o caso fecha |
| `list-categories` | `POST /webhook/support/list-categories` | Lista as categorias de atendimento |
| `save-category` | `POST /webhook/support/save-category` | Cria, altera ou desativa uma categoria |

URL base: `https://<host-do-n8n>/webhook/support/`. Em testes no editor do n8n, use
`/webhook-test/support/...` com o workflow em modo "Listen for test event".

## Regras comuns

- **Método e corpo:** sempre `POST` com `Content-Type: application/json`.
- **Autenticação:** cabeçalho `X-Support-Token: <segredo>` (credencial *Header Auth* do n8n).
  Sem o cabeçalho ou com valor errado, o n8n responde `401` (corpo definido pelo próprio n8n).
- **E-mail:** o servidor aplica `trim` e converte para minúsculas antes de qualquer consulta.
  Formato aceito: `^[^@\s]+@[^@\s]+\.[^@\s]+$`.
- **Campos de texto:** strings vazias ou só com espaços são tratadas como `null`.
- **Códigos de resposta:**

  | Código | Quando |
  |---|---|
  | `200` | Operação concluída, inclusive resultados de negócio como "não encontrado" |
  | `400` | Entrada inválida ou regra violada |
  | `401` | Token ausente ou incorreto |
  | `500` | Falha inesperada (banco fora do ar etc.) |

- **Formato de erro (`400` e `500`):**

  ```json
  { "ok": false, "error": "mensagem em português" }
  ```

## `identify`

Descobre quem está usando o navegador. **Não grava vínculo** entre navegador e usuário (isso é
papel do `register`, depois que a pessoa confirma "sou eu").

### Requisição

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `user_id` | string | sim | `sys.user_id` do WebApp do Dify |
| `windows_user` | string \| null | não | Login do Windows recebido pela URL do WebApp |

### Regra

1. Se `windows_user` vier preenchido e corresponder (sem diferenciar maiúsculas) a **exatamente
   um** usuário → devolve esse usuário com `source = "windows"`.
2. Senão, procura `user_id` em `browsers`; se achar, atualiza `browsers.last_seen_at` e devolve
   o usuário com `source = "browser"`.
3. Senão → `found = false`.

### Respostas

`200` — encontrado:

```json
{
  "found": true,
  "source": "browser",
  "email": "maria.silva@exemplo.gov.br",
  "full_name": "Maria Silva",
  "phone_ext": "1234"
}
```

`200` — não encontrado:

```json
{ "found": false }
```

`400` — `user_id` ausente: `{"ok": false, "error": "user_id é obrigatório"}`

## `register`

Cadastra ou atualiza o usuário pelo e-mail e liga o navegador a ele, numa única transação.

### Requisição

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `user_id` | string | sim | `sys.user_id` do WebApp do Dify |
| `email` | string | sim | E-mail do usuário (chave) |
| `full_name` | string | sim | Nome informado |
| `phone_ext` | string \| null | não | Ramal informado |
| `windows_user` | string \| null | não | Login do Windows, se recebido pela URL |

### Regra

- *Upsert* em `users` por `email`: grava `full_name`, `phone_ext` e `updated_at = now()`.
  `windows_user` só é gravado quando vier preenchido; um valor existente nunca é apagado.
- *Upsert* em `browsers` por `user_id`: aponta para o `email` informado e grava
  `last_seen_at = now()` (se o navegador apontava para outra pessoa, passa a apontar para esta;
  o cadastro da outra pessoa não é alterado).
- Se o e-mail já existia, os dados digitados agora **substituem** nome e ramal (regra A) e nada
  do que estava gravado é devolvido.

Quando o Chatflow chama: cadastro em navegador novo; "não sou eu" seguido dos dados da nova
pessoa; "sou eu" (reenvia os dados existentes só para ligar o navegador).

### Respostas

`200`:

```json
{ "ok": true, "email": "maria.silva@exemplo.gov.br" }
```

`400` — exemplos:

```json
{ "ok": false, "error": "e-mail inválido" }
{ "ok": false, "error": "full_name é obrigatório" }
```

## `save-ticket`

Grava o atendimento quando o caso fecha. Repetir a chamada para a mesma conversa atualiza o
registro em vez de duplicar.

### Requisição

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `conversation_id` | string | sim | `sys.conversation_id` do Dify |
| `email` | string | sim | E-mail do usuário já cadastrado |
| `user_id` | string \| null | não | Navegador usado na conversa |
| `summary` | string | sim | Resumo do problema |
| `category` | string | sim | `code` de uma categoria ativa (veja `list-categories`) |
| `suggestions` | string \| null | não | Sugestões dadas pelo usuário |

### Regra

- Se o e-mail não existir em `users` → `400`.
- Se `category` não existir ou estiver inativa, o atendimento é gravado como `outros` e a resposta
  traz `category_fallback: true` (o atendimento nunca se perde por causa da categoria).
- `INSERT ... ON CONFLICT (conversation_id) DO UPDATE` de `email`, `user_id`, `summary`,
  `category`, `suggestions` e `updated_at` (`created_at` mantém o valor da primeira gravação).

### Respostas

`200`:

```json
{ "ok": true, "ticket_id": 123, "category": "outros", "category_fallback": true }
```

`400` — exemplos:

```json
{ "ok": false, "error": "usuário não cadastrado" }
{ "ok": false, "error": "summary é obrigatório" }
```

## `list-categories`

Lista as categorias usadas para classificar os atendimentos. O Chatflow chama no fechamento do
caso e coloca a lista no prompt do LLM; o Workflow administrativo do Dify chama para exibir.

### Requisição

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `include_inactive` | boolean | não | `true` inclui as desativadas (padrão `false`) |

### Respostas

`200` (ordenado por `label`, com `outros` sempre por último):

```json
{
  "ok": true,
  "categories": [
    { "code": "impressora", "label": "Impressora", "description": "Problemas de impressão e scanner", "active": true },
    { "code": "outros", "label": "Outros", "description": "Quando nenhuma outra categoria se aplica", "active": true }
  ]
}
```

## `save-category`

Cria, altera ou desativa uma categoria (*upsert* por `code`). Categorias nunca são apagadas.

### Requisição

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `code` | string | sim | Identificador; normalizado para minúsculas, espaços viram `_`; formato final `^[a-z0-9_]+$` |
| `label` | string | só na criação | Nome exibido |
| `description` | string \| null | não | Orienta o LLM sobre o que entra na categoria |
| `active` | boolean \| null | não | `false` desativa; na criação o padrão é `true` |

### Regra

- Na alteração, campos enviados como `null` mantêm o valor atual.
- `outros` não pode ser desativada (garantido também por `CHECK` no banco).

### Respostas

`200`:

```json
{ "ok": true, "category": { "code": "impressora", "label": "Impressora", "description": "...", "active": true } }
```

`400` — exemplos:

```json
{ "ok": false, "error": "code inválido" }
{ "ok": false, "error": "label é obrigatório para criar uma categoria" }
{ "ok": false, "error": "a categoria outros não pode ser desativada" }
```

## Exemplos com curl

```bash
N8N=https://<host-do-n8n>/webhook/support
TOKEN='<segredo>'
# em dev/hmg, acrescente --cacert docker/certs/localCA.pem

curl -sS -X POST "$N8N/identify" \
  -H "X-Support-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"user_id": "teste-navegador-1", "windows_user": null}'

curl -sS -X POST "$N8N/register" \
  -H "X-Support-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"user_id": "teste-navegador-1", "email": " Maria.Silva@Exemplo.gov.br ",
       "full_name": "Maria Silva", "phone_ext": "1234", "windows_user": "msilva"}'

curl -sS -X POST "$N8N/save-ticket" \
  -H "X-Support-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"conversation_id": "teste-conversa-1", "email": "maria.silva@exemplo.gov.br",
       "user_id": "teste-navegador-1", "summary": "Impressora não imprime",
       "category": "impressora", "suggestions": null}'

curl -sS -X POST "$N8N/list-categories" \
  -H "X-Support-Token: $TOKEN" -H 'Content-Type: application/json' -d '{}'

curl -sS -X POST "$N8N/save-category" \
  -H "X-Support-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"code": "teste_impressora", "label": "Impressora (teste)",
       "description": "Problemas de impressão e scanner", "active": true}'
```

## Roteiro de testes

Execute em ordem num banco vazio. Use identificadores com prefixo `teste-` para facilitar a
limpeza (veja o fim desta seção).

| # | Chamada | Esperado |
|---|---|---|
| 1 | Qualquer operação sem `X-Support-Token` | `401` |
| 2 | `identify` `{"user_id": "teste-navegador-1"}` | `200` `{"found": false}` |
| 3 | `register` navegador 1, `Maria.Silva@Exemplo.gov.br`, Maria Silva, 1234, `msilva` | `200`, `email` em minúsculas |
| 4 | `identify` navegador 1 | `found: true`, `source: "browser"`, dados da Maria |
| 5 | `identify` `{"user_id": "teste-navegador-2", "windows_user": "MSILVA"}` | `found: true`, `source: "windows"` |
| 6 | `identify` navegador 2 sem `windows_user` | `found: false` (o passo 5 não gravou vínculo) |
| 7 | `register` navegador 1 com `joao@exemplo.gov.br`, João, 5678 ("não sou eu") | `200`; `identify` navegador 1 passa a devolver João; a Maria continua em `users` sem alteração |
| 8 | `register` navegador 2 com o e-mail da Maria, nome "Maria S.", ramal 9999, sem `windows_user` | `200`; Maria fica com nome/ramal novos e `windows_user` continua `msilva` |
| 9 | `register` com `email` `"invalido"` | `400` `e-mail inválido` |
| 10 | `list-categories` `{}` | `200`; banco novo traz só `outros` |
| 11 | `save-category` `{"code": "Teste Impressora", "label": "Impressora (teste)"}` | `200`; `code` gravado como `teste_impressora` |
| 12 | `save-category` `{"code": "teste_rede"}` (sem `label`) | `400` `label é obrigatório...` |
| 13 | `save-category` `{"code": "outros", "active": false}` | `400` `a categoria outros não pode ser desativada` |
| 14 | `save-ticket` `teste-conversa-1` da Maria, `category: "teste_impressora"` | `200` com `ticket_id`, `category_fallback: false` |
| 15 | Repetir o passo 14 com outro `summary` | `200` com o **mesmo** `ticket_id`; registro atualizado |
| 16 | `save-category` `{"code": "teste_impressora", "active": false}`; depois `list-categories` | some da lista; aparece com `include_inactive: true` |
| 17 | `save-ticket` `teste-conversa-2` com `category: "teste_impressora"` (agora inativa) | `200`, `category: "outros"`, `category_fallback: true` |
| 18 | `save-ticket` com e-mail inexistente | `400` `usuário não cadastrado` |
| 19 | `save-ticket` sem `summary` | `400` |

Limpeza dos dados de teste (como dono do banco, já que `support_app` não tem `DELETE`):

```sql
DELETE FROM tickets  WHERE conversation_id LIKE 'teste-%';
DELETE FROM browsers WHERE user_id LIKE 'teste-%';
DELETE FROM users    WHERE email IN ('maria.silva@exemplo.gov.br', 'joao@exemplo.gov.br');
DELETE FROM categories WHERE code LIKE 'teste_%';
```
