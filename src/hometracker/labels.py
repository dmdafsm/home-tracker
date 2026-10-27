"""Etichetele puse din interfață: salvează, ascunde, confirmat real / fals, „nu e același apartament”."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import DistinctProperties, LabelEvent, Listing, Property, PropertyLabel, utcnow

ACTIONS = {
    "save": ("saved", True),
    "unsave": ("saved", False),
    "hide": ("hidden", True),
    "unhide": ("hidden", False),
    "real": ("verdict", "real"),
    "fake": ("verdict", "fake"),
    "clear_verdict": ("verdict", None),
}


def label_for(session: Session, property_id: int) -> PropertyLabel:
    """Starea curentă (fără să o salveze, dacă nu există încă)."""
    return session.get(PropertyLabel, property_id) or PropertyLabel(property_id=property_id, saved=False, hidden=False)


def labels_for(session: Session, property_ids) -> dict[int, PropertyLabel]:
    ids = list(property_ids)
    if not ids:
        return {}
    return {
        label.property_id: label
        for label in session.scalars(select(PropertyLabel).where(PropertyLabel.property_id.in_(ids)))
    }


def apply_label(session: Session, property_id: int, action: str) -> PropertyLabel:
    if action not in ACTIONS:
        raise ValueError(f"acțiune necunoscută: {action}")
    if session.get(Property, property_id) is None:
        raise LookupError(f"apartament inexistent: {property_id}")
    label = session.get(PropertyLabel, property_id)
    if label is None:
        label = PropertyLabel(property_id=property_id, saved=False, hidden=False)
        session.add(label)
    attribute, value = ACTIONS[action]
    setattr(label, attribute, value)
    label.updated_at = utcnow()
    session.add(LabelEvent(property_id=property_id, action=action))
    session.flush()
    return label


def split_listing(session: Session, listing_id: int) -> int:
    """„Nu e același apartament”: mută anunțul într-un apartament nou și întoarce id-ul lui."""
    listing = session.get(Listing, listing_id)
    if listing is None or listing.property_id is None:
        raise LookupError(f"anunț inexistent sau negrupat: {listing_id}")
    old_property = listing.property_id
    siblings = session.scalars(
        select(Listing).where(Listing.property_id == old_property, Listing.id != listing.id)
    ).all()
    if not siblings:
        raise ValueError("anunțul e singur în apartamentul lui")
    new = Property()
    session.add(new)
    session.flush()
    listing.property_id = new.id
    listing.match_reason, listing.match_signals, listing.match_score = "manual", None, None
    listing.matched_listing_id, listing.republished_from_id, listing.shared_photos = None, None, 0
    for sibling in siblings:  # legăturile care treceau prin anunțul mutat nu mai sunt valabile
        if sibling.republished_from_id == listing.id:
            sibling.republished_from_id = None
    session.add(LabelEvent(property_id=old_property, listing_id=listing.id, action="not_same"))
    # Apartamentul nou nu mai e sugerat ca „posibil același” cu cel din care a fost scos.
    session.add(DistinctProperties(property_a=old_property, property_b=new.id))
    session.flush()
    return new.id
