# kb_admin: documentos de apoio e perfil somente leitura

Data: 2026-10-02. Adendo à spec `2026-10-01-kb-admin-design.md`.

## Objetivo

1. Criar no `kb_admin` uma aba **Documentos de apoio**: uma biblioteca de arquivos
   (prompts de conversão, manuais de uso etc.) com descrição, em que é possível
   enviar, baixar, substituir, editar a descrição e apagar.
2. Mover o prompt `PDF_TO_RAG.md` do repositório para dentro do app, como primeiro
   documento da biblioteca.
3. Tornar o papel `editor` somente leitura no app inteiro.

## Permissões

| Ação | owner / admin | editor |
|---|---|---|
| Entrar no app | sim | sim |
| Ver lista de manuais, tela do manual, galeria e parâmetros; baixar o `.md` | sim | sim |
| Ver e baixar documentos de apoio | sim | sim |
| Enviar, confirmar ou cancelar substituição e apagar manuais | sim | não |
| Enviar, substituir, editar descrição e apagar documentos | sim | não |

- `ALLOWED_ROLES` continua `owner`, `admin`, `editor`. Um novo conjunto
  `MANAGER_ROLES = {owner, admin}` define quem altera.
- Para o editor, botões e links de alteração não aparecem. As rotas de alteração
  conferem o papel e respondem 403 com "Seu papel no Dify (editor) permite só
  consulta." mesmo se chamadas diretamente.
- O papel é o capturado no login (a sessão dura 8 horas, como já está na spec).

## Navegação

O cabeçalho ganha duas abas, **Manuais** (`/kb-admin/`) e **Documentos de apoio**
(`/kb-admin/docs`), com a aba atual destacada.

## Documentos de apoio

- **Tabela** (`/kb-admin/docs`): nome do arquivo, descrição, tamanho, data da última
  alteração, quem enviou e ações. Baixar aparece para todos; Substituir, Editar
  descrição e Apagar só para owner e admin. Ordem alfabética pelo nome.
- **Enviar** (`/kb-admin/docs/new`): arquivo e descrição (obrigatória, até 500
  caracteres, sem quebras de linha).
  - Qualquer tipo de arquivo, até 50 MB.
  - Nome do arquivo: o nome original sem o caminho. São recusados nomes vazios, `.`
    e `..`, nomes com `/`, `\` ou caracteres de controle e nomes com mais de 200
    caracteres.
  - Nome já existente (comparação sem diferenciar maiúsculas) é recusado, com a
    orientação de usar "Substituir".
- **Substituir** (`/kb-admin/docs/<id>/replace`): envia um arquivo novo para o mesmo
  documento. A descrição é mantida, e o nome passa a ser o do arquivo novo (com a
  mesma regra de nome repetido, ignorando o próprio documento).
- **Editar descrição** (`/kb-admin/docs/<id>/edit`).
- **Apagar** (`/kb-admin/docs/<id>/delete`): pede confirmação.
- **Baixar** (`/kb-admin/docs/<id>/download`): sempre como anexo
  (`Content-Disposition: attachment` com o nome codificado em RFC 5987,
  `Content-Type` adivinhado pela extensão ou `application/octet-stream`, e
  `X-Content-Type-Options: nosniff`).
- Todo formulário de alteração leva o token CSRF.

## Armazenamento

- Volume privado `dify_kb_admin_data`, em `/data/admin/docs/<id>/`: o arquivo com o
  nome original e um `meta.json` (`id`, `filename`, `description`, `size`,
  `updated_at`, `updated_by`).
- O `id` é um token aleatório (`secrets.token_hex(8)`). As rotas só aceitam ids no
  formato `^[0-9a-f]{16}$`.
- A escrita é atômica: o app grava numa pasta oculta temporária e troca pela atual,
  com lock por documento, como nos manuais. A limpeza de temporários na subida
  também vale para `docs/`.

## Conteúdo inicial (seed)

- O `docker/kb_assets/docs/PDF_TO_RAG.md` é movido para
  `docker/kb_admin/seed/PDF_TO_RAG.md` e entra na imagem.
- Na subida, se o marcador `/data/admin/docs/.seeded` não existe, o app cria o
  documento `PDF_TO_RAG.md` com a descrição "Prompt de conversão PDF → Markdown
  (usar na LLM externa para gerar o .zip do manual)", autor "conteúdo inicial", e
  grava o marcador.
- Apagar o documento não o recria. Só um volume novo (por exemplo, depois do
  `remove.sh` com exclusão de volumes) volta a criá-lo.
- Os textos que citam `docker/kb_assets/docs/PDF_TO_RAG.md` passam a citar a aba
  Documentos de apoio: `docker/kb_assets/README.md`, `docker/README.swarm.md`,
  `docker/kb_admin/README.md`, a tela de erro do envio de manual e a spec original.

## Testes

- Armazenamento: criar, listar, substituir (com e sem troca de nome), editar a
  descrição, apagar, nome repetido, nomes inválidos, id inválido, seed só uma vez e
  seed que não volta depois de apagar.
- Web: o editor vê as listas e baixa, mas recebe 403 em todas as rotas de alteração
  de manuais e documentos, e não vê os botões de alteração. O admin faz o fluxo
  completo de documentos. O download sai como anexo com `nosniff`. Todas as
  alterações exigem CSRF.
- Verificação no DEV: deploy, o prompt aparece na aba, download e substituição.
