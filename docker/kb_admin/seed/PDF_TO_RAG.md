# PDF_TO_RAG

Quero que você converta um arquivo PDF em um pacote estruturado para uso em RAG/LLM, preservando o conteúdo original e tornando-o mais adequado para ingestão em uma base de conhecimento.

## Objetivo

A saída deve ser um pacote contendo:

```text
nome-do-documento-rag/
├── nome-do-documento-rag.md
├── README.md
└── images/
    ├── imagem-01.png
    ├── imagem-02.png
    ├── imagem-03.png
    └── ...
```

O arquivo `.md` deve ser otimizado para leitura por LLMs e sistemas RAG, mantendo referências relativas às imagens extraídas do PDF.

Exemplo:

```markdown
## Etapa 3 — Configuração do sistema

Abra o aplicativo e clique em **Configurações**.

![Tela de configurações](images/03-tela-configuracoes.png)
```

## Regras gerais

1. Preserve fielmente o conteúdo informativo do PDF.
2. Não invente informações.
3. Não complemente o conteúdo usando conhecimento externo, salvo se isso for explicitamente solicitado.
4. Não altere instruções, URLs, nomes de produtos, caminhos de arquivos ou termos técnicos existentes no documento.
5. Corrija apenas problemas óbvios causados pela extração do PDF, como:
   - quebras de linha artificiais;
   - palavras divididas incorretamente;
   - espaços duplicados;
   - cabeçalhos e rodapés repetidos;
   - números de página misturados ao conteúdo.
6. Preserve a ordem lógica do documento original.
7. Preserve títulos, subtítulos, listas, alertas, observações, tabelas e procedimentos.
8. Não reproduza cabeçalhos e rodapés repetitivos em todas as seções, a menos que tragam informação relevante.

## Estrutura Markdown

Use uma hierarquia clara de títulos:

```markdown
# Título principal

## Seção

### Subseção

#### Etapa
```

Prefira seções autocontidas, especialmente quando o documento contiver procedimentos passo a passo.

Evite blocos excessivamente grandes de texto.

Sempre que possível, organize procedimentos como listas numeradas:

```markdown
### Instalação

1. Abra o aplicativo.
2. Clique em **Instalar**.
3. Aguarde a conclusão.
```

Use listas com marcadores para pré-requisitos, opções ou características.

## Otimização para RAG

O documento Markdown será utilizado por sistemas de recuperação semântica.

Portanto:

- cada seção deve conter contexto suficiente para ser compreendida isoladamente;
- evite referências vagas como "conforme mostrado acima", quando puder preservar o contexto;
- mantenha o título da seção próximo das instruções relacionadas;
- não separe uma instrução da imagem que a ilustra;
- não crie seções muito fragmentadas;
- evite concentrar assuntos diferentes no mesmo bloco.

O objetivo é que, caso o sistema de RAG recupere apenas uma seção, ela ainda seja compreensível.

Exemplo recomendado:

```markdown
## Configuração do Microsoft Authenticator — Escanear QR Code

1. Abra o Microsoft Authenticator no celular.
2. Selecione **Adicionar conta**.
3. Escolha **Conta corporativa ou de estudante**.
4. Selecione a opção para escanear QR Code.
5. Aponte a câmera para o QR Code exibido no computador.

![Escaneamento do QR Code](images/authenticator-qr-code.png)
```

## Extração das imagens

Extraia todas as imagens úteis do PDF.

Considere como imagens úteis:

- capturas de tela;
- diagramas;
- fluxogramas;
- ilustrações explicativas;
- gráficos;
- figuras que auxiliem algum procedimento.

Evite extrair elementos puramente decorativos, como:

- logotipos repetidos em todas as páginas;
- cabeçalhos;
- rodapés;
- elementos gráficos sem valor informativo.

Quando uma página contiver várias imagens independentes, tente extraí-las separadamente quando isso fizer sentido.

## Nome dos arquivos de imagem

Use nomes descritivos, estáveis e legíveis.

