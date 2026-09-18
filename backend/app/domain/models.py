from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TxType = Literal["CREDIT_SALE", "CASH_SALE", "SALE", "CREDIT_REPAYMENT",
                 "INVENTORY_PURCHASE", "STOCK_ADJUSTMENT"]
Unit = Literal["kg", "g", "litre", "ml", "piece", "packet", "dozen"]

# Parser-side flags. Never produced by the LLM (its contract is LLMExtraction below).
FLAG_MULTIPLE_ITEMS = "MULTIPLE_ITEMS"        # two products / two customers / two actions in one sentence
FLAG_UNSUPPORTED = "UNSUPPORTED"              # e.g. cash handed to a customer (not modelled in v1)
FLAG_EXTRA_NUMBERS = "EXTRA_NUMBERS"          # numbers the parser could not attach to a field


class LLMExtraction(BaseModel):
    """Exactly what an LLM is allowed to return. Mentions only, never IDs or totals it computed."""
    model_config = ConfigDict(extra="forbid")
    transaction_type: TxType | None = None
    customer_mention: str | None = Field(default=None, max_length=80)
    product_mention: str | None = Field(default=None, max_length=80)
    quantity: Decimal | None = Field(default=None, gt=0, le=100000)
    unit: Unit | None = None
    total_amount_rupees: Decimal | None = Field(default=None, gt=0, le=10_000_000)
    unit_price_rupees: Decimal | None = Field(default=None, gt=0, le=1_000_000)
    unit_price_unit: Unit | None = None
    adjustment_direction: Literal["OUT", "IN"] | None = None


class ParsedCommand(LLMExtraction):
    """LLMExtraction plus parser-side signals. This is what decide() consumes."""
    is_question: bool = False
    flags: list[str] = Field(default_factory=list)

    def is_complete(self) -> bool:
        """True when the rules produced everything decide() needs for this type (hybrid parser gate)."""
        t = self.transaction_type
        if t is None or self.is_question or self.flags:
            return False
        if t == "CREDIT_REPAYMENT":
            return bool(self.customer_mention and self.total_amount_rupees)
        if t == "STOCK_ADJUSTMENT":
            return bool(self.product_mention and self.quantity)
        if t == "INVENTORY_PURCHASE":
            return bool(self.product_mention and self.quantity)
        if t in {"CREDIT_SALE", "SALE"}:
            return bool(self.customer_mention and self.product_mention and self.quantity)
        if t == "CASH_SALE":
            return bool(self.product_mention and self.quantity)
        return False


@dataclass
class Customer:
    id: int
    name: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class Product:
    id: int
    name: str
    base_unit: str
    selling_price_paise: int | None
    aliases: list[str] = field(default_factory=list)


@dataclass
class Resolution:
    status: Literal["RESOLVED", "AMBIGUOUS", "UNKNOWN", "MISSING", "MULTIPLE"]
    value: object | None = None
    candidates: list = field(default_factory=list)


@dataclass
class Overrides:
    """Answers the user has already given (from pending_actions). decide() applies them before asking again."""
    customer_id: int | None = None
    product_id: int | None = None
    payment: str | None = None              # "CREDIT" | "CASH" resolves a bare SALE
    confirm_large_amount: bool = False
    unit_price_paise: int | None = None     # USER_REPLY price, per base unit
    quantity_base: Decimal | None = None    # USER_REPLY quantity, in base unit
    total_amount_paise: int | None = None   # USER_REPLY total (repayment amount or sale total)
    new_customer_name: str | None = None    # CONFIRM_NEW_CUSTOMER accepted; services create the row
    new_product_name: str | None = None     # "naya item" accepted; unit/price collected, then services create the row
    new_product_unit: str | None = None     # kg | litre | piece | packet
    new_product_price_paise: int | None = None   # None = not asked yet; 0 = skipped (no catalog price)
    transcript_confirmed: bool = False
    catalog_only: bool = False              # /add_product wizard: create the item, book nothing


@dataclass
class Decision:
    action: Literal["COMMIT", "ASK", "REJECT"]
    reason: str | None = None
    type: str | None = None
    customer: Customer | None = None
    product: Product | None = None
    quantity_base: Decimal | None = None
    unit: str | None = None
    unit_price_paise: int | None = None
    amount_paise: int | None = None
    price_source: str | None = None
    direction: str | None = None
    candidates: list = field(default_factory=list)
    customer_mention: str | None = None
    product_mention: str | None = None
