-- Banco próprio do CRM (SQLite). O ERP (Firebird) só é lido; nada aqui volta para ele.

-- Espelho do ERP: reescrito a cada sincronização.
CREATE TABLE IF NOT EXISTS cliente (
    empresa       TEXT NOT NULL,
    codigo        TEXT NOT NULL,
    nome          TEXT,
    telefone      TEXT,            -- só dígitos, com 55
    segmento      TEXT,
    ult_venda     TEXT,
    nascimento    TEXT,            -- AAAA-MM-DD
    ativo         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (empresa, codigo)
);

CREATE TABLE IF NOT EXISTS venda (
    empresa       TEXT NOT NULL,
    codigo        TEXT NOT NULL,
    data          TEXT NOT NULL,   -- AAAA-MM-DD
    cliente       TEXT,
    vendedor      TEXT,
    vendedor_nome TEXT,
    total         REAL NOT NULL,
    codprof       TEXT,            -- eletricista indicado no próprio ERP (COMPPEDIDOC.CODPROF), se houver
    PRIMARY KEY (empresa, codigo)
);
CREATE INDEX IF NOT EXISTS venda_cliente ON venda (empresa, cliente);
CREATE INDEX IF NOT EXISTS venda_data ON venda (data);

-- Orçamentos (DAVs em aberto). situacao: aberto | provavel_baixado | copia
CREATE TABLE IF NOT EXISTS orcamento (
    empresa       TEXT NOT NULL,
    codigo        TEXT NOT NULL,
    data          TEXT NOT NULL,
    cliente       TEXT,
    vendedor      TEXT,
    vendedor_nome TEXT,
    total         REAL NOT NULL,
    situacao      TEXT NOT NULL DEFAULT 'aberto',
    PRIMARY KEY (empresa, codigo)
);

-- Profissionais do cadastro do ERP, usados só como sugestão de importação.
CREATE TABLE IF NOT EXISTS erp_profissional (
    empresa       TEXT NOT NULL,
    codigo        TEXT NOT NULL,
    nome          TEXT,
    telefone      TEXT,
    nascimento    TEXT,
    cpf           TEXT,
    ativo         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (empresa, codigo)
);

-- Dados do CRM (nunca apagados pela sincronização).
CREATE TABLE IF NOT EXISTS profissional (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nome            TEXT NOT NULL,
    telefone        TEXT,
    cpf             TEXT,
    nascimento      TEXT,
    inicio_programa TEXT NOT NULL, -- só vendas desta data em diante geram pontos
    ativo           INTEGER NOT NULL DEFAULT 1,
    obs             TEXT,
    criado_em       TEXT NOT NULL
);

-- Cadastros do próprio eletricista como cliente no ERP (pode ter um em cada empresa).
-- As compras feitas neles contam para o eletricista automaticamente.
CREATE TABLE IF NOT EXISTS profissional_cliente (
    profissional_id INTEGER NOT NULL REFERENCES profissional (id) ON DELETE CASCADE,
    empresa         TEXT NOT NULL,
    cliente_codigo  TEXT NOT NULL,
    PRIMARY KEY (empresa, cliente_codigo)
);

-- Cadastro dele na tabela PROFISSIONAL do ATS: quando o ERP indicar este código numa venda, ela é dele.
CREATE TABLE IF NOT EXISTS profissional_erp (
    profissional_id INTEGER NOT NULL REFERENCES profissional (id) ON DELETE CASCADE,
    empresa         TEXT NOT NULL,
    codprof         TEXT NOT NULL,
    PRIMARY KEY (empresa, codprof)
);

-- Quem trouxe a venda. origem: manual | cliente (compra no próprio nome) | erp (indicado no ATS)
CREATE TABLE IF NOT EXISTS atribuicao (
    empresa         TEXT NOT NULL,
    venda           TEXT NOT NULL,
    profissional_id INTEGER NOT NULL REFERENCES profissional (id),
    origem          TEXT NOT NULL,
    usuario         TEXT,
    criado_em       TEXT NOT NULL,
    PRIMARY KEY (empresa, venda)
);

-- Livro de pontos (1 ponto = R$ 1,00). tipo: acumulo | resgate | ajuste
CREATE TABLE IF NOT EXISTS pontos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profissional_id INTEGER NOT NULL REFERENCES profissional (id),
    tipo            TEXT NOT NULL,
    valor           REAL NOT NULL,
    empresa         TEXT,
    venda           TEXT,
    descricao       TEXT,
    usuario         TEXT,
    criado_em       TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS pontos_acumulo ON pontos (empresa, venda) WHERE tipo = 'acumulo';

-- Cada contato feito (ou tentado) a partir da fila do dia.
CREATE TABLE IF NOT EXISTS contato (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT NOT NULL,
    chave           TEXT NOT NULL, -- identifica o alvo da tarefa (ex.: orçamento, cliente)
    alvo_nome       TEXT,
    empresa         TEXT,
    cliente_codigo  TEXT,
    profissional_id INTEGER,
    resultado       TEXT NOT NULL,
    obs             TEXT,
    usuario         TEXT,
    criado_em       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS contato_chave ON contato (tipo, chave);

CREATE TABLE IF NOT EXISTS usuario (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    login       TEXT NOT NULL UNIQUE,
    nome        TEXT NOT NULL,
    senha_hash  TEXT NOT NULL,
    admin       INTEGER NOT NULL DEFAULT 0,
    ativo       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS config (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS modelo_mensagem (
    tipo  TEXT PRIMARY KEY,
    texto TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sincronizacao (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio  TEXT NOT NULL,
    fim     TEXT,
    ok      INTEGER NOT NULL DEFAULT 0,
    resumo  TEXT
);