Prefira:

```text
01-acesso-pasta-office365.png
02-instalador-office.png
03-login-microsoft.png
04-authenticator-adicionar-metodo.png
```

Evite nomes genéricos como:

```text
image1.png
image2.png
img123.png
```

Use:

- letras minúsculas;
- hífens;
- sem espaços;
- sem caracteres especiais;
- numeração sequencial quando útil.

## Referências relativas

Todas as imagens devem ser referenciadas usando caminho relativo.

Use:

```markdown
![Descrição da imagem](images/03-login-microsoft.png)
```

Não use inicialmente URLs absolutas.

Não use:

```markdown
![Imagem](https://servidor/imagens/03-login.png)
```

Não use Base64:

```text
data:image/png;base64,...
```

A finalidade do caminho relativo é permitir que o pacote seja posteriormente publicado em qualquer servidor ou convertido para URLs absolutas pela pipeline de deploy.

## Associação texto ↔ imagem

Este ponto é crítico.

Cada imagem deve aparecer imediatamente após ou próximo do texto que ela ilustra.

Evite:

```markdown
## Procedimento

Clique em Instalar.

... vários parágrafos ...

## Outras informações

...

![Imagem de instalação](images/instalar.png)
```

Prefira:

```markdown
## Procedimento de instalação

Clique em **Instalar**.

![Botão Instalar](images/instalar.png)

Aguarde a conclusão do processo.
```

Uma imagem relacionada a uma etapa não deve ser colocada em outra seção.

## Tabelas

Quando o PDF contiver tabelas simples, converta-as para Markdown.

Exemplo:

```markdown
| Problema | Possível causa | Procedimento |
|---|---|---|
| Office pede ativação | Licença não vinculada | Entre com a conta institucional |
| QR Code não funciona | QR Code expirado | Gere um novo QR Code |
```

Se a tabela for muito complexa e sua conversão para Markdown causar perda significativa de informação, preserve também uma imagem da tabela e explique isso no Markdown.

Exemplo:

```markdown
A tabela completa de configurações é mostrada abaixo.

![Tabela de configurações](images/tabela-configuracoes.png)
```

## Alertas e observações

Preserve avisos importantes.

Use:

```markdown
> **ATENÇÃO:** Nunca aprove uma solicitação de autenticação que você não tenha iniciado.
```

Para notas:

```markdown
> **Nota:** Este procedimento requer acesso à rede institucional.
```

## URLs e caminhos técnicos

Preserve URLs exatamente como aparecem no documento.

Preserve também:

- caminhos de rede;
- caminhos de arquivos;
- nomes de executáveis;
- comandos;
- nomes de sistemas;
- endereços internos.

Use bloco de código quando isso melhorar a legibilidade:

```text
D:\PastaSetores\DTI\Atendimento\Instala\Office 365
```

## Repetições e ruído

Remova:

- número de página repetido;
- cabeçalhos institucionais repetidos;
- rodapés repetidos;
- marcas gráficas sem função informativa.

Não remova conteúdo técnico real.

## Regras exigidas pela ingestão no Dify

O Markdown será processado pelo Dify, que divide o arquivo pelos títulos antes
de cortar o texto em trechos (chunks). As regras abaixo são obrigatórias para que
cada seção chegue inteira ao chatbot, com as suas imagens.

1. **Nome do pacote:** use só letras minúsculas sem acento, números e hífen (ex.:
   `manual-office365-rag`). A pasta, o arquivo `.md` e o `.zip` têm exatamente
   esse mesmo nome.
2. **Títulos:** todo título é uma linha que começa com `#` seguido de espaço
   (`## Seção`). Não use linhas em negrito como título (`**Seção**`), pois o
   Dify não as reconhece. Não use `#` no texto do título (escreva "C Sharp" em vez
   de "C#"), pois o Dify apaga esse caractere.
