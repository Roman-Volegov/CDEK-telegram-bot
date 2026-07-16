from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OrderSequence(Base):
    __tablename__ = "order_sequences"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    our_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    cdek_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cdek_number: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    tariff_code: Mapped[int] = mapped_column(Integer, nullable=False)
    tariff_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    to_address: Mapped[str] = mapped_column(Text, nullable=False)
    to_city_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_point: Mapped[str | None] = mapped_column(String(64), nullable=True)

    recipient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_phone: Mapped[str] = mapped_column(String(32), nullable=False)

    item_cost: Mapped[float] = mapped_column(Float, nullable=False)
    delivery_sum: Mapped[float | None] = mapped_column(Float, nullable=True)

    waybill_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    barcode_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserSettings(Base):
    __tablename__ = "user_settings"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserProfile(Base):
    """Настройки и зашифрованные секреты пользователя Telegram."""

    __tablename__ = "user_profiles"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    setup_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    cdek_client_id_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    cdek_client_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    dadata_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    dadata_secret_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    cdek_test_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cdek_shipment_point: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cdek_order_type: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    weight_g: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    length_cm: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    width_cm: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    height_cm: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    item_name: Mapped[str] = mapped_column(String(255), nullable=False, default="Бижутерия")
    item_ware_key: Mapped[str] = mapped_column(String(64), nullable=False, default="JEWELRY-1")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
