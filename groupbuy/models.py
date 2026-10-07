"""Public products are separate from unsupported personal purchase records."""
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Shop:
    task_id: str
    platform: str
    shop_id: str
    name: Optional[str]
    category: Optional[str]
    classification: str
    classification_basis: Optional[str]
    rating: Optional[str]
    average_price: Optional[str]
    average_price_unit: Optional[str]
    review_count: Optional[str]
    address: Optional[str]
    business_area: Optional[str]
    distance_m: Optional[float]
    distance_text: Optional[str]
    distance_center: Optional[str]
    distance_source: Optional[str]
    captured_at: str
    quality_flags: list
    provenance_id: str


@dataclass
class PublicProduct:
    task_id: str
    platform: str
    shop_id: str
    product_id: Optional[str]
    name: Optional[str]
    product_type: Optional[str]
    sale_price: Optional[str]
    original_price: Optional[str]
    price_unit: Optional[str]
    sales_text: Optional[str]
    visible_rules: Optional[str]
    captured_at: str
    provenance_id: str


@dataclass
class PersonalPurchaseOrder:
    """Distinct model boundary only; no collector, serializer or input support."""
    order_id: str
    authorized_account_reference: str

    def __post_init__(self):
        raise TypeError('Personal purchase orders are unsupported')


def row(model):
    return asdict(model)
