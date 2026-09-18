-- Catalog wizards (/add_product without args) reuse the message + pending_actions machinery but create no transaction.
ALTER TABLE messages DROP CONSTRAINT messages_status_check;
ALTER TABLE messages ADD CONSTRAINT messages_status_check
  CHECK (status IN ('RECEIVED','PROCESSING','AWAITING_CONFIRMATION','COMMITTED','REJECTED','FAILED','DONE'));