3. **Tamanho das seções:** cada seção (do título até o próximo título, de qualquer
   nível) deve ter no máximo **3.000 caracteres**, contando as linhas de imagem.
   Se uma seção ficar maior, divida-a com subtítulos que façam sentido sozinhos
   (ex.: "Problemas Comuns — Instalação" e "Problemas Comuns — MFA"). Uma seção
   grande demais é cortada pelo Dify em pontos arbitrários e pode separar um passo
   da sua imagem.
4. **Tamanho das linhas:** nenhuma linha (parágrafo sem quebra, item de lista ou
   linha de tabela) deve passar de **1.000 caracteres**. Não junte vários passos
   numa linha só; quebre parágrafos longos em frases ou itens.
5. **Tabelas:** cada linha da tabela pode ser recuperada sozinha pela busca.
   Escreva cada linha de forma autocontida, repetindo o nome do item quando
   necessário, e não use células como "idem" ou "o mesmo acima". Tabelas com
   mais de 3.000 caracteres devem ser divididas em seções com subtítulos.
6. **Texto entre `<` e `>`:** o Dify apaga tudo o que estiver entre `<` e `>`
   numa mesma linha, inclusive dentro de blocos de código. Não use HTML nem
   marcações como `<Enter>` ou `<nome-do-usuário>`; escreva **Enter** ou
   `nome-do-usuário` (entre crases, sem os sinais de menor e maior).
7. **Imagens:** use somente a forma `![descrição](images/arquivo.png)`, sem
   `<img>` e sem o estilo de referência (`![descrição][id]`). A descrição também
   é usada na busca, então descreva o que a imagem mostra (ex.: "Tela de
   escolha do método de autenticação"), não apenas "Imagem 3".

## README.md

Crie também um `README.md` explicando a estrutura do pacote.

Exemplo:

```markdown
# Pacote RAG

Este pacote contém uma versão estruturada em Markdown do documento original.

## Estrutura

- `manual-rag.md`: conteúdo preparado para RAG/LLM.
- `images/`: imagens extraídas do PDF.

## Referências de imagens

As imagens usam caminhos relativos:

```markdown
![Descrição](images/nome-da-imagem.png)
```

Para publicação em servidor Web, esses caminhos podem ser convertidos posteriormente para URLs absolutas.
```

Inclua no README uma observação de que o Markdown foi reorganizado visando:

- melhor chunking;
- preservação de contexto;
- associação entre texto e imagens;
- recuperação semântica por sistemas RAG.

## Validação final

Antes de concluir:

1. verifique se todas as imagens referenciadas no Markdown existem na pasta `images/`;
2. verifique se não há imagens extraídas sem necessidade;
3. verifique se os caminhos relativos estão corretos;
4. verifique se a ordem das imagens corresponde à ordem lógica do documento;
5. verifique se tabelas foram convertidas corretamente;
6. verifique se alertas foram preservados;
7. verifique se URLs e caminhos técnicos foram preservados;
8. verifique se nenhuma informação foi inventada;
9. verifique se não há referências quebradas;
10. verifique se as seções permanecem semanticamente compreensíveis quando recuperadas isoladamente;
11. verifique se nenhuma seção passa de 3.000 caracteres e nenhuma linha passa de 1.000 caracteres;
12. verifique se não há texto entre `<` e `>` em nenhum lugar, nem em blocos de código;
13. verifique se a pasta, o `.md` e o `.zip` têm o mesmo nome, só com letras minúsculas, números e hífen.

## Entrega

Gere ao final:

1. o arquivo Markdown estruturado;
2. a pasta `images/` com as imagens extraídas;
3. o `README.md`;
4. um arquivo `.zip` contendo o pacote completo.

A estrutura final deve ser semelhante a:

```text
documento-rag/
├── documento-rag.md
├── README.md
└── images/
    ├── 01-imagem.png
    ├── 02-imagem.png
    └── ...
```

O objetivo principal é gerar uma versão do PDF especialmente adequada para ingestão em Dify, RAG ou outro sistema baseado em LLM, mantendo o conteúdo textual e visual semanticamente associados.
