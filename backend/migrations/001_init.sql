-- KiranaCrew schema. Money is paise BIGINT, quantity NUMERIC(12,3) in the product's base unit.
CREATE TABLE shops (
  id               BIGSERIAL PRIMARY KEY,
  name             TEXT NOT NULL,
  language         TEXT NOT NULL DEFAULT 'hi',
  confirm_above_paise BIGINT NOT NULL DEFAULT 500000,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE shop_members (
  telegram_user_id BIGINT PRIMARY KEY,
  shop_id          BIGINT NOT NULL REFERENCES shops(id),
  role             TEXT NOT NULL DEFAULT 'OWNER' CHECK (role IN ('OWNER','STAFF')),
  display_name     TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE customers (
  id               BIGSERIAL PRIMARY KEY,
  shop_id          BIGINT NOT NULL REFERENCES shops(id),
  name             TEXT NOT NULL,
  aliases          TEXT[] NOT NULL DEFAULT '{}',
  phone            TEXT,
  is_active        BOOLEAN NOT NULL DEFAULT true,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (shop_id, name),
  UNIQUE (shop_id, id)
);

CREATE TABLE products (
  id                  BIGSERIAL PRIMARY KEY,
  shop_id             BIGINT NOT NULL REFERENCES shops(id),
  name                TEXT NOT NULL,
  aliases             TEXT[] NOT NULL DEFAULT '{}',
  base_unit           TEXT NOT NULL CHECK (base_unit IN ('kg','litre','piece','packet')),
  selling_price_paise BIGINT CHECK (selling_price_paise > 0),
  low_stock_threshold NUMERIC(12,3),
  is_active           BOOLEAN NOT NULL DEFAULT true,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (shop_id, name),
  UNIQUE (shop_id, id)
);

CREATE TABLE messages (
  id                  BIGSERIAL PRIMARY KEY,
  shop_id             BIGINT NOT NULL REFERENCES shops(id),
  idempotency_key     TEXT NOT NULL UNIQUE,
  source              TEXT NOT NULL CHECK (source IN ('TELEGRAM','SIMULATOR')),
  telegram_chat_id    BIGINT,
  telegram_user_id    BIGINT,
  telegram_message_id BIGINT,
  kind                TEXT NOT NULL CHECK (kind IN ('VOICE','TEXT')),
  telegram_file_id    TEXT,
  audio_duration_s    INT,
  transcript          TEXT,
  normalized_text     TEXT,
  parsed              JSONB,
  parser_used         TEXT,
  stt_provider        TEXT,
  stt_confidence      REAL,
  decision            JSONB,
  status              TEXT NOT NULL DEFAULT 'RECEIVED'
                        CHECK (status IN ('RECEIVED','PROCESSING','AWAITING_CONFIRMATION',
                                          'COMMITTED','REJECTED','FAILED')),
  error               TEXT,
  attempts            INT NOT NULL DEFAULT 0,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_messages_open ON messages (status, updated_at) WHERE status IN ('RECEIVED','PROCESSING');
CREATE INDEX ix_messages_shop_time ON messages (shop_id, created_at DESC);

CREATE TABLE transactions (
  id                BIGSERIAL PRIMARY KEY,
  shop_id           BIGINT NOT NULL REFERENCES shops(id),
  message_id        BIGINT REFERENCES messages(id),
  type              TEXT NOT NULL CHECK (type IN ('CREDIT_SALE','CASH_SALE','CREDIT_REPAYMENT',
                                                  'INVENTORY_PURCHASE','STOCK_ADJUSTMENT','REVERSAL')),
  customer_id       BIGINT,
  product_id        BIGINT,
  quantity          NUMERIC(12,3) CHECK (quantity > 0),
  unit              TEXT,
  unit_price_paise  BIGINT,
  amount_paise      BIGINT CHECK (amount_paise >= 0),
  price_source      TEXT CHECK (price_source IN ('CATALOG','EXPLICIT_TOTAL','EXPLICIT_UNIT','USER_REPLY')),
  reverses_transaction_id BIGINT UNIQUE REFERENCES transactions(id),
  created_by        TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (message_id),
  UNIQUE (shop_id, id),
  FOREIGN KEY (shop_id, customer_id) REFERENCES customers(shop_id, id),
  FOREIGN KEY (shop_id, product_id)  REFERENCES products(shop_id, id)
);
CREATE INDEX ix_txn_shop_time ON transactions (shop_id, created_at DESC);

CREATE TABLE inventory_movements (
  id             BIGSERIAL PRIMARY KEY,
  shop_id        BIGINT NOT NULL,
  transaction_id BIGINT NOT NULL,
  product_id     BIGINT NOT NULL,
  qty_delta      NUMERIC(12,3) NOT NULL CHECK (qty_delta <> 0),
  reason         TEXT NOT NULL CHECK (reason IN ('SALE','PURCHASE','DAMAGED','EXPIRED','LOST',
                                                 'FOUND','CORRECTION','REVERSAL')),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (shop_id, transaction_id) REFERENCES transactions(shop_id, id),
  FOREIGN KEY (shop_id, product_id)     REFERENCES products(shop_id, id)
);
CREATE INDEX ix_inv_shop_product ON inventory_movements (shop_id, product_id);

CREATE TABLE credit_ledger (
  id             BIGSERIAL PRIMARY KEY,
  shop_id        BIGINT NOT NULL,
  transaction_id BIGINT NOT NULL,
  customer_id    BIGINT NOT NULL,
  amount_paise   BIGINT NOT NULL CHECK (amount_paise <> 0),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (shop_id, transaction_id) REFERENCES transactions(shop_id, id),
  FOREIGN KEY (shop_id, customer_id)    REFERENCES customers(shop_id, id)
);
CREATE INDEX ix_ledger_shop_customer ON credit_ledger (shop_id, customer_id);

CREATE TABLE pending_actions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  shop_id          BIGINT NOT NULL REFERENCES shops(id),
  message_id       BIGINT NOT NULL REFERENCES messages(id),
  kind             TEXT NOT NULL CHECK (kind IN ('CHOOSE_CUSTOMER','CHOOSE_PRODUCT','CHOOSE_PAYMENT',
                                                 'CONFIRM_NEW_CUSTOMER','CONFIRM_TRANSCRIPT',
                                                 'CONFIRM_LARGE_AMOUNT','ENTER_TEXT')),
  field            TEXT,
  draft            JSONB NOT NULL,
  options          JSONB NOT NULL DEFAULT '[]',
  status           TEXT NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING','RESOLVED','CANCELLED','EXPIRED')),
  telegram_chat_id BIGINT,
  telegram_reply_message_id BIGINT,
  expires_at       TIMESTAMPTZ NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at      TIMESTAMPTZ
);
CREATE UNIQUE INDEX ux_pending_one_per_message ON pending_actions (message_id) WHERE status = 'PENDING';
CREATE INDEX ix_pending_open_chat ON pending_actions (telegram_chat_id) WHERE status = 'PENDING';

CREATE TABLE jobs (
  id           BIGSERIAL PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('PROCESS_MESSAGE','RESOLVE_CALLBACK','DAILY_SUMMARY',
                                             'FORECAST','INSIGHTS','TEST_FAIL')),
  payload      JSONB NOT NULL,
  dedupe_key   TEXT UNIQUE,
  status       TEXT NOT NULL DEFAULT 'QUEUED'
                 CHECK (status IN ('QUEUED','RUNNING','DONE','FAILED','DEAD')),
  attempts     INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 5,
  run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_error   TEXT,
  locked_at    TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_jobs_ready ON jobs (run_after) WHERE status = 'QUEUED';

CREATE TABLE llm_calls (
  id            BIGSERIAL PRIMARY KEY,
  message_id    BIGINT REFERENCES messages(id),
  stage         TEXT NOT NULL CHECK (stage IN ('STT','PARSE','INSIGHTS','ASK')),
  provider      TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT,
  latency_ms    INT NOT NULL, input_tokens INT, output_tokens INT,
  ok            BOOLEAN NOT NULL, error TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_llm_calls_msg ON llm_calls (message_id);

CREATE TABLE forecasts (
  id           BIGSERIAL PRIMARY KEY,
  shop_id      BIGINT NOT NULL REFERENCES shops(id),
  product_id   BIGINT NOT NULL,
  horizon_date DATE NOT NULL,
  qty_forecast NUMERIC(12,3) NOT NULL,
  model        TEXT NOT NULL, baseline_mase REAL, model_mase REAL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (shop_id, product_id, horizon_date, model),
  FOREIGN KEY (shop_id, product_id) REFERENCES products(shop_id, id)
);

CREATE TABLE insights (
  id         BIGSERIAL PRIMARY KEY,
  shop_id    BIGINT NOT NULL REFERENCES shops(id),
  week_start DATE NOT NULL,
  body       TEXT NOT NULL,
  facts      JSONB NOT NULL DEFAULT '{}',
  approved   BOOLEAN NOT NULL DEFAULT false,
  sent_at    TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (shop_id, week_start)
);

CREATE TABLE audit_logs (
  id         BIGSERIAL PRIMARY KEY,
  shop_id    BIGINT,
  actor      TEXT NOT NULL,
  action     TEXT NOT NULL,
  entity     TEXT, entity_id TEXT,
  details    JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_shop_time ON audit_logs (shop_id, created_at DESC);

CREATE TABLE worker_heartbeats (
  worker_id  TEXT PRIMARY KEY,
  seen_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE VIEW v_product_stock AS
  SELECT p.shop_id, p.id AS product_id, p.name, p.base_unit,
         COALESCE(SUM(m.qty_delta), 0) AS stock, p.low_stock_threshold, p.selling_price_paise, p.is_active
  FROM products p
  LEFT JOIN inventory_movements m ON m.shop_id = p.shop_id AND m.product_id = p.id
  GROUP BY p.shop_id, p.id;

CREATE VIEW v_customer_balance AS
  SELECT c.shop_id, c.id AS customer_id, c.name,
         COALESCE(SUM(l.amount_paise), 0) AS balance_paise, MAX(l.created_at) AS last_activity_at
  FROM customers c
  LEFT JOIN credit_ledger l ON l.shop_id = c.shop_id AND l.customer_id = c.id
  GROUP BY c.shop_id, c.id;
