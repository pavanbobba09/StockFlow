from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Float, Date, Time, DateTime,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, func,
)
from sqlalchemy.orm import relationship
from data.db import Base


class Store(Base):
    __tablename__ = "stores"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    inventory = relationship("Inventory", foreign_keys="Inventory.location_id",
                             primaryjoin="and_(Inventory.location_id==Store.id, "
                                         "Inventory.location_type=='store')",
                             viewonly=True)
    demand_history = relationship("DemandHistory", back_populates="store")
    delivery_schedules = relationship("DeliverySchedule", back_populates="store")
    orders = relationship("Order", back_populates="store")


class Warehouse(Base):
    __tablename__ = "warehouses"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    shelf_life_days = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    demand_history = relationship("DemandHistory", back_populates="item")
    orders = relationship("Order", back_populates="item")


class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True)
    location_id = Column(Integer, nullable=False)
    location_type = Column(String(50), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
    expiry_date = Column(Date, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("location_type IN ('store', 'warehouse')", name="ck_location_type"),
        Index("idx_inventory_location", "location_id", "location_type"),
        Index("idx_inventory_item", "item_id"),
    )

    item = relationship("Item")


class DemandHistory(Base):
    __tablename__ = "demand_history"

    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    date = Column(Date, nullable=False)
    quantity = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_demand_history_store_item", "store_id", "item_id", "date"),
    )

    store = relationship("Store", back_populates="demand_history")
    item = relationship("Item", back_populates="demand_history")


class DeliverySchedule(Base):
    __tablename__ = "delivery_schedules"

    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    weekday = Column(Integer, nullable=False)  # 0=Mon … 6=Sun
    cutoff_time = Column(Time, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_weekday"),
        Index("idx_delivery_schedules_store", "store_id"),
    )

    store = relationship("Store", back_populates="delivery_schedules")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    idempotency_key = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'fulfilled')",
            name="ck_order_status",
        ),
        Index("idx_orders_store", "store_id"),
        Index("idx_orders_status", "status"),
    )

    store = relationship("Store", back_populates="orders")
    item = relationship("Item", back_populates="orders")


class Transfer(Base):
    __tablename__ = "transfers"

    id = Column(Integer, primary_key=True)
    from_store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    to_store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    idempotency_key = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'fulfilled')",
            name="ck_transfer_status",
        ),
        Index("idx_transfers_from_store", "from_store_id"),
        Index("idx_transfers_to_store", "to_store_id"),
        Index("idx_transfers_status", "status"),
    )

    item = relationship("Item")
